from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Mapping

from .library import ArrangementRef
from .lights import KeyRange, Light
from .sng import ACCENT, NAMES, PALM_MUTE, note_name
from .tones import Tone, tone

# The notes' sound, apart from the lights: the lights stay on the keyboard's navigate channel, silent,
# and each note sounds on a channel of its own kind, each with its tone: palm-muted notes, notes below
# a split point, and the rest. The keyboard plays all 16 channels at once.

# (main, low, mute) for each part played at once: clear of the navigate channels (3, 4), the keyboard's
# own part (1) and the drums (10)
CHANNEL_SETS = ((11, 12, 13), (14, 15, 16), (5, 6, 7), (8, 9, 2))
VELOCITY, ACCENT_VELOCITY = 100, 127
DEFAULT_SPLIT = 40  # E2, a guitar's lowest note in standard tuning
GUITAR_TONE = 79  # DISTORTION GT
BASS_TONE = 85  # FINGERED BASS


@dataclass(frozen=True)
class Voice:
    channel: int
    tone: Tone


@dataclass(frozen=True)
class Voices:
    main: Voice
    low: Voice | None = None  # notes whose lowest pitch is below split
    mute: Voice | None = None  # palm-muted notes (before the low voice)
    split: int = DEFAULT_SPLIT
    real_pitch: bool = False  # sound the notes' own pitches, not the lit keys' (moved into range)
    key_range: KeyRange = KeyRange()
    shift: int = 0  # the arrangement's octave shift, for the lit keys' pitches

    def for_arrangement(self, shift: int) -> Voices:
        return replace(self, shift=shift)

    def all(self) -> list[Voice]:
        return [v for v in (self.main, self.low, self.mute) if v is not None]

    def pick(self, light: Light) -> tuple[int, tuple[int, ...], int]:
        """(channel, notes, velocity) for a light."""
        pitches = light.pitches or light.keys
        if self.mute and light.mask & PALM_MUTE:
            voice = self.mute
        elif self.low and min(pitches) < self.split:
            voice = self.low
        else:
            voice = self.main
        if self.real_pitch:
            notes = {min(max(p, 0), 127) for p in pitches}
        else:
            notes = {self.key_range.fit(p + self.shift) for p in pitches}
        return voice.channel, tuple(sorted(notes)), ACCENT_VELOCITY if light.mask & ACCENT else VELOCITY

    def describe(self) -> str:
        parts = [f"tone {self.main.tone.number:03d} {self.main.tone.name}"]
        if self.mute:
            parts.append(f"palm mutes {self.mute.tone.number:03d} {self.mute.tone.name}")
        if self.low:
            parts.append(f"below {note_name(self.split)} {self.low.tone.number:03d} {self.low.tone.name}")
        if self.real_pitch:
            parts.append("at the real pitch")
        return ", ".join(parts)


def parse_note(text: str) -> int:
    """A MIDI note number from a name (B1, F#2, Bb2, C-1) or a number (35)."""
    text = text.strip()
    if text.isdigit():
        number = int(text)
    else:
        match = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", text)
        if not match:
            raise ValueError(f"Not a note: '{text}' (try B1, F#2 or a MIDI note number)")
        letter, accidental, octave = match.groups()
        number = (int(octave) + 1) * 12 + NAMES.index(letter.upper()) + {"#": 1, "b": -1, "": 0}[accidental]
    if not 0 <= number <= 127:
        raise ValueError(f"Note out of range: '{text}'")
    return number


class SoundChoice:
    """The sound options, checked: the voices for each part. Each table maps a part (None: every part) to a
    value; a part's own None means none for it (or the default tone, for the main one)."""

    def __init__(self, main: Mapping[str | None, Tone | None], low: Mapping[str | None, Tone | None],
                 mute: Mapping[str | None, Tone | None], split: Mapping[str | None, int | None], real_pitch: bool):
        self.main, self.low, self.mute, self.split, self.real_pitch = main, low, mute, split, real_pitch

    @property
    def parts(self) -> set[str]:
        return {part for table in (self.main, self.low, self.mute, self.split) for part in table if part}

    def voices(self, ref: ArrangementRef, channels: tuple[int, int, int] = CHANNEL_SETS[0]) -> Voices:
        def pick(table: Mapping):
            return table.get(ref.part, table.get(ref.name.lower(), table.get(None)))

        main, low, mute, split = pick(self.main), pick(self.low), pick(self.mute), pick(self.split)
        return Voices(
            main=Voice(channels[0], main or tone(BASS_TONE if ref.bass else GUITAR_TONE)),
            low=Voice(channels[1], low) if low else None,
            mute=Voice(channels[2], mute) if mute else None,
            split=DEFAULT_SPLIT if split is None else split,
            real_pitch=self.real_pitch,
        )
