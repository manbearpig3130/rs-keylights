"""Rocksmith's favourite songs, as the player's profile has them: read, and changed.

The profile (<id>_PRFLDB, RS Re-ARMed's: its Steam stand-in keeps it under steam/<user>/221680/remote) is a 20-byte
header ("EVAS", 1, 8 bytes of the profile's, the JSON's length) and the JSON (null-ended), zlib-compressed (level 9:
the game's own bytes, as it writes them) and AES-256-ECB encrypted with the game's key. Its favourites are
FavoritesListRoot.FavoritesList: songs' keys (a library song's id after its "#").

A change is written only while the game's closed: it keeps its own copy as it runs and writes that back, so one made
meanwhile waits (pending) until it's closed. Only the favourites' list is changed, the rest of the JSON byte for byte;
the profile's saved first (BACKUPS, the newest BACKUPS_KEPT), and read back after to check it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import time
import zlib
from pathlib import Path

PROFILES = Path.home() / "Library" / "Application Support" / "RS Re-ARMed" / "steam"
BACKUPS = Path.home() / "Library" / "Application Support" / "rs-keylights" / "profile-backups"
BACKUPS_KEPT = 20
KEY = bytes.fromhex("728B369E24ED0134768511021812AFC0A3C25D02065F166B4BCC58CD2644F29E")
HEADER = 20
MAGIC = b"EVAS"
LIST = re.compile(rb'"FavoritesList"\s*:\s*\[([^\]]*)\]')
GAME = "Rocksmith2014"  # (the game's process)


class ProfileError(Exception):
    pass


def profile_path(root: Path = PROFILES) -> Path | None:
    """The player's profile: the newest written (a Steam user's, under the game's 221680)."""
    found = [p for p in root.glob("*/221680/remote/*_PRFLDB") if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _cipher():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(KEY), modes.ECB())


def decode(raw: bytes) -> tuple[bytes, bytes]:
    """A profile's header and its JSON (null-ended, as the game writes it)."""
    if raw[:4] != MAGIC or len(raw) < HEADER + 16:
        raise ProfileError("Not a Rocksmith profile")
    body = raw[HEADER:]
    plain = _cipher().decryptor().update(body[: len(body) // 16 * 16])
    try:
        data = zlib.decompressobj().decompress(plain)
    except zlib.error as exc:
        raise ProfileError(f"The profile couldn't be read: {exc}") from exc
    if len(data) != struct.unpack("<I", raw[16:HEADER])[0]:
        raise ProfileError("The profile's length doesn't match its header")
    return raw[:HEADER], data


def encode(header: bytes, data: bytes) -> bytes:
    """A profile from its header (its length set to the JSON's) and its JSON."""
    packed = zlib.compress(data, 9)
    packed += b"\0" * (-len(packed) % 16)
    encryptor = _cipher().encryptor()
    return header[:16] + struct.pack("<I", len(data)) + encryptor.update(packed) + encryptor.finalize()


def favorites_in(data: bytes) -> list[str]:
    found = LIST.search(data)
    if not found:
        return []
    return [json.loads(item) for item in re.findall(rb'"(?:[^"\\]|\\.)*"', found.group(1))]


def with_favorites(data: bytes, keys: list[str]) -> bytes:
    """The JSON with its favourites' list these keys (written as the game writes it), the rest as it was."""
    found = LIST.search(data)
    if not found:
        raise ProfileError("The profile has no favourites' list")
    items = ",\n".join(json.dumps(key) for key in keys)
    listed = b'"FavoritesList" : \n[\n' + items.encode() + (b"\n" if keys else b"") + b"]"
    return data[:found.start()] + listed + data[found.end():]


def read(path: Path) -> list[str]:
    """The favourites' keys in a profile, in its order."""
    return favorites_in(decode(path.read_bytes())[1])


def write(path: Path, keys: list[str], backups: Path | None = None) -> None:
    """A profile's favourites set to these keys: saved first, written whole (a temporary file put in its place),
    and read back."""
    raw = path.read_bytes()
    header, data = decode(raw)
    changed = encode(header, with_favorites(data, keys))
    backups = backups or BACKUPS
    backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved = backups / f"{stamp}_{path.name}"
    n = 1
    while saved.exists():  # (two in a second: each its own, the first never written over)
        saved, n = backups / f"{stamp}-{n}_{path.name}", n + 1
    shutil.copy2(path, saved)
    for old in sorted(backups.glob(f"*_{path.name}"))[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    temp = path.with_name(f".{path.name}.rs-keylights")
    temp.write_bytes(changed)
    os.replace(temp, path)
    back = decode(path.read_bytes())[1]
    if favorites_in(back) != list(keys):
        raise ProfileError("The profile didn't read back as written")


def stats(path: Path) -> dict[str, dict[str, object]]:
    """Each arrangement's play (by its persistent id, upper case): when it was last played (Learn a Song or Score
    Attack, "YYYY-MM-DD HH:MM:SS"), how many times, and its mastery's peak (0 to 1)."""
    data = decode(path.read_bytes())[1]
    profile = json.loads(data.rstrip(b"\0"))
    out: dict[str, dict[str, object]] = {}
    for pid, played in (profile.get("Stats", {}).get("Songs") or {}).items():
        if not isinstance(played, dict) or not played:
            continue
        dates = [d for d in (played.get("DateLAS"), played.get("DateSA")) if isinstance(d, str) and d]
        plays = int(float(played.get("PlayedCount", 0) or 0) + float(played.get("ScoreAttack_PlayCount", 0) or 0))
        mastery = played.get("MasteryPeak")
        out[pid.upper()] = {"played": max(dates) if dates else None, "plays": plays,
                            "mastery": round(float(mastery), 3) if isinstance(mastery, (int, float)) else None}
    return out


def game_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", GAME], capture_output=True).returncode == 0
    except OSError:
        return False


def song_key(sid: str) -> str:
    """A library song's key, as the profile names it (its id after its "#")."""
    return sid.rsplit("#", 1)[-1]
