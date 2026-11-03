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
from .sound import KEYBOARD_CHANNELS, Voices

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
    shift: int  # octaves (in semitones) its lit keys are moved up (its sound: by its own notes' octaves, voices.shift)
    lights: list[Light]
    voices: Voices | None
    channels: tuple[int, ...] = ()  # the keyboard's channels it sounds on (none: silent, or another MIDI device)
    pooled: bool = False  # its keys come from the 4 the lit parts share, a moment at a time (plan's pooled)
    priority: int = 0  # its rank for them: 0 first


def channels_needed(voices: Voices) -> int:
    """The keyboard's channels a part's voices take: its main voice, and its palm mutes and low notes if they have
    tones of their own; on another MIDI device, none."""
    return 0 if voices.output is not None else 1 + (voices.low is not None) + (voices.mute is not None)


MOST_LIT = 4  # keys the keyboard lights at once

CHANNELS_USED_UP = (f"The keyboard's {len(KEYBOARD_CHANNELS)} channels are used up: play fewer parts on it, give "
                    "fewer palm-mute and low-note tones of their own, or move one to another MIDI device")


def plan(parts: Sequence[Part], voices_for: VoicesFor | None, strict: bool = True, pooled: bool = False,
         first: ArrangementRef | None = None) -> list[PlannedPart]:
    """Lights and voices for each part. The lit parts' keys share an octave shift, so they line up; each part's
    sound is moved by its own (its notes' octaves: the same however many keys it lights); each part on
    the keyboard sounds on channels of its own, as many as its voices need, in turn from KEYBOARD_CHANNELS (one
    on another MIDI device, none). Too many for them: a ValueError (strict), else the parts that don't fit just
    light their keys. Pooled, the lit parts share the keyboard's 4 lit keys, a moment at a time, as many different
    keys as they play (one two parts play: one key), the one first (the arrangement played in the game), then the
    rest in turn, ranked first; else each lights as many as it's given."""
    full = KeyRange()
    lit_shift = full.octave_shift([n for p in parts if p.lights for n in p.arrangement.notes])
    free = list(KEYBOARD_CHANNELS)
    planned = []
    ranked = sorted(range(len(parts)), key=lambda i: (first is None or parts[i].ref != first, i))
    for i, part in enumerate(parts):
        own = full.octave_shift(part.arrangement.notes)
        shift = lit_shift if part.lights else own
        voices, channels = None, ()
        if voices_for:
            wanted = voices_for(part.ref, (0, 0, 0))  # (its voices: which it has)
            need = channels_needed(wanted)
            if need > len(free) and strict:
                raise ValueError(CHANNELS_USED_UP)
            if need <= len(free):
                channels, free = tuple(free[:need]), free[need:]
                main = channels[0] if channels else 0
                low = channels[1] if wanted.low is not None and channels else main
                mute = channels[-1] if wanted.mute is not None and channels else main
                voices = voices_for(part.ref, (main, low, mute)).for_arrangement(own)
        most = MOST_LIT if pooled and part.lights else part.lights or MOST_LIT
        lights = schedule(part.arrangement.notes, KeyRange(max_lit=most), shift, part.arrangement.shapes,
                          voices.overlap if voices is not None else 0.0)
        planned.append(PlannedPart(part, shift, lights, voices, channels, pooled and bool(part.lights),
                                   ranked.index(i)))
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
    return Player([_track(p) for p in planned], keyboard, lead=lead, min_period=min_period,
                  most_keys=MOST_LIT)  # (pooled or not: at most the 4 the keyboard lights, the first ranked first)


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
                 sound=p.voices.output if p.voices is not None else None, priority=p.priority)


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
