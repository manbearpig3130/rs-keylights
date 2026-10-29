from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .sng import CHILD, Note

# Turns an arrangement's notes into key lights for a keyboard with lit keys (the Casio LK-170:
# 61 keys, C2 to C7, and at most 4 lit at once). Each note or chord stays lit until the next one,
# short notes stay lit long enough to see, and repeats of the same key blink.

MIN_LIGHT = 0.08  # seconds a short note stays lit
GAP = 0.03  # dark time before the same key lights again


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


def together(notes: Sequence[Note]) -> list[Note]:
    """Notes that start at the same time as one: a held chord can go on as one linked note per string."""
    out: list[Note] = []
    for note in notes:
        if out and note.time == out[-1].time:
            last = out[-1]
            child = last.mask & note.mask & CHILD  # a continuation only if every one of them is
            mask = (last.mask | note.mask) & ~CHILD | child
            out[-1] = Note(last.time, max(last.sustain, note.sustain), tuple(sorted(set(last.pitches + note.pitches))), mask)
        else:
            out.append(note)
    return out


def schedule(notes: Sequence[Note], keys: KeyRange = KeyRange(), shift: int | None = None) -> list[Light]:
    if shift is None:
        shift = keys.octave_shift(notes)
    lights: list[Light] = []
    for note in together(notes):
        lit = tuple(sorted({keys.fit(p + shift) for p in note.pitches}))[: keys.max_lit]
        end = note.time + max(note.sustain, MIN_LIGHT)
        if lights and note.mask & CHILD and lights[-1].keys == lit:
            # A linked note continues the one before it: keep the keys lit
            lights[-1] = replace(lights[-1], off=max(lights[-1].off, end))
            continue
        if lights:
            last = lights[-1]
            until = note.time - (GAP if set(last.keys) & set(lit) else 0.0)
            # At least briefly lit, but never past the next note's start
            off = max(min(last.off, until), min(last.on + 0.02, note.time))
            lights[-1] = replace(last, off=off)
        lights.append(Light(note.time, end, lit, note.pitches, note.mask))
    return lights


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
