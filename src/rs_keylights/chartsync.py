from __future__ import annotations

import json
import os
import tempfile
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .library import ArrangementRef

# How far a song's chart is from its audio. A chart's notes are timed by its beats, and some charts (custom ones
# mostly) sit a few tens of milliseconds off their song's audio, the same all through it (a chart stretched or drifting
# is rare: measured over the songs split here, a handful of milliseconds end to end). Everything played from the chart
# (the Casio's parts, the drum part, lined up onto the chart as it's made) is then that far off the song's own audio:
# played here, the audio's moved by it; following the game (whose audio is that far off its own chart too), the
# parts are. Measured from the song's drums (its drum part's stem, if it's split; else the whole song, less
# cleanly): where its onsets line up best with the chart's beats and half-beats, in WINDOW-second stretches; applied
# only when most stretches agree (within SPREAD) and it's off by LEAST or more (less is within the measuring's own
# error: none). Kept for each song (CACHE), measured again once the song's split (its drums then, not the mix).

VERSION = 1
WINDOW = 20.0  # seconds a stretch measured
FARTHEST = 0.12  # seconds either way a stretch's offset is looked for
STEP = 0.002  # seconds between the offsets tried
LEAST = 0.015  # seconds off at least to apply it
SPREAD = 0.03  # seconds the middle half of the stretches' offsets agree within, to apply it
STRETCHES = 4  # stretches clear enough (drums on the beats) at least
CLEAR = 1.3  # a stretch's best offset this much above its median: clear
HOP = 0.005  # seconds between the onsets' frames
FRAME = 512  # samples a frame (at 44.1 or 48 kHz: about 11 ms)
CHUNK = 4096  # frames worked through at a time (the sound and the keys going on meanwhile: a moment's pause each)


@dataclass(frozen=True)
class Offset:
    offset: float  # seconds the audio's after the chart (its drums after the chart's beats); 0: in time, or not clear
    measured: float  # (as measured: applied or not)
    spread: float  # (the middle half of the stretches' offsets)
    stretches: int  # (clear ones)
    source: str  # "drums" (a split song's drums) or "mix" (the whole song)
    version: int = VERSION


def cache_file() -> Path:
    return Path.home() / "Library" / "Caches" / "rs-keylights" / "chart-offsets.json"


def onsets(path: Path) -> tuple[Any, float]:
    """A WAV's onsets: its spectral flux (log magnitudes rising), less its local average, every HOP seconds; and the
    time of the first frame's centre."""
    import numpy as np
    with wave.open(str(path)) as w:
        rate, channels, count = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = np.frombuffer(w.readframes(count), dtype=np.int16).reshape(-1, channels)
    x = raw.mean(axis=1, dtype=np.float32) / 32768.0
    del raw
    hop = max(int(round(rate * HOP)), 1)
    n = max((len(x) - FRAME) // hop, 0)
    window = np.hanning(FRAME).astype(np.float32)
    flux = np.zeros(n, dtype=np.float32)
    last = None
    for start in range(0, n, CHUNK):
        k = min(CHUNK, n - start)
        frames = np.lib.stride_tricks.as_strided(x[start * hop:], shape=(k, FRAME), strides=(x.strides[0] * hop,
                                                                                             x.strides[0]))
        mag = np.log1p(10 * np.abs(np.fft.rfft(frames * window, axis=1))).astype(np.float32)
        before = np.vstack([last[None] if last is not None else mag[:1], mag[:-1]])
        flux[start:start + k] = np.maximum(mag - before, 0).sum(axis=1)
        last = mag[-1]
        time.sleep(0.001)  # (the sound and the keys go on meanwhile)
    flux -= np.convolve(flux, np.ones(40, dtype=np.float32) / 40, mode="same")
    return np.maximum(flux, 0), FRAME / 2 / rate


def measure(flux: Any, first: float, beats: Sequence[float], source: str) -> Offset:
    """How far a song's onsets (flux, every HOP seconds from first) are after its chart's beats (and half-beats):
    each WINDOW-second stretch's best offset; applied if STRETCHES are clear and they agree."""
    import numpy as np
    b = np.array(sorted(beats), dtype=np.float64)
    if len(b) < 8 or not len(flux):
        return Offset(0.0, 0.0, 0.0, 0, source)
    grid = np.sort(np.concatenate([b, (b[:-1] + b[1:]) / 2]))
    times = first + np.arange(len(flux)) * HOP
    lags = np.arange(-FARTHEST, FARTHEST + STEP / 2, STEP)
    found = []
    for start in np.arange(grid[0], grid[-1] - WINDOW / 2, WINDOW):
        g = grid[(grid >= start) & (grid < start + WINDOW)]
        if len(g) < 16:
            continue
        scores = np.array([np.interp(g + lag, times, flux).mean() for lag in lags])
        best = int(np.argmax(scores))
        if scores[best] > CLEAR * np.median(scores):
            found.append(float(lags[best]))
    if len(found) < STRETCHES:
        return Offset(0.0, float(np.median(found)) if found else 0.0, 0.0, len(found), source)
    measured = float(np.median(found))
    spread = float(np.percentile(found, 75) - np.percentile(found, 25))
    applied = measured if abs(measured) >= LEAST and spread <= SPREAD else 0.0
    return Offset(round(applied, 3), round(measured, 3), round(spread, 3), len(found), source)


def _key(ref: ArrangementRef) -> str:
    return f"{Path(ref.archive).name}#{ref.song_key}"


def kept(ref: ArrangementRef, cache: Path | None = None) -> Offset | None:
    """A song's offset as measured before (None: not yet, measured another way, or the song split since)."""
    from . import stems
    try:
        got = json.loads((cache or cache_file()).read_text()).get(_key(ref))
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or got.get("version") != VERSION:
        return None
    if got.get("source") == "mix" and stems.split_with(ref):
        return None  # (split since: its drums now)
    try:
        return Offset(**got)
    except TypeError:
        return None


def offset_for(ref: ArrangementRef, beats: Sequence[float], game_dir: Path, cache: Path | None = None) -> Offset:
    """A song's offset: as kept, else measured (its drums, if it's split, else the whole song; a few seconds) and
    kept."""
    from . import drums, stems
    got = kept(ref, cache)
    if got is not None:
        return got
    by = stems.split_with(ref)
    with tempfile.TemporaryDirectory(prefix="rs-keylights-sync-") as work:
        if by:
            wav = stems.decode(ref, Path(work), separator=by[0], parts=("drums",))["drums"]  # type: ignore[index]
        else:
            wav = Path(work) / "song.wav"
            drums.decode(ref, game_dir, wav)
        flux, first = onsets(wav)
    got = measure(flux, first, beats, "drums" if by else "mix")
    path = cache or cache_file()
    try:
        everything = json.loads(path.read_text())
    except (OSError, ValueError):
        everything = {}
    everything[_key(ref)] = asdict(got)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    temp.write_text(json.dumps(everything, indent=1))
    temp.replace(path)
    return got
