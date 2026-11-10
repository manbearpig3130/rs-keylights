from __future__ import annotations

import json
import threading
import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from glob import escape
from pathlib import Path
from typing import Callable, Sequence

from .riffloop import RiffLoop
from .sng import Note

# What you hit and missed as you play: the game counts your hits and misses (its note statistics, read each frame by
# RS Re-ARMed's riffloop mod, each change with the song's time), and a run's changes are kept here as you play, a
# file each (the arrangement, when it started, and each change: the song's time, hits and misses then). Matched to
# the chart's notes (judge), they say which notes you hit and missed: each hit counted is the note nearest its time,
# each miss the earliest note not counted yet before it (the game counts a miss once its note's gone by).

PLAYS_VERSION = 1
POLL = 0.5  # seconds between looks at the counts (the mod keeps 4096 changes: none lost meanwhile)
HIT_EARLY = 0.25  # seconds a hit's counted before its note at most (played early), and after it (late)
HIT_LATE = 0.35
MISS_LATE = 1.0  # seconds a miss is counted after its note at most (and before it: none, but a frame's)
MISS_EARLY = 0.05
PASS_BACK = 1.0  # seconds the song's time goes back: another pass (Riff Repeater), its notes counted again


def plays_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "rs-keylights" / "plays"


@dataclass
class Run:
    """A run of a song as the game counted it: its arrangement (its .sng's name; None: not seen), when it started
    (seconds since the epoch), and each change of the counts: the song's time (None: not known), hits and misses."""
    arrangement: str | None
    started: float
    changes: list[tuple[float | None, int, int]] = field(default_factory=list)
    file: Path | None = None

    @property
    def counts(self) -> tuple[int, int]:
        return (self.changes[-1][1], self.changes[-1][2]) if self.changes else (0, 0)

    @property
    def played(self) -> bool:
        return any(hits or misses for _at, hits, misses in self.changes)

    def to_json(self) -> dict:
        return {"version": PLAYS_VERSION, "arrangement": self.arrangement, "started": round(self.started, 3),
                "changes": [[None if at is None else round(at, 3), hits, misses] for at, hits, misses in self.changes]}


def read_run(path: Path) -> Run | None:
    try:
        data = json.loads(path.read_text())
        if data.get("version") != PLAYS_VERSION:
            return None
        return Run(data.get("arrangement"), float(data["started"]),
                   [(None if at is None else float(at), int(hits), int(misses)) for at, hits, misses in data["changes"]],
                   path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def runs_of(arrangements: Sequence[str], folder: Path | None = None, most: int = 20) -> list[Run]:
    """The runs kept of these arrangements (by their .sng's names), the newest first."""
    folder = folder or plays_dir()
    paths = {p for name in arrangements for p in folder.glob(f"*/*-{escape(name.lower())}.json")}
    runs = [run for run in (read_run(p) for p in paths) if run is not None]
    return sorted(runs, key=lambda r: r.started, reverse=True)[:most]


class Recorder:
    """The counts' changes asked of the mod (poll: in a thread of its own, started), each run kept in a file as it
    goes (plays/DATE/TIME-ARRANGEMENT.json), once something's counted in it."""

    def __init__(self, riff_loop: RiffLoop, folder: Path | None = None, clock: Callable[[], float] = time.time):
        self._riff_loop = riff_loop
        self._folder = folder
        self._clock = clock
        self._next: int | None = None  # (the mod's next change; None: not asked yet)
        self._runs: dict[int, Run] = {}  # (its runs being kept, by its number for them)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.changed = 0  # (how many times something was kept: the page asks again when it's more)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rs-keylights plays", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)

    def _run(self) -> None:
        while not self._stop.wait(POLL):
            try:
                self.poll()
            except Exception:  # (the next look tries again)
                pass

    def poll(self) -> bool:
        """One look: the changes since the last kept; whether there were any."""
        first = self._next is None  # (the first look: the run going on, not those before it, kept already)
        counts = self._riff_loop.notes(self._next or 0)
        if counts is None:
            return False
        if self._next is not None and counts.next < self._next:  # (the mod started again: its numbers from 0)
            self._next, self._runs = 0, {}
            return False
        touched: set[int] = set()
        for _number, run, at, hits, misses in counts.changes:
            if first and run != counts.run:
                continue
            kept = self._runs.get(run)
            if kept is None:
                kept = self._runs[run] = Run(counts.arrangement, self._clock())
                for old in sorted(self._runs)[:-2]:  # (two kept: the one going on, and one just before)
                    del self._runs[old]
            kept.arrangement = kept.arrangement or counts.arrangement
            kept.changes.append((at, hits, misses))
            touched.add(run)
        self._next = counts.next
        for run in touched:
            if run in self._runs and self._runs[run].played:
                self._save(self._runs[run])
        if touched:
            self.changed += 1
        return bool(touched)

    def _save(self, run: Run) -> None:
        if run.file is None:
            started = datetime.fromtimestamp(run.started)
            run.file = (self._folder or plays_dir()) / started.strftime("%Y-%m-%d") / (
                f"{started.strftime('%H%M%S')}-{(run.arrangement or 'unknown').lower()}.json")
            run.file.parent.mkdir(parents=True, exist_ok=True)
        temp = run.file.with_suffix(".tmp")
        temp.write_text(json.dumps(run.to_json()))
        temp.replace(run.file)


@dataclass(frozen=True)
class Judged:
    note: int  # the note's index (in the arrangement's notes)
    hit: bool
    at: float  # the song's time the game counted it


@dataclass(frozen=True)
class Judgement:
    judged: tuple[Judged, ...]  # (a note counted again on another pass: again)
    hits: int  # the game's counts at the end
    misses: int
    unmatched: int  # counted with no note found for it
    passes: int  # times through (Riff Repeater's: more than one)


def judge(notes: Sequence[Note], changes: Sequence[tuple[float | None, int, int]]) -> Judgement:
    """Which notes a run's counts were of: each hit the note nearest it not counted yet (HIT_LATE before it to
    HIT_EARLY after), each miss the earliest not counted yet (up to MISS_LATE before it); its first counts as they
    were when the run was found (hits before that aren't known). The song going back (Riff Repeater's passes): its
    notes counted again."""
    times = [n.time for n in notes]
    counted = [False] * len(notes)
    judged: list[Judged] = []
    unmatched, passes, last_at = 0, 1, None
    hits_was, misses_was = changes[0][1:] if changes else (0, 0)
    for at, hits, misses in changes:
        new_hits, new_misses = hits - hits_was, misses - misses_was
        hits_was, misses_was = hits, misses
        if at is None or new_hits < 0 or new_misses < 0:
            continue
        if last_at is not None and at < last_at - PASS_BACK:
            counted, passes = [False] * len(notes), passes + 1
        last_at = at
        for _ in range(new_misses):
            lo, hi = bisect_left(times, at - MISS_LATE), bisect_right(times, at + MISS_EARLY)
            found = next((i for i in range(lo, hi) if not counted[i]), None)
            if found is None:
                unmatched += 1
                continue
            counted[found] = True
            judged.append(Judged(found, False, at))
        for _ in range(new_hits):
            lo, hi = bisect_left(times, at - HIT_LATE), bisect_right(times, at + HIT_EARLY)
            when = at
            found = min((i for i in range(lo, hi) if not counted[i]), key=lambda i: abs(times[i] - when), default=None)
            if found is None:
                unmatched += 1
                continue
            counted[found] = True
            judged.append(Judged(found, True, at))
    end = changes[-1] if changes else (None, 0, 0)
    return Judgement(tuple(judged), end[1], end[2], unmatched, passes)
