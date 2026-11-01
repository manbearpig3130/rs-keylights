from __future__ import annotations

import math
import time
from bisect import bisect_right
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .lights import MIN_PERIOD, KeyLight, Light, merge_keys

SEEK_BACK = 0.25  # seconds; going back further than this is a seek (riff repeat, restart)
SEEK_AHEAD = 1.0  # going forward further than this is a seek too, not a burst of notes
KEY = -1  # the track index of a key's light in the actions
BEND_STEP = 0.02  # seconds between a bend's pitch bends, as it moves
BEND_MOVE = 0.15  # seconds a bend takes, at most, to reach each of its points
VIBRATO_DEPTH = 64  # Modulation, for a note's vibrato


class KeysProtocol(Protocol):
    def light(self, keys: tuple[int, ...]) -> None:
        ...

    def unlight(self, keys: tuple[int, ...]) -> None:
        ...

    def play(self, channel: int, notes: tuple[int, ...], velocity: int) -> None:
        ...

    def stop(self, channel: int, notes: tuple[int, ...]) -> None:
        ...

    def all_off(self) -> None:
        ...

    def set_voice(self, channel: int, program: int, bank: int | None = None, ours: bool = True,
                  volume: int = 100) -> None:
        ...

    def bend(self, channel: int, semitones: float) -> None:
        ...

    def modulate(self, channel: int, depth: int) -> None:
        ...


class VoicesProtocol(Protocol):
    @property
    def ring(self) -> bool:  # notes ring on after their light (its rings), or stop as it goes off
        ...

    @property
    def volume(self) -> int:  # its channels' volume
        ...

    @property
    def techniques(self) -> bool:  # bends and vibrato played (and its extra sounds: lights.techniques)
        ...

    def pick(self, light: Light) -> tuple[int, tuple[int, ...], int]:
        ...

    def notes_for(self, light: Light) -> tuple[int, ...]:
        ...


@dataclass(frozen=True)
class Track:
    """One part: its lights, the voices it sounds with (none: silent), whether it lights keys, its main tone as
    the song switches tone (when, its channel, program and bank; the first, where it starts, from -inf), what
    sounds besides its lights (lights.techniques' extras), and what sounds it (another MIDI device; None: the
    player's keyboard)."""

    lights: Sequence[Light]
    voices: VoicesProtocol | None = None
    lit: bool = True
    changes: Sequence[tuple[float, int, int, int | None]] = ()
    extras: Sequence[Light] = ()
    sound: KeysProtocol | None = None


class Player:
    """Lights the keys and plays the notes of one or more parts, for a song position that moves forward
    and now and then jumps. The keys follow one timeline per key made from every lit part (so two parts
    on the same key don't fight over it); each part's notes sound on their own, each pitch as long as it
    rings (a note sounding twice at once stops once both are done)."""

    def __init__(self, tracks: Sequence[Track], keys: KeysProtocol, rate: float = 1.0, lead: float = 0.0,
                 min_period: float = MIN_PERIOD):
        self.tracks = list(tracks)
        self.keys = keys
        self.rate = rate  # song seconds per second: the keys' flashing is timed for it
        self.lead = lead  # seconds the keys light before the notes
        self.min_period = min_period  # a key's repeats closer than this flash on every other one
        self._position: float | None = None
        self._sounding: dict[tuple[int, int], tuple[int, dict[int, int]]] = {}  # (track, light) -> its channel,
        # and each pitch still sounding's note (by its place in the light)
        self._held: dict[tuple[int, int, int], int] = {}  # (what sounds it, channel, note) -> how many sound it
        self._build()

    def _build(self) -> None:
        self.display: list[KeyLight] = merge_keys([t.lights for t in self.tracks if t.lit], self.rate, self.lead,
                                                  self.min_period)
        self._display_ons = [k.on for k in self.display]
        self._sounds = [sorted([*t.lights, *t.extras], key=lambda light: light.on) for t in self.tracks]  # (what
        # each part sounds: its lights and their extras)
        self._ons = [[light.on for light in sounds] for sounds in self._sounds]
        self._longest = [max((self._sound_end(t, light) - light.on for light in sounds), default=0.0)
                         for t, sounds in zip(self.tracks, self._sounds)]  # (how far back a seek looks for notes
        # still sounding)
        self._controls = [self._controls_of(t, sounds) for t, sounds in zip(self.tracks, self._sounds)]
        # (time, 0 for off / 1 for a tone change / 2 for a control (bend, vibrato) / 3 for on, KEY or a track, index,
        # the places of the pitches ending): at the same time offs come first, then tones and controls. A light (or
        # a pitch) that ends as it starts would have its off before its on, so it isn't played.
        self._actions = sorted(
            [(time, kind, KEY, i, ()) for i, k in enumerate(self.display) for time, kind in ((k.off, 0), (k.on, 3))]
            + [(change[0], 1, ti, ci, ()) for ti, t in enumerate(self.tracks) if t.voices is not None
               for ci, change in enumerate(t.changes) if change[0] > -math.inf]
            + [(control[0], 2, ti, ci, ()) for ti, controls in enumerate(self._controls)
               for ci, control in enumerate(controls)]
            + [
                action
                for ti, t in enumerate(self.tracks)
                if t.voices is not None
                for li, light in enumerate(self._sounds[ti])
                if self._sound_end(t, light) > light.on
                for action in [(light.on, 3, ti, li, ()), *self._offs(t, ti, li)]
            ]
        )
        self._times = [action[0] for action in self._actions]
        self._next = 0

    def retime(self, rate: float | None = None, lead: float | None = None, min_period: float | None = None) -> None:
        """Another playback speed, lead or fastest flash: the keys' timeline is made again, going on from
        where it is."""
        rate = self.rate if rate is None else rate
        lead = self.lead if lead is None else lead
        min_period = self.min_period if min_period is None else min_period
        if abs(rate - self.rate) < 0.005 and lead == self.lead and min_period == self.min_period:
            return
        self.rate, self.lead, self.min_period = rate, lead, min_period
        position = self._position
        self._build()
        if position is not None:
            self.seek(position)

    @property
    def position(self) -> float | None:
        return self._position

    def next_time(self) -> float | None:
        return self._times[self._next] if self._next < len(self._times) else None

    def advance(self, position: float) -> None:
        if self._position is None or position < self._position - SEEK_BACK or position > self._position + SEEK_AHEAD:
            self.seek(position)
            return
        if position <= self._position:
            return  # a little back: the clock's estimate moved, not the song
        while self._next < len(self._actions) and self._actions[self._next][0] <= position:
            _time, kind, ti, i, ending = self._actions[self._next]
            if kind == 3:
                self._start(ti, i)
            elif kind == 2:
                self._control(ti, i)
            elif kind == 1:
                self._change(ti, i)
            else:
                self._end(ti, i, ending)
            self._next += 1
        self._position = position

    def seek(self, position: float) -> None:
        self._all_off()
        self._next = bisect_right(self._times, position)
        for i in range(bisect_right(self._display_ons, position)):
            if self.display[i].off > position:
                self._start(KEY, i)
        for ti, t in enumerate(self.tracks):  # (the tone there, and the notes still sounding there)
            if t.voices is not None and t.changes:
                self._change(ti, bisect_right([change[0] for change in t.changes], position) - 1)
            if t.voices is not None:
                for li in range(bisect_right(self._ons[ti], position - self._longest[ti]),
                                bisect_right(self._ons[ti], position)):
                    self._start(ti, li, position)
            latest: dict[tuple[int, str], int] = {}  # (and a bend or vibrato going on there: its latest)
            for ci, (at, li, what, _value) in enumerate(self._controls[ti]):
                if at <= position < self._sound_end(t, self._sounds[ti][li]):
                    latest[(li, what)] = ci
            for ci in sorted(latest.values()):
                self._control(ti, ci)
        self._position = position

    def keys_at(self, position: float) -> list[int]:
        """The keys lit at a song position."""
        return [k.key for k in self.display[: bisect_right(self._display_ons, position)] if k.off > position]

    def stop(self) -> None:
        if self._position is not None:
            self._all_off()
        self._position = None

    def done(self) -> bool:
        return self._next >= len(self._actions)

    def _offs(self, track: Track, ti: int, li: int) -> list[tuple[float, int, int, int, tuple[int, ...]]]:
        """A light's notes' offs: one for each time some of its pitches end (the places of those)."""
        light = self._sounds[ti][li]
        by_end: dict[float, list[int]] = {}
        for k, end in enumerate(self._ends(track, light)):
            if end > light.on:
                by_end.setdefault(end, []).append(k)
        return [(end, 0, ti, li, tuple(ks)) for end, ks in by_end.items()]

    @staticmethod
    def _ends(track: Track, light: Light) -> tuple[float, ...]:
        """When each of a light's pitches stops sounding: as it rings (a technique's extra sound: always), or as the
        light goes off."""
        if light.extra or (track.voices is not None and track.voices.ring and light.rings):
            return light.rings
        return (light.off,) * max(len(light.pitches or light.keys), 1)

    def _sound_end(self, track: Track, light: Light) -> float:
        """When a light's sound stops (its last pitch)."""
        return max(self._ends(track, light), default=light.on)

    def _controls_of(self, track: Track, sounds: Sequence[Light]) -> list[tuple[float, int, str, float]]:
        """A part's bends and vibrato, as its notes sound (with techniques): when, the note (its place in sounds),
        "bend" (semitones) or "vibrato" (depth), and back to none as the note stops; by when."""
        if track.voices is None or not track.voices.techniques:
            return []
        out: list[tuple[float, int, str, float]] = []
        for li, light in enumerate(sounds):
            end = self._sound_end(track, light)
            if light.vibrato and end > light.on:
                out += [(light.on, li, "vibrato", VIBRATO_DEPTH), (end, li, "vibrato", 0)]
            points = [(at, semitones) for at, semitones in light.bends if at < end]
            if points and end > light.on:
                curve = [(light.on, 0.0), *points]
                for (start, value), (reach, target) in zip(curve, curve[1:]):
                    moving = max(start, reach - BEND_MOVE)  # (held, then moved to its next point)
                    steps = max(int((reach - moving) / BEND_STEP), 1)
                    out += [(moving + (reach - moving) * k / steps, li, "bend", value + (target - value) * k / steps)
                            for k in range(steps)]
                out += [(points[-1][0], li, "bend", points[-1][1]), (end, li, "bend", 0.0)]
        return sorted(out, key=lambda control: control[0])

    def _control(self, ti: int, ci: int) -> None:
        """A bend or vibrato on the channel its note sounds on."""
        track = self.tracks[ti]
        _at, li, what, value = self._controls[ti][ci]
        if track.voices is None:
            return
        channel, _notes, _velocity = track.voices.pick(self._sounds[ti][li])
        if what == "bend":
            self._sound(ti).bend(channel, value)
        else:
            self._sound(ti).modulate(channel, round(value))

    def _sound(self, ti: int) -> KeysProtocol:
        """What sounds a part: another MIDI device, or the keyboard."""
        return self.tracks[ti].sound or self.keys

    def _start(self, ti: int, i: int, at: float | None = None) -> None:
        """A light on, or its notes sounding (at: from there, those still sounding)."""
        if ti == KEY:
            self.keys.light((self.display[i].key,))
            return
        track = self.tracks[ti]
        voices, light = track.voices, self._sounds[ti][i]
        if voices is None:
            return
        channel, _notes, velocity = voices.pick(light)
        ends = self._ends(track, light)
        sounding = {k: note for k, note in enumerate(voices.notes_for(light))
                    if k < len(ends) and ends[k] > (light.on if at is None else at)}
        if not sounding:
            return
        sound = self._sound(ti)
        for note in set(sounding.values()):
            self._held[(id(sound), channel, note)] = self._held.get((id(sound), channel, note), 0) + 1
        sound.play(channel, tuple(sorted(set(sounding.values()))), velocity)
        self._sounding[(ti, i)] = (channel, sounding)

    def _end(self, ti: int, i: int, ending: tuple[int, ...] = ()) -> None:
        """A light off, or some of its pitches' notes stopped (ending: their places in the light)."""
        if ti == KEY:
            self.keys.unlight((self.display[i].key,))
            return
        if (ti, i) not in self._sounding:
            return
        channel, sounding = self._sounding[(ti, i)]
        notes = {sounding.pop(k) for k in ending if k in sounding}
        stopping = []
        sound = self._sound(ti)
        for note in notes - set(sounding.values()):  # (another of its pitches on the same note: on with it)
            held = self._held.get((id(sound), channel, note), 0) - 1
            if held > 0:
                self._held[(id(sound), channel, note)] = held  # (another note sounds it still)
            else:
                self._held.pop((id(sound), channel, note), None)
                stopping.append(note)
        if stopping:
            sound.stop(channel, tuple(sorted(stopping)))
        if not sounding:
            del self._sounding[(ti, i)]

    def _change(self, ti: int, ci: int) -> None:
        """A part's main tone as the song switches tone (one of its changes)."""
        track = self.tracks[ti]
        if track.voices is not None and 0 <= ci < len(track.changes):
            _at, channel, program, bank = track.changes[ci]
            self._sound(ti).set_voice(channel, program, bank, volume=track.voices.volume)

    def _all_off(self) -> None:
        self.keys.all_off()
        for sound in {id(t.sound): t.sound for t in self.tracks if t.sound is not None}.values():
            sound.all_off()
        self._sounding.clear()
        self._held.clear()


def play(
    player: Player,
    start: float = 0.0,
    speed: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Plays from a song position in real time (times speed) to the end."""
    player.retime(rate=speed)
    began = clock()
    while True:
        position = start + (clock() - began) * speed
        player.advance(position)
        upcoming = player.next_time()
        if upcoming is None:
            break
        sleep(min(max(upcoming - position, 0.0) / speed, 0.05))
    player.stop()
