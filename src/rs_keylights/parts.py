from __future__ import annotations

import math
from bisect import bisect_right

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from .drums import CLOSED_HAT, CRASH, DRUM_CHANNEL, KICK, OPEN_HAT, RIDE, SNARE, TOMS, Hit
from .keyboard import BEND_RANGE, NORMAL_VOLUME, Keyboard
from .library import ArrangementRef
from .lights import LIGHTS_EARLY, MIN_PERIOD, KeyRange, Light, schedule, strums, techniques
from .player import Player, Track
from .sng import Arrangement
from .sound import HARMONICS, KEYBOARD_CHANNELS, Voices
from .tones import DRUM_SET_BANK

# The parts of a song played together, as play, the page and following make them: each part's lights
# and voices, and one player for them all.

VoicesFor = Callable[[ArrangementRef, tuple[int, int, int]], Voices]  # a part's voices, on these channels
HIT_LENGTH = 0.1  # seconds a drum's hit sounds (its sound decays by itself)
DRUM_ORDER = (KICK, SNARE, CRASH, RIDE, CLOSED_HAT, OPEN_HAT, *reversed(TOMS))  # which wins a device's drum, two at once
RINGS = {CRASH: 2.0, RIDE: 1.2, OPEN_HAT: 0.6, **{t: 0.4 for t in TOMS}}  # seconds a drum's heard for, about...
RING = 0.15  # ...the others' (a kick, a closed hi-hat)
TOGETHER = 0.03  # seconds: hits this close, at once (one of a device's drums can't play both)


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
    """The keyboard's channels a part's voices take: its main voice, and its palm mutes and pinch harmonics if they
    have tones of their own; on another MIDI device, none."""
    return 0 if voices.output is not None else 1 + (voices.squeal is not None) + (voices.mute is not None)


MOST_LIT = 4  # keys the keyboard lights at once

CHANNELS_USED_UP = (f"The keyboard's {len(KEYBOARD_CHANNELS)} channels are used up: play fewer parts on it, give "
                    "fewer palm-mute and pinch-harmonic tones of their own, or move one to another MIDI device")


class PlanCache:
    """Parts' lights and tracks as they were last made (for the same notes and choices), kept so a change to one
    part (a light toggled, a tone turned) remakes just that one: the others' are the same objects as before (and
    a player's lists for them too, Player's). The latest MOST of each are kept."""

    MOST = 64

    def __init__(self) -> None:
        self._lights: dict[tuple[int, int, int, float], tuple[Arrangement, list[Light]]] = {}
        self._tracks: dict[tuple[int, int, bool, int], list[tuple[PlannedPart, Track]]] = {}
        self._drums: dict[tuple[int, int], list[tuple[Sequence[Hit], DrumVoices, list[Track]]]] = {}
        self.player: dict[int, tuple[Track, Any]] = {}  # (Player's, by its track)

    def lights(self, arrangement: Arrangement, keys: KeyRange, shift: int, overlap: float) -> list[Light]:
        key = (id(arrangement), keys.max_lit, shift, overlap)
        got = self._lights.get(key)
        if got is not None and got[0] is arrangement:
            return got[1]
        lights = schedule(arrangement.notes, keys, shift, arrangement.shapes, overlap, arrangement.anchors,
                          arrangement.capo, arrangement.arpeggios, arrangement.muted)
        self._keep(self._lights)[key] = (arrangement, lights)
        return lights

    def track(self, p: PlannedPart) -> Track:
        key = (id(p.lights), id(p.part.arrangement), bool(p.part.lights), p.priority)
        for was, track in self._tracks.get(key, ()):
            if was.lights is p.lights and was.part.arrangement is p.part.arrangement and was.voices == p.voices \
                    and was.part.ref.bass == p.part.ref.bass:
                return track
        track = _track(p)
        self._keep(self._tracks).setdefault(key, []).append((p, track))
        return track

    def drums(self, hits: Sequence[Hit], voices: DrumVoices, sound: Any) -> list[Track]:
        key = (id(hits), id(sound))
        for was_hits, was_voices, tracks in self._drums.get(key, ()):
            if was_hits is hits and was_voices == voices and all(t.sound is sound for t in tracks):
                return tracks
        tracks = drum_tracks(hits, voices, sound)
        self._keep(self._drums).setdefault(key, []).append((hits, voices, tracks))
        return tracks

    def _keep(self, store: dict[Any, Any]) -> dict[Any, Any]:
        """The store, emptied first when it's full."""
        if len(store) >= self.MOST:
            store.clear()
        return store


def plan(parts: Sequence[Part], voices_for: VoicesFor | None, strict: bool = True, pooled: bool = True,
         first: ArrangementRef | None = None, cache: PlanCache | None = None) -> list[PlannedPart]:
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
            squeals = wanted.squeal is not None and wanted.output is None and \
                any(n.mask & HARMONICS for n in part.arrangement.notes)
            if squeals and channels_needed(wanted) > len(free):
                squeals = False  # (on the note's own tone, rather than not at all)
            if not squeals:
                wanted = replace(wanted, squeal=None)
            need = channels_needed(wanted)
            if need > len(free) and strict:
                raise ValueError(CHANNELS_USED_UP)
            if need <= len(free):
                channels, free = tuple(free[:need]), free[need:]
                main = channels[0] if channels else 0
                pinch = channels[1] if squeals and channels else main
                mute = channels[-1] if wanted.mute is not None and channels else main
                voices = voices_for(part.ref, (main, pinch, mute)).for_arrangement(own)
                if not squeals:
                    voices = replace(voices, squeal=None)
        most = MOST_LIT if pooled and part.lights else part.lights or MOST_LIT
        overlap = voices.overlap if voices is not None else 0.0
        lights = cache.lights(part.arrangement, KeyRange(max_lit=most), shift, overlap) if cache is not None else \
            schedule(part.arrangement.notes, KeyRange(max_lit=most), shift, part.arrangement.shapes, overlap,
                     part.arrangement.anchors, part.arrangement.capo, part.arrangement.arpeggios,
                     part.arrangement.muted)
        planned.append(PlannedPart(part, shift, lights, voices, channels, pooled and bool(part.lights),
                                   ranked.index(i)))
    return planned


@dataclass(frozen=True)
class DrumVoices:
    """A drum part's sound: each hit on its channel (General MIDI's drums, 10), its drum's note mapped (another
    device's own notes: a Novation Circuit's 4 drums; a drum mapped to none isn't played), at the hit's velocity
    (its light's mask carries it). Its hits are short: they don't ring."""

    channel: int = DRUM_CHANNEL
    notes: Mapping[int, int | None] = field(default_factory=dict)  # (General MIDI note -> the device's; absent: as is)
    volume: int = NORMAL_VOLUME
    ring: bool = False
    techniques: bool = False
    # A drum's sound chosen on the device first (General MIDI note -> a Control Change and its value: a Novation
    # Circuit's drum patch select, so one of its 4 drums plays several), sent as it's needed (another than the
    # last on that control); what was sent last, by (channel, control), for a device that keeps no record of its own
    # (a Synth does, controls_sent: so a drum struck on the page, choosing a sound there, is known to the part too)
    patches: Mapping[int, tuple[int, int]] = field(default_factory=dict)
    sent: dict[tuple[int, int], int] = field(default_factory=dict, compare=False)
    # Drums that share some of the device's (each hit on whichever suits it then: place): their sounds there
    # (General MIDI note -> the patch select's value), and the drums they share (the device's note -> its patch
    # select's Control Change)
    shared: Mapping[int, int] = field(default_factory=dict)
    pool: Mapping[int, int] = field(default_factory=dict)
    kit: int | None = None  # the keyboard's drum kit (its program, tones.DRUM_SETS'); none: as it is

    def pick(self, light: Light) -> tuple[int, tuple[int, ...], int]:
        return self.channel, self.notes_for(light), max(1, min(127, light.mask))

    def prepare(self, sound: Any, light: Light) -> None:
        """Before a hit sounds: its drum's sound chosen on the device, if it isn't already."""
        record = self._record(sound)
        for pitch in light.pitches:
            if pitch in self.patches:
                number, value = self.patches[pitch]
                if record.get((self.channel, number)) != value:
                    sound.control(self.channel, number, value)
                    record[(self.channel, number)] = value

    def _record(self, sound: Any) -> dict[tuple[int, int], int]:
        """What's been sent on the device's controls: its own record, if it keeps one, else this part's."""
        record = getattr(sound, "controls_sent", None)
        return record if isinstance(record, dict) else self.sent

    def choose_again(self, sound: Any) -> None:
        """Each of its drums' sounds sent again before its next hit (a new player: as a new part's would be, in
        case the device was changed meanwhile, by hand say)."""
        record = self._record(sound)
        for number, _value in self.patches.values():
            record.pop((self.channel, number), None)

    def notes_for(self, light: Light) -> tuple[int, ...]:
        mapped = (self.notes.get(p, p) for p in light.pitches)
        return tuple(n for n in mapped if n is not None)


def drum_track(hits: Sequence[Hit], voices: DrumVoices, sound: Any = None) -> Track:
    """A drum part's track: its hits sounding (none lit), on the keyboard or another device (sound). Two at once on
    one of the device's drums (its notes) that choose their sound there: the first of DRUM_ORDER (a drum plays one
    sound at a time)."""
    if voices.patches:
        first: dict[tuple[float, int | None], Hit] = {}
        for h in sorted(hits, key=lambda h: (h.time, DRUM_ORDER.index(h.note) if h.note in DRUM_ORDER else 99)):
            first.setdefault((h.time, voices.notes.get(h.note, h.note)), h)
        hits = sorted(first.values(), key=lambda h: (h.time, h.note))
    lights = [Light(h.time, h.time + HIT_LENGTH, (), (h.note,), h.velocity) for h in hits]
    return Track(lights, voices, lit=False, sound=sound)


def drum_tracks(hits: Sequence[Hit], voices: DrumVoices, sound: Any = None) -> list[Track]:
    """A drum part's tracks: its drum_track, and with drums that share some of the device's (voices.shared), a
    track for each of those (pool) with the hits placed on it."""
    if not (voices.shared and voices.pool):
        return [drum_track(hits, voices, sound)]
    out = [drum_track([h for h in hits if h.note not in voices.shared], voices, sound)]
    placed = place([h for h in hits if h.note in voices.shared], tuple(voices.pool))
    for device_note, number in voices.pool.items():
        own = DrumVoices(channel=voices.channel, notes={n: device_note for n in voices.shared}, volume=voices.volume,
                         patches={n: (number, value) for n, value in voices.shared.items()})
        out.append(Track([Light(h.time, h.time + HIT_LENGTH, (), (h.note,), h.velocity) for h in placed[device_note]],
                         own, lit=False, sound=sound))
    return out


def place(hits: Sequence[Hit], drums: Sequence[int]) -> dict[int, list[Hit]]:
    """Hits shared by several of a device's drums (each plays one sound at a time, its sound chosen first), each on
    one of them: one that has its sound already (nothing to change; struck again, it stops itself, as it would),
    else the one whose sound dies away first (RINGS: a crash isn't stopped while another's free); never one struck
    at the same moment (TOGETHER). More at once than there are drums: the first of DRUM_ORDER."""
    out: dict[int, list[Hit]] = {d: [] for d in drums}
    has: dict[int, int | None] = {d: None for d in drums}
    struck = {d: -math.inf for d in drums}
    heard = {d: -math.inf for d in drums}  # (until)
    for h in sorted(hits, key=lambda h: (h.time, DRUM_ORDER.index(h.note) if h.note in DRUM_ORDER else 99)):
        free = [d for d in drums if h.time - struck[d] >= TOGETHER]
        if not free:
            continue
        same = [d for d in free if has[d] == h.note]
        drum = same[0] if same else min(free, key=lambda d: (heard[d], struck[d]))
        out[drum].append(h)
        has[drum], struck[drum], heard[drum] = h.note, h.time, h.time + RINGS.get(h.note, RING)
    return out


def player_for(planned: Sequence[PlannedPart], keyboard: Keyboard, lead: float = LIGHTS_EARLY,
               min_period: float = MIN_PERIOD, extra: Sequence[Track] = (), cache: PlanCache | None = None) -> Player:
    """Sets the parts' tones on the keyboard and makes their player (lead: seconds the keys light early;
    min_period: repeats closer than this flash on every other note), with extra tracks of their own (a drum part)."""
    for track in extra:
        if isinstance(track.voices, DrumVoices):  # (its channel's volume; its device's drum sounds as they are)
            sound: Any = track.sound or keyboard  # (a Keyboard or a Synth: both set a channel's volume)
            track.voices.choose_again(sound)  # (its drums' sounds sent again, as a new player's are)
            if track.sound is None and track.voices.kit is not None:  # (the keyboard's: its kit too)
                keyboard.set_voice(track.voices.channel, track.voices.kit, DRUM_SET_BANK, volume=track.voices.volume)
            else:
                sound.set_volume(track.voices.channel, track.voices.volume)
    for p in planned:
        if p.voices is None:
            continue
        sound = p.voices.output or keyboard  # (another MIDI device: its own sound, or its program chosen)
        if p.voices.output is not None and p.voices.program is not None:
            p.voices.output.program(p.voices.main.channel, p.voices.program)
        for voice in p.voices.all():
            sound.set_voice(voice.channel, voice.tone.program, voice.tone.bank, volume=p.voices.volume)
        if p.voices.output is None:  # (the keyboard's: another device's as it is)
            for channel, semitones in bend_ranges(p).items():
                keyboard.bend_range(channel, semitones)
    tracks = [cache.track(p) if cache is not None else _track(p) for p in planned]
    return Player([*tracks, *extra], keyboard, lead=lead, min_period=min_period, most_keys=MOST_LIT,
                  cache=cache.player if cache is not None else None,  # (pooled or not: at most the 4 the keyboard lights, the first ranked first)
                  spare=spare_channels(planned, extra))


def spare_channels(planned: Sequence[PlannedPart], extra: Sequence[Track] = ()) -> tuple[int, ...]:
    """The keyboard's channels no part sounds on (nor a drum part): a note whose pitch moves borrows one (Player)."""
    used = {c for p in planned for c in p.channels}
    used |= {t.voices.channel for t in extra if isinstance(t.voices, DrumVoices)}
    return tuple(c for c in KEYBOARD_CHANNELS if c not in used)


def bend_ranges(p: PlannedPart) -> dict[int, int]:
    """The pitch-bend range a part's channels need (semitones, whole): its biggest bend (with techniques), its
    squeals' with their vibrato over it and their scoop under; BEND_RANGE at least."""
    voices = p.voices
    if voices is None or not voices.techniques:
        return {}
    most = max((up for n in p.part.arrangement.notes for _at, up in n.bends), default=0.0)
    out = {voice.channel: max(BEND_RANGE, math.ceil(most - 1e-6)) for voice in (voices.main, voices.mute) if voice}
    if voices.squeal is not None:
        shape = voices.squeal_shape
        out[voices.squeal.channel] = max(BEND_RANGE, math.ceil(max(most + shape.width, shape.scoop) - 1e-6))
    return out


def _track(p: PlannedPart) -> Track:
    """A planned part's track: its lights, and with techniques what sounds besides them (dead notes too: on the
    palm-mute tone, else quieter on the part's own); its tone changes."""
    lights: list[Light] = list(p.lights)
    extras: list[Light] = []
    if p.voices is not None and p.voices.techniques:
        lights, extras = techniques(strums(p.lights, p.part.arrangement.beats), p.part.arrangement.muted)
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
