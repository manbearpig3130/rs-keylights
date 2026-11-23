from __future__ import annotations

import json
import math
import os
import threading
import time
import wave
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .sng import Note

# Your guitar's tone against the record's. An input's level, live (Tap: the guitar's input, and the pedal's sound
# coming back in on an interface's input: the Guitar card's meters), and, while a song plays here or the game's
# followed, the pedal's sound recorded with where the song was as each piece of it came in. Then compared with the
# song's guitar (its stem: the side you play, its lead and rhythm apart) only where what you played is what the record
# plays then: each moment's notes (its chroma: how much of each of the 12 notes sounds) close to the record's, a
# moment either way (your timing a little off is fine; how late your sound comes back, measured and taken off), and
# your part's chart having notes there. Missed notes, wrong ones, noodling and silence are left out; what's left
# says how your tone differs (MATCHED_LEAST seconds of it at least):
# - its colour: the spectrum in third-octave bands, each moment's shape (its level taken off), yours against the
#   record's: dB more (or less) the record has in each (0 dB: the typical band, 100 Hz to 5 kHz);
# - how distorted: how far each moment's peaks rise over its average (its crest factor: distortion squashes them,
#   10 dB on a record's guitar down to 5 with a little more drive; an EQ moves it half a dB), yours against the
#   record's (a rough guide: the record's guitars are mixed, often doubled, which raises it, and mastered).
# Each check kept (CHECKS_MOST, with its recording), so what's the same over several songs (the pedal's input level's
# share) shows apart from what differs (each preset's).

RATE = 48000  # (an input read at this rate, as the guitar's is to be heard)
BLOCK = 512  # samples read at a time (about 11 ms)
CLIP = 0.995  # a sample this near full scale: clipped
PEAK_FOR, HOLD_FOR, LOUDEST_FOR = 0.12, 2.0, 15.0  # seconds a meter's peak, its held peak and its loudest are over
QUIETEST = -90.0  # dB: silence, on a meter
RECORD_MOST = 360.0  # seconds recorded at most (then it stops: about 70 MB)
FRAME = 4096  # samples a moment's spectrum (85 ms at 48 kHz)
HOP = 0.025  # seconds between your moments compared
GRID = 0.01  # seconds between the record's moments looked at
LAG_MOST = 0.25  # seconds your sound's looked for either side of the record's (how late it comes back, and the game's
# audio off its chart)
NEAR = 0.07  # seconds either side of that each moment's matched in (your timing)
MATCH = 0.75  # how alike a moment's notes are to the record's (their chroma's correlation) to count
QUIET = 30.0  # dB under its loudest moments (95th percentile) a moment's quiet: not playing
NOTES_NEAR = 0.1  # seconds either side of a moment the part's chart has a note for it to count
MATCHED_LEAST = 8.0  # seconds matched at least to say anything
BANDS = tuple(1000 * 2 ** (k / 3) for k in range(-12, 11))  # third-octave bands, 63 Hz to 10 kHz
SHAPED = (100.0, 5000.0)  # Hz: a moment's level, to take off its shape (and the difference's middle: 0 dB)
CHROMA = (80.0, 2000.0)  # Hz: the notes' range, for each moment's 12 notes
REGIONS = (("lows", 63, 160), ("low mids", 160, 400), ("mids", 400, 1000), ("upper mids", 1000, 2500),
           ("presence", 2500, 6000), ("treble", 6000, 10000))
EMPTY = -50.0  # dB under a moment's level (100 Hz to 5 kHz) a band's empty
SAYS = 2.0  # dB a region's off at least to be said
CREST_SAYS = 1.5  # dB more (or less) squashed to be said
CHECKS_MOST = 20


def cache_dir() -> Path:
    return Path.home() / "Library" / "Caches" / "rs-keylights" / "tone-checks"


def db(value: float) -> float:
    return 20 * math.log10(value) if value > 0 else QUIETEST


def _input_stream(device: str, channels: int) -> Any:
    """An audio input, open (sounddevice: numpy and sounddevice, the listen extra)."""
    import sounddevice
    return sounddevice.InputStream(samplerate=RATE, channels=channels, device=device, blocksize=BLOCK,
                                   dtype="float32", latency="low")


class Tap:
    """An audio input's channel, read as it comes in: its level (a meter's), and while it's recording, its samples,
    each block with where the song was as it came in (where: the chart's time at a time on time.monotonic's
    clock)."""

    def __init__(self, device: str, channel: int) -> None:
        self.device, self.channel = device, channel
        self.error: str | None = None
        self.asked = time.monotonic()  # (a meter shown: asked for now and then; not asked a while, closed)
        self._peaks: deque[tuple[float, float]] = deque(maxlen=int(LOUDEST_FOR * RATE / BLOCK) + 1)
        self._clipped = -math.inf
        self._lock = threading.Lock()
        self._where: Callable[[float], float | None] | None = None
        self._blocks: list[tuple[float | None, Any]] = []  # (recording: the chart's time at each block's end, it)
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def recording(self) -> bool:
        return self._where is not None

    def start(self) -> None:
        if not self.running:
            self._quit.clear()
            self._thread = threading.Thread(target=self._run, name="rs-keylights input", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._quit.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self) -> None:
        import numpy as np
        try:
            with _input_stream(self.device, self.channel) as stream:
                latency = float(stream.latency if not isinstance(stream.latency, (tuple, list)) else stream.latency[0])
                self.error = None
                while not self._quit.is_set():
                    samples, _overflowed = stream.read(BLOCK)
                    now = time.monotonic()
                    x = np.array(samples[:, self.channel - 1], dtype=np.float32)
                    peak = float(np.abs(x).max(initial=0.0))
                    with self._lock:
                        self._peaks.append((now, peak))
                        if peak >= CLIP:
                            self._clipped = now
                        where = self._where
                        if where is not None and len(self._blocks) * BLOCK < RECORD_MOST * RATE:
                            self._blocks.append((where(now - latency), x))
        except Exception as exc:  # (no such input, unplugged: said)
            self.error = str(exc) or type(exc).__name__

    def level(self, now: float | None = None) -> dict[str, Any]:
        """Its level: the peak now, held a while, the loudest lately (dB of full scale), and whether it's clipped
        lately."""
        now = time.monotonic() if now is None else now
        self.asked = now
        with self._lock:
            peaks = list(self._peaks)
            clipped = now - self._clipped <= HOLD_FOR

        def most(within: float) -> float:
            return round(db(max((p for at, p in peaks if now - at <= within), default=0.0)), 1)
        return {"peak": most(PEAK_FOR), "hold": most(HOLD_FOR), "loudest": most(LOUDEST_FOR), "clipped": clipped,
                "error": self.error, "recording": self.recording}

    def record(self, where: Callable[[float], float | None]) -> None:
        """Recording from now: each block with where the song was as it came in."""
        with self._lock:
            self._blocks, self._where = [], where

    def take(self) -> tuple[Any, list[tuple[int, float | None]]]:
        """What it recorded (mono, float32, at RATE), and where the song was at the end of each block (the sample
        after it, the chart's time); recording stops."""
        import numpy as np
        with self._lock:
            blocks, self._blocks, self._where = self._blocks, [], None
        if not blocks:
            return np.zeros(0, dtype=np.float32), []
        samples = np.concatenate([x for _at, x in blocks])
        ends = np.cumsum([len(x) for _at, x in blocks])
        return samples, [(int(end), at) for end, (at, _x) in zip(ends, blocks)]

    @property
    def seconds(self) -> float:
        with self._lock:
            return sum(len(x) for _at, x in self._blocks) / RATE


def chart_times(anchors: Sequence[tuple[int, float | None]], at: Any) -> Any:
    """Where the song was (the chart's time) at each of some samples (at: indexes), from where it was at the end of
    each block recorded (anchors); NaN where it isn't known: before the first, paused (going at under a fifth of its
    speed), or jumping (looped, moved)."""
    import numpy as np
    out = np.full(len(at), np.nan)
    if len(anchors) < 2:
        return out
    ends = np.array([e for e, _c in anchors], dtype=np.float64)
    charts = np.array([np.nan if c is None else c for _e, c in anchors], dtype=np.float64)
    k = np.searchsorted(ends, at, side="left")  # (the block each's in: between the end before and its own)
    ok = (k >= 1) & (k < len(ends))
    k = np.clip(k, 1, len(ends) - 1)
    e0, e1, c0, c1 = ends[k - 1], ends[k], charts[k - 1], charts[k]
    elapsed = (e1 - e0) / RATE
    moved = c1 - c0
    going = ok & ~np.isnan(moved) & (moved >= 0.2 * elapsed) & (moved <= 3 * elapsed + 0.02)
    out[going] = (c0 + (np.asarray(at) - e0) / np.maximum(e1 - e0, 1) * moved)[going]
    return out


@dataclass
class Moments:
    """Moments of a recording: their bands' power, their notes (chroma: centred, unit length), how far their peaks
    rise over their average (dB: crest factor), and their level (dB)."""
    bands: Any
    chroma: Any
    crest: Any
    level: Any


def moments(x: Any, rate: int, centres: Any) -> Moments:
    """Moments of a mono recording at some sample indexes (each FRAME long, centred there; silence past its ends)."""
    import numpy as np
    window = np.hanning(FRAME).astype(np.float32)
    freqs = np.fft.rfftfreq(FRAME, 1 / rate)
    edges = [(f * 2 ** (-1 / 6), f * 2 ** (1 / 6)) for f in BANDS]
    band_of = np.full(len(freqs), -1)
    for i, (lo, hi) in enumerate(edges):
        band_of[(freqs >= lo) & (freqs < hi)] = i
    in_bands = band_of >= 0
    notes = (freqs >= CHROMA[0]) & (freqs < CHROMA[1])
    pitch_class = np.zeros(len(freqs), dtype=int)
    pitch_class[notes] = np.round(12 * np.log2(freqs[notes] / 440.0) + 69).astype(int) % 12
    padded = np.concatenate([np.zeros(FRAME // 2, np.float32), np.asarray(x, np.float32), np.zeros(FRAME, np.float32)])
    centres = np.asarray(centres, dtype=int)
    n = len(centres)
    out = Moments(np.zeros((n, len(BANDS))), np.zeros((n, 12)), np.zeros(n), np.zeros(n))
    for start in range(0, n, 256):  # (a few at a time: a few MB)
        at = np.clip(centres[start:start + 256], 0, len(x))
        raw = np.stack([padded[c:c + FRAME] for c in at])
        power = np.abs(np.fft.rfft(raw * window, axis=1)) ** 2
        k = slice(start, start + len(at))
        out.bands[k] = np.stack([np.bincount(band_of[in_bands], weights=p[in_bands], minlength=len(BANDS))
                                 for p in power])
        chroma = np.stack([np.bincount(pitch_class[notes], weights=np.sqrt(p[notes]), minlength=12) for p in power])
        chroma -= chroma.mean(axis=1, keepdims=True)
        out.chroma[k] = chroma / np.maximum(np.linalg.norm(chroma, axis=1, keepdims=True), 1e-12)
        mean_square = np.mean(raw ** 2, axis=1) + 1e-12
        out.crest[k] = 10 * np.log10(np.max(raw ** 2, axis=1) / mean_square + 1e-12)
        out.level[k] = 10 * np.log10(mean_square)
    return out


@dataclass
class Result:
    """A tone check: how much of what you played matched the record (seconds), how late your sound came back
    (ms), and if enough matched, how the record's colour differs from yours (dB more the record has, each of
    BANDS, a moment's level taken off: 0 dB around the middle), each region's (REGIONS) said, and how far each's
    peaks rise over its average (dB: crest factor; lower, more distorted)."""
    played: float
    matched: float
    late_ms: int
    enough: bool
    eq: list[float] = field(default_factory=list)
    regions: dict[str, float] = field(default_factory=dict)
    crest_yours: float | None = None
    crest_record: float | None = None
    said: list[str] = field(default_factory=list)


def _sounding(notes: Sequence[Note], times: Any) -> Any:
    """Whether a part's chart has a note at each time (or NOTES_NEAR either side of it)."""
    import numpy as np
    spans = sorted((n.time - NOTES_NEAR, n.time + max(n.sustain, 0.2) + NOTES_NEAR) for n in notes if n.pitches)
    if not spans:
        return np.zeros(len(times), dtype=bool)
    starts = np.array([a for a, _b in spans])
    ends = np.maximum.accumulate(np.array([b for _a, b in spans]))
    k = np.searchsorted(starts, times, side="right") - 1
    return (k >= 0) & (np.asarray(times) <= ends[np.clip(k, 0, None)])


def analyse(take: Any, anchors: Sequence[tuple[int, float | None]], reference: Any, ref_rate: int, ref_start: float,
            notes: Sequence[Note], offset: float = 0.0, rate: int = RATE) -> Result:
    """Your recording (take: mono at rate; anchors: where the song was at the end of each block, Tap.take's) against
    the record's guitar (reference: mono at ref_rate, its first sample ref_start seconds into the song's audio), where
    your part's chart (notes) has notes and what you played is what the record plays then (offset: seconds the song's
    audio is after its chart)."""
    import numpy as np
    step = int(HOP * rate)
    centres = np.arange(FRAME // 2, len(take) - FRAME // 2, step)
    charts = chart_times(anchors, centres)
    known = ~np.isnan(charts)
    centres, charts = centres[known], charts[known]
    if not len(centres):
        return Result(0.0, 0.0, 0, False)
    yours = moments(take, rate, centres)
    loud = np.percentile(yours.level, 95)
    played = (yours.level >= max(loud - QUIET, -70.0)) & _sounding(notes, charts)
    centres, charts = centres[played], charts[played]
    yours = Moments(yours.bands[played], yours.chroma[played], yours.crest[played], yours.level[played])
    if not len(centres):
        return Result(0.0, 0.0, 0, False)
    audio = charts + offset - ref_start  # (each moment's place in the reference)
    first, last = audio.min() - LAG_MOST - NEAR, audio.max() + LAG_MOST + NEAR
    grid = np.arange(first, last + GRID, GRID)
    record = moments(reference, ref_rate, np.round(grid * ref_rate).astype(int))
    heard = record.level >= max(np.percentile(record.level, 95) - QUIET, -70.0)
    heard &= (grid >= 0) & (grid * ref_rate < len(reference))

    def alike(shift: float) -> Any:  # (each moment's notes against the record's, shift seconds on)
        k = np.clip(np.round((audio + shift - first) / GRID).astype(int), 0, len(grid) - 1)
        return np.where(heard[k], np.einsum("ij,ij->i", yours.chroma, record.chroma[k]), -1.0), k

    lags = np.arange(-LAG_MOST, LAG_MOST + GRID / 2, GRID)
    lag = float(lags[int(np.argmax([np.mean(alike(shift)[0]) for shift in lags]))])
    near = np.arange(-NEAR, NEAR + GRID / 2, GRID)
    tried = [alike(lag + shift) for shift in near]
    scores = np.stack([s for s, _k in tried])  # (shifts x moments)
    best = np.argmax(scores, axis=0)
    score = scores[best, np.arange(len(best))]
    at = np.stack([k for _s, k in tried])[best, np.arange(len(best))]
    kept = score >= MATCH
    matched = float(kept.sum() * HOP)
    result = Result(round(len(centres) * HOP, 1), round(matched, 1), -round(lag * 1000), matched >= MATCHED_LEAST)
    if not result.enough:
        return result
    middle = (np.array(BANDS) >= SHAPED[0]) & (np.array(BANDS) <= SHAPED[1])

    def shape(bands: Any) -> Any:  # (the moments' average spectrum, each moment's level taken off)
        each = bands / np.maximum(bands[:, middle].sum(axis=1, keepdims=True), 1e-12)
        return 10 * np.log10(each.mean(axis=0) + 1e-12)
    theirs, mine = shape(record.bands[at[kept]]), shape(yours.bands[kept])
    difference = theirs - mine
    difference[(theirs < EMPTY) & (mine < EMPTY)] = 0.0  # (nothing there in either: no difference to speak of)
    difference -= np.median(difference[middle])  # (0 dB: the middle's typical band; a region changed, the rest at 0)
    result.eq = [round(float(d), 1) for d in difference]
    centres_hz = np.array(BANDS)
    for name, lo, hi in REGIONS:
        result.regions[name] = round(float(difference[(centres_hz >= lo) & (centres_hz < hi)].mean()), 1)
    result.crest_yours = round(float(np.median(yours.crest[kept])), 1)
    result.crest_record = round(float(np.median(record.crest[at[kept]])), 1)
    result.said = _said(result)
    return result


def _said(result: Result) -> list[str]:
    """What a check found, in words: the regions the record has more or less of, and how distorted yours is."""
    out = []
    for name, lo, hi in REGIONS:
        d = result.regions.get(name, 0.0)
        if abs(d) >= SAYS:
            span = f"{lo} Hz–{hi / 1000:g} kHz" if hi >= 1000 and lo < 1000 else \
                f"{lo / 1000:g}–{hi / 1000:g} kHz" if lo >= 1000 else f"{lo}–{hi} Hz"
            out.append(f"{'More' if d > 0 else 'Less'} {name} ({span}): the record has {abs(d):.0f} dB "
                       f"{'more' if d > 0 else 'less'}")
    if result.crest_yours is not None and result.crest_record is not None:
        d = result.crest_record - result.crest_yours  # (the record's peaks over yours: yours squashed more)
        out.append(f"More distorted than the record (its peaks squashed {d:.0f} dB more): try less gain"
                   if d >= CREST_SAYS else
                   f"Cleaner than the record (its peaks {-d:.0f} dB less squashed): try more gain"
                   if d <= -CREST_SAYS else "About as distorted as the record")
    return out


def _history_file(cache: Path | None = None) -> Path:
    return (cache or cache_dir()) / "checks.json"


def history(cache: Path | None = None) -> list[dict[str, Any]]:
    """The checks kept, the latest last."""
    try:
        got = json.loads(_history_file(cache).read_text())
    except (OSError, ValueError):
        return []
    return got if isinstance(got, list) else []


def keep(check: dict[str, Any], take: Any, rate: int = RATE, cache: Path | None = None,
         anchors: Sequence[tuple[int, float | None]] = ()) -> dict[str, Any]:
    """A check kept, with its recording (16-bit WAV beside it, and where the song was at the end of each of its
    blocks: anchors, as JSON, to check it again); the oldest gone over CHECKS_MOST."""
    import numpy as np
    folder = cache or cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S")
    with wave.open(str(folder / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(np.round(np.asarray(take) * 32767), -32768, 32767).astype(np.int16).tobytes())
    (folder / f"{name}.json").write_text(json.dumps([[e, None if c is None else round(c, 4)] for e, c in anchors]))
    checks = [*history(cache), {**check, "recording": f"{name}.wav"}]
    for gone in checks[:-CHECKS_MOST]:
        if gone.get("recording"):
            recording = folder / Path(str(gone["recording"])).name
            recording.unlink(missing_ok=True)
            recording.with_suffix(".json").unlink(missing_ok=True)
    checks = checks[-CHECKS_MOST:]
    temp = folder / f".checks.json.{os.getpid()}"
    temp.write_text(json.dumps(checks, indent=1))
    temp.replace(_history_file(cache))
    return checks[-1]


def as_dict(result: Result) -> dict[str, Any]:
    return asdict(result)
