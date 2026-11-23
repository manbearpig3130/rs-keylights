from __future__ import annotations

import bisect
import json
import os
import tempfile
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .library import ArrangementRef
from .sng import Arrangement

# A split song's guitar parted into its lead and its rhythm guitar, where its two guitarists are on either side of
# the stereo (a beta feature: the stem mixer then has Lead and Rhythm for its Guitar). Where each sits is measured
# from its charts: each of the lead's notes the rhythm isn't playing then (the same notes, octaves aside), its
# harmonics' energy in the guitar stem's left channel against its right, and the rhythm's the same way; the song's
# apart when the lead's notes sit to one side (SIDE dB at least, the middle one), the rhythm's to the other, APART
# between them at least (Pursuit of Vikings: the lead +6.8 dB left, the rhythm 7.8 dB right). Parted then by where
# each moment's sound is (a short-time spectrum's each bin: how much is left, how much right): the lead's side's to
# the lead, the rest the rhythm, softly between (no hard edges: fewer artefacts); both stereo as they were, and the
# two together the guitar as it was (the rhythm: the guitar less the lead). Kept beside the song's parts (its
# separator's folder: gone with them, split again), as lead.m4a and rhythm.m4a, and what was measured (sides.json,
# apart or not: measured once).

VERSION = 1
SIDE = 3.0  # dB a part's notes are to its side at least (their median)
APART = 8.0  # dB between the lead's and the rhythm's at least
NOTES = 30  # notes alone each part has at least, to be measured
HARMONICS = 6  # of a note's, its energy
LONGEST, SHORTEST = 0.6, 0.15  # seconds of a note heard (its sustain, within these)
MEASURE_N, MEASURE_HOP = 8192, 1024  # a note's spectrum: its frames
SPLIT_N, SPLIT_HOP = 4096, 1024  # parting it: the frames (a quarter's hop: windowed twice, together 1.5)
SURE = 0.95  # of a bin at a part's own place in the stereo: its share
BLOCK = 256  # frames parted at a time (about 4 MB a channel)
NAMES = ("lead", "rhythm")


@dataclass(frozen=True)
class Sides:
    lead: float  # dB (+: left) the lead's notes alone are to one side, their median
    rhythm: float  # and the rhythm's
    lead_notes: int  # (how many each, alone)
    rhythm_notes: int
    apart: bool  # parted (lead.m4a and rhythm.m4a made)
    version: int = VERSION


def _folder(ref: ArrangementRef, separator: str, cache: Path | None = None) -> Path:
    from . import stems
    return stems._folder(ref, cache, separator)


def kept(ref: ArrangementRef, separator: str, cache: Path | None = None) -> Sides | None:
    """A split song's guitar as measured before (None: not yet, or split again since)."""
    try:
        got = json.loads((_folder(ref, separator, cache) / "sides.json").read_text())
        found = Sides(**got)
    except (OSError, ValueError, TypeError):
        return None
    return found if found.version == VERSION else None


def made(ref: ArrangementRef, separator: str, cache: Path | None = None) -> bool:
    """Whether a split song's guitar is parted into its lead and rhythm."""
    found = kept(ref, separator, cache)
    folder = _folder(ref, separator, cache)
    return found is not None and found.apart and all((folder / f"{name}.m4a").exists() for name in NAMES)


def _notes(arrangement: Arrangement, offset: float) -> list[tuple[float, float, frozenset[int], tuple[int, ...]]]:
    """A part's notes heard (the audio's times: its chart's, offset on): start, end, pitch classes, pitches."""
    out = []
    for n in arrangement.notes:
        if n.pitches:
            start = n.time + offset
            out.append((start, start + min(max(n.sustain, SHORTEST), LONGEST), frozenset(p % 12 for p in n.pitches),
                        tuple(n.pitches)))
    return sorted(out)


def _alone(mine: list[Any], other: list[Any]) -> list[Any]:
    """A part's notes the other part isn't playing the same notes (octaves aside) with."""
    starts = [o[0] for o in other]
    out = []
    for start, end, classes, pitches in mine:
        lo = bisect.bisect_left(starts, start - LONGEST)
        hi = bisect.bisect_left(starts, end)
        if not any(other[i][1] > start and other[i][2] & classes for i in range(lo, hi)):
            out.append((start, end, pitches))
    return out


def _pans(samples: Any, rate: int, notes: Sequence[tuple[float, float, tuple[int, ...]]]) -> list[float]:
    """Each note's harmonics' energy, left against right (dB, + left)."""
    import numpy as np
    window = np.hanning(MEASURE_N).astype(np.float32)
    width = rate / MEASURE_N
    out = []
    for start, end, pitches in notes:
        a, b = int(start * rate), int(end * rate)
        if a < 0 or b > len(samples):
            continue
        seg = samples[a:b].astype(np.float32)
        if len(seg) < MEASURE_N:
            seg = np.pad(seg, ((0, MEASURE_N - len(seg)), (0, 0)))
        count = (len(seg) - MEASURE_N) // MEASURE_HOP + 1
        frames = np.lib.stride_tricks.sliding_window_view(seg, MEASURE_N, axis=0)[::MEASURE_HOP][:count]
        power = (np.abs(np.fft.rfft(frames * window, axis=-1)) ** 2).mean(axis=0)  # (channels x bins)
        bins = sorted({k for p in pitches for h in range(1, HARMONICS + 1)
                       for k in range(int(round(440 * 2 ** ((p - 69) / 12) * h / width)) - 1,
                                      int(round(440 * 2 ** ((p - 69) / 12) * h / width)) + 2)
                       if 0 < k < power.shape[1]})
        left, right = float(power[0, bins].sum()), float(power[1, bins].sum())
        if left + right > 1e-3:
            out.append(10 * float(np.log10((left + 1e-9) / (right + 1e-9))))
    return out


def measure(samples: Any, rate: int, lead: Arrangement, rhythm: Arrangement, offset: float = 0.0) -> Sides:
    """Where a guitar stem's lead and rhythm sit (samples: frames x 2, int16), from their charts (offset: seconds the
    audio is after its chart), and whether they're apart."""
    import numpy as np
    if samples.ndim != 2 or samples.shape[1] != 2:
        return Sides(0.0, 0.0, 0, 0, False)
    leads, rhythms = _notes(lead, offset), _notes(rhythm, offset)
    lead_pans = _pans(samples, rate, _alone(leads, rhythms))
    rhythm_pans = _pans(samples, rate, _alone(rhythms, leads))
    if len(lead_pans) < NOTES or len(rhythm_pans) < NOTES:
        return Sides(0.0, 0.0, len(lead_pans), len(rhythm_pans), False)
    a, b = float(np.median(lead_pans)), float(np.median(rhythm_pans))
    apart = abs(a) >= SIDE and abs(b) >= SIDE and a * b < 0 and abs(a - b) >= APART
    return Sides(round(a, 1), round(b, 1), len(lead_pans), len(rhythm_pans), apart)


def part(samples: Any, lead: float, rhythm: float) -> Any:
    """A guitar stem's lead (frames x 2, int16, as samples), by where each bin of its sound is: as far to the lead's
    side (lead, dB) as the lead sits, all of it the lead's (SURE); as far to the rhythm's, the rhythm's; softly
    between. The rhythm: samples less it."""
    import numpy as np
    n = len(samples)
    place = lambda db: 1 / (1 + 10 ** (-db / 20))  # (a bin's share of it on the left, as far to a side as that)
    at_lead, at_rhythm = place(lead), place(rhythm)
    middle = (at_lead + at_rhythm) / 2
    steep = np.log(SURE / (1 - SURE)) / max(abs(at_lead - middle), 1e-3) * (1 if at_lead > middle else -1)
    window = np.hanning(SPLIT_N + 1)[:-1].astype(np.float32)  # (periodic: overlapped a quarter's hop, even)
    pad = SPLIT_N
    out = np.zeros((n + 2 * pad, 2), dtype=np.float32)
    frames = (n + pad) // SPLIT_HOP + 1
    for f0 in range(0, frames, BLOCK):
        count = min(BLOCK, frames - f0)
        a = f0 * SPLIT_HOP  # (in out's samples: pad before the song's first)
        b = a + (count - 1) * SPLIT_HOP + SPLIT_N
        seg = np.zeros((b - a, 2), dtype=np.float32)
        lo, hi = max(a - pad, 0), min(b - pad, n)
        if hi > lo:
            seg[lo - (a - pad):hi - (a - pad)] = samples[lo:hi] / 32768.0
        framed = np.lib.stride_tricks.sliding_window_view(seg, SPLIT_N, axis=0)[::SPLIT_HOP][:count]  # (f, 2, N)
        spectra = np.fft.rfft(framed * window, axis=-1)
        size = np.abs(spectra)
        left = size[:, 0] / (size[:, 0] + size[:, 1] + 1e-9)
        share = 1 / (1 + np.exp(-steep * (left - middle)))  # (the lead's, each bin)
        back = np.fft.irfft(spectra * share[:, None, :], n=SPLIT_N, axis=-1).astype(np.float32) * window
        for i in range(count):
            at = a + i * SPLIT_HOP
            out[at:at + SPLIT_N] += back[i].T
    out = out[pad:pad + n] / 1.5  # (the windows twice, a quarter's hop: 1.5 together)
    return np.clip(np.round(out * 32768.0), -32768, 32767).astype(np.int16)


def make(ref: ArrangementRef, separator: str, lead: Arrangement, rhythm: Arrangement, offset: float = 0.0,
         cache: Path | None = None) -> Sides:
    """A split song's guitar measured, and parted if its lead and rhythm are apart (kept beside its parts)."""
    import numpy as np

    from . import stems
    folder = _folder(ref, separator, cache)
    with tempfile.TemporaryDirectory(prefix="rs-keylights-sides-") as work_dir:
        work = Path(work_dir)
        wavs = stems.decode(ref, work, cache, separator=separator, parts=("guitar",))
        if wavs is None:
            raise stems.StemsError("The song isn't split")
        with wave.open(str(wavs["guitar"])) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            guitar = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, channels)
        found = measure(guitar, rate, lead, rhythm, offset)
        if found.apart:
            leads = part(guitar, found.lead, found.rhythm)
            rhythms = np.clip(guitar.astype(np.int32) - leads, -32768, 32767).astype(np.int16)
            del guitar
            env = dict(os.environ)
            for name, samples in (("lead", leads), ("rhythm", rhythms)):
                stems._write_wav(work / f"{name}.wav", samples, rate)
                made_now = folder / f".{name}.m4a.new"
                stems._run(["afconvert", "-f", "m4af", "-d", "aac", "-b", str(stems.BITRATE), str(work / f"{name}.wav"),
                            str(made_now)], env, f"Keeping the {name} guitar")
                made_now.replace(folder / f"{name}.m4a")
    temp = folder / f".sides.json.{os.getpid()}"
    temp.write_text(json.dumps(asdict(found)))
    temp.replace(folder / "sides.json")
    return found
