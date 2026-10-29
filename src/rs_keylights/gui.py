from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import parse_qs, urlparse

from .follow import DEFAULT_OVERLAY_PORT, OverlayClient
from .keyboard import DEFAULT_CHANNEL, DEFAULT_PORT, Keyboard
from .library import NO_NOTES, ArrangementRef, SongIndex
from .lights import LIGHTS_EARLY, MIN_PERIOD
from .session import Following, Part, PlannedPart, Playback, plan, player_for
from .sng import Arrangement
from .sound import CHANNEL_SETS, SoundChoice, Voices, parse_note
from .tones import TONES, Tone, tone

# A page in the browser for what the commands do: the song library, parts and their tones, playback
# with a time slider, following the game, and the keyboard's settings. The server answers on 127.0.0.1
# only, to requests for 127.0.0.1 or localhost (not other names pointing there: DNS rebinding), and
# changes things only for JSON requests from its own page.

DEFAULT_GUI_PORT = 8040
PART_ORDER = ("lead", "rhythm", "combo", "bass")


class GuiError(ValueError):
    """A request that can't be done: the page shows the message."""


def default_settings_file() -> Path:
    return Path.home() / "Library" / "Application Support" / "rs-keylights" / "gui.json"


def song_id(ref: ArrangementRef) -> str:
    return f"{ref.archive}#{ref.song_key}"


def part_order(ref: ArrangementRef) -> tuple[int, str]:
    return (PART_ORDER.index(ref.part) if ref.part in PART_ORDER else len(PART_ORDER), ref.part)


class Gui:
    def __init__(
        self,
        index: SongIndex,
        keyboard_factory: Callable[[str, int], Keyboard] = Keyboard,
        settings_file: Path | None = None,
        overlay_port: int = DEFAULT_OVERLAY_PORT,
        list_ports: Callable[[], list[str]] | None = None,
        other_follow: Callable[[], bool] | None = None,
        overrides: dict[str, Any] | None = None,
    ):
        self.index = index
        self.keyboard_factory = keyboard_factory
        self.settings_file = settings_file or default_settings_file()
        self.overlay_port = overlay_port
        self._list_ports = list_ports or _midi_outputs
        self._other_follow = other_follow or _other_follow_running
        self._lock = threading.RLock()
        self._session: Playback | Following | None = None
        self._playing: dict[str, Any] | None = None  # the song being played: id, title, parts
        self._loaded: OrderedDict[str, list[tuple[ArrangementRef, Arrangement]]] = OrderedDict()
        self._other = (0.0, False)
        self.settings: dict[str, Any] = {"midi_port": DEFAULT_PORT, "channel": DEFAULT_CHANNEL,
                                         "lights_early_ms": LIGHTS_EARLY * 1000, "fastest_flash_ms": MIN_PERIOD * 1000}
        try:
            saved = json.loads(self.settings_file.read_text())
            if isinstance(saved, dict):
                self.settings.update({k: saved[k] for k in self.settings if k in saved})
        except (OSError, ValueError):
            pass
        self.settings.update(overrides or {})  # from the command line: for this run, not saved

    # Library and songs

    def library(self) -> dict[str, Any]:
        songs: dict[str, list[ArrangementRef]] = {}
        for ref in self.index.arrangements():
            if ref.part not in NO_NOTES:
                songs.setdefault(song_id(ref), []).append(ref)
        out = []
        for sid, refs in songs.items():
            named = max(refs, key=lambda r: bool(r.artist))
            out.append({
                "id": sid,
                "artist": named.artist,
                "song": named.song or named.song_key,
                "file": Path(named.archive).name,
                "parts": [r.name or r.part for r in sorted(refs, key=part_order)],
            })
        out.sort(key=lambda s: (s["artist"].casefold(), s["song"].casefold(), s["file"]))
        return {"game_dir": str(self.index.game_dir), "songs": out}

    def song(self, sid: str) -> dict[str, Any]:
        loaded = self._load(sid)
        first = loaded[0][0]
        named = next((r for r, _a in loaded if r.artist), first)
        return {
            "id": sid,
            "artist": named.artist,
            "song": named.song or named.song_key,
            "file": Path(first.archive).name,
            "length": max(a.length for _r, a in loaded),
            "parts": [
                {
                    "part": ref.part,
                    "name": ref.name or ref.part,
                    "bass": ref.bass,
                    "notes": len(a.notes),
                    "tuning": " ".join(f"{t:+d}" if t else "0" for t in a.tuning),
                }
                for ref, a in loaded
            ],
        }

    def _load(self, sid: str) -> list[tuple[ArrangementRef, Arrangement]]:
        with self._lock:
            if sid in self._loaded:
                self._loaded.move_to_end(sid)
                return self._loaded[sid]
        refs = sorted((r for r in self.index.arrangements() if song_id(r) == sid and r.part not in NO_NOTES),
                      key=part_order)
        if not refs:
            raise GuiError("No such song (is its drive connected? try Reindex)")
        loaded = [(ref, ref.load()) for ref in refs]
        with self._lock:
            self._loaded[sid] = loaded
            while len(self._loaded) > 8:
                self._loaded.popitem(last=False)
        return loaded

    def tones(self) -> list[dict[str, Any]]:
        return [{"number": t.number, "name": t.name, "group": t.group} for t in TONES]

    # Playing

    def _plan(self, body: dict[str, Any]) -> tuple[list[PlannedPart], float]:
        """The parts to play from a request, checked and planned, and the song's length."""
        loaded = dict((ref.part, (ref, a)) for ref, a in self._load(str(body.get("song", ""))))
        wanted = body.get("parts") or []
        if not isinstance(wanted, list) or not wanted:
            raise GuiError("Choose a part to play")
        if len(wanted) > len(CHANNEL_SETS):
            raise GuiError(f"{len(CHANNEL_SETS)} parts at most")
        silent = bool(body.get("silent"))
        main: dict[str | None, Tone | None] = {}
        low: dict[str | None, Tone | None] = {}
        mute: dict[str | None, Tone | None] = {}
        split: dict[str | None, int | None] = {}
        parts = []
        for w in wanted:
            name = str(w.get("part", ""))
            if name not in loaded:
                raise GuiError(f"No part {name} in this song")
            lights = _number(w.get("lights", 0), "lit keys", 0, 4)
            ref, arrangement = loaded[name]
            parts.append(Part(ref, arrangement, int(lights)))
            main[name], low[name], mute[name] = _tone(w.get("tone")), _tone(w.get("low_tone")), _tone(w.get("mute_tone"))
            split[name] = _split(w.get("split"))
        if sum(p.lights for p in parts) > 4:
            raise GuiError("The keyboard lights 4 keys at most: give the parts fewer lit keys")
        channel = int(self.settings["channel"])
        if not silent and channel in [c for s in CHANNEL_SETS[: len(parts)] for c in s]:
            raise GuiError(f"The lights' channel ({channel}) is one these parts sound on: choose another in Keyboard")
        choice = None if silent else SoundChoice(main, low, mute, split, bool(body.get("real_pitch")))
        planned = plan(parts, choice)
        length = max([p.arrangement.length for p in parts] + [l.off for pp in planned for l in pp.lights[-1:]])
        return planned, length

    def _describe(self, planned: list[PlannedPart]) -> dict[str, Any]:
        first = planned[0].part.ref
        return {
            "id": song_id(first),
            "title": first.title,
            "parts": [{"part": pp.part.ref.part, "name": pp.part.ref.name,
                       "sound": pp.voices.describe() if pp.voices else "silent",
                       "lights": pp.part.lights} for pp in planned],
        }

    def play(self, body: dict[str, Any]) -> dict[str, Any]:
        planned, length = self._plan(body)
        start = _number(body.get("start", 0), "start", 0, 24 * 3600)
        speed = _number(body.get("speed", 1), "speed", 0.05, 4)
        with self._lock:
            self._stop()
            keyboard = self._open_keyboard()
            try:
                playback = Playback(player_for(planned, keyboard, *self._light_timing()), keyboard, length, start, speed)
            except Exception:
                keyboard.close()
                raise
            playback.start()
            self._session = playback
            self._playing = self._describe(planned)
        return self.state()

    def update(self, body: dict[str, Any]) -> dict[str, Any]:
        """The song playing goes on with other parts, tones or lights, from where it is."""
        planned, length = self._plan(body)
        with self._lock:
            session = self._session
            if not isinstance(session, Playback) or not session.running or not self._playing \
                    or self._playing["id"] != song_id(planned[0].part.ref):
                raise GuiError("This song isn't playing")
            session.replace(player_for(planned, session.keyboard, *self._light_timing()), length)
            self._playing = self._describe(planned)
        return self.state()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if isinstance(self._session, Playback):
                self._session.pause()
        return self.state()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if isinstance(self._session, Playback):
                if self._session.ended:
                    self._session.seek(0)
                self._session.resume()
        return self.state()

    def seek(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if isinstance(self._session, Playback):
                self._session.seek(_number(body.get("position"), "position", 0, 24 * 3600))
        return self.state()

    def speed(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if isinstance(self._session, Playback):
                self._session.set_speed(_number(body.get("speed"), "speed", 0.05, 4))
        return self.state()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop()
        return self.state()

    # Following the game

    def _follow_voices(self, body: dict[str, Any]) -> Callable[[ArrangementRef], Voices] | None:
        if not body.get("sound"):
            return None
        guitar, bass = body.get("guitar") or {}, body.get("bass") or {}
        choice = SoundChoice(
            {None: _tone(guitar.get("tone")), "bass": _tone(bass.get("tone"))},
            {None: _tone(guitar.get("low_tone")), "bass": _tone(bass.get("low_tone"))},
            {None: _tone(guitar.get("mute_tone")), "bass": _tone(bass.get("mute_tone"))},
            {None: _split(guitar.get("split")), "bass": _split(bass.get("split"))},
            bool(body.get("real_pitch")),
        )
        channel = int(self.settings["channel"])
        if channel in CHANNEL_SETS[0]:
            raise GuiError(f"The lights' channel ({channel}) is one the sound plays on: choose another in Keyboard")
        return choice.voices

    def follow(self, body: dict[str, Any]) -> dict[str, Any]:
        voices_for = self._follow_voices(body)
        offset = _number(body.get("offset_ms", 0), "offset", -2000, 2000) / 1000
        with self._lock:
            self._stop()
            keyboard = self._open_keyboard()
            following = Following(OverlayClient(self.overlay_port), self.index, keyboard, voices_for, offset,
                                  *self._light_timing())
            following.start()
            self._session = following
        return self.state()

    def update_follow(self, body: dict[str, Any]) -> dict[str, Any]:
        """Following goes on with other tones (the arrangement playing loads again) or another offset."""
        voices_for = self._follow_voices(body)
        offset = _number(body.get("offset_ms", 0), "offset", -2000, 2000) / 1000
        with self._lock:
            session = self._session
            if not isinstance(session, Following) or not session.running:
                raise GuiError("Not following the game")
            session.set_offset(offset)
            if body.get("tones_changed", True):
                session.set_voices_for(voices_for)
        return self.state()

    # State and settings

    def state(self) -> dict[str, Any]:
        with self._lock:
            session = self._session
            out: dict[str, Any] = {"mode": "idle", "other_follow": self._other_follow_cached()}
            if isinstance(session, Playback):
                if not session.running:
                    self._session = None
                else:
                    out.update(
                        mode="ended" if session.ended else "paused" if session.paused else "playing",
                        position=session.position,
                        length=session.length,
                        speed=session.speed,
                        song=self._playing,
                    )
            elif isinstance(session, Following):
                if not session.running:
                    self._session = None
                    out["follow"] = {"messages": list(session.messages)}
                else:
                    current = session.follower.current
                    out.update(mode="following", follow={
                        "song": f"{current.title} ({current.name})" if current else None,
                        "position": session.follower.position(time.monotonic()),
                        "messages": list(session.messages),
                    })
        return out

    def get_settings(self) -> dict[str, Any]:
        try:
            ports = self._list_ports()
        except Exception:
            ports = []
        return {**self.settings, "ports": ports, "game_dir": str(self.index.game_dir), "overlay_port": self.overlay_port}

    def set_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        port = str(body.get("midi_port", self.settings["midi_port"])).strip()
        channel = int(_number(body.get("channel", self.settings["channel"]), "lights channel", 1, 16))
        early = _number(body.get("lights_early_ms", self.settings["lights_early_ms"]), "lights early", 0, 200)
        flash = _number(body.get("fastest_flash_ms", self.settings["fastest_flash_ms"]), "fastest flash", 0, 300)
        if not port:
            raise GuiError("Choose the keyboard's MIDI output")
        self.settings.update(midi_port=port, channel=channel, lights_early_ms=early, fastest_flash_ms=flash)
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps(self.settings, indent=2) + "\n")
        with self._lock:  # the lights' timing applies at once; the output and channel from the next song
            if isinstance(self._session, (Playback, Following)) and self._session.running:
                self._session.retime_lights(*self._light_timing())
        return self.get_settings()

    def _light_timing(self) -> tuple[float, float]:
        """(lights early, fastest flash) in seconds."""
        return float(self.settings["lights_early_ms"]) / 1000, float(self.settings["fastest_flash_ms"]) / 1000

    def reindex(self) -> dict[str, Any]:
        self.index.refresh()
        with self._lock:
            self._loaded.clear()
        return self.library()

    def close(self) -> None:
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        if self._session is not None:
            self._session.stop()
        self._session = None
        self._playing = None

    def _open_keyboard(self) -> Keyboard:
        keyboard = self.keyboard_factory(str(self.settings["midi_port"]), int(self.settings["channel"]))
        keyboard.open()
        return keyboard

    def _other_follow_cached(self) -> bool:
        checked, running = self._other
        if time.monotonic() - checked > 3:
            try:
                running = self._other_follow()
            except Exception:
                running = False
            self._other = (time.monotonic(), running)
        return running


def _number(value: Any, what: str, lowest: float, highest: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise GuiError(f"Not a number for {what}: {value!r}") from None
    if not lowest <= number <= highest:
        raise GuiError(f"The {what} must be {lowest:g} to {highest:g}")
    return number


def _tone(value: Any) -> Tone | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return tone(int(value))
    except (KeyError, TypeError, ValueError):
        raise GuiError(f"No tone {value!r}") from None


def _split(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return parse_note(str(value))
    except ValueError as exc:
        raise GuiError(str(exc)) from None


def _midi_outputs() -> list[str]:
    import rtmidi

    out = getattr(rtmidi, "MidiOut")()
    try:
        return list(out.get_ports())
    finally:
        del out


def _other_follow_running() -> bool:
    """Another `rs-keylights follow` (e.g. the RS Re-ARMed sidekick) would light the keys too."""
    found = subprocess.run(["pgrep", "-f", "rs-keylights follow"], capture_output=True, text=True)
    return any(pid.strip() and int(pid) != os.getpid() for pid in found.stdout.split())


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, gui: Gui, port: int = DEFAULT_GUI_PORT):
        self.gui = gui
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


class _Handler(BaseHTTPRequestHandler):
    @property
    def _server(self) -> GuiServer:
        return cast(GuiServer, self.server)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (the base class's name)
        pass

    def _hosts(self) -> set[str]:
        return {f"127.0.0.1:{self._server.port}", f"localhost:{self._server.port}"}

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: Any) -> None:
        self._send(status, json.dumps(value).encode(), "application/json")

    def _allowed(self) -> bool:
        if self.headers.get("Host") not in self._hosts():
            self._json(403, {"error": "Use http://127.0.0.1 or http://localhost"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 (the base class's name)
        if not self._allowed():
            return
        url = urlparse(self.path)
        gui = self._server.gui
        try:
            if url.path == "/":
                page = files("rs_keylights").joinpath("gui.html").read_bytes()
                self._send(200, page, "text/html; charset=utf-8")
            elif url.path == "/api/library":
                self._json(200, gui.library())
            elif url.path == "/api/song":
                self._json(200, gui.song(parse_qs(url.query).get("id", [""])[0]))
            elif url.path == "/api/tones":
                self._json(200, gui.tones())
            elif url.path == "/api/state":
                self._json(200, gui.state())
            elif url.path == "/api/settings":
                self._json(200, gui.get_settings())
            else:
                self._json(404, {"error": "Not found"})
        except Exception as exc:  # shown on the page
            self._json(400 if isinstance(exc, (GuiError, ValueError, OSError)) else 500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802 (the base class's name)
        if not self._allowed():
            return
        origin = self.headers.get("Origin")
        if origin and origin.removeprefix("http://") not in self._hosts():
            self._json(403, {"error": "Not from this page"})
            return
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            self._json(415, {"error": "JSON only"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise GuiError("Expected a JSON object")
        except (ValueError, GuiError) as exc:
            self._json(400, {"error": f"Bad request: {exc}"})
            return
        gui = self._server.gui
        routes: dict[str, Callable[[], Any]] = {
            "/api/play": lambda: gui.play(body),
            "/api/pause": gui.pause,
            "/api/resume": gui.resume,
            "/api/seek": lambda: gui.seek(body),
            "/api/speed": lambda: gui.speed(body),
            "/api/stop": gui.stop,
            "/api/follow": lambda: gui.follow(body),
            "/api/update": lambda: gui.update(body),
            "/api/follow/update": lambda: gui.update_follow(body),
            "/api/settings": lambda: gui.set_settings(body),
            "/api/reindex": gui.reindex,
        }
        route = routes.get(urlparse(self.path).path)
        if route is None:
            self._json(404, {"error": "Not found"})
            return
        try:
            self._json(200, route())
        except Exception as exc:  # shown on the page
            self._json(400 if isinstance(exc, (GuiError, ValueError, OSError)) else 500, {"error": str(exc)})
