from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Callable, cast

from .keyboard import MidiInputProtocol, MidiOutputProtocol, midi_in

# Novation's Launchkey MK4 (the Mini, or the larger ones) as a remote, through its DAW port (Launchkey MK4
# Programmer's Reference Guide, v2). In DAW mode its 16 pads report notes and take colours, its 8 encoders
# and its buttons report Control Changes, and its screen shows text and pictures (bitmaps) from the host. The
# encoders are put in Transport mode, where they report steps (relative); in the other modes they report
# positions, and the change from the last one is the steps. With its feature controls all on, it says when its Scale mode goes on
# or off. Only its Scale button turns that on and off for good: told to by message (feature control 4Ah), it
# says it did, but its Scale light and its scale screen stay as they were, the screen's scale gone wrong. So
# it's only asked whether it's on, and given the song's key. Its keys and touch strips go on playing on its
# MIDI port, where they're read too: the keys, a sustain pedal and the modulation strip handed on at once, as
# they come, the pitch strip as events. The modulation strip shows its value on the screen by itself as it
# moves (a host can't turn that off, though it can put it away each time). Leaving DAW mode gives it back.

PADS = (tuple(range(96, 104)), tuple(range(112, 120)))  # notes on channel 1: the top row, the bottom row
ENCODERS = tuple(range(85, 93))  # Control Changes on channel 16 in Transport mode: steps, 64 being none
ENCODER_POSITIONS = tuple(range(21, 29))  # in the Plugin, Mixer and Sends modes: positions
SIDE_BUTTONS = (104, 105)  # the buttons right of the pads, top and bottom: Control Changes, coloured the same
PLAY, RECORD = 115, 117  # the Play and Record buttons' Control Changes (a MIDI Start or Stop counts as Play)
TRACK_BUTTONS = (106, 107)  # left of the pads: Track back and on
ENCODER_BUTTONS = (51, 52)  # right of the encoders
BUTTONS = (*SIDE_BUTTONS, PLAY, RECORD, *TRACK_BUTTONS, *ENCODER_BUTTONS)
REALTIME_START_STOP = (0xFA, 0xFB, 0xFC)  # Start, Continue, Stop
SCALE_TONIC, SCALE_TYPE, SCALE_MODE = 0x3D, 0x3E, 0x4A  # feature controls on channel 7: its Scale mode's root
# note and scale, and whether it's on (reported when the Scale button is pressed, or when asked on channel 8)
ASK_SCALE_MODE = [0xB7, SCALE_MODE, 0]
MODULATION, MODULATION_FINE, SUSTAIN = 1, 33, 64  # Control Changes on the keys' channel: the modulation strip
# (and its fine part, after it, when it's set to 14-bit in Novation Components), a sustain pedal
# Its scales, numbered from 1 as encoder 2 steps through them (Launchkey MK4 user guide: Major, Minor, Dorian,
# Mixolydian, Lydian, Phrygian, ...), for the modes a song's key can have
SCALES = {"major": 1, "minor": 2, "Dorian": 3, "Mixolydian": 4, "Lydian": 5, "Phrygian": 6}
STATIONARY, TEMPORARY = 0x20, 0x21  # screen targets: the default display, and one shown for a while
TRANSPORT_NAME = 0x27  # screen target: the Transport encoder mode's name
ENCODER_TARGETS = tuple(range(0x15, 0x1D))  # screen targets: what the Launchkey shows by itself for each encoder
THREE_LINES = 0x02  # display arrangement: title, name and value
SHOW, CANCEL = 0x7F, 0x00  # brings a display up with its text; puts it away
BITMAP = 0x09  # SysEx: a picture on the screen (bitmap.Bitmap's data)
SYSEX_MINI, SYSEX_REGULAR = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x13], [0xF0, 0x00, 0x20, 0x29, 0x02, 0x14]
DAW_MODE, DAW_PADS, TRANSPORT_ENCODERS = [0x9F, 0x0C, 0x7F], [0xB6, 0x1D, 0x02], [0xB6, 0x1E, 0x05]
FEATURE_CONTROLS = [0x9F, 0x0B, 0x7F]  # all of them on, reporting changes
STANDALONE = [0x9F, 0x0C, 0x00]
FLAT, FILLED_BOX = 0x1D, 0x1C  # the screen's flat sign, and a filled box (it has no circle)
TEXT_LENGTH = 24


@dataclass(frozen=True)
class Pad:
    row: int  # 0: top, 1: bottom
    column: int  # 0 to 7, left to right


@dataclass(frozen=True)
class Turn:
    encoder: int  # 0 to 7, left to right
    steps: int  # clockwise: more than 0


@dataclass(frozen=True)
class Button:
    number: int  # its Control Change, e.g. SIDE_BUTTONS[0]


@dataclass(frozen=True)
class ScaleMode:
    on: bool  # its Scale button turned its Scale mode on or off


@dataclass(frozen=True)
class Strip:
    name: str  # "pitch" (0 to 16383, 8192 the middle)
    value: int
    at: float = 0.0  # when it came (time.monotonic())


class Launchkey:
    def __init__(self, out_factory: Callable[[], MidiOutputProtocol] | None = None,
                 in_factory: Callable[[], MidiInputProtocol] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._out_factory = out_factory or _midi_out
        self._in_factory = in_factory or midi_in
        self._clock = clock
        self._out: MidiOutputProtocol | None = None
        self._in: MidiInputProtocol | None = None
        self._keys_in: MidiInputProtocol | None = None  # its MIDI port: the touch strips
        self._received: queue.SimpleQueue[tuple[bool, list[int], float]] = queue.SimpleQueue()  # (from the keys' port?, it, when)
        self._sysex = SYSEX_MINI
        self._positions: dict[int, int] = {}  # each encoder's last position, in the modes reporting positions
        self._texts: dict[int, tuple[str, str, str]] = {}  # what each screen target was last given
        self.name = ""
        self.on_keys: Callable[[list[int]], None] | None = None  # its keys, pedal and modulation, on its MIDI thread

    @property
    def is_open(self) -> bool:
        return self._out is not None

    def open(self) -> bool:
        """Finds a Launchkey MK4's DAW port and takes it over; False when none is connected."""
        out, in_ = self._out_factory(), self._in_factory()
        out_index, in_index = _daw_port(out.get_ports()), _daw_port(in_.get_ports())
        if out_index is None or in_index is None:
            return False
        self.name = out.get_ports()[out_index]
        self._sysex = SYSEX_MINI if "mini" in self.name.casefold() else SYSEX_REGULAR
        out.open_port(out_index, "rs-keylights")
        in_.set_callback(lambda event, _data=None: self._received.put((False, list(event[0]), self._clock())))
        in_.open_port(in_index, "rs-keylights")
        self._out, self._in, self._texts = out, in_, {}
        try:
            keys_in = self._in_factory()
            keys_index = _keys_port(keys_in.get_ports())
            if keys_index is not None:
                keys_in.set_callback(lambda event, _data=None: self._from_keys(list(event[0])))
                keys_in.open_port(keys_index, "rs-keylights")
                self._keys_in = keys_in
        except Exception:  # the remote works without its strips
            self._keys_in = None
        for message in (DAW_MODE, FEATURE_CONTROLS, DAW_PADS, TRANSPORT_ENCODERS):
            self._send(message)
        self._send([*self._sysex, 0x04, STATIONARY, THREE_LINES, 0xF7])
        self._send([*self._sysex, 0x04, TEMPORARY, THREE_LINES, 0xF7])
        for target in ENCODER_TARGETS:  # no display of its own when they turn (a 0, in relative mode): the host's
            # (bits 5 and 6 clear: not on change or touch)
            self._send([*self._sysex, 0x04, target, THREE_LINES, 0xF7])
        self._send([*self._sysex, 0x06, TRANSPORT_NAME, 0, *_screen_text("Tones"), 0xF7])
        self._send(ASK_SCALE_MODE)  # its answer comes as when the Scale button is pressed
        return True

    def close(self) -> None:
        """Gives the Launchkey back (standalone mode)."""
        if self._out is not None:
            try:
                self._send(STANDALONE)
            finally:
                self._out.close_port()
        for port in (self._in, self._keys_in):
            if port is not None:
                port.close_port()
        self._out = self._in = self._keys_in = None

    def connected(self) -> bool:
        """Its DAW port is still there (it can be unplugged)."""
        try:
            return self._out is not None and _daw_port(self._out.get_ports()) is not None
        except Exception:
            return False

    def events(self) -> list[Pad | Turn | Button | ScaleMode | Strip]:
        """What was pressed, turned and slid since the last call."""
        return [event for _port, _message, event in self.received() if event is not None]

    def received(self) -> list[tuple[str, list[int], Pad | Turn | Button | ScaleMode | Strip | None]]:
        """Everything that came since the last call: its port ("DAW" or "MIDI"), the message, and what it means."""
        found: list[tuple[str, list[int], Pad | Turn | Button | ScaleMode | Strip | None]] = []
        while True:
            try:
                from_keys, message, at = self._received.get_nowait()
            except queue.Empty:
                return found
            event = self._strip(message, at) if from_keys else self._event(message)
            found.append(("MIDI" if from_keys else "DAW", message, event))

    def pad(self, row: int, column: int, colour: int, pulse: bool = False) -> None:
        """A pad's colour from the palette (0: off), steady or pulsing."""
        self._send([0x92 if pulse else 0x90, PADS[row][column], colour])

    def pad_rgb(self, row: int, column: int, red: int, green: int, blue: int) -> None:
        """A pad's colour, each part 0 to 127."""
        self._send([*self._sysex, 0x01, 0x43, PADS[row][column], red & 0x7F, green & 0x7F, blue & 0x7F, 0xF7])

    def button(self, number: int, colour: int, brightness: int) -> None:
        """A button's light: a colour from the palette, or for one with a single colour its brightness (0-127)."""
        self._send([0xB0, number, colour])
        self._send([0xB3, number, brightness])

    def scale(self, tonic: int, mode: str) -> None:
        """Its Scale mode's root note (pitch class, C = 0) and scale (one of SCALES), as the Scale button shows."""
        if mode in SCALES:
            self._send([0xB6, SCALE_TONIC, tonic % 12])
            self._send([0xB6, SCALE_TYPE, SCALES[mode]])

    def show(self, target: int, title: str, name: str, value: str) -> None:
        """Text on the screen (STATIONARY: what it shows unless something else is shown for a while; TEMPORARY:
        that), brought up. Shown again as it was, it's only brought up."""
        if self._texts.get(target) != (title, name, value):
            for field, text in enumerate((title, name, value)):
                self._send([*self._sysex, 0x06, target, field, *_screen_text(text), 0xF7])
            self._texts[target] = (title, name, value)
        self._send([*self._sysex, 0x04, target, SHOW, 0xF7])

    def picture(self, target: int, data: list[int]) -> None:
        """A picture on the screen (STATIONARY or TEMPORARY), shown at once: a bitmap.Bitmap's data. It holds just
        one; text brought up there afterwards takes its place."""
        self._send([*self._sysex, BITMAP, target, *data, 0xF7])

    def hide(self, target: int) -> None:
        """Puts a display away (the temporary one: the stationary one shows again)."""
        self._send([*self._sysex, 0x04, target, CANCEL, 0xF7])

    def _from_keys(self, message: list[int]) -> None:
        handler = self.on_keys
        if handler is not None and _is_key(message):
            try:
                handler(message)
            except Exception:  # nothing to show it on from here: the next key tries again
                pass
            return
        self._received.put((True, message, self._clock()))

    def _strip(self, message: list[int], at: float) -> Strip | None:
        if len(message) < 3:
            return None
        if message[0] & 0xF0 == 0xE0:
            return Strip("pitch", message[1] | message[2] << 7, at)
        return None

    def _event(self, message: list[int]) -> Pad | Turn | Button | ScaleMode | None:
        if message and message[0] in REALTIME_START_STOP:
            return Button(PLAY)
        if len(message) < 3:
            return None
        status, number, value = message[0], message[1], message[2]
        if status == 0x90 and value > 0:
            for row, notes in enumerate(PADS):
                if number in notes:
                    return Pad(row, notes.index(number))
        if status & 0xF0 == 0xB0:
            if status == 0xBF and number in ENCODERS and value != 64:
                return Turn(ENCODERS.index(number), value - 64)
            elif status == 0xBF and number in ENCODER_POSITIONS:
                last = self._positions.get(number)
                self._positions[number] = value
                if last is not None and value != last:
                    return Turn(ENCODER_POSITIONS.index(number), value - last)
            elif status == 0xB6 and number == SCALE_MODE:
                return ScaleMode(value > 0)
            elif status != 0xB6 and number in BUTTONS and value > 0:
                return Button(number)
        return None

    def _send(self, message: list[int]) -> None:
        if self._out is not None:
            self._out.send_message(message)


def step_at(position: int, step: int | None) -> int:
    """A 14-bit strip position's step (0 to 127), from the last (if any): it changes half a step past the next, as
    a strip wavers a little under a finger resting on it."""
    return position >> 7 if step is None or abs(position - (step << 7 | 64)) > 128 else step


def _is_key(message: list[int]) -> bool:
    """A key going down or up, a sustain pedal, or the modulation strip."""
    return len(message) >= 3 and (message[0] & 0xF0 in (0x80, 0x90) or
                                  (message[0] & 0xF0 == 0xB0 and message[1] in (SUSTAIN, MODULATION, MODULATION_FINE)))


def _daw_port(ports: list[str]) -> int | None:
    return next((i for i, port in enumerate(ports) if "launchkey" in port.casefold() and "daw" in port.casefold()), None)


def _keys_port(ports: list[str]) -> int | None:
    return next((i for i, port in enumerate(ports)
                 if "launchkey" in port.casefold() and "daw" not in port.casefold()), None)


def _screen_text(text: str) -> list[int]:
    """ASCII for the screen, with its flat sign for ♭ and its filled box for ■."""
    return [FLAT if c == "♭" else FILLED_BOX if c == "■" else ord(c) if 0x20 <= ord(c) <= 0x7E else ord("?")
            for c in text[:TEXT_LENGTH]]


def _midi_out() -> MidiOutputProtocol:
    import rtmidi

    return cast(MidiOutputProtocol, getattr(rtmidi, "MidiOut")())
