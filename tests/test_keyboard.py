import threading

import pytest

from rs_keylights.keyboard import Keyboard, KeyboardError, PolledMidiIn


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
    keyboard.set_voice(11, 30, 2)  # the tone it has (parts played again with other lit keys): not sent again
    assert out.sent == []
    keyboard.set_voice(11, 30, 2, volume=80)  # another volume: just that
    keyboard.set_volume(11, 80)  # (the same: nothing)
    keyboard.set_volume(13, 110)
    assert out.sent == [[0xBA, 7, 80], [0xBC, 7, 110]]
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


class FakeMidiIn:
    def __init__(self):
        self.callback, self.opened = None, None

    def get_ports(self):
        return ["IAC Driver Bus 1", "CASIO USB-MIDI"]

    def open_port(self, port=0, name=None):
        self.opened = port

    def set_callback(self, func, data=None):
        self.callback = func

    def close_port(self):
        self.opened = None


def test_keys_pressed_on_the_keyboard_are_heard_but_not_its_own_notes_played_back():
    out, midi_in = FakeMidiOut(), FakeMidiIn()
    keyboard = Keyboard(midi_out_factory=lambda: out, midi_in_factory=lambda: midi_in)
    keyboard.open()
    assert midi_in.opened == 1
    keyboard.set_voice(11, 81)
    for message in ([0x93, 60, 100], [0x9A, 60, 100], [0x80, 60, 0], [0x90, 62, 0]):  # lights, sound, offs
        midi_in.callback((message, 0.0), None)
    assert not keyboard.pressed()
    midi_in.callback(([0x90, 64, 90], 0.0), None)  # a key pressed (the keyboard's own channel, 1)
    assert keyboard.pressed() and not keyboard.pressed()
    keyboard.close()
    assert midi_in.opened is None
    assert Keyboard(midi_out_factory=lambda: FakeMidiOut())._in_factory is None  # a stand-in output: no input


def test_keys_held_on_another_keyboard_light_first_and_give_the_songs_lights_back():
    keyboard, out, _clock = make()
    keyboard.open()
    keyboard.light((48, 52, 55, 60))  # the song lights all 4
    out.sent.clear()
    keyboard.hold((72,))  # played on the Launchkey: the song's highest goes dark for it
    assert out.sent == [[0x83, 60, 0], [0x93, 72, 100]] and keyboard.pressed()
    assert keyboard.lit == {48, 52, 55, 60}  # (still the song's, as a show sees them)
    keyboard.hold((55,))  # one the song lights too: lit once, and its 60 stays dark
    keyboard.unlight((48,))  # the song moves on: its 60 has room again
    keyboard.light((50,))
    out.sent.clear()
    keyboard.let_go((72, 55))
    assert out.sent == [[0x83, 72, 0], [0x93, 60, 100]]  # 55 stays: the song lights it
    keyboard.hold((61, 62, 63, 64, 65))  # 5 held: the latest 4 light, and none of the song's
    assert sorted(keyboard._on) == [62, 63, 64, 65]
    keyboard.let_go_all()
    assert sorted(keyboard._on) == [50, 52, 55, 60] and not keyboard.holding


def test_notes_from_another_keyboard_sound_on_a_channel_of_their_own():
    keyboard, out, _clock = make()
    keyboard.open()
    keyboard.set_voice(1, 0, 2, ours=False)  # the keyboard's own part: its own keys still count as pressed
    out.sent.clear()
    keyboard.sound(1, 60, 90)
    keyboard.control(1, 64, 127)  # its sustain pedal
    keyboard.control(1, 1, 90)  # and modulation
    keyboard.sound(1, 60, 0)
    keyboard.sound(1, 62, 0)  # never played: nothing
    keyboard.sound(1, 64, 70)
    assert out.sent == [[0x90, 60, 90], [0xB0, 64, 127], [0xB0, 1, 90], [0x80, 60, 0], [0x90, 64, 70]] and keyboard.holding
    out.sent.clear()
    keyboard.let_go_all()  # only what it played: no All Notes Off on the keyboard's own part
    assert out.sent == [[0x80, 64, 0], [0xB0, 1, 0], [0xB0, 64, 0]] and not keyboard.holding  # pedal up, none
    keyboard.close()
    with pytest.raises(KeyboardError):
        keyboard.sound(1, 60, 90)  # closed


def test_a_midi_input_is_read_from_a_thread_of_ours():
    class Port:  # python-rtmidi's input, asked for what came
        def __init__(self):
            self.waiting, self.opened = [([0x90, 60, 90], 0.0)], None

        def get_ports(self):
            return ["CASIO USB-MIDI"]

        def open_port(self, port=0, name=None):
            self.opened = port

        def get_message(self):
            return self.waiting.pop(0) if self.waiting else None

        def close_port(self):
            self.opened = None

    port, heard = Port(), []
    done = threading.Event()
    midi_in = PolledMidiIn(port)
    midi_in.set_callback(lambda event, data=None: (heard.append(event), done.set()))
    midi_in.open_port(0)
    assert done.wait(1) and heard == [([0x90, 60, 90], 0.0)]
    midi_in.close_port()
    assert port.opened is None
