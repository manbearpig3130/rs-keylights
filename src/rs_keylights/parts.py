from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .keyboard import Keyboard
from .library import ArrangementRef
from .lights import LIGHTS_EARLY, MIN_PERIOD, KeyRange, Light, schedule
from .player import Player, Track
from .sng import Arrangement
from .sound import CHANNEL_SETS, Voices

# The parts of a song played together, as play, the page and following make them: each part's lights
# and voices, and one player for them all.

VoicesFor = Callable[[ArrangementRef, tuple[int, int, int]], Voices]  # a part's voices, on these channels


@dataclass(frozen=True)
class Part:
    ref: ArrangementRef
    arrangement: Arrangement
    lights: int  # keys it lights (0: none); the keyboard lights 4 in all


@dataclass(frozen=True)
class PlannedPart:
    part: Part
    shift: int  # octaves (in semitones) its lit keys are moved up
    lights: list[Light]
    voices: Voices | None


def plan(parts: Sequence[Part], voices_for: VoicesFor | None) -> list[PlannedPart]:
    """Lights and voices for each part. The lit parts share an octave shift, so their keys line up; each
    part sounds on channels of its own (CHANNEL_SETS, in order)."""
    if len(parts) > len(CHANNEL_SETS):
        raise ValueError(f"{len(CHANNEL_SETS)} parts at most")
    full = KeyRange()
    lit_shift = full.octave_shift([n for p in parts if p.lights for n in p.arrangement.notes])
    planned = []
    for i, part in enumerate(parts):
        shift = lit_shift if part.lights else full.octave_shift(part.arrangement.notes)
        lights = schedule(part.arrangement.notes, KeyRange(max_lit=part.lights or 4), shift)
        voices = voices_for(part.ref, CHANNEL_SETS[i]).for_arrangement(shift) if voices_for else None
        planned.append(PlannedPart(part, shift, lights, voices))
    return planned


def player_for(planned: Sequence[PlannedPart], keyboard: Keyboard, lead: float = LIGHTS_EARLY,
               min_period: float = MIN_PERIOD) -> Player:
    """Sets the parts' tones on the keyboard and makes their player (lead: seconds the keys light early;
    min_period: repeats closer than this flash on every other note)."""
    for p in planned:
        if p.voices is None:
            continue
        for voice in p.voices.all():
            keyboard.set_voice(voice.channel, voice.tone.program, voice.tone.bank, volume=p.voices.volume)
    return Player([Track(p.lights, p.voices, lit=bool(p.part.lights)) for p in planned], keyboard,
                  lead=lead, min_period=min_period)
