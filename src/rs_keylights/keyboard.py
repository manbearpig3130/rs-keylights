from __future__ import annotations

import threading
import time
from typing import Any, Callable, Protocol, cast

# The keyboard's MIDI input. The LK-170 lights keys only for notes on its navigate channels
# (FUNCTION > Navi. Ch: 4 for the right hand and 3 for the left by default), and still lights them
# with the channel's volume at 0, which makes silent lights. The sound goes to other channels, each
# with a tone of its own (sound.py). Its MIDI output says when a key is pressed. Another keyboard can play
# it too (the Launchkey's keys, from their own thread): the keys held there light first, as at most 4 light at
# once, and the song's lights get the room left (their lowest) until they're let go.

DEFAULT_PORT = "CASIO USB-MIDI"
DEFAULT_CHANNEL = 4
BANK_SELECT = 0
VOLUME = 7
ALL_NOTES_OFF = 123
SUSTAIN = 64
MAX_LIT = 4  # keys lit at once, at most (on both navigate channels together)
NORMAL_VOLUME = 100
VELOCITY = 100
SILENCE_EVERY = 5.0  # seconds; the keyboard forgets the volume when it's switched off and on
BEND_RANGE = 2  # semitones a pitch bend goes either way (as General MIDI keyboards start, the LK-170 too)
MODULATION = 1  # its Control Change: vibrato
LISTEN_EVERY = 0.002  # seconds between looks at a MIDI input for what came (a remote's keys: about 1 ms late)
HEARD_EVERY = 0.02  # for a key pressed on the keyboard (stopping a show)


class MidiOutputProtocol(Protocol):
    def get_ports(self) -> list[str]:
        ...

    def open_port(self, port: int = 0, name: str | None = None) -> None:
        ...

    def send_message(self, message: list[int]) -> None:
        ...

    def close_port(self) -> None:
        ...


class MidiInputProtocol(Protocol):
    def get_ports(self) -> list[str]:
        ...

    def open_port(self, port: int = 0, name: str | None = None) -> None:
        ...

    def set_callback(self, func: Callable[..., None], data: object = None) -> None:
        ...

    def close_port(self) -> None:
        ...


class KeyboardError(RuntimeError):
    pass


class Keyboard:
    """The lights on the navigate channel, always silent, and the sound on channels of its own."""

    def __init__(
        self,
        port_name: str = DEFAULT_PORT,
        channel: int = DEFAULT_CHANNEL,
        midi_out_factory: Callable[[], MidiOutputProtocol] | None = None,
        clock: Callable[[], float] = time.monotonic,
        midi_in_factory: Callable[[], MidiInputProtocol] | None = None,
    ):
        if not 1 <= channel <= 16:
            raise ValueError("The channel must be 1 to 16")
        self.port_name = port_name
        self.channel = channel
        self._factory = midi_out_factory or _default_midi_out
        # Keys pressed come from the real keyboard's output (not with a stand-in for its input, as in tests)
        self._in_factory = midi_in_factory or (_heard_in if midi_out_factory is None else None)
        self._clock = clock
        self._out: MidiOutputProtocol | None = None
        self._in: MidiInputProtocol | None = None
        self._pressed = False
        self._lock = threading.RLock()  # the song's thread, and another keyboard's
        self._lit: set[int] = set()  # the keys the song (or a show) lights
        self._held: list[int] = []  # keys held on another keyboard, the latest last
        self._on: set[int] = set()  # the keys lit on the keyboard
        self._sounding: dict[int, set[int]] = {}  # channel -> notes
        self._voices: dict[int, tuple[int, int | None]] = {}  # channel -> (program, bank) set
        self._volumes: dict[int, int] = {}  # channel -> volume set
        self._played: dict[int, set[int]] = {}  # channel -> notes from another keyboard
        self._controls: set[tuple[int, int]] = set()  # another keyboard's pedal down or modulation: (channel, number)
        self._bent: set[int] = set()  # the song's channels bent or with vibrato (put back as everything stops)
        self._guitar: tuple[int, ...] = ()  # keys played on a guitar, heard (listen): lit first too
        self._silenced_at = float("-inf")

    @property
    def lit(self) -> frozenset[int]:
        """The keys the song (or a show) lights."""
        return frozenset(self._lit)

    @property
    def holding(self) -> bool:
        """Another keyboard has keys down here."""
        return bool(self._held) or any(self._played.values()) or any(n == SUSTAIN for _c, n in self._controls)

    def find_port(self) -> int | None:
        out = self._out or self._factory()
        self._out = out
        wanted = self.port_name.casefold()
        return next((i for i, port in enumerate(out.get_ports()) if wanted in port.casefold()), None)

    def open(self) -> None:
        index = self.find_port()
        if index is None:
            ports = self._out.get_ports() if self._out else []
            raise KeyboardError(f"No MIDI output named '{self.port_name}' (outputs: {', '.join(ports) or 'none'})")
        assert self._out is not None
        self._out.open_port(index, "rs-keylights")
        self._silence()
        self._listen()

    def pressed(self) -> bool:
        """A key was pressed on the keyboard since the last time this was asked."""
        pressed, self._pressed = self._pressed, False
        return pressed

    def _listen(self) -> None:
        if self._in_factory is None:
            return
        try:
            midi_in = self._in_factory()
            wanted = self.port_name.casefold()
            index = next((i for i, port in enumerate(midi_in.get_ports()) if wanted in port.casefold()), None)
            if index is not None:
                midi_in.set_callback(self._heard)
                midi_in.open_port(index, "rs-keylights")
                self._in = midi_in
        except Exception:  # the lights and sound work without it
            self._in = None

    def _heard(self, event: tuple[list[int], float], _data: object = None) -> None:
        message = event[0]
        if len(message) >= 3 and message[0] & 0xF0 == 0x90 and message[2] > 0:
            channel = (message[0] & 0x0F) + 1
            if channel != self.channel and channel not in self._sounding:  # not one of ours, played back
                self._pressed = True

    def close(self) -> None:
        with self._lock:
            if self._in is not None:
                self._in.close_port()
                self._in = None
            if self._out is None:
                return
            self.all_off()
            self._send([0xB0 | (self.channel - 1), VOLUME, NORMAL_VOLUME])
            self._out.close_port()
            self._out = None
            self._voices.clear()

    def set_voice(self, channel: int, program: int, bank: int | None = None, ours: bool = True,
                  volume: int = NORMAL_VOLUME) -> None:
        """A sound channel's tone (Bank Select when given, then Program Change) and volume; not sent again for a
        channel that has it (it can catch the notes sounding). Notes coming back from the keyboard on a channel
        of ours aren't keys pressed (not ours: another keyboard's channel)."""
        if not 1 <= channel <= 16 or channel == self.channel:
            raise ValueError(f"A sound channel must be 1 to 16 and not the lights' ({self.channel})")
        if not 0 <= program <= 127 or (bank is not None and not 0 <= bank <= 127):
            raise ValueError("The program and bank must be 0 to 127")
        status = channel - 1
        with self._lock:
            if ours:
                self._sounding.setdefault(channel, set())
            if self._voices.get(channel) != (program, bank):
                self._voices[channel] = (program, bank)
                if bank is not None:
                    self._send([0xB0 | status, BANK_SELECT, bank])
                self._send([0xC0 | status, program])
                self._volumes.pop(channel, None)  # (its volume after it)
            self._set_volume(channel, volume)

    def set_volume(self, channel: int, volume: int) -> None:
        """A sound channel's volume (Control Change 7: 0 to 127, NORMAL_VOLUME as it starts), at once."""
        with self._lock:
            self._set_volume(channel, volume)

    def _set_volume(self, channel: int, volume: int) -> None:
        if self._volumes.get(channel) != volume:
            self._volumes[channel] = volume
            self._send([0xB0 | (channel - 1), VOLUME, volume])

    def light(self, keys: tuple[int, ...]) -> None:
        with self._lock:
            self._keep_silent()
            self._lit.update(keys)
            self._show(keys, again=True)

    def unlight(self, keys: tuple[int, ...]) -> None:
        with self._lock:
            keys = tuple(k for k in keys if k in self._lit)
            self._lit.difference_update(keys)
            self._show(keys)

    def hold(self, keys: tuple[int, ...]) -> None:
        """Keys held on another keyboard: lit before the song's, until let go. Pressing them counts as a key
        pressed here (it stops a show)."""
        with self._lock:
            self._keep_silent()
            self._held = [k for k in self._held if k not in keys] + list(keys)
            self._pressed = True
            self._show(keys)

    def let_go(self, keys: tuple[int, ...]) -> None:
        with self._lock:
            self._held = [k for k in self._held if k not in keys]
            self._show(keys)

    def sound(self, channel: int, note: int, velocity: int) -> None:
        """A note from another keyboard (velocity 0: let go), on a channel of its own."""
        with self._lock:
            played = self._played.setdefault(channel, set())
            if velocity > 0:
                self._send([0x90 | (channel - 1), note, velocity])
                played.add(note)
            elif note in played:
                self._send([0x80 | (channel - 1), note, 0])
                played.discard(note)

    def bend(self, channel: int, semitones: float) -> None:
        """A song's channel's pitch bend (a note bent: semitones up, or down; BEND_RANGE at most; 0: none)."""
        value = min(max(8192 + round(semitones / BEND_RANGE * 8192), 0), 16383)
        with self._lock:
            self._send([0xE0 | (channel - 1), value & 0x7F, value >> 7])
            self._bent.add(channel)

    def modulate(self, channel: int, depth: int) -> None:
        """A song's channel's vibrato (Modulation, 0: none)."""
        with self._lock:
            self._send([0xB0 | (channel - 1), MODULATION, min(max(depth, 0), 127)])
            self._bent.add(channel)

    def control(self, channel: int, number: int, value: int) -> None:
        """Another keyboard's sustain pedal or modulation, as a Control Change (0: up, or none)."""
        with self._lock:
            self._send([0xB0 | (channel - 1), number, value])
            if value > 0:
                self._controls.add((channel, number))
            else:
                self._controls.discard((channel, number))

    def let_go_all(self) -> None:
        """Another keyboard gone: its keys let go, its notes stopped, its pedal up and its modulation none."""
        with self._lock:
            self._held.clear()
            self._show()
            self._stop_played()

    def play(self, channel: int, notes: tuple[int, ...], velocity: int = VELOCITY) -> None:
        with self._lock:
            sounding = self._sounding.setdefault(channel, set())
            for note in notes:
                self._send([0x90 | (channel - 1), note, velocity])
                sounding.add(note)

    def stop(self, channel: int, notes: tuple[int, ...]) -> None:
        with self._lock:
            sounding = self._sounding.get(channel, set())
            for note in notes:
                if note in sounding:
                    self._send([0x80 | (channel - 1), note, 0])
                    sounding.discard(note)

    def all_off(self) -> None:
        with self._lock:
            for key in sorted(self._on):
                self._send([0x80 | (self.channel - 1), key, 0])
            self._lit.clear()
            self._held.clear()
            self._on.clear()
            self._send([0xB0 | (self.channel - 1), ALL_NOTES_OFF, 0])
            for channel, notes in self._sounding.items():
                for note in sorted(notes):
                    self._send([0x80 | (channel - 1), note, 0])
                notes.clear()
                self._send([0xB0 | (channel - 1), ALL_NOTES_OFF, 0])
            for channel in sorted(self._bent):  # (no bend, no vibrato)
                self._send([0xE0 | (channel - 1), 0, 64])
                self._send([0xB0 | (channel - 1), MODULATION, 0])
            self._bent.clear()
            self._stop_played()

    def hear(self, keys: tuple[int, ...]) -> None:
        """The keys a guitar plays, heard (listen): lit first, as another keyboard's held keys are (the song's
        lights get the room left)."""
        with self._lock:
            self._guitar = tuple(dict.fromkeys(keys))
            self._show()

    def _show(self, keys: tuple[int, ...] = (), again: bool = False) -> None:
        """Lights what should be: the keys held on another keyboard (the latest MAX_LIT) and a guitar's heard, and
        the song's in the room left, its lowest (all of them, with none held or heard). The keys asked about go
        first, in their order; again: lit again, even if they are."""
        held = list(dict.fromkeys([*self._held[-MAX_LIT:], *self._guitar]))[:MAX_LIT]
        song = sorted(k for k in self._lit if k not in held)
        target = set(held) | set(song[:MAX_LIT - len(held)] if held else song)
        asked = list(dict.fromkeys(keys))
        for key in [k for k in asked if k in self._on and k not in target] + sorted(self._on - target - set(asked)):
            self._send([0x80 | (self.channel - 1), key, 0])
            self._on.discard(key)
        for key in [k for k in asked if k in target and (again or k not in self._on)] + \
                sorted(target - self._on - set(asked)):
            self._send([0x90 | (self.channel - 1), key, VELOCITY])
            self._on.add(key)

    def _stop_played(self) -> None:
        """Only what the other keyboard played: its channel can be the keyboard's own, played on it too."""
        for channel, notes in self._played.items():
            for note in sorted(notes):
                self._send([0x80 | (channel - 1), note, 0])
            notes.clear()
        for channel, number in sorted(self._controls):
            self._send([0xB0 | (channel - 1), number, 0])
        self._controls.clear()

    def _keep_silent(self) -> None:
        if self._clock() - self._silenced_at >= SILENCE_EVERY:
            self._silence()

    def _silence(self) -> None:
        self._send([0xB0 | (self.channel - 1), VOLUME, 0])
        self._silenced_at = self._clock()

    def _send(self, message: list[int]) -> None:
        if self._out is None:
            raise KeyboardError("The keyboard isn't open")
        self._out.send_message(message)


def _heard_in() -> MidiInputProtocol:
    return midi_in(HEARD_EVERY)


def _default_midi_out() -> MidiOutputProtocol:
    try:
        import rtmidi
    except ImportError as exc:
        raise KeyboardError("python-rtmidi is required: pip install python-rtmidi") from exc
    midi_out = getattr(rtmidi, "MidiOut", None)
    if not callable(midi_out):
        raise KeyboardError("python-rtmidi has no MidiOut on this platform")
    return cast(MidiOutputProtocol, midi_out())


class Synth:
    """Another MIDI device sounding parts (a synth, say): their notes, bends and vibrato on its channel, with its own
    sound (no tones sent: it keeps the one it's on). The keyboard keeps their lights. Unplugged while it plays, its
    sound stops; the rest goes on."""

    def __init__(self, port_name: str, midi_out_factory: Callable[[], MidiOutputProtocol] | None = None) -> None:
        self.port_name = port_name
        self._factory = midi_out_factory or _default_midi_out
        self._out: MidiOutputProtocol | None = None
        self._lock = threading.RLock()  # (the song's thread, and a seek)
        self._sounding: dict[int, set[int]] = {}  # channel -> its notes on
        self._bent: set[int] = set()
        self._volumes: dict[int, int] = {}

    def open(self) -> None:
        out = self._factory()
        ports = out.get_ports()
        wanted = self.port_name.casefold()
        index = next((i for i, port in enumerate(ports) if port.casefold() == wanted), None)
        if index is None:
            index = next((i for i, port in enumerate(ports) if wanted in port.casefold()), None)
        if index is None:
            raise KeyboardError(f"No MIDI output {self.port_name!r}")
        out.open_port(index, "rs-keylights")
        self._out = out

    @property
    def is_open(self) -> bool:
        return self._out is not None

    def set_voice(self, channel: int, program: int, bank: int | None = None, ours: bool = True,
                  volume: int = NORMAL_VOLUME) -> None:
        """Its volume (the keyboard's tones aren't sent: it keeps its own sound)."""
        self.set_volume(channel, volume)

    def set_volume(self, channel: int, volume: int) -> None:
        """A channel's volume (Control Change 7: if it takes it), sent again only if it's another."""
        with self._lock:
            if self._volumes.get(channel) != volume:
                self._send([0xB0 | (channel - 1), VOLUME, min(max(volume, 0), 127)])
                self._volumes[channel] = volume

    def program(self, channel: int, number: int) -> None:
        """One of its own programs (Program Change, 0 the first), chosen for the part."""
        with self._lock:
            self._send([0xC0 | (channel - 1), min(max(number, 0), 127)])

    def play(self, channel: int, notes: tuple[int, ...], velocity: int = VELOCITY) -> None:
        with self._lock:
            sounding = self._sounding.setdefault(channel, set())
            for note in notes:
                self._send([0x90 | (channel - 1), note, velocity])
                sounding.add(note)

    def stop(self, channel: int, notes: tuple[int, ...]) -> None:
        with self._lock:
            sounding = self._sounding.get(channel, set())
            for note in notes:
                self._send([0x80 | (channel - 1), note, 0])
                sounding.discard(note)

    def bend(self, channel: int, semitones: float) -> None:
        value = min(max(8192 + round(semitones / BEND_RANGE * 8192), 0), 16383)
        with self._lock:
            self._send([0xE0 | (channel - 1), value & 0x7F, value >> 7])
            self._bent.add(channel)

    def modulate(self, channel: int, depth: int) -> None:
        with self._lock:
            self._send([0xB0 | (channel - 1), MODULATION, min(max(depth, 0), 127)])
            self._bent.add(channel)

    def all_off(self) -> None:
        with self._lock:
            for channel, notes in self._sounding.items():
                for note in sorted(notes):
                    self._send([0x80 | (channel - 1), note, 0])
                notes.clear()
                self._send([0xB0 | (channel - 1), ALL_NOTES_OFF, 0])
            for channel in sorted(self._bent):
                self._send([0xE0 | (channel - 1), 0, 64])
                self._send([0xB0 | (channel - 1), MODULATION, 0])
            self._bent.clear()

    def close(self) -> None:
        with self._lock:
            if self._out is None:
                return
            self.all_off()
            try:
                self._out.close_port()
            except Exception:  # unplugged
                pass
            self._out = None

    def _send(self, message: list[int]) -> None:
        if self._out is None:
            return
        try:
            self._out.send_message(message)
        except Exception:  # unplugged: its sound stops, the rest goes on
            pass


class PolledMidiIn:
    """A python-rtmidi input whose messages come from a thread of ours asking for them, not from python-rtmidi's
    callbacks: a port opened or closed (any port) while one of those runs hangs the whole program, as it keeps the
    GIL while CoreMIDI waits for the callback, which waits for the GIL. A touch strip sends hundreds a second."""

    def __init__(self, port: Any, every: float = LISTEN_EVERY) -> None:
        self._port, self._every = port, every
        self._callback: Callable[..., None] | None = None
        self._data: object = None
        self._quit: threading.Event | None = None

    def get_ports(self) -> list[str]:
        return list(self._port.get_ports())

    def set_callback(self, func: Callable[..., None], data: object = None) -> None:
        self._callback, self._data = func, data

    def open_port(self, port: int = 0, name: str | None = None) -> None:
        self._port.open_port(port, name)
        self._quit = quit_ = threading.Event()
        threading.Thread(target=self._listen, args=(quit_,), name="rs-keylights MIDI in", daemon=True).start()

    def close_port(self) -> None:
        if self._quit is not None:
            self._quit.set()
            self._quit = None
        self._port.close_port()

    def _listen(self, quit_: threading.Event) -> None:
        while not quit_.is_set():
            event = self._port.get_message()
            if event is None:
                time.sleep(self._every)
            elif self._callback is not None:
                try:
                    self._callback(event, self._data)
                except Exception:  # as a callback's would be: the next message goes on
                    pass


def midi_in(every: float = LISTEN_EVERY) -> MidiInputProtocol:
    """A MIDI input (python-rtmidi's), its messages handed on from a thread of ours."""
    import rtmidi

    return PolledMidiIn(getattr(rtmidi, "MidiIn")(), every)
