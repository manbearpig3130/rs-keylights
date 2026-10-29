from __future__ import annotations

from dataclasses import dataclass
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
    keys: tuple[int, ...]


def schedule(notes: Sequence[Note], keys: KeyRange = KeyRange(), shift: int | None = None) -> list[Light]:
    if shift is None:
        shift = keys.octave_shift(notes)
    lights: list[Light] = []
    for note in notes:
        lit = tuple(sorted({keys.fit(p + shift) for p in note.pitches}))[: keys.max_lit]
        end = note.time + max(note.sustain, MIN_LIGHT)
        if lights and note.mask & CHILD and lights[-1].keys == lit:
            # A linked note continues the one before it: keep the keys lit
            lights[-1] = Light(lights[-1].on, max(lights[-1].off, end), lit)
            continue
        if lights:
            last = lights[-1]
            until = note.time - (GAP if set(last.keys) & set(lit) else 0.0)
            # At least briefly lit, but never past the next note's start
            off = max(min(last.off, until), min(last.on + 0.02, note.time))
            lights[-1] = Light(last.on, off, last.keys)
        lights.append(Light(note.time, end, lit))
    return lights
