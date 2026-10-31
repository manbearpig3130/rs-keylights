from rs_keylights.bitmap import Bitmap
from rs_keylights.launchkey import STATIONARY, TEMPORARY, Button, Launchkey, Pad, ScaleMode, Turn, step_at

PORTS = ["CASIO USB-MIDI", "Launchkey Mini MK4 25 MIDI In", "Launchkey Mini MK4 25 DAW In"]


class FakeOut:
    def __init__(self, ports=PORTS):
        self.ports, self.sent, self.opened = ports, [], None

    def get_ports(self):
        return self.ports

    def open_port(self, port=0, name=None):
        self.opened = self.ports[port]

    def send_message(self, message):
        self.sent.append(message)

    def close_port(self):
        self.opened = None


class FakeIn:
    def __init__(self, ports=("Launchkey Mini MK4 25 MIDI Out", "Launchkey Mini MK4 25 DAW Out")):
        self.ports, self.callback, self.opened = list(ports), None, None

    def get_ports(self):
        return self.ports

    def open_port(self, port=0, name=None):
        self.opened = self.ports[port]

    def set_callback(self, func, data=None):
        self.callback = func

    def close_port(self):
        self.opened = None

    def receive(self, *message):
        self.callback((list(message), 0.0), None)


def opened(clock=lambda: 1.0):
    out, ins = FakeOut(), []

    def new_in():
        ins.append(FakeIn())
        return ins[-1]

    launchkey = Launchkey(lambda: out, new_in, clock=clock)
    assert launchkey.open()
    return launchkey, out, ins[0]  # its DAW port; ins[1]: its keys' port


def test_it_takes_over_the_daw_port_and_gives_it_back():
    launchkey, out, in_ = opened()
    assert (out.opened, in_.opened) == ("Launchkey Mini MK4 25 DAW In", "Launchkey Mini MK4 25 DAW Out")
    assert launchkey._keys_in.opened == "Launchkey Mini MK4 25 MIDI Out"  # its MIDI port, for the touch strips
    assert out.sent[:4] == [[0x9F, 0x0C, 0x7F], [0x9F, 0x0B, 0x7F], [0xB6, 0x1D, 0x02], [0xB6, 0x1E, 0x05]]
    assert [0xF0, 0x00, 0x20, 0x29, 0x02, 0x13, 0x04, 0x20, 0x02, 0xF7] in out.sent  # the screen: 3 lines
    head = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x13]
    assert all([*head, 0x04, target, 0x02, 0xF7] in out.sent for target in range(0x15, 0x1D))  # no 0 when knobs turn
    assert out.sent[-1] == [0xB7, 0x4A, 0]  # is its Scale mode on? (it answers as when its button is pressed)
    launchkey.close()
    assert out.sent[-1] == [0x9F, 0x0C, 0x00] and out.opened is None
    assert not Launchkey(lambda: FakeOut(["CASIO USB-MIDI"]), lambda: FakeIn([])).open()


def test_pads_encoders_and_buttons_become_events():
    launchkey, _out, in_ = opened()
    in_.receive(0x90, 98, 100)  # the top row's third pad
    in_.receive(0x90, 98, 0)  # let go
    in_.receive(0x90, 112, 90)  # the bottom row's first
    in_.receive(0xBF, 86, 66)  # the second encoder, 2 steps clockwise
    in_.receive(0xBF, 85, 63)  # the first encoder, a step back
    in_.receive(0xBF, 104, 127)
    in_.receive(0xBF, 115, 127)  # Play
    in_.receive(0xFA)  # a MIDI Start: Play too
    in_.receive(0xB6, 0x1E, 0x02)  # a mode report: nothing to do
    in_.receive(0xBF, 23, 40)  # the third encoder in a mode reporting positions: where it is...
    in_.receive(0xBF, 23, 43)  # ... then 3 steps on
    in_.receive(0xBF, 117, 127)  # Record
    in_.receive(0xBF, 106, 127)  # Track back
    in_.receive(0xB6, 0x68, 2)  # a feature control's report that shares a button's number: not the button
    in_.receive(0xB6, 0x4A, 127)  # its Scale button: Scale mode on
    assert launchkey.events() == [Pad(0, 2), Pad(1, 0), Turn(1, 2), Turn(0, -1), Button(104), Button(115), Button(115),
                                  Turn(2, 3), Button(117), Button(106), ScaleMode(True)]
    keys = launchkey._keys_in
    keys.receive(0xE0, 0x00, 0x60)  # the pitch strip, three quarters up
    keys.receive(0xB0, 1, 90)  # the modulation strip: no handler for it here
    keys.receive(0x90, 60, 100)  # a key: not the remote's
    strips = launchkey.events()
    assert [(e.name, e.value) for e in strips] == [("pitch", 12288)] and strips[0].at > 0
    assert launchkey.events() == []


def test_colours_and_text():
    launchkey, out, _in = opened()
    out.sent.clear()
    launchkey.pad(0, 1, 9)
    launchkey.pad(0, 2, 21, pulse=True)
    launchkey.pad_rgb(1, 7, 127, 45, 0)
    launchkey.button(104, 3, 127)
    launchkey.show(TEMPORARY, "Lead", "Tone", "296 GM OVERDRIVE ♭é■")
    head = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x13]
    assert out.sent[:5] == [[0x90, 97, 9], [0x92, 98, 21], [*head, 0x01, 0x43, 119, 127, 45, 0, 0xF7], [0xB0, 104, 3],
                            [0xB3, 104, 127]]  # a button: its colour, or its brightness if it has one colour
    assert out.sent[5] == [*head, 0x06, TEMPORARY, 0, *b"Lead", 0xF7]
    assert out.sent[7][-5:] == [0x20, 0x1D, ord("?"), 0x1C, 0xF7]  # the flat sign, ? for what the screen lacks, a box
    assert out.sent[8] == [*head, 0x04, TEMPORARY, 0x7F, 0xF7]  # brought up
    launchkey.show(STATIONARY, "Song", "E minor", "E F# G")
    assert out.sent[-1] == [*head, 0x04, STATIONARY, 0x7F, 0xF7]  # brought up too
    out.sent.clear()
    launchkey.show(STATIONARY, "Song", "E minor", "E F# G")  # the same again: only brought up
    assert out.sent == [[*head, 0x04, STATIONARY, 0x7F, 0xF7]]


def test_its_scale_mode_takes_a_songs_key():
    launchkey, out, _in = opened()
    out.sent.clear()
    launchkey.scale(1, "minor")
    launchkey.scale(4, "Phrygian")
    launchkey.scale(4, "Locrian")  # not one of its scales here: left alone
    assert out.sent == [[0xB6, 0x3D, 1], [0xB6, 0x3E, 2], [0xB6, 0x3D, 4], [0xB6, 0x3E, 6]]  # numbered from 1


def test_its_keys_pedal_and_modulation_go_straight_to_a_handler_and_its_pitch_strip_stays_events():
    launchkey, out, _in = opened()
    heard = []
    launchkey.on_keys = heard.append
    keys = launchkey._keys_in
    out.sent.clear()
    for message in ([0x94, 60, 100], [0xB4, 64, 127], [0x84, 60, 0], [0xE4, 0, 0x60], [0xB4, 1, 90], [0xB4, 33, 5]):
        keys.receive(*message)
    assert heard == [[0x94, 60, 100], [0xB4, 64, 127], [0x84, 60, 0], [0xB4, 1, 90], [0xB4, 33, 5]]  # as they came,
    assert out.sent == []  # on its MIDI thread
    assert [(e.name, e.value) for e in launchkey.events()] == [("pitch", 12288)]
    launchkey.on_keys = lambda message: 1 / 0  # a handler that fails: the next key tries again
    keys.receive(0x94, 62, 100)
    assert launchkey.events() == []


HEAD = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x13]
def test_pictures_on_the_screen():
    launchkey, out, _in = opened()
    out.sent.clear()
    data = Bitmap().data()
    launchkey.picture(STATIONARY, data)
    launchkey.hide(TEMPORARY)
    assert out.sent == [[*HEAD, 0x09, STATIONARY, *data, 0xF7], [*HEAD, 0x04, TEMPORARY, 0, 0xF7]]


def test_a_strips_steps_change_half_a_step_past_the_next():
    assert step_at(40 * 128 + 100, None) == 40
    assert step_at(41 * 128 + 10, 40) == 40 and step_at(41 * 128 + 70, 40) == 41  # wavering at the edge: not yet
    assert step_at(40 * 128 + 127, 41) == 41 and step_at(20 * 128, 41) == 20  # (back, and far)
