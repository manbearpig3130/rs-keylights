from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping

from .library import ArrangementRef
from .lights import KeyRange, Light
from .sng import (ACCENT, FRET_HAND_MUTE, HAMMER_ON, HARMONIC, NAMES, PALM_MUTE, PINCH_HARMONIC, POP, PULL_OFF, SLAP,
                  TAP, Arrangement)
from .songtones import kind_changes
from .tones import Tone, tone

# The notes' sound, apart from the lights: the lights stay on the keyboard's navigate channel, silent,
# and each note sounds on a channel of its own kind, each with its tone: palm-muted notes, pinch harmonics' squeals,
# and the rest. The keyboard plays all 16 channels at once.

# The channels the parts sound on: clear of the navigate channels (3, 4), the keyboard's own part (1) and the drums
# (10). Each part on the keyboard takes as many as its voices need, in turn (parts.plan): its main voice, and its
# palm mutes and pinch harmonics if they have tones of their own; one on another MIDI device takes none
KEYBOARD_CHANNELS = (11, 12, 13, 14, 15, 16, 5, 6, 7, 8, 9, 2)
CHANNEL_SETS = ((11, 12, 13), (14, 15, 16), (5, 6, 7), (8, 9, 2))  # (main, pinch, mute) each (the first: a part's
# own)
VELOCITY, ACCENT_VELOCITY = 100, 127
LEGATO_VELOCITY, DEAD_VELOCITY = 75, 50  # hammer-ons, pull-offs and a slide's steps; dead notes (techniques)
UP_SOFTER = 12  # a chord strummed up: this much softer (techniques)
PALM_VELOCITY = 88  # palm mutes (techniques)
PLAIN_DEAD_VELOCITY = 35  # dead notes on a part's own tone (no palm-mute tone: just a tick)
PINCH_TONE = 298  # GM GT HARMONICS: pinch harmonics' squeals' tone, and natural harmonics', unless a part has its own
# (or the same tone)
HARMONICS = PINCH_HARMONIC | HARMONIC  # a part with these has a channel for them (parts.plan)
GUITAR_TONE = 79  # DISTORTION GT
BASS_TONE = 85  # FINGERED BASS


@dataclass(frozen=True)
class Squeal:
    """How pinch harmonics sound (techniques; the page's Pinch harmonics card): the squeal UP semitones over the
    fretted note, as hard as velocity, the note under it as hard as note_velocity (unless accented); the squeal
    struck SCOOP semitones flat, up to its pitch over scoop_ms, then a vibrato of WIDTH semitones either way, RATE
    times a second, growing in over grows_ms (pitch bend, on its own channel); always, or only where the chart has
    vibrato ("charted")."""

    up: int = 19  # (an octave and a fifth)
    velocity: int = 127
    note_velocity: int = 70
    scoop: float = 0.5
    scoop_ms: float = 80.0
    width: float = 1.05
    rate: float = 15.0
    grows_ms: float = 200.0
    vibrato: str = "always"


SQUEAL_LIMITS = {"up": (0, 36), "velocity": (1, 127), "note_velocity": (1, 127), "scoop": (0.0, 2.0),
                 "scoop_ms": (0.0, 500.0), "width": (0.0, 2.0), "rate": (0.5, 15.0), "grows_ms": (0.0, 2000.0)}
SQUEAL_VIBRATO = ("always", "charted")


def squeal_from(was: Squeal, change: Mapping[str, Any]) -> Squeal:
    """A squeal's settings changed (those given; checked: a ValueError if one isn't a number in its range)."""
    out: dict[str, Any] = {}
    for name, (low, high) in SQUEAL_LIMITS.items():
        if name not in change:
            continue
        try:
            value = float(change[name])
        except (TypeError, ValueError):
            raise ValueError(f"Pinch harmonics: {name} isn't a number") from None
        if not low <= value <= high:
            raise ValueError(f"Pinch harmonics: {name} goes from {low:g} to {high:g}")
        out[name] = round(value) if isinstance(low, int) else value
    if "vibrato" in change:
        if change["vibrato"] not in SQUEAL_VIBRATO:
            raise ValueError("Pinch harmonics: vibrato is always or charted")
        out["vibrato"] = change["vibrato"]
    return replace(was, **out)


@dataclass(frozen=True)
class Voice:
    channel: int
    tone: Tone


@dataclass(frozen=True)
class Voices:
    main: Voice
    mute: Voice | None = None  # palm-muted notes
    squeal: Voice | None = None  # pinch harmonics' squeals and natural harmonics (techniques; none: the note's voice)
    squeal_shape: Squeal = Squeal()  # and how they sound
    real_pitch: bool = False  # sound the notes' own pitches, not the lit keys' (moved into range)
    key_range: KeyRange = KeyRange()
    shift: int = 0  # the arrangement's octave shift, for the lit keys' pitches
    volume: int = 100  # its channels' volume (Control Change 7; 100: as they start)
    ring: bool = True  # notes ring on after their light, as a guitar's strings do (lights.rings); else just as lit
    overlap: float = 0.0  # seconds a note rings on after the next (of another pitch) sounds, ringing
    tone_map: tuple[tuple[str, Tone], ...] = ()  # the main tone for each kind the song's tone switches to (none: as
    # it is), songtones' kinds
    techniques: bool = True  # bends, slides, vibrato, tremolo, legato, slaps and dead notes played (lights.techniques,
    # and the player's controls); else accents alone
    output: Any = None  # another MIDI device sounding the part (keyboard.Synth; None: the keyboard)
    mono: bool = False  # a note at a time (each stopping as the next sounds: a monophonic synth)
    program: int | None = None  # the device's own program for it (None: as it is)
    transpose: int = 0  # semitones its notes sound up (an output's octaves)

    def for_arrangement(self, shift: int) -> Voices:
        return replace(self, shift=shift)

    def all(self) -> list[Voice]:
        return [v for v in (self.main, self.mute, self.squeal) if v is not None]

    def pick(self, light: Light) -> tuple[int, tuple[int, ...], int]:
        """(channel, notes, velocity) for a light."""
        squeal = self.techniques and light.extra and light.mask & PINCH_HARMONIC
        if self.squeal and (squeal or self.techniques and light.mask & HARMONIC):  # (natural harmonics too: chiming)
            voice = self.squeal
        elif self.mute and light.mask & PALM_MUTE:
            voice = self.mute
        else:
            voice = self.main
        velocity = self.velocity(light.mask)
        if self.techniques and light.extra and light.mask & PINCH_HARMONIC:
            velocity = self.squeal_shape.velocity
        if self.techniques and light.mask & FRET_HAND_MUTE and voice is not self.mute:
            velocity = PLAIN_DEAD_VELOCITY
        if self.techniques and light.up and len(light.pitches) > 1:
            velocity = max(velocity - UP_SOFTER, 1)
        if self.techniques and light.level != 1.0:
            velocity = max(round(velocity * light.level), 1)
        return voice.channel, tuple(sorted(set(self.notes_for(light)))), velocity

    def velocity(self, mask: int) -> int:
        """How hard a note's played: accented (slapped or popped too) hardest, palm-muted a little softer,
        hammered-on, pulled-off or tapped softer still, a pinch harmonic's note (under its squeal) and a dead note
        softest
        (the techniques: else accents alone)."""
        if not self.techniques:
            return ACCENT_VELOCITY if mask & ACCENT else VELOCITY
        if mask & FRET_HAND_MUTE:
            return DEAD_VELOCITY
        if mask & (ACCENT | SLAP | POP):
            return ACCENT_VELOCITY
        if mask & PINCH_HARMONIC:
            return self.squeal_shape.note_velocity
        if mask & (HAMMER_ON | PULL_OFF | TAP):  # (no pick: legato)
            return LEGATO_VELOCITY
        return PALM_VELOCITY if mask & PALM_MUTE else VELOCITY

    def changes_for(self, arrangement: Arrangement, bass: bool = False) -> tuple[tuple[float, Tone], ...]:
        """When the main tone changes as the chart switches tone: back to its own, the main tone; to a kind of tone,
        the one for it (tone_map); to another, none."""
        table = dict(self.tone_map)
        if not table:
            return ()
        out: list[tuple[float, Tone]] = []
        current = self.main.tone
        for at, kind in kind_changes(arrangement, bass):
            switched = self.main.tone if kind is None else table.get(kind)
            if switched is not None and switched != current:
                out.append((at, switched))
                current = switched
        return tuple(out)

    def notes_for(self, light: Light) -> tuple[int, ...]:
        """Each of a light's pitches' note, as they are (pick's, in order); a pinch harmonic's squeal (its extra
        sound, lights.techniques) over the note."""
        pitches = light.pitches or light.keys
        squeal = self.techniques and light.extra and light.mask & PINCH_HARMONIC
        up = self.transpose + (self.squeal_shape.up if squeal else 0)
        if self.real_pitch:
            return tuple(min(max(p + up, 0), 127) for p in pitches)
        return tuple(min(max(self.key_range.fit(p + self.shift) + up, 0), 127) for p in pitches)

    def describe(self) -> str:
        parts = [f"tone {self.main.tone.number:03d} {self.main.tone.name}"]
        if self.mute:
            parts.append(f"palm mutes {self.mute.tone.number:03d} {self.mute.tone.name}")
        if self.squeal:
            parts.append(f"pinch harmonics {self.squeal.tone.number:03d} {self.squeal.tone.name}")
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

    def __init__(self, main: Mapping[str | None, Tone | None], pinch: Mapping[str | None, Tone | None],
                 mute: Mapping[str | None, Tone | None], real_pitch: bool, ring: bool = True,
                 tone_map: Mapping[str, Tone] | None = None, techniques: bool = True, overlap: float = 0.0,
                 squeal: Squeal = Squeal()):
        self.main, self.pinch, self.mute, self.real_pitch = main, pinch, mute, real_pitch
        self.squeal = squeal
        self.ring, self.tone_map, self.techniques = ring, tuple((tone_map or {}).items()), techniques
        self.overlap = overlap

    @property
    def parts(self) -> set[str]:
        return {part for table in (self.main, self.pinch, self.mute) for part in table if part}

    def voices(self, ref: ArrangementRef, channels: tuple[int, int, int] = CHANNEL_SETS[0]) -> Voices:
        def pick(table: Mapping):
            return table.get(ref.part, table.get(ref.name.lower(), table.get(None)))

        main, pinch, mute = pick(self.main), pick(self.pinch), pick(self.mute)
        return Voices(
            main=Voice(channels[0], main or tone(BASS_TONE if ref.bass else GUITAR_TONE)),
            mute=Voice(channels[2], mute) if mute else None,
            squeal=Voice(channels[1], pinch) if pinch and self.techniques else None,
            squeal_shape=self.squeal,
            real_pitch=self.real_pitch,
            ring=self.ring,
            tone_map=self.tone_map,
            techniques=self.techniques,
            overlap=self.overlap,
        )
