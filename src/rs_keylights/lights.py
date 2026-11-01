from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, replace
from typing import Sequence

from .sng import ACCENT, CHILD, HAMMER_ON, PALM_MUTE, TREMOLO, Note

# Turns an arrangement's notes into key lights for a keyboard with lit keys (the Casio LK-170:
# 61 keys, C2 to C7, and at most 4 lit at once). Each note or chord stays lit until the next one,
# short notes stay lit long enough to see, and repeats of the same key blink. Their sound can ring on
# after their light, as a guitar's strings do (rings).

MIN_LIGHT = 0.08  # seconds a short note stays lit
GAP = 0.03  # dark time before the same key lights again
RING_MAX = 4.0  # seconds a note rings at most (more, if the chart holds it longer)
# Techniques (techniques): the notes besides a part's own sounding them
GLIDE_LEAST = 0.12  # seconds a slide takes at least (its steps a semitone each, over the note's sustain)
SLIDE_OFF = 0.15  # seconds a slide off takes at most, at the note's end
TREMOLO_EVERY = 0.075  # seconds between a tremolo-picked note's strikes
DEAD = 0.05  # seconds a dead note (fret-hand muted) sounds


@dataclass(frozen=True)
class KeyRange:
    lowest: int = 36  # C2
    highest: int = 96  # C7
    max_lit: int = 4

    def octave_shift(self, notes: Sequence[Note]) -> int:
        """Octaves (in semitones) to move a part up so its lowest note fits, if its top still fits."""
        pitches = [p for n in notes for p in n.pitches]
        if not pitches:
            return 0
        shift = 0
        while min(pitches) + shift < self.lowest and max(pitches) + shift + 12 <= self.highest:
            shift += 12
        return shift

    def fit(self, pitch: int) -> int:
        while pitch < self.lowest:
            pitch += 12
        while pitch > self.highest:
            pitch -= 12
        return pitch


@dataclass(frozen=True)
class Light:
    on: float
    off: float
    keys: tuple[int, ...]  # the keys lit: moved into range, at most max_lit
    pitches: tuple[int, ...] = ()  # the note's own pitches, all of them (for the sound)
    mask: int = 0  # the note's techniques (sng's mask bits: palm mute, accent, ...)
    rings: tuple[float, ...] = ()  # when each pitch stops sounding, letting it ring (none: when the light goes off)
    slide: int = 0  # the note's techniques (sng.Note's): semitones it slides to, and off to at its end
    slide_off: int = 0
    bends: tuple[tuple[float, float], ...] = ()  # (when, and semitones up)
    vibrato: bool = False
    extra: bool = False  # sound alone, from a technique (no keys): a slide's step, a tremolo's strike, a dead note


def together(notes: Sequence[Note]) -> list[Note]:
    """Notes that start at the same time as one: a held chord can go on as one linked note per string."""
    out: list[Note] = []
    for note in notes:
        if out and note.time == out[-1].time:
            last = out[-1]
            child = last.mask & note.mask & CHILD  # a continuation only if every one of them is
            mask = (last.mask | note.mask) & ~CHILD | child
            played = sorted(set(_strung(last)) | set(_strung(note)))
            out[-1] = Note(last.time, max(last.sustain, note.sustain), tuple(sorted({p for p, _s in played})), mask,
                           tuple(s for _p, s in sorted({p: s for p, s in played}.items())), last.slide or note.slide,
                           last.slide_off or note.slide_off, last.bends or note.bends, last.vibrato or note.vibrato)
        else:
            out.append(note)
    return out


def _strung(note: Note) -> list[tuple[int, int]]:
    """A note's pitches with their strings (-1: not known)."""
    return list(zip(note.pitches, note.strings or (-1,) * len(note.pitches)))


def rings(notes: Sequence[Note], shapes: Sequence[tuple[float, float]] = (),
          overlap: float = 0.0) -> list[tuple[float, ...]]:
    """When each note's pitches stop sounding, letting them ring as a guitar's strings do: until the string is
    played again (and overlap after, smoothing it: but a pitch played again stops as it is); held in a hand
    shape (shapes: start, end), until it ends; else until the part's next note (overlap after). RING_MAX at most,
    the note's own sustain at least (unless it's played again first). Palm mutes don't ring: none (as lit)."""
    starts = [start for start, _end in shapes]
    out: list[tuple[float, ...]] = []
    next_string: dict[int, float] = {}
    next_pitch: dict[int, float] = {}
    following = math.inf  # the part's next note
    for note in reversed(notes):
        if note.mask & PALM_MUTE:
            out.append(())
        else:
            k = bisect_right(starts, note.time) - 1
            held = shapes[k][1] if k >= 0 and note.time < shapes[k][1] else None
            least = note.time + max(note.sustain, MIN_LIGHT)
            ends = []
            for pitch, string in _strung(note):
                pitched = next_pitch.get(pitch, math.inf)
                again = min(next_string.get(string, math.inf) + overlap if string >= 0 else math.inf, pitched)
                natural = min(held if held is not None else following + overlap, again)
                ends.append(max(min(least, again), min(note.time + RING_MAX, natural)))
            out.append(tuple(ends))
        for pitch, string in _strung(note):
            next_pitch[pitch] = note.time
            if string >= 0:
                next_string[string] = note.time
        following = note.time
    out.reverse()
    return out


def schedule(notes: Sequence[Note], keys: KeyRange = KeyRange(), shift: int | None = None,
             shapes: Sequence[tuple[float, float]] = (), overlap: float = 0.0) -> list[Light]:
    """Each note's light (and how long its pitches ring: rings, with the arrangement's hand shapes and the overlap
    after the next note)."""
    if shift is None:
        shift = keys.octave_shift(notes)
    lights: list[Light] = []
    grouped = together(notes)
    for note, ends in zip(grouped, rings(grouped, shapes, overlap)):
        lit = tuple(sorted({keys.fit(p + shift) for p in note.pitches}))[: keys.max_lit]
        end = note.time + max(note.sustain, MIN_LIGHT)
        if lights and note.mask & CHILD and lights[-1].keys == lit:
            # A linked note continues the one before it: keep the keys lit, and its sound ringing as long
            last = lights[-1]
            longer = tuple(max(a, b) for a, b in zip(last.rings, ends)) if len(last.rings) == len(ends) else last.rings
            lights[-1] = replace(last, off=max(last.off, end), rings=longer)
            continue
        if lights:
            last = lights[-1]
            until = note.time - (GAP if set(last.keys) & set(lit) else 0.0)
            # At least briefly lit, but never past the next note's start
            off = max(min(last.off, until), min(last.on + 0.02, note.time))
            lights[-1] = replace(last, off=off)
        lights.append(Light(note.time, end, lit, note.pitches, note.mask, ends, note.slide, note.slide_off, note.bends,
                            note.vibrato))
    return lights


def techniques(lights: Sequence[Light], muted: Sequence[Note] = ()) -> tuple[list[Light], list[Light]]:
    """A part's sound with its techniques: its lights (those sliding or tremolo-picked stopping as their first step
    or strike sounds), and what sounds besides them (extra, no keys): a slide's steps, a semitone at a time over its
    sustain (the last left to the note it slides into, if it's linked to it); a slide off's, quicker, at its end;
    a tremolo's strikes; and dead notes (muted: palm-muted, for the palm-mute tone). Bends and vibrato are the
    player's, as controls."""
    out: list[Light] = []
    extras: list[Light] = []
    for i, light in enumerate(lights):
        ends = light.rings or (light.off,) * len(light.pitches)
        end = max(ends, default=light.off)
        times: list[float] = []
        offsets: list[int] = []
        if light.slide:
            linked = i + 1 < len(lights) and bool(lights[i + 1].mask & CHILD) and \
                lights[i + 1].pitches == tuple(p + light.slide for p in light.pitches)
            span, steps = max(end - light.on, GLIDE_LEAST), abs(light.slide)
            times = [light.on + span * k / (steps + 1) for k in range(1, steps + 1 - linked)]
            offsets = [k * (1 if light.slide > 0 else -1) for k in range(1, len(times) + 1)]
        elif light.slide_off:
            steps = abs(light.slide_off)
            begin = max(end - min(SLIDE_OFF, (end - light.on) / 2), light.on)
            times = [begin + (end - begin) * k / steps for k in range(steps)]
            offsets = [k * (1 if light.slide_off > 0 else -1) for k in range(1, steps + 1)]
        elif light.mask & TREMOLO:
            count = max(int((end - light.on) / TREMOLO_EVERY) - 1, 0)
            times = [light.on + TREMOLO_EVERY * k for k in range(1, count + 1)]
            offsets = [0] * count
        times = [at for at in times if light.on < at < end]
        if not times:
            out.append(light)
            continue
        out.append(replace(light, rings=tuple(min(ring, times[0]) for ring in ends)))
        for k, (at, offset) in enumerate(zip(times, offsets)):
            until = times[k + 1] if k + 1 < len(times) else max(end, at + MIN_LIGHT)
            mask = light.mask & ~ACCENT | (HAMMER_ON if offset else 0)  # (a slide's steps: legato)
            extras.append(Light(at, at, (), tuple(p + offset for p in light.pitches), mask,
                                (until,) * len(light.pitches), extra=True))
    for note in muted:
        extras.append(Light(note.time, note.time, (), note.pitches, note.mask | PALM_MUTE,
                            (note.time + DEAD,) * len(note.pitches), extra=True))
    extras.sort(key=lambda light: light.on)
    return out, extras


TOGETHER = 0.05  # song seconds: strikes of the same key by different parts this close are one strike
# How the LK-170's key lights look, measured (real seconds): a key flashing every 80 ms or slower reads
# as flashes (best every ~115 ms with ~40-55% of it dark), every 60 ms almost as a steady light.
MIN_PERIOD = 0.07  # a key's strikes closer than this flash on every other one (the default)
LIGHTS_EARLY = 0.015  # the keys light this much before the notes by default: 10-20 ms looked in time, 40 late
DARK = 0.45  # dark before a key's next strike: this much of the time to it...
DARK_MIN, DARK_MAX = 0.03, 0.06  # ...but at least and at most this
MIN_ON = 0.02


@dataclass(frozen=True)
class KeyLight:
    on: float
    off: float
    key: int


def merge_keys(parts: Sequence[Sequence[Light]], rate: float = 1.0, lead: float = 0.0,
               min_period: float = MIN_PERIOD) -> list[KeyLight]:
    """One timeline per key from the lights of all the lit parts: what the keys show.

    Strikes of a key by different parts within TOGETHER of each other are one strike. Before a key's
    next strike it goes dark for DARK of the time to it, so repeats flash, and a strike while the key
    is lit keeps it lit as long as either note lasts. Repeats faster than min_period flash on every
    other strike. The limits are in real time: rate is the song seconds per second (the playback
    speed). lead (real seconds) lights the keys that much before the notes."""
    strikes: dict[int, list[tuple[float, float, int]]] = {}
    for part, lights in enumerate(parts):
        for light in lights:
            if light.off > light.on:
                for key in light.keys:
                    strikes.setdefault(key, []).append((light.on, light.off, part))
    min_period, dark_min, dark_max, min_on = (x * rate for x in (min_period, DARK_MIN, DARK_MAX, MIN_ON))
    early = lead * rate
    out: list[KeyLight] = []
    for key, found in strikes.items():
        found.sort()
        merged: list[list] = []  # [on, off, parts]
        for on, off, part in found:
            last = merged[-1] if merged else None
            if last and part not in last[2] and on - last[0] <= TOGETHER:
                last[1] = max(last[1], off)
                last[2].add(part)
            elif last and on - last[0] < min_period:  # too soon to flash again: lit on through it
                last[1] = max(last[1], off)
            else:
                merged.append([on, off, {part}])
        for i, (on, off, _parts) in enumerate(merged):
            if i + 1 < len(merged):
                following = merged[i + 1]
                if following[0] < off:  # struck again while lit: lit on through the longer note
                    following[1] = max(following[1], off)
                dark = min(max(DARK * (following[0] - on), dark_min), dark_max)
                off = min(max(min(off, following[0] - dark), on + min_on), following[0])
            out.append(KeyLight(on - early, off - early, key))
    out.sort(key=lambda k: (k.on, k.key))
    return out
