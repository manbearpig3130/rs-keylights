from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

# A split server: tools/rs-keylights-split-server.py, run on another computer of yours with an NVIDIA GPU (it sets
# itself up there): BS-RoFormer SW, many times faster than on the Mac's GPU. A song's audio's sent to it, and its parts
# fetched once they're made, as 16-bit FLAC (lossless: each in line with the song). Each request carries the key it
# prints as it starts.

KEY_HEADER = "X-RS-Keylights-Key"
PORT = 8042  # (its own, unless it's started with another)
POLL = 1.0  # seconds between asking how far it's got
LOST = 60.0  # seconds without an answer, splitting, before it's given up on
SENT, MADE = 0.03, 0.93  # (of a split: the song sent; then the parts made, before they're fetched)


class SplitServerError(Exception):
    """It answered, but couldn't: the wrong key, or the split failed."""


class Unreachable(SplitServerError):
    """It didn't answer: off, asleep, or not at that address."""


def _call(url: str, key: str, path: str, method: str = "GET", data: Any = None, headers: dict[str, str] | None = None,
          timeout: float = 10.0) -> Any:
    """A request to it (the answer, open)."""
    request = urllib.request.Request(url.rstrip("/") + path, data=data, method=method,
                                     headers={KEY_HEADER: key, **(headers or {})})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            said = json.loads(exc.read()).get("error")
        except (ValueError, OSError, AttributeError):
            said = None
        raise SplitServerError(said or f"{exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:  # (refused, timed out, no such name)
        raise Unreachable(str(getattr(exc, "reason", exc))) from exc


def _json(url: str, key: str, path: str, **kwargs: Any) -> dict[str, Any]:
    with _call(url, key, path, **kwargs) as answer:
        return json.loads(answer.read())


def status(url: str, key: str, timeout: float = 3.0) -> dict[str, Any]:
    """What it is and what it runs on (device), and how many songs it has waiting."""
    got = _json(url, key, "/status", timeout=timeout)
    if got.get("server") != "rs-keylights split":
        raise SplitServerError("That isn't an rs-keylights split server")
    return got


def log(url: str, key: str, since: int = 0, timeout: float = 3.0) -> dict[str, Any]:
    """What it's printed since line since (its last few hundred lines: {"lines": [[n, "HH:MM:SS", text]...], "next":
    the last's number}), and the song it's splitting now and how far ("working": {"job", "progress"}, or None). A
    server from before it: SplitServerError."""
    return _json(url, key, f"/log?since={int(since)}", timeout=timeout)


def has_melody(url: str, key: str) -> bool:
    """Whether it tracks a vocal melody too (RMVPE; a server from before it doesn't)."""
    return bool(status(url, key).get("melody"))


def has_kit(url: str, key: str) -> bool:
    """Whether it splits a drum part into the kit's pieces too (DrumSep: kick, snare, toms, hh, ride, crash; a server
    from before it doesn't)."""
    from .stems import KIT
    return all(piece in (status(url, key).get("kit") or []) for piece in KIT)


def split(url: str, key: str, song: Path, parts: Sequence[str], out: Path,
          progress: Callable[[float], None] = lambda _done: None, from_drums: bool = False,
          source: str | None = None) -> dict[str, Path]:
    """A song's parts (song: its audio, a WAV) made there: each part's FLAC (the melody's, melody.json), in out. How
    far along: 0 to 1, to progress (sent by SENT, made by MADE, then fetched). From drums (source "drums"): the audio's
    a drum part already, the parts the kit's pieces (DrumSep's); source "vocals": a vocal part, its melody."""
    source = "drums" if from_drums else source
    said = status(url, key)  # (there, the key right, the parts its: else said before the song's sent)
    known = (said.get("kit") if source == "drums" else ["melody"] if source == "vocals" and said.get("melody")
             else [] if source else said.get("parts")) or []
    if any(part not in known for part in parts):
        said_of = {"drums": " of a drum part", "vocals": " of a vocal part"}.get(source or "", "")
        raise SplitServerError(f"The parts{said_of}: {', '.join(known) or 'none'}")
    with open(song, "rb") as audio:
        job = _json(url, key, f"/split?parts={','.join(parts)}" + (f"&from={source}" if source else ""), method="POST",
                    data=audio,
                    headers={"Content-Type": "audio/wav", "Content-Length": str(song.stat().st_size)},
                    timeout=120)["id"]
    progress(SENT)
    try:
        heard = time.monotonic()
        while True:
            time.sleep(POLL)
            try:
                info = _json(url, key, f"/split/{job}")
            except Unreachable:
                if time.monotonic() - heard > LOST:
                    raise SplitServerError(f"It stopped answering as it split the song (for {LOST:.0f} s)")
                continue
            heard = time.monotonic()
            if info["state"] == "failed":
                raise SplitServerError(info.get("error") or "It couldn't split the song")
            progress(SENT + (MADE - SENT) * float(info.get("progress") or 0.0))
            if info["state"] == "done":
                break
        made = {}
        for k, part in enumerate(parts):
            made[part] = out / (f"{part}.json" if part == "melody" else f"{part}.flac")
            with _call(url, key, f"/split/{job}/{made[part].name}", timeout=60) as answer, open(made[part], "wb") as f:
                while block := answer.read(1 << 20):
                    f.write(block)
            progress(MADE + (1 - MADE) * (k + 1) / len(parts))
        return made
    finally:
        try:
            _call(url, key, f"/split/{job}", method="DELETE", timeout=5).close()
        except SplitServerError:
            pass
