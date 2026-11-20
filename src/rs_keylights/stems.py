from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import drums
from .library import ArrangementRef

# A song's parts (stems), for its own audio with each part at a volume of its own (songaudio): Song Stems' Demucs
# model (htdemucs_6s, by drums-setup.sh's rs-stems-drums) takes each of five out of the song in turn, the part being
# the song less what's left; "other" is what's left of the song without the five (ambience, synths, effects...).
# Kept as AAC (afconvert: sample-aligned, a few MB a part), the least played first gone over CACHE_MOST.

STEMS = ("drums", "bass", "guitar", "vocals", "piano", "other")
SEPARATED = STEMS[:-1]  # (each taken out by the separator; other: what's left)
STEMS_VERSION = 1
BITRATE = 256000  # a part's AAC
CACHE_MOST = 2_000_000_000  # bytes of parts kept, at most


class StemsError(Exception):
    pass


def cache_dir() -> Path:
    return Path.home() / "Library" / "Caches" / "rs-keylights" / "stems"


def _folder(ref: ArrangementRef, cache: Path | None = None) -> Path:
    return (cache or cache_dir()) / f"{Path(ref.archive).name}#{ref.song_key}"


def made(ref: ArrangementRef, cache: Path | None = None) -> bool:
    """Whether a song's parts are made (by this version)."""
    try:
        done = json.loads((_folder(ref, cache) / "done.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(done, dict) and done.get("version") == STEMS_VERSION and \
        all((_folder(ref, cache) / f"{stem}.m4a").exists() for stem in SEPARATED)


_can: dict[Path, tuple[float, int, bool]] = {}  # (separators looked at: as each was then, its time and size)


def can_make(tools: Path | None = None) -> bool:
    """Whether the separator takes out every part (drums-setup.sh run again since it took out drums alone). (Asked
    often: it's read once, again only when it changes.)"""
    helper = (tools or drums.tools_dir()) / "rs-stems-drums"
    try:
        stat = helper.stat()
        seen = _can.get(helper)
        if seen is not None and seen[:2] == (stat.st_mtime, stat.st_size):
            return seen[2]
        can = b"\x00piano\x00" in helper.read_bytes()
    except OSError:
        return False
    _can[helper] = (stat.st_mtime, stat.st_size, can)
    return can


def make(ref: ArrangementRef, game_dir: Path, tools: Path | None = None, stems_home: Path | None = None,
         cache: Path | None = None, progress: Callable[[float], None] = lambda _done: None) -> None:
    """A song's parts made and kept (a few times its drum part's separating: about 20 s a part for 4 minutes of song),
    how far along going to progress (0 to 1)."""
    import numpy as np
    tools = tools or drums.tools_dir()
    stems_home = stems_home or Path.home() / "Library" / "Application Support" / "RS Re-ARMed" / "stems"
    helper = tools / "rs-stems-drums"
    if not can_make(tools):
        raise StemsError("The song's parts need the separator rebuilt: run tools/drums-setup.sh")
    if not (stems_home / "htdemucs_6s.mlmodelc").exists():
        raise StemsError(f"No Song Stems model at {stems_home} (rearmed-mods)")
    if not (tools / "codebooks.bin").exists():
        drums.codebooks(game_dir, tools / "codebooks.bin")
    folder = _folder(ref, cache)
    env = {**os.environ, "RS_STEMS_CACHE": str(tools), "RS_STEMS_HOME": str(stems_home)}
    with tempfile.TemporaryDirectory(prefix="rs-keylights-stems-") as work_dir:
        work = Path(work_dir)
        (work / "song.wem").write_bytes(drums.song_audio(ref, game_dir))
        _run([str(helper), "decode", str(work / "song.wem"), str(work / "song.wav")], env, "Decoding the song")
        with wave.open(str(work / "song.wav")) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            mix = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, channels)
        made_now = work / "made"
        made_now.mkdir()
        for k, stem in enumerate(SEPARATED):
            rest = work / f"{stem}.pcm"

            def along(_took: float, k: int = k, rest: Path = rest) -> None:
                done = drums._ready(rest)
                if done is not None:
                    progress((k + min(max(done, 0.0), 1.0)) / len(SEPARATED))

            _run([str(helper), "separate", str(work / "song.wem"), "1", stem, str(rest)], env,
                 f"Taking out the {stem}", along)
            raw = rest.read_bytes()
            total, state = struct.unpack_from("<I", raw, 24)[0], struct.unpack_from("<I", raw, 28)[0]
            if state != 1:
                raise StemsError(f"Taking out the {stem} didn't finish")
            left = np.frombuffer(raw, dtype=np.int16, offset=64, count=total * channels).reshape(-1, channels)
            part = np.clip(mix[:len(left)].astype(np.int32) - left, -32768, 32767).astype(np.int16)
            _write_wav(work / f"{stem}.wav", part, rate)
            _run(["afconvert", "-f", "m4af", "-d", "aac", "-b", str(BITRATE), str(work / f"{stem}.wav"),
                  str(made_now / f"{stem}.m4a")], env, f"Keeping the {stem}")
            rest.unlink()
            progress((k + 1) / len(SEPARATED))
        (made_now / "done.json").write_text(json.dumps({"version": STEMS_VERSION, "song": ref.title, "rate": rate}))
        if folder.exists():
            shutil.rmtree(folder)
        folder.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(made_now), str(folder))
    _prune(cache or cache_dir(), keep=folder)


def load(ref: ArrangementRef, mix: Any, cache: Path | None = None) -> dict[str, Any] | None:
    """A song's parts taken out of it (SEPARATED: samples x channels, int16, as long as mix, the song's own, decoded);
    None if they aren't made. Other, what's left, isn't kept apart: it's the song less them (mix_weights). (Used:
    kept longer, the least played going first.)"""
    import numpy as np
    if not made(ref, cache):
        return None
    folder = _folder(ref, cache)
    env = dict(os.environ)

    def read(stem: str, work: Path) -> Any:
        wav = work / f"{stem}.wav"
        _run(["afconvert", "-f", "WAVE", "-d", "LEI16", str(folder / f"{stem}.m4a"), str(wav)], env,
             f"Reading the {stem}")
        with wave.open(str(wav)) as w:
            part = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, w.getnchannels())
        if part.shape == mix.shape:
            return part  # (as long as the song, as it should be: not copied; read-only, as it's only read)
        fitted = np.zeros(mix.shape, dtype=np.int16)
        n = min(len(part), len(mix))
        fitted[:n, :part.shape[1]] = part[:n, :mix.shape[1]]
        return fitted

    with tempfile.TemporaryDirectory(prefix="rs-keylights-stems-") as work, \
            ThreadPoolExecutor(len(SEPARATED), thread_name_prefix="rs-keylights stems") as pool:
        # (all at once: afconvert's one core each, about a fifth of the time)
        reading = {stem: pool.submit(read, stem, Path(work)) for stem in SEPARATED}
        out = {stem: part.result() for stem, part in reading.items()}
    os.utime(folder)
    return out


def mix_weights(levels: dict[str, float]) -> dict[str, float]:
    """The song and its parts taken out of it (load), as weights to mix them at for each part's level (0 to 1, by
    STEMS: other's too): other is the song less the rest, so the song is at other's level, and each part at its own
    less that. (The song's audio and its parts, without a seventh copy of it in memory for other.)"""
    other = levels.get("other", 1.0)
    return {"song": other, **{stem: levels.get(stem, 1.0) - other for stem in SEPARATED}}


def _run(args: list[str], env: dict[str, str], what: str, along: Callable[[float], None] | None = None) -> None:
    with tempfile.TemporaryFile("w+") as out:
        started = time.monotonic()
        tool = subprocess.Popen(drums.in_background(args), env=env, stdout=out, stderr=subprocess.STDOUT, text=True)
        while True:
            try:
                code = tool.wait(drums.PROGRESS_EVERY)
                break
            except subprocess.TimeoutExpired:
                if along is not None:
                    along(time.monotonic() - started)
        if code != 0:
            out.seek(0)
            raise StemsError(f"{what} failed: {out.read().strip()[-300:]}")


def _write_wav(path: Path, samples: Any, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def _prune(cache: Path, keep: Path) -> None:
    """Songs' parts gone, the least recently used first, until they're CACHE_MOST at most (keep: never)."""
    folders = [f for f in cache.iterdir() if f.is_dir()] if cache.exists() else []
    sizes = {f: sum(p.stat().st_size for p in f.iterdir() if p.is_file()) for f in folders}
    total = sum(sizes.values())
    for folder in sorted(folders, key=lambda f: f.stat().st_mtime):
        if total <= CACHE_MOST:
            break
        if folder != keep:
            shutil.rmtree(folder, ignore_errors=True)
            total -= sizes[folder]
