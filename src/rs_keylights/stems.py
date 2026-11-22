from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Sequence

from . import drums
from .library import ArrangementRef

# A song's parts (stems), for its own audio with each part at a volume of its own (songaudio), by one of two
# separators, each song's kept by each apart:
# - BS-RoFormer SW (roformer): much the better (cleaner parts, the guitar's most), about the song's length to make on
#   the Mac's GPU (roformer.py).
# - Demucs (htdemucs_6s, Song Stems' model, by drums-setup.sh's rs-stems-drums): a sixth of that on the Neural Engine,
#   all five from one pass (separate-all; a separator built before it, each in turn, the part being the song less
#   what's left).
# "other" is what's left of the song without the five (ambience, synths, effects... and what the parts don't add up
# to): all at their full volume, the song as it is. Kept as AAC (afconvert: sample-aligned, a few MB a part), the
# least played first gone over CACHE_MOST.

STEMS = ("drums", "bass", "guitar", "vocals", "piano", "other")
SEPARATED = STEMS[:-1]  # (each taken out by the separator; other: what's left)
STEMS_VERSION = 1
BITRATE = 256000  # a part's AAC
CACHE_MOST = 2_000_000_000  # bytes of parts kept, at most
SEPARATING = 0.85  # of making them: the separating (the rest, keeping each as AAC)
ROFORMER, DEMUCS = "roformer", "demucs"
SEPARATORS = (ROFORMER, DEMUCS)  # (the better first)
SEPARATOR_NAMES = {ROFORMER: "BS-RoFormer SW", DEMUCS: "Demucs"}


class StemsError(Exception):
    pass


def cache_dir() -> Path:
    return Path.home() / "Library" / "Caches" / "rs-keylights" / "stems"


def _folder(ref: ArrangementRef, cache: Path | None = None, separator: str = DEMUCS) -> Path:
    name = f"{Path(ref.archive).name}#{ref.song_key}"
    return (cache or cache_dir()) / (name if separator == DEMUCS else f"{name}@{separator}")


def made(ref: ArrangementRef, cache: Path | None = None, separator: str = DEMUCS) -> bool:
    """Whether a song's parts are made by a separator (and this version)."""
    folder = _folder(ref, cache, separator)
    try:
        done = json.loads((folder / "done.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(done, dict) and done.get("version") == STEMS_VERSION and \
        all((folder / f"{stem}.m4a").exists() for stem in SEPARATED)


def split_with(ref: ArrangementRef, cache: Path | None = None) -> list[str]:
    """The separators a song's parts are made by, the better first."""
    return [separator for separator in SEPARATORS if made(ref, cache, separator)]


_seen: dict[Path, tuple[float, int, bool, bool]] = {}  # (separators looked at: as each was then, its time and size,
# and what it does)


def _does(helper: Path) -> tuple[bool, bool]:
    """What a separator does: take out every part (drums-setup.sh run again since it took out drums alone), and all of
    them in one pass (since separate-all). (Asked often: it's read once, again only when it changes.)"""
    try:
        stat = helper.stat()
        seen = _seen.get(helper)
        if seen is not None and seen[:2] == (stat.st_mtime, stat.st_size):
            return seen[2], seen[3]
        made = helper.read_bytes()
    except OSError:
        return False, False
    does = b"\x00piano\x00" in made, b"separate-all" in made
    _seen[helper] = (stat.st_mtime, stat.st_size, *does)
    return does


def can_make(tools: Path | None = None, separator: str = DEMUCS) -> bool:
    """Whether a separator's set up to take out every part: Demucs, drums-setup.sh run again since it took out drums
    alone (tools: its); BS-RoFormer SW, roformer-setup.sh run (tools: its)."""
    if separator == ROFORMER:
        from . import roformer
        return roformer.ready(tools)
    return _does((tools or drums.tools_dir()) / "rs-stems-drums")[0]


def make(ref: ArrangementRef, game_dir: Path, tools: Path | None = None, stems_home: Path | None = None,
         cache: Path | None = None, progress: Callable[[float], None] = lambda _done: None,
         separator: str = DEMUCS) -> None:
    """A song's parts made by a separator and kept, how far along going to progress (0 to 1). Demucs: about 35 s for 4
    minutes of song (a separator built before separate-all, three times that); BS-RoFormer SW, about the song's own
    length. (tools: the separator's; stems_home: Song Stems' model, Demucs'.)"""
    if separator == ROFORMER:
        from . import roformer
        roformer.make(ref, game_dir, tools=tools, cache=cache, progress=progress)
        return
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
    env = {**os.environ, "RS_STEMS_CACHE": str(tools), "RS_STEMS_HOME": str(stems_home)}
    with tempfile.TemporaryDirectory(prefix="rs-keylights-stems-") as work_dir:
        work = Path(work_dir)
        (work / "song.wem").write_bytes(drums.song_audio(ref, game_dir))
        _run([str(helper), "decode", str(work / "song.wem"), str(work / "song.wav")], env, "Decoding the song")
        with wave.open(str(work / "song.wav")) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            mix = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, channels)
        one_pass, each = _does(helper)[1], work / "parts"
        if one_pass:  # (every part from one pass of the model, each as it is)
            each.mkdir()

            def along_all(_took: float) -> None:
                try:
                    k, n = (int(v) for v in (each / "progress").read_text().split())
                except (OSError, ValueError):
                    return
                progress(SEPARATING * k / max(n, 1))

            _run([str(helper), "separate-all", str(work / "song.wem"), str(each), *SEPARATED], env,
                 "Taking out the parts", along_all)
        for k, stem in enumerate(SEPARATED):
            if one_pass:
                part = np.fromfile(each / f"{stem}.raw", dtype=np.int16)
                if part.size != mix.size:
                    raise StemsError(f"Taking out the {stem} didn't finish")
                part = part.reshape(-1, channels)
                (each / f"{stem}.raw").unlink()
            else:  # (each in turn: the song less what's left)
                rest = work / f"{stem}.pcm"

                def along(_took: float, k: int = k, rest: Path = rest) -> None:
                    done = drums._ready(rest)
                    if done is not None:
                        progress((k + min(max(done, 0.0), 1.0)) / len(SEPARATED) * SEPARATING)

                _run([str(helper), "separate", str(work / "song.wem"), "1", stem, str(rest)], env,
                     f"Taking out the {stem}", along)
                raw = rest.read_bytes()
                total, state = struct.unpack_from("<I", raw, 24)[0], struct.unpack_from("<I", raw, 28)[0]
                if state != 1:
                    raise StemsError(f"Taking out the {stem} didn't finish")
                left = np.frombuffer(raw, dtype=np.int16, offset=64, count=total * channels).reshape(-1, channels)
                part = np.clip(mix[:len(left)].astype(np.int32) - left, -32768, 32767).astype(np.int16)
                rest.unlink()
                progress((k + 1) / len(SEPARATED) * SEPARATING)
            _write_wav(work / f"{stem}.wav", part, rate)
            del part
        store(ref, {stem: work / f"{stem}.wav" for stem in SEPARATED}, rate, DEMUCS, cache, progress, SEPARATING)


def store(ref: ArrangementRef, wavs: dict[str, Path], rate: int, separator: str = DEMUCS, cache: Path | None = None,
          progress: Callable[[float], None] = lambda _done: None, start: float = 0.0) -> None:
    """A song's parts kept as a separator's (wavs: each of SEPARATED's, 16-bit, as long as the song and at its rate),
    each as AAC, side by side (afconvert takes one core); those it had before gone, then the least used songs' over
    CACHE_MOST. How far along: from start to 1, to progress."""
    folder = _folder(ref, cache, separator)
    folder.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix=".made-", dir=folder.parent) as made_dir:  # (beside it: moved in at once)
        made_now = Path(made_dir) / "parts"
        made_now.mkdir()

        def keep(stem: str) -> None:
            _run(["afconvert", "-f", "m4af", "-d", "aac", "-b", str(BITRATE), str(wavs[stem]),
                  str(made_now / f"{stem}.m4a")], env, f"Keeping the {stem}")

        with ThreadPoolExecutor(len(SEPARATED), thread_name_prefix="rs-keylights stems") as pool:
            for k, kept in enumerate(as_completed([pool.submit(keep, stem) for stem in SEPARATED])):
                kept.result()
                progress(start + (1 - start) * (k + 1) / len(SEPARATED))
        (made_now / "done.json").write_text(json.dumps({"version": STEMS_VERSION, "song": ref.title, "rate": rate,
                                                        "separator": separator}))
        if folder.exists():
            shutil.rmtree(folder)
        made_now.rename(folder)
    _prune(cache or cache_dir(), keep=folder)


def load(ref: ArrangementRef, mix: Any, cache: Path | None = None, parts: Sequence[str] = SEPARATED,
         separator: str = DEMUCS) -> dict[str, Any] | None:
    """A song's parts taken out of it by a separator (SEPARATED, or those of them asked for: samples x channels, int16,
    as long as mix, the song's own, decoded); None if they aren't made. Other, what's left, isn't kept apart: it's the
    song less them (mix_weights). (Used: kept longer, the least played going first.)"""
    import numpy as np
    if not made(ref, cache, separator):
        return None
    folder = _folder(ref, cache, separator)
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
            ThreadPoolExecutor(len(parts), thread_name_prefix="rs-keylights stems") as pool:
        # (all at once: afconvert's one core each, about a fifth of the time)
        reading = {stem: pool.submit(read, stem, Path(work)) for stem in parts}
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
    folders = [f for f in cache.iterdir() if f.is_dir() and not f.name.startswith(".")] if cache.exists() else []
    # (not one being made)
    sizes = {f: sum(p.stat().st_size for p in f.iterdir() if p.is_file()) for f in folders}
    total = sum(sizes.values())
    for folder in sorted(folders, key=lambda f: f.stat().st_mtime):
        if total <= CACHE_MOST:
            break
        if folder != keep:
            shutil.rmtree(folder, ignore_errors=True)
            total -= sizes[folder]
