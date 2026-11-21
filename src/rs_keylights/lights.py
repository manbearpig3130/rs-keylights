from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from typing import Sequence

from .sng import ACCENT, CHILD, HAMMER_ON, HARMONIC, PALM_MUTE, PINCH_HARMONIC, TREMOLO, Note

# Turns an arrangement's notes into key lights for a keyboard with lit keys (the Casio LK-170:
# 61 keys, C2 to C7, and at most 4 lit at once). Each note or chord stays lit until the next one,
# short notes stay lit long enough to see, and repeats of the same key blink. Their sound can ring on
# after their light, as a guitar's strings do (rings).

MIN_LIGHT = 0.08  # seconds a short note stays lit
GAP = 0.03  # dark time before the same key lights again
RING_MAX = 4.0  # seconds a note rings at most (more, if the chart holds it longer)
DAMPING = 3  # strings: a chord of this many (or a palm mute) stops the open strings ringing under it
PALM_SHARE = 0.6  # of the time to the part's next note a palm mute sounds (a chug)...
PALM_LEAST, PALM_MOST = 0.05, 0.2  # ...but at least and at most this (seconds)
# Techniques (techniques): the notes besides a part's own sounding them
GLIDE_LEAST = 0.12  # seconds a slide takes at least (its steps a semitone each, over the note's sustain)
SLIDE_OFF = 0.15  # seconds a slide off takes at most, at the note's end...
SLIDE_OFF_STEPS = 6  # ...in this many steps at most (spread over its semitones: a smear, not a run)...
SLIDE_OFF_FADE = 0.35  # ...fading to this much of the note's loudness, as the hand comes off
TREMOLO_EVERY = 0.075  # seconds between a tremolo-picked note's strikes
DEAD = 0.05  # seconds a dead note (fret-hand muted) sounds
# Strums (strums): a chord's strings one after another, down (low to high) on the beat and its eighths, up (high to
# low, softer) on the sixteenths between, as the charts don't say which (their pick direction isn't set)
STRUM_STRING = 0.007  # seconds from a string to the next, strummed down
STRUM_UP_STRING = 0.005  # and up
STRUM_SHARE = 0.25  # of the time to the part's next note, at most, a strum takes (fast strumming: tighter)


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
    strum: tuple[float, ...] = ()  # seconds after on each pitch sounds, strummed (as the pitches are; none: at once)
    level: float = 1.0  # how loud, of how it's played (a slide off's steps fading)
    up: bool = False  # strummed (or picked) up: softer
    slide_at: float | None = None  # when its slide starts: a linked note's start, its slide its (None: on)


def together(notes: Sequence[Note]) -> list[Note]:
    """Notes that start at the same time as one: a held chord can go on as one linked note per string."""
    out: list[Note] = []
    for note in notes:
        if out and note.time == out[-1].time:
            last = out[-1]
            child = last.mask & note.mask & CHILD  # a continuation only if every one of them is
            mask = (last.mask | note.mask) & ~CHILD | child
            played = sorted({p: (s, f) for p, s, f in [*_strung(last), *_strung(note)]}.items())
            known = all(f >= 0 for _p, (_s, f) in played)
            out[-1] = Note(last.time, max(last.sustain, note.sustain), tuple(p for p, _sf in played), mask,
                           tuple(s for _p, (s, _f) in played), last.slide or note.slide,
                           last.slide_off or note.slide_off, last.bends or note.bends, last.vibrato or note.vibrato,
                           tuple(f for _p, (_s, f) in played) if known else (), last.up or note.up)
        else:
            out.append(note)
    return out


def _strung(note: Note) -> list[tuple[int, int, int]]:
    """A note's pitches with their strings and frets (-1: not known)."""
    n = len(note.pitches)
    return list(zip(note.pitches, note.strings or (-1,) * n, note.frets if len(note.frets) == n else (-1,) * n))


def rings(notes: Sequence[Note], shapes: Sequence[tuple[float, float]] = (), overlap: float = 0.0,
          anchors: Sequence[tuple[float, int]] = (), capo: int = 0,
          arpeggios: Sequence[tuple[float, float]] | None = None,
          muted: Sequence[Note] = ()) -> list[tuple[float, ...]]:
    """When each note's pitches stop sounding, letting them ring as a guitar's strings do: until the string is
    played again (and overlap after, smoothing it: but a pitch played again stops as it is); held in a hand
    shape (shapes: start, end), until it ends, or the fretting hand moves first (anchors: from when, its fret): any
    note in an arpeggio (arpeggios, of the shapes; None: all of them), and in a chord's shape a chord, and its single
    notes unless palm mutes are played in it too (a riff, the shape its fingering: they're as outside it);
    else a fretted note until the part's next note (overlap after), an open string (or a harmonic) on under the
    notes after it, until a chord (DAMPING strings) or a palm mute stops it. RING_MAX at most, the note's own
    sustain at least (unless it's played again first). Palm mutes don't ring: a chug, PALM_SHARE of the time to the
    next note (PALM_LEAST to PALM_MOST), or its sustain if the chart holds it (until the next note). Dead notes
    (muted: fret-hand mutes) stop their strings, a dead chord (DAMPING strings) the open strings too, and count as
    the next note for a fretted one (the fretting hand off it)."""
    starts = [start for start, _end in shapes]
    dead = sorted(n.time for n in muted)
    dead_chords = sorted(n.time for n in muted if len(n.pitches) >= DAMPING)
    dead_on: dict[int, list[float]] = {}
    for n in muted:
        for _pitch, string, _fret in _strung(n):
            if string >= 0:
                dead_on.setdefault(string, []).append(n.time)
    for times in dead_on.values():
        times.sort()

    def after(times: Sequence[float], t: float) -> float:
        i = bisect_right(times, t + 1e-6)
        return times[i] if i < len(times) else math.inf

    let_ring = set(shapes) if arpeggios is None else set(arpeggios)
    muted_at = [note.time for note in notes if note.mask & PALM_MUTE]
    riffs = {shape for shape in shapes if shape not in let_ring
             and bisect_left(muted_at, shape[0] - 1e-3) < bisect_left(muted_at, shape[1])}
    moves = [at for (at, fret), (_was, before) in zip(anchors[1:], anchors) if fret != before]  # (the hand moving)
    out: list[tuple[float, ...]] = []
    next_string: dict[int, float] = {}
    next_pitch: dict[int, float] = {}
    following = math.inf  # the part's next note
    damped = math.inf  # the part's next chord or palm mute
    for note in reversed(notes):
        next_sound = min(following, after(dead, note.time))  # (the part's next note, or dead note)
        if note.mask & PALM_MUTE:
            gap = next_sound - note.time
            chug = note.time + min(max(gap * PALM_SHARE, PALM_LEAST), PALM_MOST)
            held_to = min(note.time + note.sustain, next_sound)
            out.append((max(chug, held_to),) * len(note.pitches))
        else:
            k = bisect_right(starts, note.time) - 1
            held = shapes[k][1] if k >= 0 and note.time < shapes[k][1] else None
            if held is not None and len(note.pitches) < 2 and shapes[k] in riffs:
                held = None  # (a note picked in a riff's fingering)
            m = bisect_right(moves, note.time + 1e-3)
            moved = moves[m] + overlap if m < len(moves) else math.inf
            least = note.time + max(note.sustain, MIN_LIGHT)
            ends = []
            for pitch, string, fret in _strung(note):
                pitched = next_pitch.get(pitch, math.inf)
                again = min(next_string.get(string, math.inf) + overlap if string >= 0 else math.inf, pitched,
                            after(dead_on.get(string, ()), note.time))
                open_string = 0 <= fret <= max(capo, 0) or bool(note.mask & HARMONIC)  # (no capo: -1)
                if held is not None:
                    natural = min(held if open_string else min(held, moved), again)
                elif open_string and string >= 0:
                    natural = min(min(damped, after(dead_chords, note.time)) + overlap, again)
                else:
                    natural = min(next_sound + overlap, again)
                ends.append(max(min(least, again), min(note.time + RING_MAX, natural)))
            out.append(tuple(ends))
        for pitch, string, _fret in _strung(note):
            next_pitch[pitch] = note.time
            if string >= 0:
                next_string[string] = note.time
        following = note.time
        if note.mask & PALM_MUTE or len(note.pitches) >= DAMPING:
            damped = note.time
    out.reverse()
    return out


def schedule(notes: Sequence[Note], keys: KeyRange = KeyRange(), shift: int | None = None,
             shapes: Sequence[tuple[float, float]] = (), overlap: float = 0.0,
             anchors: Sequence[tuple[float, int]] = (), capo: int = 0,
             arpeggios: Sequence[tuple[float, float]] | None = None, muted: Sequence[Note] = ()) -> list[Light]:
    """Each note's light (and how long its pitches ring: rings, with the arrangement's hand shapes and positions, its
    capo and the overlap after the next note; arpeggios: those of the shapes that are, None: all; muted: its dead
    notes)."""
    if shift is None:
        shift = keys.octave_shift(notes)
    lights: list[Light] = []
    grouped = together(notes)
    for note, ends in zip(grouped, rings(grouped, shapes, overlap, anchors, capo, arpeggios, muted)):
        lit = tuple(sorted({keys.fit(p + shift) for p in note.pitches}))[: keys.max_lit]
        end = note.time + max(note.sustain, MIN_LIGHT)
        if lights and note.mask & CHILD and lights[-1].keys == lit:
            # A linked note continues the one before it: keep the keys lit, and its sound ringing as long; and what
            # it does (often the reason it's charted: held, then slid off or along, bent or shaken)
            last = lights[-1]
            longer = tuple(max(a, b) for a, b in zip(last.rings, ends)) if len(last.rings) == len(ends) else last.rings
            lights[-1] = replace(last, off=max(last.off, end), rings=longer, slide=note.slide or last.slide,
                                 slide_at=note.time if note.slide else last.slide_at,
                                 slide_off=note.slide_off or last.slide_off, bends=last.bends + note.bends,
                                 vibrato=last.vibrato or note.vibrato)
            continue
        if lights:
            last = lights[-1]
            until = note.time - (GAP if set(last.keys) & set(lit) else 0.0)
            # At least briefly lit, but never past the next note's start
            off = max(min(last.off, until), min(last.on + 0.02, note.time))
            lights[-1] = replace(last, off=off)
        lights.append(Light(note.time, end, lit, note.pitches, note.mask, ends, note.slide, note.slide_off, note.bends,
                            note.vibrato, up=note.up))
    return lights


def techniques(lights: Sequence[Light], muted: Sequence[Note] = ()) -> tuple[list[Light], list[Light]]:
    """A part's sound with its techniques: its lights (those sliding or tremolo-picked stopping as their first step
    or strike sounds), and what sounds besides them (extra, no keys): a slide's steps, a semitone at a time over its
    sustain (the last left to the note it slides into, if it's linked to it), the last ringing on; a slide off's,
    quicker, at its sustain's end, fading (SLIDE_OFF_STEPS at most), the note ending with it (both by the charted end,
    the light's, not how long it would ring: a song's last note rings 4 s, and its slide off came then);
    a tremolo's strikes; a pinch harmonic's squeal (sounding with its note: Voices puts it above); and dead notes
    (muted: palm-muted, for the palm-mute tone if there is one). Bends and vibrato are the
    player's, as controls."""
    out: list[Light] = []
    extras: list[Light] = []
    slid_into = {i + 1 for i, light in enumerate(lights[:-1]) if light.slide and lights[i + 1].mask & CHILD and
                 lights[i + 1].pitches == tuple(p + light.slide for p in light.pitches)}  # (a slide's own end: legato,
    # as a finger sliding into it, not picked again; a fret's slide has no step between, just that)
    for i, light in enumerate(lights):
        if i in slid_into:
            light = replace(light, mask=light.mask | HAMMER_ON)
        ends = light.rings or (light.off,) * len(light.pitches)
        end = max(ends, default=light.off)
        held = min(end, light.off)  # (the sustain's end: where a slide gets to, and a slide off ends)
        times: list[float] = []
        offsets: list[int] = []
        levels: list[float] = []
        if light.slide:
            linked = i + 1 < len(lights) and bool(lights[i + 1].mask & CHILD) and \
                lights[i + 1].pitches == tuple(p + light.slide for p in light.pitches)
            start = light.on if light.slide_at is None else light.slide_at  # (a linked note's: from its start)
            span, steps = max(held - start, GLIDE_LEAST), abs(light.slide)
            times = [start + span * k / (steps + 1) for k in range(1, steps + 1 - linked)]
            offsets = [k * (1 if light.slide > 0 else -1) for k in range(1, len(times) + 1)]
        elif light.slide_off:
            semitones, steps = abs(light.slide_off), min(abs(light.slide_off), SLIDE_OFF_STEPS)
            begin = max(held - min(SLIDE_OFF, (held - light.on) / 2), light.on)
            times = [begin + (held - begin) * k / steps for k in range(steps)]
            offsets = [round(semitones * k / steps) * (1 if light.slide_off > 0 else -1) for k in range(1, steps + 1)]
            levels = [1.0 - (1.0 - SLIDE_OFF_FADE) * k / steps for k in range(1, steps + 1)]
        elif light.mask & TREMOLO:
            count = max(int((end - light.on) / TREMOLO_EVERY) - 1, 0)
            times = [light.on + TREMOLO_EVERY * k for k in range(1, count + 1)]
            offsets = [0] * count
        times = [at for at in times if light.on < at < end]
        if light.mask & PINCH_HARMONIC and not light.extra:
            extras.append(replace(light, keys=(), rings=tuple(min(ring, times[0]) for ring in ends) if times else ends,
                                  strum=(), extra=True))
        if not times:
            out.append(light)
            continue
        out.append(replace(light, rings=tuple(min(ring, times[0]) for ring in ends)))
        for k, (at, offset) in enumerate(zip(times, offsets)):
            last = held if light.slide_off else end  # (slid off: it ends there; slid: the new note rings on)
            until = times[k + 1] if k + 1 < len(times) else max(last, at + MIN_LIGHT)
            mask = light.mask & ~(ACCENT | PINCH_HARMONIC) | (HAMMER_ON if offset else 0)  # (a slide's steps: legato)
            extras.append(Light(at, at, (), tuple(p + offset for p in light.pitches), mask,
                                (until,) * len(light.pitches), extra=True, level=levels[k] if levels else 1.0))
    for note in muted:
        extras.append(Light(note.time, note.time, (), note.pitches, note.mask | PALM_MUTE,
                            (note.time + DEAD,) * len(note.pitches), extra=True))
    extras.sort(key=lambda light: light.on)
    return out, extras


def strums(lights: Sequence[Light], beats: Sequence[float]) -> list[Light]:
    """A part's chords strummed (2 strings or more, not a technique's extra sound): their strings one after another,
    down (low to high) or up (high to low): up as charted, else by where it falls in its beat (beats: when each is),
    up on a sixteenth between the beat and its eighths. A strum takes STRUM_SHARE of the time to the next note at
    most; each string rings until its own end, after it sounds."""
    ons = sorted({light.on for light in lights})
    out: list[Light] = []
    for light in lights:
        n = len(light.pitches)
        if n < 2 or light.extra:
            out.append(light)
            continue
        k = bisect_right(beats, light.on + 1e-3) - 1
        up = light.up
        if not up and 0 <= k < len(beats) - 1 and beats[k + 1] > beats[k]:
            up = round((light.on - beats[k]) / (beats[k + 1] - beats[k]) * 4) % 2 == 1
        nxt = ons[bisect_right(ons, light.on)] if bisect_right(ons, light.on) < len(ons) else math.inf
        step = min(STRUM_UP_STRING if up else STRUM_STRING, (nxt - light.on) * STRUM_SHARE / (n - 1))
        order = range(n - 1, -1, -1) if up else range(n)
        delays = [0.0] * n
        for place, string in enumerate(order):
            delays[string] = place * step
        rings = tuple(max(ring, light.on + d + MIN_ON) for ring, d in zip(light.rings, delays)) if light.rings else ()
        out.append(replace(light, strum=tuple(delays), up=up, rings=rings))
    return out


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
               min_period: float = MIN_PERIOD, most: int | None = None,
               priorities: Sequence[int] = ()) -> list[KeyLight]:
    """One timeline per key from the lights of all the lit parts: what the keys show; at most most keys at once
    (None: as the parts' own lights have it), the ones ranked first kept (most_at_once: the parts' priorities, 0
    first, as they're given; else their order).

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
    ranks: list[tuple[int, int]] = []  # (each one's: its parts' best priority, its key)
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
        for i, (on, off, struck_by) in enumerate(merged):
            if i + 1 < len(merged):
                following = merged[i + 1]
                if following[0] < off:  # struck again while lit: lit on through the longer note
                    following[1] = max(following[1], off)
                dark = min(max(DARK * (following[0] - on), dark_min), dark_max)
                off = min(max(min(off, following[0] - dark), on + min_on), following[0])
            out.append(KeyLight(on - early, off - early, key))
            ranks.append((min(priorities[p] if p < len(priorities) else p for p in struck_by), key))
    if most is not None:
        out = most_at_once(out, ranks, most)
    out.sort(key=lambda k: (k.on, k.key))
    return out


def most_at_once(lights: Sequence[KeyLight], ranks: Sequence[tuple[int, int]], most: int) -> list[KeyLight]:
    """At most most keys lit at once: a key lit while that many are takes the place of the one lit ranked last (a
    rank: its part's priority, then the key: the lower first) if it's ranked before it, cutting that one short;
    else it isn't lit."""
    kept: list[KeyLight | None] = []
    lit: list[tuple[int, tuple[int, int]]] = []  # (where it's kept, its rank)
    for light, rank in sorted(zip(lights, ranks), key=lambda pair: (pair[0].on, pair[1])):
        lit = [(i, r) for i, r in lit if (k := kept[i]) is not None and k.off > light.on]
        if len(lit) >= most:
            last = max(lit, key=lambda entry: entry[1])
            if last[1] <= rank:
                continue
            lit.remove(last)
            cut = kept[last[0]]
            assert cut is not None
            kept[last[0]] = replace(cut, off=light.on) if light.on > cut.on else None
        kept.append(light)
        lit.append((len(kept) - 1, rank))
    return [light for light in kept if light is not None]
