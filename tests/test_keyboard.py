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


def make(sound=False, program=None, bank=None, ports=("IAC Driver Bus 1", "CASIO USB-MIDI"), now=None):
    out = FakeMidiOut(ports)
    clock = now or [0.0]
    keyboard = Keyboard("casio", 4, sound=sound, program=program, bank=bank, midi_out_factory=lambda: out, clock=lambda: clock[0])
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


def test_with_sound_and_a_tone():
    keyboard, out, _clock = make(sound=True, program=30, bank=2)
    keyboard.open()
    assert out.sent == [[0xB3, 0, 2], [0xC3, 30], [0xB3, 7, 100]]
    keyboard.close()
    assert out.sent[-1] == [0xB3, 123, 0]


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
    with pytest.raises(ValueError):
        Keyboard(program=128)
    with pytest.raises(ValueError):
        Keyboard(bank=-1)


def test_set_tone_later():
    keyboard, out, _clock = make(sound=True)
    keyboard.open()
    keyboard.set_tone(33, 2)
    keyboard.set_tone(30)
    assert out.sent[1:] == [[0xB3, 0, 2], [0xC3, 33], [0xC3, 30]]
