from __future__ import annotations

import math
from bisect import bisect_right

from dataclasses import dataclass, replace
from typing import Callable, Sequence

from .keyboard import Keyboard
from .library import ArrangementRef
from .lights import LIGHTS_EARLY, MIN_PERIOD, KeyRange, Light, schedule, techniques
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
        voices = voices_for(part.ref, CHANNEL_SETS[i]).for_arrangement(shift) if voices_for else None
        lights = schedule(part.arrangement.notes, KeyRange(max_lit=part.lights or 4), shift, part.arrangement.shapes,
                          voices.overlap if voices is not None else 0.0)
        planned.append(PlannedPart(part, shift, lights, voices))
    return planned


def player_for(planned: Sequence[PlannedPart], keyboard: Keyboard, lead: float = LIGHTS_EARLY,
               min_period: float = MIN_PERIOD) -> Player:
    """Sets the parts' tones on the keyboard and makes their player (lead: seconds the keys light early;
    min_period: repeats closer than this flash on every other note)."""
    for p in planned:
        if p.voices is None:
            continue
        sound = p.voices.output or keyboard  # (another MIDI device: its own sound, or its program chosen)
        if p.voices.output is not None and p.voices.program is not None:
            p.voices.output.program(p.voices.main.channel, p.voices.program)
        for voice in p.voices.all():
            sound.set_voice(voice.channel, voice.tone.program, voice.tone.bank, volume=p.voices.volume)
    return Player([_track(p) for p in planned], keyboard, lead=lead, min_period=min_period)


def _track(p: PlannedPart) -> Track:
    """A planned part's track: its lights, and with techniques what sounds besides them (dead notes, with a palm-mute
    tone to sound them); its tone changes."""
    lights: list[Light] = list(p.lights)
    extras: list[Light] = []
    if p.voices is not None and p.voices.techniques:
        lights, extras = techniques(p.lights, p.part.arrangement.muted if p.voices.mute else ())
    if p.voices is not None and p.voices.mono:
        everything = [*lights, *extras]
        lights, extras = _one_at_a_time(lights, everything), _one_at_a_time(extras, everything)
    return Track(lights, p.voices, lit=bool(p.part.lights), changes=_tone_changes(p), extras=extras,
                 sound=p.voices.output if p.voices is not None else None)


def _one_at_a_time(lights: list[Light], among: list[Light]) -> list[Light]:
    """Lights each sounding until the next sound starts (among them all), as a monophonic synth plays them: a held
    note let go after the next would bring it back."""
    ons = sorted({light.on for light in among})
    out = []
    for light in lights:
        k = bisect_right(ons, light.on)
        until = ons[k] if k < len(ons) else math.inf
        out.append(replace(light, rings=tuple(min(ring, until) for ring in light.rings)) if light.rings else light)
    return out


def _tone_changes(p: PlannedPart) -> tuple[tuple[float, int, int, int | None], ...]:
    """A part's main tone as the chart switches tone: when, its channel, program and bank; first, where it starts
    (from the start: -inf)."""
    if p.voices is None:
        return ()
    changes = p.voices.changes_for(p.part.arrangement, p.part.ref.bass)
    if not changes:
        return ()
    main = p.voices.main
    return tuple((at, main.channel, t.program, t.bank) for at, t in ((-math.inf, main.tone), *changes))
