from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

# The guitar's pedal, a Harley Benton DNAfx GiT (the Core too): its presets switched over USB by dnafx-preset
# (~/git/dnafx-preset, installed with: uv pip install --python .venv/bin/python -e ~/git/dnafx-preset), and their
# names from a backup the DNAfx editor saved (a .bhb file). dnafx-preset's own listener (RS Re-ARMed's sidekick,
# following RS Companion's song tones) switches it too: its log says to what.

SLOTS = 200
NEEDS = "Needs dnafx-preset: uv pip install --python .venv/bin/python -e ~/git/dnafx-preset"
BACKUP_FOLDERS = ("Music", "Documents", "Desktop", "Downloads")  # where a backup's looked for (the newest)
BACKUP_PRESETS = b"HB80_PRESET"  # a backup's presets: after this, a record each (its number, then its name)
FIRST_NAME, RECORD, NAME = 0x122, 194, 16  # bytes: the first one's name after it, each record, a name at most
SIDEKICK_LOG = Path.home() / "Library" / "Application Support" / "RS Re-ARMed" / "logs" / "sidekick-dnafx-preset.log"
SWITCHED = re.compile(rb"Switched to slot (\d+)")
LOG_TAIL = 8192  # bytes of its log read, for the last switch
SWITCH = (sys.executable, "-m", "dnafx_preset", "set")  # dnafx-preset switching it (the slot after)
SWITCH_TIMEOUT = 3.0  # seconds it gets


class PedalError(RuntimeError):
    pass


def preset_names(path: Path) -> dict[int, str]:
    """The presets' names in a DNAfx editor's backup, by slot (1 to 200); {} if it isn't one (or can't be read)."""
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    start = data.find(BACKUP_PRESETS)
    if start < 0:
        return {}
    names: dict[int, str] = {}
    for i in range(SLOTS):
        at = start + FIRST_NAME + i * RECORD
        if at + NAME > len(data) or int.from_bytes(data[at - 2:at], "little") != i:  # (its number before its name)
            return names if i else {}
        names[i + 1] = data[at:at + NAME].split(b"\0")[0].decode("latin-1").strip()
    return names


def find_backup(home: Path | None = None) -> Path | None:
    """The newest DNAfx editor backup in the usual folders, or None."""
    home = home or Path.home()
    found: list[tuple[float, Path]] = []
    for folder in BACKUP_FOLDERS:
        try:
            found += [(path.stat().st_mtime, path) for path in (home / folder).glob("*.bhb")]
        except OSError:
            continue
    return max(found)[1] if found else None


def logged_slot(log: Path) -> int | None:
    """The slot dnafx-preset's listener switched to last, from its log (None: none said)."""
    try:
        with open(log, "rb") as file:
            file.seek(max(file.seek(0, os.SEEK_END) - LOG_TAIL, 0))
            said = SWITCHED.findall(file.read())
    except OSError:
        return None
    return int(said[-1]) if said else None


def dnafx_switcher() -> Callable[[int], None]:
    """Switches a DNAfx GiT to a slot with dnafx-preset (its HID transport: no sudo), in a process of its own (and
    killed after SWITCH_TIMEOUT): hidapi keeps Python's lock while it talks to the pedal, so a call that hangs would
    hang every thread here, and on macOS it's only safe from the one thread (switching here, it'd be a new one each
    time)."""

    def switch(slot: int) -> None:
        child = subprocess.Popen([*SWITCH, str(slot)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            out, err = child.communicate(timeout=SWITCH_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            child.kill()
            raise PedalError("The pedal didn't answer: unplug it and plug it in again") from exc
        if child.returncode:
            if "No module named dnafx_preset" in err:
                raise PedalError(NEEDS)
            said = (err or out).strip().splitlines()
            raise PedalError(said[-1].removeprefix("Error: ") if said else f"dnafx-preset failed ({child.returncode})")

    return switch


class Pedal:
    """The pedal's presets: switch(slot) switches it (after a wait: the last one asked for, so turning through them
    switches to the one turned to), back() to the one it was on before. slot is the one it's on as far as it's
    known: the last switched to, here or by dnafx-preset's listener (its log); None, not known."""

    def __init__(self, switcher: Callable[[int], None] | None = None, backup: Path | None = None,
                 log: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self._switcher = switcher or dnafx_switcher()
        self._log, self._clock = log or SIDEKICK_LOG, clock
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._sending = threading.Lock()  # (one switch at a time)
        self._backup, self._names = backup, None  # (read once they're wanted)
        self._slot: int | None = None  # the one it's on, or going to
        self._on: int | None = None  # the one it was last switched to
        self._previous: int | None = None  # and the one before
        self._at = -math.inf  # when it was last switched here (the clock's: the log's times are the same)
        self._log_seen: float | None = None
        self.error: str | None = None

    @property
    def backup(self) -> Path | None:
        """The backup the names are from (the newest found, if none was given)."""
        self._read_names()
        return self._backup

    def use_backup(self, backup: Path | None) -> None:
        """The names from another backup (None: the newest found)."""
        with self._lock:
            self._backup, self._names = backup, None

    def name(self, slot: int | None) -> str:
        return self._read_names().get(slot, "") if slot is not None else ""

    def names(self) -> dict[int, str]:
        return dict(self._read_names())

    @property
    def slot(self) -> int | None:
        with self._lock:
            self._notice()
            return self._slot

    @property
    def previous(self) -> int | None:
        with self._lock:
            self._notice()
            return self._previous

    def switch(self, slot: int, wait: float = 0.0) -> int:
        """Switches to a slot (1 to SLOTS) once wait is up, if no other's asked for by then; the slot."""
        slot = min(max(int(slot), 1), SLOTS)
        with self._lock:
            self._notice()
            self._slot, self._at = slot, self._clock()
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(wait, self._send, args=(slot,))
            self._timer.daemon = True
            self._timer.start()
        return slot

    def back(self) -> int | None:
        """Switches back to the slot it was on before (None: none known)."""
        previous = self.previous
        return None if previous is None else self.switch(previous)

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()

    def _send(self, slot: int) -> None:
        with self._sending:
            with self._lock:
                if slot != self._slot:  # (another asked for since)
                    return
            try:
                self._switcher(slot)
            except Exception as exc:  # (not plugged in, or dnafx-preset missing: said, on the page and the Launchkey)
                with self._lock:
                    self.error = str(exc) or type(exc).__name__
                return
            with self._lock:
                self.error = None
                self._switched(slot)

    def _switched(self, slot: int) -> None:
        if slot != self._on:
            self._previous, self._on = self._on, slot

    def _notice(self) -> None:
        """(Locked.) dnafx-preset's listener switching it since it was switched here: to what its log says."""
        try:
            changed = os.stat(self._log).st_mtime
        except OSError:
            return
        if changed == self._log_seen:
            return
        self._log_seen = changed
        if changed <= self._at:
            return
        slot = logged_slot(self._log)
        if slot is not None and 1 <= slot <= SLOTS:
            self._slot, self._at = slot, changed
            self._switched(slot)

    def _read_names(self) -> dict[int, str]:
        with self._lock:
            if self._names is None:
                if self._backup is None:
                    self._backup = find_backup()
                self._names = preset_names(self._backup) if self._backup is not None else {}
            return self._names
