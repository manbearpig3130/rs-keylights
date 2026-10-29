"""Small .sng and .psarc files for the tests, in the game's formats (no game files needed)."""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from rs_keylights.psarc import CFB, TOC_IV, TOC_KEY
from rs_keylights.sng import SNG_KEY_MAC


@dataclass
class N:
    """A note record: one string and fret, or a chord (chord >= 0)."""

    time: float
    string: int = 0
    fret: int = 0
    sustain: float = 0.0
    mask: int = 0
    chord: int = -1
    iteration: int = 0
    bends: int = 0


@dataclass
class Song:
    levels: dict[int, list[N]] = field(default_factory=dict)
    phrase_levels: list[int] = field(default_factory=lambda: [0])  # max difficulty per phrase
    iterations: list[int] = field(default_factory=lambda: [0])  # phrase of each iteration
    chords: list[tuple[int, ...]] = field(default_factory=list)  # six frets, -1 for unplayed
    tuning: tuple[int, ...] = (0, 0, 0, 0, 0, 0)
    capo: int = -1
    length: float = 60.0
    vocals: int = 0


def array(records: list[bytes]) -> bytes:
    return struct.pack("<i", len(records)) + b"".join(records)


def note_record(n: N) -> bytes:
    return (
        struct.pack("<IIIfbbbbiiii", n.mask, 0, 0, n.time, n.string, n.fret, 0, 4, n.chord, -1, 0, n.iteration)
        + struct.pack("<2h3h7bh", -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, 0, -1, 0)
        + struct.pack("<ffi", n.sustain, 0.0, n.bends)
        + b"\x00" * (12 * n.bends)
    )


def sng_plain(song: Song) -> bytes:
    out = array([struct.pack("<fhhii", 0.0, 0, 0, 0, 0)])  # beats
    out += array([struct.pack("<4b2i32s", 0, 0, 0, 0, level, 1, b"p") for level in song.phrase_levels])
    out += array([struct.pack("<I6b6b6i32s", 0, *frets, *([-1] * 6), *([-1] * 6), b"c") for frets in song.chords])
    out += array([])  # chord notes
    out += array([struct.pack("<fif48s", 0.0, 60, 0.5, b"la")] * song.vocals)
    if song.vocals:
        out += array([]) + array([]) + array([])  # symbols
    out += array([struct.pack("<iff3i", phrase, 0.0, 0.0, 0, 0, 0) for phrase in song.iterations])
    out += array([])  # phrase extra info
    out += struct.pack("<i", 1) + struct.pack("<i", 0) + array([struct.pack("<i", 0)])  # new linked difficulties
    out += array([]) + array([]) + array([struct.pack("<fi", 1.0, 1)]) + array([]) + array([])  # actions .. sections
    out += struct.pack("<i", len(song.levels))
    for difficulty, notes in song.levels.items():
        out += struct.pack("<i", difficulty) + array([]) + array([]) + array([]) + array([])
        out += array([note_record(n) for n in notes])
        out += array([]) + array([]) + array([])
    out += struct.pack("<4d2fb32shf", 0, 0, 0, 0, 0, 0, song.capo, b"", 0, song.length)
    out += array([struct.pack("<h", t) for t in song.tuning])
    out += struct.pack("<ffi", 0.0, 0.0, max(song.levels, default=0))
    return out


def sng(song: Song, iv: bytes = bytes(range(16))) -> bytes:
    plain = sng_plain(song)
    body = struct.pack("<I", len(plain)) + zlib.compress(plain)
    encryptor = Cipher(algorithms.AES(SNG_KEY_MAC), modes.CTR(iv)).encryptor()
    return struct.pack("<II", 0x4A, 3) + iv + encryptor.update(body) + encryptor.finalize() + b"\x00" * 56


def psarc(files: dict[str, bytes], encrypt: bool = True, block_size: int = 0x10000) -> bytes:
    names = list(files)
    contents = ["\n".join(names).encode()] + [files[name] for name in names]
    entry_size = 30
    blocks: list[bytes] = []
    entries = []
    for content in contents:
        first = len(blocks)
        for start in range(0, max(len(content), 1), block_size):
            raw = content[start:start + block_size]
            packed = zlib.compress(raw)
            blocks.append(packed if len(packed) < len(raw) else raw)
        entries.append((first, len(content)))
    toc_length = 32 + len(contents) * entry_size + 2 * len(blocks)
    offset = toc_length
    toc = b""
    for first, length in entries:
        toc += b"\x00" * 16 + first.to_bytes(4, "big") + length.to_bytes(5, "big") + offset.to_bytes(5, "big")
        offset += sum(len(b) for b in blocks[first:first + max(1, -(-length // block_size))])
    toc += b"".join((len(b) if len(b) < block_size else 0).to_bytes(2, "big") for b in blocks)
    if encrypt:
        encryptor = Cipher(algorithms.AES(TOC_KEY), CFB(TOC_IV)).encryptor()
        toc = encryptor.update(toc) + encryptor.finalize()
    header = struct.pack(">4sI4sIIIII", b"PSAR", 0x00010004, b"zlib", toc_length, entry_size, len(contents), block_size,
                         4 if encrypt else 0)
    return header + toc + b"".join(blocks)


def manifest(persistent_id: str, sng_name: str, name: str, song: str = "Song", artist: str = "Artist",
             song_key: str = "SongKey") -> bytes:
    return json.dumps({"Entries": {persistent_id: {"Attributes": {
        "PersistentID": persistent_id, "SongKey": song_key, "ArrangementName": name,
        "SongName": song, "ArtistName": artist,
        "SongAsset": f"urn:application:musicgame-song:{sng_name}",
    }}}}).encode()


def song_archive(key: str, parts: dict[str, tuple[str, Song]], **names: str) -> bytes:
    """parts: {part: (persistent id, Song)}"""
    files: dict[str, bytes] = {}
    for part, (persistent_id, song) in parts.items():
        files[f"songs/bin/macos/{key}_{part}.sng"] = sng(song)
        files[f"manifests/songs_dlc_{key}/{key}_{part}.json"] = manifest(
            persistent_id, f"{key}_{part}", part.capitalize(), song_key=key, **names)
    return psarc(files)
