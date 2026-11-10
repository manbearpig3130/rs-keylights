from __future__ import annotations

import math
import re
import struct
from bisect import bisect_right
from dataclasses import dataclass
from typing import Sequence

from .drums import Hit
from .sng import ACCENT, PALM_MUTE, Arrangement, Syllable

# A song as a Standard MIDI File (type 1), for a DAW (Logic, GarageBand): its tempo map made from the chart's beats,
# so its bars are the record's (a tempo a beat: the file's time is the song's, its audio laid under it lines up), its
# time signatures from where the bars start, its key, and its sections as markers; a track a part (its notes at their
# hardest, at the pitch they sound: bends, slides it can and vibrato too; its tones and chords named in text events),
# the vocals (their lyrics, and their melody when the chart has one) and a drum part if one was made.

PPQ = 480  # ticks a beat
STEADY = 120.0  # beats a minute, for a chart without beats
LEAD_IN_MOST = 64  # beats the time before the chart's first beat is made into, at most
BEND_RANGE = 2  # semitones a full pitch bend is, for a part that bends no further (MIDI's usual: an instrument that
# ignores the range sent needs its own set to the part's: it's in its track's name when it isn't this)
BEND_STEP = 0.015  # seconds between pitch bends as a bend or slide moves
SHORTEST = PPQ // 4  # ticks a picked note (no sustain) lasts: a 16th, unless the next on its key comes sooner
DEAD = PPQ // 8  # ticks a dead note (fret-hand mute) lasts
VELOCITY, PALM_MUTED, ACCENTED, DEAD_VELOCITY = 96, 64, 120, 36
VIBRATO_DEPTH = 64  # the mod wheel, while a note with vibrato rings
PROGRAMS = {"lead": 29, "rhythm": 30, "bass": 33, "vocals": 53}  # General MIDI: overdriven and distortion guitar,
# electric bass (finger), voice oohs
LOWEST_SUNG = 36  # C2: a syllable charted lower has no pitch (some charts mark spoken ones C1)
MELODY_PITCHES = 4  # a vocals part's syllables on fewer pitches than this have none (a chart's placeholder)
DRUM_CHANNEL = 9  # (General MIDI's channel 10)
MODE_STEPS = {"major": 0, "minor": 9, "Dorian": 2, "Phrygian": 4, "Lydian": 5, "Mixolydian": 7}  # degree of its
# major scale a mode starts on (its key signature: that major's)


@dataclass(frozen=True)
class ExportPart:
    name: str  # its track's name ("Lead", "Rhythm", "Bass"...)
    kind: str  # lead, rhythm or bass: its instrument
    arrangement: Arrangement


class Grid:
    """The song's time in beats: each beat's time (the chart's, after a lead-in made up of the time before its first,
    so the file starts when the song does) and the beats bars start on."""

    def __init__(self, beats: Sequence[float], bars: Sequence[float], length: float):
        chart = []
        for t in beats:
            if t >= 0 and (not chart or t > chart[-1] + 1e-4):
                chart.append(t)
        if len(chart) < 2:  # (none: a steady tempo, bars of 4)
            step = 60.0 / STEADY
            chart = [i * step for i in range(max(2, math.ceil(max(length, 1.0) / step) + 1))]
            bars = chart[::4]
        lead = []
        if chart[0] > 1e-3:
            count = min(max(1, round(chart[0] / (chart[1] - chart[0]))), LEAD_IN_MOST)
            lead = [chart[0] * k / count for k in range(count)]
        self.times = lead + chart
        bar_times = set(bars)
        starts = [len(lead) + i for i, t in enumerate(chart) if t in bar_times] or \
            list(range(len(lead), len(self.times), 4))
        first = starts[0]
        size = (starts[1] - first) if len(starts) > 1 else 4
        opening = list(range(first % size, first, size))  # (before the first bar: bars of its size, a short one first)
        self.bar_starts = ([0] if first % size and first else []) + opening + starts

    def tick(self, t: float) -> int:
        times = self.times
        i = bisect_right(times, t) - 1
        if i < 0:
            return 0
        if i >= len(times) - 1:
            i = len(times) - 2
        return round((i + (t - times[i]) / (times[i + 1] - times[i])) * PPQ)

    def tempos(self) -> list[tuple[int, int]]:
        """Each beat's tempo (microseconds a beat) where it changes: tick, tempo."""
        out: list[tuple[int, int]] = []
        for i in range(len(self.times) - 1):
            tempo = round((self.times[i + 1] - self.times[i]) * 1_000_000)
            if not out or out[-1][1] != tempo:
                out.append((i * PPQ, tempo))
        return out

    def signatures(self) -> list[tuple[int, int]]:
        """Each bar's beats where they change: tick, beats."""
        out: list[tuple[int, int]] = []
        starts = self.bar_starts
        for k, start in enumerate(starts):
            beats = (starts[k + 1] - start) if k + 1 < len(starts) else (out[-1][1] if out else 4)
            beats = min(max(beats, 1), 32)
            if not out or out[-1][1] != beats:
                out.append((start * PPQ, beats))
        return out


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def _text(kind: int, text: str) -> bytes:
    return _meta(kind, text.encode("utf-8"))


OFF, META, ON = 0, 1, 2  # (at the same tick: notes ending, then the rest, then notes starting)


class _Track:
    def __init__(self, name: str):
        self.events: list[tuple[int, int, int, bytes]] = []  # tick, order, how many before, the event
        self.add(0, META, _text(0x03, name))

    def add(self, tick: int, order: int, event: bytes) -> None:
        self.events.append((max(tick, 0), order, len(self.events), event))

    def chunk(self) -> bytes:
        body, now = bytearray(), 0
        for tick, _order, _n, event in sorted(self.events):
            body += _vlq(tick - now) + event
            now = tick
        body += b"\x00" + _meta(0x2F, b"")
        return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def _notes(track: _Track, channel: int, notes: list[tuple[int, int, int, int]]) -> None:
    """Notes (start, end, key, velocity) into a track, each ended before the next on its key starts."""
    by_key: dict[int, list[tuple[int, int, int]]] = {}
    for start, end, key, velocity in notes:
        if 0 <= key <= 127:
            by_key.setdefault(key, []).append((start, end, velocity))
    for key, played in by_key.items():
        played.sort()
        for i, (start, end, velocity) in enumerate(played):
            if i + 1 < len(played) and played[i + 1][0] < end:
                end = played[i + 1][0]
            if i > 0 and played[i - 1][0] == start:  # (the same key struck twice at once: once)
                continue
            end = max(end, start + 1)
            track.add(start, ON, bytes([0x90 | channel, key, max(1, min(velocity, 127))]))
            track.add(end, OFF, bytes([0x80 | channel, key, 0]))


def _bend_value(semitones: float, bend_range: int) -> int:
    return max(0, min(16383, round(8192 + semitones / bend_range * 8192)))


def _part(part: ExportPart, channel: int, grid: Grid) -> _Track:
    a = part.arrangement
    most = max((up for n in a.notes for _at, up in n.bends), default=0.0)
    bend_range = max(BEND_RANGE, math.ceil(most - 1e-6))
    track = _Track(part.name if bend_range == BEND_RANGE else f"{part.name} (bend range {bend_range})")
    program = PROGRAMS.get(part.kind, PROGRAMS["lead"])
    track.add(0, META, bytes([0xC0 | channel, program]))
    for cc, value in ((101, 0), (100, 0), (6, bend_range), (38, 0), (101, 127), (100, 127)):  # (its bend range)
        track.add(0, META, bytes([0xB0 | channel, cc, value]))

    def bend(at: float, semitones: float) -> None:
        track.add(grid.tick(at), META, bytes([0xE0 | channel]) + struct.pack(
            "<BB", (v := _bend_value(semitones, bend_range)) & 0x7F, v >> 7))

    def ramp(points: list[tuple[float, float]]) -> None:
        """A bend's or slide's points, with the pitch moved between them in steps."""
        for (t0, s0), (t1, s1) in zip(points, points[1:]):
            steps = max(1, int((t1 - t0) / BEND_STEP))
            for k in range(steps):
                bend(t0 + (t1 - t0) * k / steps, s0 + (s1 - s0) * k / steps)
        bend(*points[-1])

    notes: list[tuple[int, int, int, int]] = []
    for n in a.notes:
        start = grid.tick(n.time)
        end = grid.tick(n.time + n.sustain) if n.sustain > 0.01 else start + SHORTEST
        velocity = ACCENTED if n.mask & ACCENT else PALM_MUTED if n.mask & PALM_MUTE else VELOCITY
        notes += [(start, end, pitch, velocity) for pitch in n.pitches]
        moved = False
        if n.bends and n.sustain > 0.01:
            points = [(at, up) for at, up in n.bends if at <= n.time + n.sustain]
            if points:
                ramp(([(n.time, 0.0)] if points[0][0] > n.time + 1e-3 else []) + points)
                moved = True
        elif n.slide and abs(n.slide) <= bend_range and n.sustain > 0.01:
            ramp([(n.time, 0.0), (n.time + n.sustain, float(n.slide))])
            moved = True
        if moved:  # (back to its pitch as it ends)
            track.add(end, OFF, bytes([0xE0 | channel, 0x00, 0x40]))
        if n.vibrato:
            track.add(start, META, bytes([0xB0 | channel, 1, VIBRATO_DEPTH]))
            track.add(end, OFF, bytes([0xB0 | channel, 1, 0]))
    for n in a.muted:  # (dead notes: short and quiet, for the riff's rhythm)
        start = grid.tick(n.time)
        notes += [(start, start + DEAD, pitch, DEAD_VELOCITY) for pitch in n.pitches]
    _notes(track, channel, notes)

    tones = [(0.0, a.base_tone.name if a.base_tone else None)]
    for at, slot in a.tone_changes:
        changed = a.tones[slot] if 0 <= slot < len(a.tones) else None
        tones.append((at, changed.name if changed else None))
    for at, name in tones:
        if name:
            track.add(grid.tick(at), META, _text(0x01, f"Tone: {name}"))
    last = None
    for chord in a.chords:
        if last is None or chord.name != last.name or chord.start - last.end > 0.05:
            track.add(grid.tick(chord.start), META, _text(0x01, chord.name))
        last = chord
    return track


def _lyric(syllable: Syllable) -> str:
    """A syllable as a lyric event has it: a space after a word ends, a line's end a carriage return."""
    text = syllable.text
    if text.endswith("+"):
        return text[:-1].removesuffix("-") + "\r"
    if text.endswith("-"):
        return text[:-1]
    return text + " "


def _vocals(lyrics: Sequence[Syllable], channel: int, grid: Grid) -> _Track:
    track = _Track("Vocals")
    sung = [s for s in lyrics if s.note >= LOWEST_SUNG]  # (lower: unpitched, as some charts mark them)
    melody = len({s.note for s in sung}) >= MELODY_PITCHES
    if melody:
        track.add(0, META, bytes([0xC0 | channel, PROGRAMS["vocals"]]))
        _notes(track, channel, [(grid.tick(s.start), grid.tick(s.end), s.note, VELOCITY) for s in sung])
    for s in lyrics:
        track.add(grid.tick(s.start), META, _text(0x05, _lyric(s)))
    return track


def _drums(hits: Sequence[Hit], grid: Grid) -> _Track:
    track = _Track("Drums")
    _notes(track, DRUM_CHANNEL, [(t := grid.tick(h.time), t + DEAD, h.note, h.velocity) for h in hits])
    return track


def key_signature(tonic: int, mode: str) -> bytes:
    """A key signature event's data: sharps (or flats, negative) and major (0) or minor (1); a mode, its major's."""
    major = (tonic - MODE_STEPS.get(mode, 0)) % 12
    sharps = major * 7 % 12
    if sharps > 6:
        sharps -= 12
    return struct.pack("bB", sharps, 1 if mode == "minor" else 0)


def song_midi(title: str, parts: Sequence[ExportPart], *, markers: Sequence[tuple[float, str]] = (),
              lyrics: Sequence[Syllable] = (), drums: Sequence[Hit] = (),
              key: tuple[int, str] | None = None) -> bytes:
    """A song's MIDI file: its tempo map (from the part with the most beats), its parts, vocals and drums."""
    timed = max((p.arrangement for p in parts), key=lambda a: len(a.beats), default=None)
    length = max([p.arrangement.length for p in parts] + [s.end for s in lyrics[-1:]] + [h.time for h in drums[-1:]]
                 + [0.0])
    grid = Grid(timed.beats if timed else (), timed.bars if timed else (), length)
    conductor = _Track(title)
    for tick, tempo in grid.tempos():
        conductor.add(tick, META, _meta(0x51, tempo.to_bytes(3, "big")))
    for tick, beats in grid.signatures():
        conductor.add(tick, META, _meta(0x58, bytes([beats, 2, 24, 8])))
    if key is not None:
        conductor.add(0, META, _meta(0x59, key_signature(*key)))
    for at, name in markers:
        conductor.add(grid.tick(at), META, _text(0x06, name))
    tracks = [conductor]
    channels = [c for c in range(16) if c != DRUM_CHANNEL]
    for i, part in enumerate(parts):
        tracks.append(_part(part, channels[i % len(channels)], grid))
    if lyrics:
        tracks.append(_vocals(lyrics, channels[len(parts) % len(channels)], grid))
    if drums:
        tracks.append(_drums(drums, grid))
    head = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), PPQ)
    return head + b"".join(t.chunk() for t in tracks)


def file_name(title: str, suffix: str) -> str:
    """A file's name for a song: its title, without what a file name can't have."""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', " ", title).strip(" .") or "song"
    return re.sub(r"\s+", " ", name)[:120] + suffix
