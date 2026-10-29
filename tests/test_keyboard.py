import pytest

from rs_keylights.keyboard import Keyboard, KeyboardError


class FakeMidiOut:
    def __init__(self, ports=("IAC Driver Bus 1", "CASIO USB-MIDI")):
        self.ports = list(ports)
        self.opened = None
        self.sent = []
        self.closed = False

    def get_ports(self):
        return self.ports

    def open_port(self, port=0, name=None):
        self.opened = port

    def send_message(self, message):
        self.sent.append(message)

    def close_port(self):
        self.closed = True


def make(ports=("IAC Driver Bus 1", "CASIO USB-MIDI"), now=None):
    out = FakeMidiOut(ports)
    clock = now or [0.0]
    keyboard = Keyboard("casio", 4, midi_out_factory=lambda: out, clock=lambda: clock[0])
    return keyboard, out, clock


def test_silent_lights_on_the_navigate_channel():
    keyboard, out, _clock = make()
    keyboard.open()
    assert out.opened == 1
    assert out.sent == [[0xB3, 7, 0]]
    keyboard.light((60, 64))
    keyboard.unlight((60,))
    assert out.sent[1:] == [[0x93, 60, 100], [0x93, 64, 100], [0x83, 60, 0]]
    assert keyboard.lit == {64}
    keyboard.close()
    assert out.sent[-3:] == [[0x83, 64, 0], [0xB3, 123, 0], [0xB3, 7, 100]]
    assert out.closed


def test_silences_again_now_and_then():
    keyboard, out, clock = make()
    keyboard.open()
    clock[0] = 6.0
    keyboard.light((60,))
    assert out.sent[1:] == [[0xB3, 7, 0], [0x93, 60, 100]]


def test_sound_on_channels_of_its_own():
    keyboard, out, _clock = make()
    keyboard.open()
    keyboard.set_voice(11, 30, 2)
    keyboard.set_voice(13, 28)
    assert out.sent[1:] == [[0xBA, 0, 2], [0xCA, 30], [0xBA, 7, 100], [0xCC, 28], [0xBC, 7, 100]]
    del out.sent[:]
    keyboard.play(11, (47, 54), 127)
    keyboard.play(13, (47,), 100)
    keyboard.stop(11, (47, 54))
    keyboard.stop(11, (47,))  # already stopped: nothing
    assert out.sent == [[0x9A, 47, 127], [0x9A, 54, 127], [0x9C, 47, 100], [0x8A, 47, 0], [0x8A, 54, 0]]
    del out.sent[:]
    keyboard.all_off()  # the lights' channel, then each sound channel
    assert out.sent == [[0xB3, 123, 0], [0xBA, 123, 0], [0x8C, 47, 0], [0xBC, 123, 0]]


def test_unlighting_an_unlit_key_sends_nothing():
    keyboard, out, _clock = make()
    keyboard.open()
    keyboard.unlight((60,))
    assert out.sent == [[0xB3, 7, 0]]


def test_missing_port():
    keyboard, _out, _clock = make(ports=("IAC Driver Bus 1",))
    with pytest.raises(KeyboardError, match="No MIDI output named 'casio'"):
        keyboard.open()


def test_bad_settings():
    with pytest.raises(ValueError):
        Keyboard(channel=17)
    keyboard, _out, _clock = make()
    keyboard.open()
    with pytest.raises(ValueError, match="not the lights'"):
        keyboard.set_voice(4, 30)
    with pytest.raises(ValueError):
        keyboard.set_voice(11, 128)


def test_lighting_a_lit_key_sends_its_note_on_again():
    # pressing a lit key turns its light off on the keyboard: the next light must reach it
    keyboard, out, _clock = make()
    keyboard.open()
    keyboard.light((60,))
    keyboard.light((60,))
    keyboard.unlight((60,))
    keyboard.unlight((60,))
    assert out.sent[1:] == [[0x93, 60, 100], [0x93, 60, 100], [0x83, 60, 0]]
