from __future__ import annotations

import functools
import math
import operator
import struct
import zlib
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .chords import chord_name

# A Mac arrangement file (songs/bin/macos/<name>.sng): u32 0x4A, u32 flags, 16-byte IV, then
# AES-256-CTR (the well-known Mac key) over u32 plain length + a zlib stream. The plain data is a
# sequence of arrays (i32 count + records, little-endian) whose layout follows Rocksmith2014.NET by
# iminashi. Each difficulty level has its own notes; a phrase's hardest level is the full part.

SNG_KEY_MAC = bytes.fromhex("9821330E34B91F70D0A48CBD625993126970CEA09192C0E6CDA676CC9838289D")
SNG_MAGIC = 0x4A
MAX_PLAIN = 64 << 20

GUITAR = (40, 45, 50, 55, 59, 64)  # E2 A2 D3 G3 B3 E4
BASS = (28, 33, 38, 43)  # E1 A1 D2 G2

# Note mask bits
CHORD = 0x02
FRET_HAND_MUTE = 0x08
TREMOLO = 0x10
HARMONIC = 0x20
PALM_MUTE = 0x40
SLAP = 0x80
POP = 0x100
HAMMER_ON = 0x200
PULL_OFF = 0x400
SLIDE = 0x800
BEND = 0x1000
TAP = 0x4000
PINCH_HARMONIC = 0x8000
VIBRATO = 0x10000
IGNORE = 0x40000
UNPITCHED_SLIDE = 0x400000
ACCENT = 0x4000000
CHILD = 0x10000000  # linked to the note before it (a sustain that continues)
TECHNIQUES = (TREMOLO | HARMONIC | PALM_MUTE | SLAP | POP | HAMMER_ON | PULL_OFF | SLIDE | BEND | TAP | PINCH_HARMONIC
              | VIBRATO | UNPITCHED_SLIDE | ACCENT)  # (a chord's strings' own, added to its)
HARMONIC_PITCH = {3: 31, 4: 28, 5: 24, 7: 19, 9: 28, 12: 12, 16: 28, 19: 19, 24: 24}  # a natural harmonic at a fret:
# how far above the open string it sounds

NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


class SngError(ValueError):
    pass


@dataclass(frozen=True)
class Note:
    time: float
    sustain: float
    pitches: tuple[int, ...]  # MIDI note numbers, low to high
    mask: int = 0
    strings: tuple[int, ...] = ()  # each pitch's string (0: the lowest), as the pitches are
    slide: int = 0  # semitones it slides to (up or down) over its sustain
    slide_off: int = 0  # semitones it slides off to at its end (unpitched: a slide away)
    bends: tuple[tuple[float, float], ...] = ()  # its bend: when, and semitones up (from 0 at its start)
    vibrato: bool = False
    frets: tuple[int, ...] = ()  # each pitch's fret, as the pitches are (0: open; with a capo, the capo's)


@dataclass(frozen=True)
class Section:
    name: str  # as charted: intro, verse, chorus, riff, noguitar...
    start: float
    end: float


@dataclass(frozen=True)
class Chord:
    name: str  # the chart's, else made from its notes
    start: float  # the hand shape held for it (strummed, or picked as an arpeggio)
    end: float
    frets: tuple[int, ...] = ()  # its shape: each string's fret, low to high (-1: not played; with a capo, 0 is it)


@dataclass(frozen=True)
class Syllable:
    text: str  # as charted: ending in "-" when the word goes on, "+" at the end of a line
    start: float
    end: float


@dataclass(frozen=True)
class SongTone:
    name: str  # as the chart names it ("amonpurs_dist")
    gear: tuple[str, ...] = ()  # its amp, cabinet, pedals and racks (the game's keys for them)


@dataclass(frozen=True)
class Arrangement:
    notes: tuple[Note, ...]
    tuning: tuple[int, ...]  # semitones per string, low to high
    capo: int
    length: float
    beats: tuple[float, ...] = ()  # when each beat is
    sections: tuple[Section, ...] = ()
    chords: tuple[Chord, ...] = ()  # by when they start
    lyrics: tuple[Syllable, ...] = ()  # (a vocals part's)
    shapes: tuple[tuple[float, float], ...] = ()  # each hand shape held (chords and arpeggios), named or not: start, end
    tone_changes: tuple[tuple[float, int], ...] = ()  # when the chart switches tone, and to which (0 to 3: A to D)
    base_tone: SongTone | None = None  # the tone it starts with (from its manifest: ArrangementRef.load)
    tones: tuple[SongTone | None, ...] = ()  # its tones A to D (None: none)
    muted: tuple[Note, ...] = ()  # its fret-hand mutes (dead notes: no keys, a click)


def note_name(pitch: int) -> str:
    return f"{NAMES[pitch % 12]}{pitch // 12 - 1}"


def decrypt(raw: bytes) -> bytes:
    if len(raw) < 28 or struct.unpack_from("<I", raw)[0] != SNG_MAGIC:
        raise SngError("not a Mac .sng")
    decryptor = Cipher(algorithms.AES(SNG_KEY_MAC), modes.CTR(raw[8:24])).decryptor()
    plain = decryptor.update(raw[24:]) + decryptor.finalize()
    length = struct.unpack_from("<I", plain)[0]
    if length > MAX_PLAIN:
        raise SngError(f"implausible size {length}")
    try:
        data = zlib.decompressobj().decompress(plain[4:], length)
    except zlib.error as exc:
        raise SngError(f"bad zlib data: {exc}") from exc
    if len(data) != length:
        raise SngError(f"inflated {len(data)} bytes, expected {length}")
    return data


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, fmt: str) -> tuple:
        try:
            values = struct.unpack_from("<" + fmt, self.data, self.pos)
        except struct.error as exc:
            raise SngError(f"truncated at {self.pos}") from exc
        self.pos += struct.calcsize("<" + fmt)
        return values

    def count(self) -> int:
        n = self.take("i")[0]
        if not 0 <= n <= len(self.data):
            raise SngError(f"bad count {n} at {self.pos - 4}")
        return n

    def skip(self, record_size: int) -> int:
        n = self.count()
        self.pos += n * record_size
        return n


def parse(data: bytes, bass: bool = False) -> Arrangement:
    r = _Reader(data)
    beats = tuple(r.take("f2h2i")[0] for _ in range(r.count()))
    phrase_levels = [r.take("4b2i32s")[4] for _ in range(r.count())]  # max difficulty per phrase
    templates = [r.take("I6b6b6i32s") for _ in range(r.count())]
    chord_frets = [template[1:7] for template in templates]
    chord_names = [_chart_name(template[19]) for template in templates]
    chord_notes = []  # each chord's strings' own techniques: masks, bends, slides, slides off and vibrato
    for _ in range(r.count()):
        masks = r.take("6I")
        bent = []
        for _string in range(6):
            points = [r.take("ff4x") for _point in range(32)]
            bent.append(points[:max(0, min(r.take("i")[0], 32))])
        chord_notes.append((masks, bent, r.take("6b"), r.take("6b"), r.take("6h")))
    vocals = [r.take("fif48s") for _ in range(r.count())]  # (a vocals part's: when, a note, how long, the syllable)
    lyrics = tuple(Syllable(text.split(b"\0")[0].decode("utf-8", "replace"), at, at + length)
                   for at, _note, length, text in vocals)
    if vocals:  # their symbols
        r.skip(32)
        r.skip(144)
        r.skip(44)
    iterations = [r.take("iff3i")[:2] for _ in range(r.count())]  # phrase of each phrase iteration, and its start
    r.skip(16)  # phrase extra info
    for _ in range(r.count()):  # new linked difficulties
        r.take("i")
        r.skip(4)
    r.skip(260)  # actions
    r.skip(260)  # events
    tone_changes = tuple((at, slot) for at, slot in (r.take("fi") for _ in range(r.count())))
    r.skip(8)  # DNA
    sections = []
    for _ in range(r.count()):
        name, _number, start, end = r.take("32siff8x36x")  # (then its phrase iterations and string mask)
        sections.append(Section(name.split(b"\0")[0].decode("ascii", "replace"), start, end))

    levels: dict[int, list[tuple[Any, ...]]] = {}
    shapes: dict[int, list[tuple[int, float, float]]] = {}  # each level's hand shapes: chord, start, end
    for _ in range(r.count()):
        difficulty = r.take("i")[0]
        r.skip(28)  # anchors
        r.skip(12)  # anchor extensions
        shapes[difficulty] = [r.take("iff8x") for _ in range(r.count())]  # fingerprints: hand shapes
        shapes[difficulty] += [r.take("iff8x") for _ in range(r.count())]  # and arpeggios
        records = []
        for _ in range(r.count()):
            mask, _flags, _hash, time, string, fret = r.take("IIIfbb")
            _anchor_fret, _anchor_width, chord, chord_note, _phrase, iteration = r.take("bbiiii")
            *_linked, slide_to, slide_off_to, _hand, _tap, _pick, _slap, _pop, vibrato = r.take("2h3h7bh")
            sustain, _max_bend = r.take("ff")
            bends = [r.take("ff4x") for _ in range(r.count())]
            records.append((mask, time, string, fret, chord, iteration, sustain, chord_note, slide_to, slide_off_to,
                            vibrato, bends))
        r.skip(4)  # average notes per iteration
        r.skip(4)  # notes per iteration
        r.skip(4)
        levels[difficulty] = records

    meta = r.take("4d2fb32shf")
    capo, length = meta[6], meta[9]
    tuning = tuple(r.take("h")[0] for _ in range(r.count()))
    r.take("ffi")
    if r.pos != len(data):
        raise SngError(f"read {r.pos} of {len(data)} bytes")
    if not levels:
        return Arrangement((), tuning, capo, length, beats, tuple(sections), lyrics=lyrics, tone_changes=tone_changes)

    open_strings = BASS if bass else GUITAR
    offsets = tuning + (0,) * (len(open_strings) - len(tuning))

    def strung(fretted: list[tuple[int, int]], harmonics: set[int] = set()) -> list[tuple[int, int, int]]:
        """Each string's pitch, the string and its fret, low to high (harmonics: strings played as natural
        harmonics)."""
        return sorted((open_strings[s] + offsets[s] + (HARMONIC_PITCH.get(f, f) if s in harmonics else
                                                       capo if f == 0 and capo > 0 else f), s,
                       capo if f == 0 and capo > 0 and s not in harmonics else f)
                      for s, f in fretted if 0 <= s < len(open_strings) and f >= 0)

    def pitched(fretted: list[tuple[int, int]]) -> list[int]:
        return [pitch for pitch, _string, _fret in strung(fretted)]

    def chord_frets_of(chord: int) -> list[tuple[int, int]]:
        return [(s, f) for s, f in enumerate(chord_frets[chord]) if f >= 0]

    def played_as(record: tuple[Any, ...]) -> Note | None:
        """A note record as played: a chord's strings' techniques (chord notes) with its own."""
        mask, time, string, fret, chord, _iteration, sustain, chord_note, slide_to, slide_off_to, vibrato, bends = record
        if mask & CHORD and 0 <= chord < len(chord_frets):
            fretted = chord_frets_of(chord)
            own = chord_notes[chord_note] if 0 <= chord_note < len(chord_notes) else None
            masks = own[0] if own else (0,) * 6
            mask |= functools.reduce(operator.or_, (masks[s] for s, _f in fretted if s < 6), 0) & TECHNIQUES
            harmonics = {s for s, _f in fretted if s < 6 and (masks[s] | mask) & HARMONIC}
            slides = [own[2][s] - f for s, f in fretted if own and s < 6 and own[2][s] >= 0] if own else []
            offs = [own[3][s] - f for s, f in fretted if own and s < 6 and own[3][s] >= 0] if own else []
            bent = next((points for s, _f in fretted if own and s < 6 and (points := own[1][s])), [])
            vibrating = bool(mask & VIBRATO) or bool(own and any(own[4][s] > 0 for s, _f in fretted if s < 6))
        else:
            fretted = [(string, fret)]
            harmonics = {string} if mask & HARMONIC else set()
            slides = [slide_to - fret] if slide_to >= 0 else []
            offs = [slide_off_to - fret] if slide_off_to >= 0 else []
            bent, vibrating = bends, bool(mask & VIBRATO) or vibrato > 0
        played = strung(fretted, harmonics)
        if not played:
            return None
        return Note(time, sustain, tuple(p for p, _s, _f in played), mask, tuple(s for _p, s, _f in played),
                    slides[0] if slides else 0, offs[0] if offs else 0,
                    tuple((at, step) for at, step in bent if at >= time), vibrating,  # (steps: semitones)
                    tuple(f for _p, _s, f in played))

    hardest = max(levels)
    notes: list[Note] = []
    muted: list[Note] = []
    chords: list[Chord] = []
    held: list[tuple[float, float]] = []
    for index, (phrase, start) in enumerate(iterations):
        level = min(phrase_levels[phrase], hardest) if 0 <= phrase < len(phrase_levels) else hardest
        for record in levels.get(level, ()):
            if record[5] != index or record[0] & IGNORE:
                continue
            note = played_as(record)
            if note is not None:
                (muted if note.mask & FRET_HAND_MUTE else notes).append(note)
        until = iterations[index + 1][1] if index + 1 < len(iterations) else math.inf  # (the phrase's hand shapes)
        for chord, at, end in shapes.get(level, ()):
            if start <= at < until and 0 <= chord < len(templates):
                held.append((at, end))
                name = chord_names[chord] or chord_name(pitched(chord_frets_of(chord)))
                if name:
                    shape = tuple(capo if f == 0 and capo > 0 else f for f in chord_frets[chord][:len(open_strings)])
                    chords.append(Chord(name, at, end, shape))
    notes.sort(key=lambda n: n.time)
    muted.sort(key=lambda n: n.time)
    chords.sort(key=lambda c: c.start)
    return Arrangement(tuple(notes), tuning, capo, length, beats, tuple(sections), tuple(chords), lyrics,
                       tuple(sorted(held)), tone_changes, muted=tuple(muted))


def _chart_name(raw: bytes) -> str:
    """A chord's name as charted, in the screen's letters (dim, half-diminished and major-seventh signs spelt out)."""
    name = raw.split(b"\0")[0].decode("utf-8", "replace").strip()
    return name.replace("°", "dim").replace("ø", "m7b5").replace("Δ", "maj7")
