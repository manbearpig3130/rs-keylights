from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

from .library import ArrangementRef
from .sng import Arrangement, Note, Syllable

# A song's vocal melody, to play on the keys (or light them) as one more of its parts: the vocals' pitch tracked from
# its vocals (RMVPE, on a split server: every 10 ms, with how sure it is), made into notes. Where the song has lyrics
# (its vocals part's syllables), each syllable is a note (or a few, a melisma: its pitch moving on and staying a while),
# at the pitch it's sung at most; without them, its voiced stretches are, split where the pitch moves on. Its times
# the chart's (the song's audio that far off it moved back: chartsync's), so it plays with the other parts. Kept for
# each song (cache_dir), as its notes. (Growled or screamed vocals have no pitch to speak of: those syllables are
# left out, the voice there unsure.)

VERSION = 1
SURE = 0.3  # how sure of a frame's pitch (RMVPE's salience) for it to count as sung
VOICED = 0.4  # of a syllable's frames sung, at least, for it to be a note
SHORTEST = 0.08  # seconds a note is at least (a pitch held shorter: part of the one before or after)
MOVES = 0.6  # semitones from the note's pitch a sung pitch is off for long enough (SHORTEST): a new note
GAP = 0.03  # seconds unsung between two frames still one stretch (no lyrics)
SMOOTH = 5  # frames a sung pitch is smoothed over (its median)


def cache_dir() -> Path:
    return Path.home() / "Library" / "Caches" / "rs-keylights" / "melody"


def _path(ref: ArrangementRef, cache: Path | None = None) -> Path:
    return (cache or cache_dir()) / f"{Path(ref.archive).name}#{ref.song_key}.json"


def kept(ref: ArrangementRef, cache: Path | None = None) -> list[tuple[float, float, int]] | None:
    """A song's melody as made before (start, end, pitch: the chart's times), or None."""
    try:
        got = json.loads(_path(ref, cache).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or got.get("version") != VERSION:
        return None
    return [(float(a), float(b), int(p)) for a, b, p in got.get("notes", [])]


def keep(ref: ArrangementRef, notes: Sequence[tuple[float, float, int]], cache: Path | None = None) -> None:
    path = _path(ref, cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    temp.write_text(json.dumps({"version": VERSION, "notes": [[round(a, 4), round(b, 4), p] for a, b, p in notes]}))
    temp.replace(path)


def arrangement(base: Arrangement, notes: Sequence[tuple[float, float, int]]) -> Arrangement:
    """The song's vocals part (its lyrics, beats and sections) with the melody as its notes."""
    return replace(base, notes=tuple(Note(a, max(b - a, 0.0), (p,)) for a, b, p in notes), tuning=(), capo=0)


def notes_from(f0: Any, sure: Any, hop: float, syllables: Sequence[Syllable] = (), offset: float = 0.0) \
        -> list[tuple[float, float, int]]:
    """The melody's notes from its pitch (f0: Hz every hop seconds, 0 none; sure: how sure of each), at the chart's
    times (the audio offset seconds after its chart): a syllable's (or a sung stretch's) frames, each a note where its
    pitch holds, a new one where it moves on (MOVES, for SHORTEST at least)."""
    import numpy as np
    f0 = np.asarray(f0, dtype=np.float64)
    sung = (f0 > 0) & (np.asarray(sure, dtype=np.float64) >= SURE)
    midi = np.full(len(f0), np.nan)
    midi[sung] = 69 + 12 * np.log2(f0[sung] / 440.0)
    smooth = midi.copy()  # (each sung frame: the median of those around it, sung)
    half = SMOOTH // 2
    for i in np.flatnonzero(sung):
        around = midi[max(i - half, 0):i + half + 1]
        smooth[i] = np.nanmedian(around)
    notes: list[tuple[float, float, int]] = []
    if syllables:
        for s in syllables:
            lo, hi = int(np.floor((s.start + offset) / hop)), int(np.ceil((s.end + offset) / hop))
            frames = np.arange(max(lo, 0), min(max(hi, lo + 1), len(f0)))
            if len(frames) < 2 or sung[frames].mean() < VOICED:
                continue  # (not sung on a pitch: spoken, screamed, or not heard)
            notes += _held(frames, smooth, hop, offset, s.start, s.end)
    else:
        frames = np.flatnonzero(sung)
        gap = max(int(round(GAP / hop)), 1)
        breaks = np.flatnonzero(np.diff(frames) > gap + 1) + 1
        for run in np.split(frames, breaks):
            if len(run) * hop >= SHORTEST:
                notes += _held(run, smooth, hop, offset, run[0] * hop - offset, (run[-1] + 1) * hop - offset)
    return notes


def _held(frames: Any, smooth: Any, hop: float, offset: float, start: float, end: float) -> list[tuple[float, float, int]]:
    """A syllable's (or a stretch's) notes, start to end (the chart's times): its pitch at each held stretch (a move of
    MOVES or more lasting SHORTEST or more: a new one), each at least SHORTEST long."""
    import numpy as np
    pitched = [(int(i), float(smooth[i])) for i in frames if not np.isnan(smooth[i])]
    if not pitched:
        return []
    least = max(int(round(SHORTEST / hop)), 1)
    held: list[list[Any]] = [[pitched[0][0], [pitched[0][1]]]]  # (where each starts, its frames' pitches)
    pending: list[tuple[int, float]] = []
    for i, m in pitched[1:]:
        now = float(np.median(held[-1][1]))
        if abs(m - now) >= MOVES:
            pending.append((i, m))
            if len(pending) >= least and abs(np.median([p for _i, p in pending]) - now) >= MOVES:
                held.append([pending[0][0], [p for _i, p in pending]])
                pending = []
            continue
        held[-1][1] += [p for _i, p in pending] + [m]
        pending = []
    if pending:
        held[-1][1] += [p for _i, p in pending]
    out = []
    for k, (first, pitches) in enumerate(held):
        begin = start if k == 0 else first * hop - offset
        finish = end if k == len(held) - 1 else held[k + 1][0] * hop - offset
        if finish - begin >= SHORTEST or not out:
            out.append((round(begin, 4), round(finish, 4), int(round(float(np.median(pitches))))))
        else:  # (too short: the note before goes on through it)
            out[-1] = (out[-1][0], round(finish, 4), out[-1][2])
    return out


def make(ref: ArrangementRef, vocals: ArrangementRef | None, game_dir: Path, server: dict[str, str] | None,
         offset: float = 0.0, cache: Path | None = None, progress: Callable[[float], None] = lambda _done: None,
         say: Callable[[str], None] = lambda _text: None) -> list[tuple[float, float, int]]:
    """A song's melody made and kept: its vocals' pitch tracked on the split server (its vocals stem sent, if it's
    split; else the song, its vocals separated there first), made into notes along its lyrics (vocals' part's
    syllables), at the chart's times (offset: how far its audio's after its chart)."""
    from . import drums, splitserver, stems
    if server is None:
        raise stems.StemsError("A vocal melody needs a split server (Split on another computer): RMVPE runs there")
    try:
        has = splitserver.status(server["url"], server["key"]).get("melody")
    except splitserver.SplitServerError as exc:
        raise stems.StemsError(f"The split server at {server['url']}: {exc}") from exc
    if not has:
        raise stems.StemsError("The split server's script is older: copy the new one to it (for RMVPE)")
    by = stems.split_with(ref)
    with tempfile.TemporaryDirectory(prefix="rs-keylights-melody-") as work_dir:
        work = Path(work_dir)
        if by:
            say("its vocals, to the split server")
            wav = stems.decode(ref, work, separator=by[0], parts=("vocals",))["vocals"]  # type: ignore[index]
            source = "vocals"
        else:
            say("the song, to the split server (its vocals separated there)")
            wav = work / "song.wav"
            drums.decode(ref, game_dir, wav)
            source = None
        out = work / "out"
        out.mkdir()
        try:
            got = splitserver.split(server["url"], server["key"], wav, ["melody"], out, progress, source=source)
        except splitserver.SplitServerError as exc:
            raise stems.StemsError(f"The split server couldn't track its melody: {exc}") from exc
        notes = notes_from_file(got["melody"], vocals, offset)
    keep(ref, notes, cache)
    return notes


def notes_from_file(path: Path, vocals: ArrangementRef | None, offset: float) -> list[tuple[float, float, int]]:
    """The notes from a split server's melody.json, along the song's lyrics (its vocals part, if it has one)."""
    got = json.loads(Path(path).read_text())
    syllables: tuple[Syllable, ...] = ()
    if vocals is not None:
        try:
            syllables = vocals.load().lyrics
        except Exception:  # (a broken vocals part: by its sung stretches)
            syllables = ()
    return notes_from(got["f0"], got["sure"], float(got["hop"]), syllables, offset)
