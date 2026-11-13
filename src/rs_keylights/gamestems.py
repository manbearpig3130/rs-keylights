from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

# The game's own song, its guitar or bass turned up or down (following): rearmed-mods' Song Stems mod takes the part
# being played out of the song the game plays (the guitar, or the bass with a bass tone), at a level of its own. Its
# levels are lines of RSMods.ini in the game's folder ([Mod Settings] SongStemsGuitarLevel and SongStemsBassLevel, 0
# to 100: 0 the part out, 100 the song as it is), which the mod reads again as the file changes (once a second at
# most): written here, they're heard within a second, the mod ramping to them. Its switch: [Toggle Switches]
# SongStems = on.

INI = "RSMods.ini"
SECTION = "Mod Settings"
SWITCHES = "Toggle Switches"
SWITCH = "SongStems"
LEVELS = {"guitar": "SongStemsGuitarLevel", "bass": "SongStemsBassLevel"}
PARTS = tuple(LEVELS)
FULL = 100  # (a level the file hasn't: the song as it is)

_LINE = re.compile(r"^\s*([^=;#\[]+?)\s*=\s*(.*?)\s*$")
_SECTION = re.compile(r"^\s*\[(.+?)\]\s*$")


def ini_path(game_dir: Path) -> Path:
    return Path(game_dir) / INI


def _entries(text: str) -> list[tuple[str, str, str]]:
    """Each line's section, key and value (as the mod reads them: the last of a key wins, keys any case)."""
    out, section = [], ""
    for line in text.splitlines():
        heading = _SECTION.match(line)
        if heading:
            section = heading.group(1).strip()
            continue
        found = _LINE.match(line)
        if found:
            out.append((section.lower(), found.group(1).strip().lower(), found.group(2)))
    return out


def _value(entries: list[tuple[str, str, str]], section: str, key: str) -> str | None:
    found = [v for s, k, v in entries if s == section.lower() and k == key.lower()]
    return found[-1] if found else None


def read(path: Path) -> dict[str, Any] | None:
    """The mod's switch ("on") and each part's level (0 to 100); None if there's no RSMods.ini (no rearmed-mods)."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    entries = _entries(text)
    out: dict[str, Any] = {"on": (_value(entries, SWITCHES, SWITCH) or "").strip().lower() == "on"}
    for part, key in LEVELS.items():
        try:
            level = int(float(_value(entries, SECTION, key) or FULL))
        except ValueError:
            level = FULL
        out[part] = min(max(level, 0), 100)
    return out


def write(path: Path, levels: dict[str, int]) -> None:
    """Parts' levels (0 to 100) into RSMods.ini: every line of each one's key replaced, or added at the end of its
    section (the section added at the end, if it hasn't one); the rest as it is. Written whole to a new file first
    and put in its place (the mod, and the game's own slider, write it so too)."""
    text = path.read_bytes().decode(errors="replace")  # (as it is: its line endings kept)
    lines = text.splitlines()
    wanted = {LEVELS[part].lower(): (LEVELS[part], min(max(int(level), 0), 100)) for part, level in levels.items()}
    done: set[str] = set()
    section, end_of_section = "", None  # (the line after the section's last setting)
    out: list[str] = []
    for line in lines:
        heading = _SECTION.match(line)
        if heading:
            section = heading.group(1).strip().lower()
            out.append(line)
            if section == SECTION.lower():
                end_of_section = len(out)
            continue
        found = _LINE.match(line)
        if section == SECTION.lower() and found:
            key = found.group(1).strip().lower()
            if key in wanted:
                name, level = wanted[key]
                line = f"{name} = {level}"
                done.add(key)
            out.append(line)
            end_of_section = len(out)
            continue
        out.append(line)
    missing = [f"{name} = {level}" for key, (name, level) in wanted.items() if key not in done]
    if missing and end_of_section is not None:
        out[end_of_section:end_of_section] = missing
    elif missing:
        out += ([""] if out and out[-1].strip() else []) + [f"[{SECTION}]", *missing]
    ending = "\r\n" if "\r\n" in text else "\n"
    handle, temp = tempfile.mkstemp(prefix=".RSMods.", suffix=".ini", dir=path.parent)
    try:
        with os.fdopen(handle, "w", newline="") as f:
            f.write(ending.join(out) + ending)
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
