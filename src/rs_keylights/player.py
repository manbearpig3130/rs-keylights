from __future__ import annotations

import math
import time
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from .lights import MIN_PERIOD, KeyLight, Light, merge_keys
from .sound import Squeal
from .sng import PINCH_HARMONIC

SEEK_BACK = 0.25  # seconds; going back further than this is a seek (riff repeat, restart)
SEEK_AHEAD = 1.0  # going forward further than this is a seek too, not a burst of notes
KEY = -1  # the track index of a key's light in the actions
BEND_STEP = 0.02  # seconds between a bend's pitch bends, as it moves
BEND_MOVE = 0.15  # seconds a bend takes, at most, to reach each of its points
VIBRATO_DEPTH = 64  # Modulation, for a note's vibrato
# A pinch harmonic's squeal on its note's own channel (none of its own: no pitch of its own, as squeal_bends gives
# it): Modulation, growing in
SQUEAL_VIBRATO, SQUEAL_VIBRATO_AFTER, SQUEAL_VIBRATO_GROWS = 90, 0.15, 0.45


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
    priority: int = 0  # its lights' rank, the keys pooled (Player's most_keys): 0 first


class Player:
    """Lights the keys and plays the notes of one or more parts, for a song position that moves forward
    and now and then jumps. The keys follow one timeline per key made from every lit part (so two parts
    on the same key don't fight over it); each part's notes sound on their own, each pitch as long as it
    rings (a note sounding twice at once stops once both are done)."""

    def __init__(self, tracks: Sequence[Track], keys: KeysProtocol, rate: float = 1.0, lead: float = 0.0,
                 min_period: float = MIN_PERIOD, most_keys: int | None = None,
                 cache: dict[int, tuple[Track, Any]] | None = None):
        self.tracks = list(tracks)
        self._cache = cache  # each track's sounds, controls and actions, as made for it before (parts.PlanCache's:
        # by the track, the same object when the part's unchanged)
        self.most_keys = most_keys  # the lit tracks' keys pooled: at most this many at once, by their priority (None:
        # each track's own lights)
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
        lit = [t for t in self.tracks if t.lit]
        self.display: list[KeyLight] = merge_keys([t.lights for t in lit], self.rate, self.lead, self.min_period,
                                                  self.most_keys, [t.priority for t in lit])
        self._display_ons = [k.on for k in self.display]
        made = [self._made(ti, t) for ti, t in enumerate(self.tracks)]
        self._sounds = [m[0] for m in made]  # (what each part sounds: its lights and their extras)
        self._ons = [m[1] for m in made]
        self._longest = [m[2] for m in made]  # (how far back a seek looks for notes still sounding)
        self._controls = [m[3] for m in made]
        # (time, 0 for off / 1 for a tone change / 2 for a control (bend, vibrato) / 3 for on, KEY or a track, index,
        # the places of the pitches ending, or starting, strummed: none, all of them): at the same time offs come
        # first, then tones and controls. A light (or a pitch) that ends as it starts would have its off before its
        # on, so it isn't played. (Each track's are in order already: sorting them all together merges them.)
        actions = sorted((time, kind, KEY, i, ()) for i, k in enumerate(self.display)
                         for time, kind in ((k.off, 0), (k.on, 3)))
        for m in made:
            actions += m[4]
        actions.sort()
        self._actions = actions
        self._times = [action[0] for action in self._actions]
        self._next = 0

    def _made(self, ti: int, t: Track) -> tuple[list[Light], list[float], float, list[Any], list[Any]]:
        """A track's sounds (in order), when they start, the longest, its controls, and its actions (in order: tone
        changes, controls, notes on and off), as its ti'th; as made for it before, if it was (as another's: its
        actions made its)."""
        got = self._cache.get(id(t)) if self._cache is not None else None
        if got is not None and got[0] is t:
            was_ti, made = got[1]
            if was_ti == ti:
                return made
            return (*made[:4], [(a[0], a[1], ti, a[3], a[4]) for a in made[4]])  # type: ignore[return-value]
        sounds = sorted([*t.lights, *t.extras], key=lambda light: light.on)
        ons = [light.on for light in sounds]
        longest = max((self._sound_end(t, light) - light.on for light in sounds), default=0.0)
        controls = self._controls_of(t, sounds)
        actions: list[Any] = []
        if t.voices is not None:
            actions += [(change[0], 1, ti, ci, ()) for ci, change in enumerate(t.changes) if change[0] > -math.inf]
            actions += [(control[0], 2, ti, ci, ()) for ci, control in enumerate(controls)]
            for li, light in enumerate(sounds):
                if self._sound_end(t, light) > light.on:
                    actions += self._ons_of(ti, li, light)
                    actions += self._offs_of(t, ti, li, light)
        actions.sort()
        made = (sounds, ons, longest, controls, actions)
        if self._cache is not None:
            if len(self._cache) >= 64:
                self._cache.clear()
            self._cache[id(t)] = (t, (ti, made))
        return made

    def retime(self, rate: float | None = None, lead: float | None = None, min_period: float | None = None) -> None:
        """Another playback speed, lead or fastest flash: the keys' timeline is made again, going on from
        where it is: the keys lit there changed, what sounds left as it is (the sounds' timeline doesn't change; a
        seek struck every note still ringing again, at each step of a speed being measured)."""
        rate = self.rate if rate is None else rate
        lead = self.lead if lead is None else lead
        min_period = self.min_period if min_period is None else min_period
        if abs(rate - self.rate) < 0.005 and lead == self.lead and min_period == self.min_period:
            return
        self.rate, self.lead, self.min_period = rate, lead, min_period
        position = self._position
        lit = set(self.keys_at(position)) if position is not None else set()
        self._build()
        if position is not None:
            now = set(self.keys_at(position))
            if lit - now:
                self.keys.unlight(tuple(sorted(lit - now)))
            if now - lit:
                self.keys.light(tuple(sorted(now - lit)))
            self._next = bisect_right(self._times, position)

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
                self._start(ti, i, places=ending)
            elif kind == 2:
                self._control(ti, i)
            elif kind == 1:
                self._change(ti, i)
            else:
                self._end(ti, i, ending)
            self._next += 1
        self._position = position

    def seek(self, position: float, sounding: bool = True) -> None:
        """To a position: the keys lit there, the tones there and (sounding) the notes still sounding there struck
        again, from there; else none (going on after a stop: Rocksmith's resume goes 2 s back, and every note still
        ringing there, of every part, would come at once)."""
        self._all_off()
        self._next = bisect_right(self._times, position)
        for i in range(bisect_right(self._display_ons, position)):
            if self.display[i].off > position:
                self._start(KEY, i)
        for ti, t in enumerate(self.tracks):  # (the tone there, and the notes still sounding there)
            if t.voices is not None and t.changes:
                self._change(ti, bisect_right([change[0] for change in t.changes], position) - 1)
            if not sounding:
                continue
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

    @staticmethod
    def _ons_of(ti: int, li: int, light: Light) -> list[tuple[float, int, int, int, tuple[int, ...]]]:
        """A light's notes' ons: at once, or strummed, one for each time some of its pitches start (their places)."""
        if not light.strum:
            return [(light.on, 3, ti, li, ())]
        by_start: dict[float, list[int]] = {}
        for k, delay in enumerate(light.strum):
            by_start.setdefault(light.on + delay, []).append(k)
        return [(at, 3, ti, li, tuple(ks)) for at, ks in sorted(by_start.items())]

    def _offs_of(self, track: Track, ti: int, li: int, light: Light) -> list[tuple[float, int, int, int,
                                                                                  tuple[int, ...]]]:
        """A light's notes' offs: one for each time some of its pitches end (the places of those)."""
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
        "bend" (semitones) or "vibrato" (depth), and back to none as the note stops; by when. A pinch harmonic's
        squeal gets a pitch of its own (_squeal), or on its note's channel, vibrato growing in."""
        if track.voices is None or not track.voices.techniques:
            return []
        out: list[tuple[float, int, str, float]] = []
        for li, light in enumerate(sounds):
            end = self._sound_end(track, light)
            squeal = light.extra and bool(light.mask & PINCH_HARMONIC)
            shape = getattr(track.voices, "squeal_shape", None) or Squeal()
            wobbles = shape.vibrato == "always" or light.vibrato
            if squeal and end > light.on and getattr(track.voices, "squeal", None) is not None:
                out += [(at, li, "bend", up) for at, up in squeal_bends(light, end, shape)]  # (the chart's bend in it)
                continue
            if squeal and end > light.on and wobbles:
                out += self._squeal_vibrato(li, light.on, end)
            elif light.vibrato and end > light.on:
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

    @staticmethod
    def _squeal_vibrato(li: int, on: float, end: float) -> list[tuple[float, int, str, float]]:
        """A squeal's vibrato on its note's own channel: Modulation, growing in a little after it sounds."""
        span = SQUEAL_VIBRATO_GROWS
        steps = max(int(span / BEND_STEP), 1)
        start = on + SQUEAL_VIBRATO_AFTER
        out = [(start + span * k / steps, li, "vibrato", SQUEAL_VIBRATO * k / steps) for k in range(steps + 1)
               if start + span * k / steps < end]
        return [*out, (end, li, "vibrato", 0)]

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

    def _start(self, ti: int, i: int, at: float | None = None, places: tuple[int, ...] = ()) -> None:
        """A light on, or its notes sounding (at: from there, those started and still sounding; places: those
        pitches alone, strummed, else all of them)."""
        if ti == KEY:
            self.keys.light((self.display[i].key,))
            return
        track = self.tracks[ti]
        voices, light = track.voices, self._sounds[ti][i]
        if voices is None:
            return
        channel, _notes, velocity = voices.pick(light)
        ends = self._ends(track, light)
        delays = light.strum if len(light.strum) == len(ends) else (0.0,) * len(ends)
        sounding = {k: note for k, note in enumerate(voices.notes_for(light))
                    if k < len(ends) and (not places or k in places)
                    and (light.on + delays[k] <= at if at is not None else True)
                    and ends[k] > max(light.on + delays[k], light.on if at is None else at)}
        if not sounding:
            return
        sound = self._sound(ti)
        prepare = getattr(voices, "prepare", None)  # (a drum's sound chosen on its device first)
        if prepare is not None:
            prepare(sound, light)
        for note in set(sounding.values()):
            self._held[(id(sound), channel, note)] = self._held.get((id(sound), channel, note), 0) + 1
        sound.play(channel, tuple(sorted(set(sounding.values()))), velocity)
        if (ti, i) in self._sounding:  # (strummed: its other pitches sounding already)
            sounding = {**self._sounding[(ti, i)][1], **sounding}
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


def squeal_bends(light: Light, end: float, shape: Squeal = Squeal()) -> list[tuple[float, float]]:
    """A squeal's pitch on its own channel, as it sounds until end: a bend (semitones) every BEND_STEP, scooped up
    into, the chart's bend (held, then moved to each point), and the vibrato over it (the shape's: always, or where the
    chart has vibrato); none as it ends."""
    curve = [(light.on, 0.0), *((at, up) for at, up in light.bends if at < end)]

    def charted(t: float) -> float:
        value = 0.0
        for (start, was), (reach, target) in zip(curve, curve[1:]):
            moving = max(start, reach - BEND_MOVE)
            if t < moving:
                return was
            if t < reach:
                return was + (target - was) * (t - moving) / (reach - moving)
            value = target
        return value

    scoop_time, grows = shape.scoop_ms / 1000, shape.grows_ms / 1000
    width = shape.width if shape.vibrato == "always" or light.vibrato else 0.0
    out: list[tuple[float, float]] = []
    t = light.on
    while t < end:
        since = t - light.on
        scoop = -shape.scoop * max(0.0, 1.0 - since / scoop_time) if scoop_time > 0 else 0.0
        grown = min(max((since - scoop_time) / grows, 0.0), 1.0) if grows > 0 else float(since >= scoop_time)
        wobble = width * grown * math.sin(2 * math.pi * shape.rate * max(since - scoop_time, 0.0))
        out.append((t, charted(t) + scoop + wobble))
        t += BEND_STEP
    out.append((end, 0.0))
    return out


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
