from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Any, Sequence

from . import stems
from .library import ArrangementRef

# A song's parts (stems) in the game's own song: the riffloop mod (rearmed-sidekicks) mixes them into the game's song
# as it plays, each at a level of its own, so the stem mixer (the Launchkey's, the page's, the game's MIXER's stem
# page) works on the game's song while following as it does on the song's audio here. Its file, written here and
# mapped there: "RSKPARTS", then little-endian u32s: version, the song's .wem's file ID (the number Wwise streams it
# by: the mod mixes that source's blocks), its rate, its channels, its frames, the parts; their names from offset 32
# (4 bytes each); from offset 64 the samples, int16, a frame at a time, each part's channels in turn. Frame N is frame
# N of the song as the game decodes it (the parts are made from the song decoded as the game does: rs-stems-drums).
# The parts that are silent in a song (no piano, say) are left out. One song's file kept at a time (about 58 MB a
# minute of a song in 5 parts).

MAGIC = b"RSKPARTS"
VERSION = 1
HEADER = 64
MOST = 6  # parts a file has at most (the mod's)
NAMES = {"drums": b"drum", "bass": b"bass", "guitar": b"guit", "vocals": b"voca", "piano": b"keys"}
SILENT = 256  # a part never louder than this (of 32767: -42 dB) is silent: left out (a separator leaves a part a
# song hasn't at a whisper, its loudest about -53 dB: BS-RoFormer SW's piano in Blackwater Park)
CHUNK = 1 << 18  # frames written at a time (the file made a piece at a time: no second copy of the song in memory)


def folder() -> Path:
    return stems.cache_dir().parent / "game-parts"


def path_for(ref: ArrangementRef, separator: str) -> Path:
    """Where a song's parts by a separator are kept for the game."""
    return folder() / f"{Path(ref.archive).name}#{ref.song_key}@{separator}.pcm"


def wem_format(data: bytes) -> tuple[int, int]:
    """A Wwise .wem's sample rate and channels (its RIFF fmt chunk)."""
    if data[:4] not in (b"RIFF", b"RIFX") or data[8:12] != b"WAVE":
        raise ValueError("Not a Wwise .wem")
    endian = "<" if data[:4] == b"RIFF" else ">"
    at = 12
    while at + 8 <= len(data):
        kind, size = data[at:at + 4], struct.unpack_from(endian + "I", data, at + 4)[0]
        if kind == b"fmt ":
            channels, rate = struct.unpack_from(endian + "HI", data, at + 10)
            return rate, channels
        at += 8 + size + (size & 1)
    raise ValueError("No fmt chunk in the .wem")


def header(path: Path) -> dict[str, Any] | None:
    """A parts file's header: its file ID, rate, channels, frames and parts' names; None if it isn't one (or whole)."""
    try:
        with open(path, "rb") as f:
            head = f.read(HEADER)
        size = path.stat().st_size
    except OSError:
        return None
    if len(head) < HEADER or head[:8] != MAGIC:
        return None
    version, file_id, rate, channels, frames, count = struct.unpack_from("<6I", head, 8)
    if version != VERSION or not 1 <= count <= MOST or size < HEADER + frames * count * channels * 2:
        return None
    codes = {code: name for name, code in NAMES.items()}
    names = [codes.get(head[32 + 4 * i:36 + 4 * i], "?") for i in range(count)]
    return {"file_id": file_id, "rate": rate, "channels": channels, "frames": frames, "names": names}


def write(path: Path, file_id: int, rate: int, wavs: dict[str, Path]) -> list[str]:
    """A song's parts (16-bit WAVs, by name: stems.decode's) written as the mod maps them, a piece at a time (no
    copy of the whole song in memory), whole before it's put in place (the mod may have the one before mapped); the
    silent ones left out. The names of those in it, in its order. Every other song's file is removed (one kept)."""
    import wave
    from contextlib import ExitStack

    import numpy as np
    with ExitStack() as stack:
        opened = {name: stack.enter_context(wave.open(str(wavs[name]))) for name in stems.SEPARATED if name in wavs}
        if not opened:
            raise ValueError("No parts to write")

        def chunks(w: wave.Wave_read) -> Any:
            w.rewind()
            while True:
                data = w.readframes(CHUNK)
                if not data:
                    return
                yield np.frombuffer(data, dtype=np.int16).reshape(-1, w.getnchannels())

        loudest = {name: max((int(np.abs(c).max(initial=0)) for c in chunks(w)), default=0) for name, w in opened.items()}
        names = [name for name in opened if loudest[name] > SILENT] or list(opened)[:1]  # (all silent: one, so the
        frames = min(opened[name].getnframes() for name in names)                    # file's still the song's)
        channels = opened[names[0]].getnchannels()
        if any(opened[name].getnchannels() != channels for name in names):
            raise ValueError("The parts' channels differ")
        head = bytearray(HEADER)
        head[:8] = MAGIC
        struct.pack_into("<6I", head, 8, VERSION, file_id, rate, channels, frames, len(names))
        for i, name in enumerate(names):
            head[32 + 4 * i:36 + 4 * i] = NAMES[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.new")
        readers = [chunks(opened[name]) for name in names]
        with open(temp, "wb") as f:
            f.write(head)
            done = 0
            while done < frames:
                pieces = [next(reader) for reader in readers]
                n = min(min(len(piece) for piece in pieces), frames - done)
                block = np.empty((n, len(names), channels), dtype=np.int16)
                for i, piece in enumerate(pieces):
                    block[:, i, :] = piece[:n]
                f.write(block.tobytes())
                done += n
    os.replace(temp, path)
    for other in path.parent.glob("*.pcm"):
        if other != path:
            other.unlink(missing_ok=True)
    return names


def weights(levels: dict[str, float], names: Sequence[str]) -> list[float]:
    """The mod's weights for a file's parts (names, in its order) at each part's level (0 to 1, by stems.STEMS: other's
    too): the song's, then each part's (stems.mix_weights: other is the song less the parts, at the song's level).
    A part not in the file (silent) has nothing to mix."""
    mixed = stems.mix_weights(levels)
    return [mixed["song"], *(mixed[name] for name in names)]
