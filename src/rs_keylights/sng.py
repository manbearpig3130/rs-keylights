from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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
IGNORE = 0x40000
CHILD = 0x10000000  # linked to the note before it (a sustain that continues)

NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


class SngError(ValueError):
    pass


@dataclass(frozen=True)
class Note:
    time: float
    sustain: float
    pitches: tuple[int, ...]  # MIDI note numbers, low to high
    mask: int = 0


@dataclass(frozen=True)
class Arrangement:
    notes: tuple[Note, ...]
    tuning: tuple[int, ...]  # semitones per string, low to high
    capo: int
    length: float


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
    r.skip(16)  # beats
    phrase_levels = [r.take("4b2i32s")[4] for _ in range(r.count())]  # max difficulty per phrase
    chord_frets = [r.take("I6b6b6i32s")[1:7] for _ in range(r.count())]
    r.skip(2376)  # chord notes (techniques)
    if r.skip(60):  # vocals, and their symbols when there are any
        r.skip(32)
        r.skip(144)
        r.skip(44)
    iterations = [r.take("iff3i")[0] for _ in range(r.count())]  # phrase of each phrase iteration
    r.skip(16)  # phrase extra info
    for _ in range(r.count()):  # new linked difficulties
        r.take("i")
        r.skip(4)
    r.skip(260)  # actions
    r.skip(260)  # events
    r.skip(8)  # tones
    r.skip(8)  # DNA
    r.skip(88)  # sections

    levels: dict[int, list[tuple[int, float, int, int, int, int, float]]] = {}
    for _ in range(r.count()):
        difficulty = r.take("i")[0]
        r.skip(28)  # anchors
        r.skip(12)  # anchor extensions
        r.skip(20)  # fingerprints (handshapes)
        r.skip(20)  # fingerprints (arpeggios)
        records = []
        for _ in range(r.count()):
            mask, _flags, _hash, time, string, fret = r.take("IIIfbb")
            _anchor_fret, _anchor_width, chord, _chord_notes, _phrase, iteration = r.take("bbiiii")
            r.take("2h3h7bh")  # fingerprints, linked notes, slides, techniques, vibrato
            sustain, _max_bend = r.take("ff")
            bends = r.count()
            r.pos += bends * 12
            records.append((mask, time, string, fret, chord, iteration, sustain))
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
        return Arrangement((), tuning, capo, length)

    open_strings = BASS if bass else GUITAR
    offsets = tuning + (0,) * (len(open_strings) - len(tuning))
    hardest = max(levels)
    notes: list[Note] = []
    for index, phrase in enumerate(iterations):
        level = min(phrase_levels[phrase], hardest) if 0 <= phrase < len(phrase_levels) else hardest
        for mask, time, string, fret, chord, iteration, sustain in levels.get(level, ()):
            if iteration != index or mask & (FRET_HAND_MUTE | IGNORE):
                continue
            if mask & CHORD and 0 <= chord < len(chord_frets):
                fretted = [(s, f) for s, f in enumerate(chord_frets[chord]) if f >= 0]
            else:
                fretted = [(string, fret)]
            pitches = sorted(
                open_strings[s] + offsets[s] + (capo if f == 0 and capo > 0 else f)
                for s, f in fretted
                if 0 <= s < len(open_strings) and f >= 0
            )
            if pitches:
                notes.append(Note(time, sustain, tuple(pitches), mask))
    notes.sort(key=lambda n: n.time)
    return Arrangement(tuple(notes), tuning, capo, length)
