from __future__ import annotations

import os
import socket
import threading
from dataclasses import dataclass
from typing import Any, Sequence

# Rocksmith's Riff Repeater looping a range of our own (the A-B loop, following the game), through RS Re-ARMed's
# riffloop mod (rearmed-sidekicks), which makes it phrases of their own in the song for Riff Repeater to pick (and
# pauses the game's song, or has it go on, types its keys, and reads and moves its song list's highlight): a command a
# datagram on 127.0.0.1, answered with its state.

PORT = int(os.environ.get("RS_RIFFLOOP_PORT", "9939"))
WAIT = 0.25  # seconds an answer's waited for (none: the game isn't running, or hasn't the mod)
TIME_WAIT = 0.02  # and the time's, asked many times a second (a game this slow: its time's no use)
KEYS = ("up", "down", "left", "right", "enter", "escape", "pageup", "pagedown")  # the keys it types


@dataclass(frozen=True)
class GameLoop:
    """What the mod said: whether the game has the hook points it needs (not an RS Re-ARMed with mods: no), whether
    the range is phrases of their own in the song now, for Riff Repeater to pick, the range (None: none), where
    the game's song is, exactly (seconds; None: the mod hasn't seen its playback yet), the game's screen (None:
    not seen yet, or a mod without it), and how many keys it has still to type there."""
    hook: bool
    riff: bool
    loop: tuple[float, float] | None
    time: float | None = None
    screen: str | None = None
    keys: int = 0  # keys waiting to be typed in the game
    speed: float | None = None  # Riff Repeater's speed, percent, as the game has it (None: not known)
    accelerator: bool | None = None  # its Accelerator, as the game has it (its menu's switch; None: not known)
    music: float | None = None  # the game's Music volume (its mixer's, 0 to 100); None: not known (yet), or an older mod

    @property
    def playing(self) -> bool:
        """On a song's screen, playing it (LearnASong_Game, NonStopPlay_Game...: in Riff Repeater too;
        LearnASong_RiffRepeater is its menu)."""
        return self.screen is not None and self.screen.endswith("_Game")

    @property
    def paused(self) -> bool:
        """On a song's pause screen (LearnASong_Pause, RiffRepeater_Pause...)."""
        return self.screen is not None and self.screen.endswith("_Pause")


@dataclass(frozen=True)
class GameSongs:
    """Learn a Song's song list as the game has it: the highlighted song's row (from 0), how many rows there are, and
    its key (the song's, e.g. "3DooAway")."""
    row: int
    count: int
    key: str


@dataclass(frozen=True)
class NoteCounts:
    """The player's note counts as the game has them (its note statistics, through the mod): the run of the song
    they're of (a new one each time a song starts again, its counts from 0; 0: none yet), the next change's number,
    the counts now (None: no statistics now, the song left), the arrangement playing (its .sng's name; None: none),
    and the changes asked for: each one's number, run, the song's time then (None: no song's playback seen), and the
    hits and misses then."""
    run: int
    next: int
    hits: int | None
    misses: int | None
    streak: int | None
    best: int | None
    arrangement: str | None
    changes: tuple[tuple[int, int, float | None, int, int], ...]


def parse_notes(answer: str) -> NoteCounts | None:
    words = answer.split()
    if not words or words[0] != "notes":
        return None
    try:
        fields = dict(word.split("=", 1) for word in words[1:] if "=" in word)
        changes = []
        for word in words[1:]:
            if "=" not in word:
                number, run, at, hits, misses = word.split(":")
                changes.append((int(number), int(run), None if float(at) < 0 else float(at), int(hits), int(misses)))

        def count(name: str) -> int | None:
            value = int(fields[name])
            return None if value < 0 else value
        return NoteCounts(int(fields["run"]), int(fields["next"]), count("hits"), count("misses"), count("streak"),
                          count("best"), None if fields.get("arr", "-") == "-" else fields["arr"], tuple(changes))
    except (KeyError, ValueError):
        return None


def parse(answer: str) -> GameLoop | None:
    """The mod's answer ("riffloop hook=1 riff=0 loop=12.500,20.000 calls=3"); None if it isn't one."""
    words = answer.split()
    if not words or words[0] != "riffloop":
        return None
    fields = dict(word.split("=", 1) for word in words[1:] if "=" in word)
    try:
        ends = [float(x) for x in fields["loop"].split(",")] if fields["loop"] != "-" else []
    except (KeyError, ValueError):
        return None
    try:
        at = float(fields["t"]) if fields.get("ctl") == "1" else None
    except (KeyError, ValueError):
        at = None
    screen, keys = fields.get("screen"), fields.get("keys", "0")
    try:
        music = float(fields["music"])
    except (KeyError, ValueError):
        music = None
    try:
        speed = float(fields["speed"])
    except (KeyError, ValueError):
        speed = -1.0
    return GameLoop(fields.get("hook") == "1", fields.get("riff") == "1", (ends[0], ends[1]) if len(ends) == 2 else None,
                    at, screen if screen and screen != "-" else None, int(keys) if keys.isdigit() else 0,
                    speed if speed > 0 else None, {"1": True, "0": False}.get(fields.get("accel", "")), music)


class RiffLoop:
    """The game's riffloop mod: loop(a, b) has Riff Repeater loop that range, off() its own phrases again; each
    returns what the mod said (None: no answer)."""

    def __init__(self, port: int = PORT, wait: float = WAIT) -> None:
        self.port, self.wait = port, wait
        self._time_socket: socket.socket | None = None  # (the time's own, kept: it's asked 50 times a second)
        self._time_lock = threading.Lock()

    def loop(self, a: float, b: float) -> GameLoop | None:
        return self._send(f"loop {a:.3f} {b:.3f}")

    def off(self) -> GameLoop | None:
        return self._send("off")

    def speed(self, percent: float) -> GameLoop | None:
        """Riff Repeater's speed (its Accelerator's start, and where it is)."""
        return self._send(f"speed {percent:g}")

    def accelerator(self, on: bool) -> GameLoop | None:
        """Riff Repeater's Accelerator on or off: a little faster each time through."""
        return self._send(f"accel {'on' if on else 'off'}")

    def passes(self, count: int) -> GameLoop | None:
        """Times through before each speed-up."""
        return self._send(f"passes {count}")

    def pause(self) -> GameLoop | None:
        """The song paused, as the game pauses it when it loses focus (on a song's screen only)."""
        return self._send("pause")

    def resume(self) -> GameLoop | None:
        """The song going on, as Escape does on its pause screen (on that screen only)."""
        return self._send("resume")

    def riff(self) -> GameLoop | None:
        """Riff Repeater's settings opened over the song, as the game's own Riff Repeater key opens them (on a song's
        screen only; a range set: once it's phrases of its own and picked, so its menu opens on them, for its Start)."""
        return self._send("riff")

    def kmix(self, rows: Sequence[tuple[Any, ...]]) -> GameLoop | None:
        """RS-Keylights' mixers in the game (the mod's menus, from Learn a Song's pause menu): its rows, each an id, a
        percent (0 to 100), whether it's muted, a label and (for the Launchkey-like mixer) its lit keys (-1: none to
        light), kind (lead, rhythm, bass, drums, keys, music, all, stem:bass...) and column (0-7 its tone mixer
        page, 8-15 its stem mixer page; -1 none); sent as they change (the mod's follow)."""
        def label(text: str) -> str:
            return "".join(c for c in text if c not in ",;\n").strip()[:30] or "?"
        return self._send("kmix " + ";".join(
            f"{row[0]},{row[1]:.1f},{int(row[2])},{label(row[3])}" + (f",{int(row[4])},{label(row[5])}" if len(row) > 5 else "")
            + (f",{int(row[6])}" if len(row) > 6 else "")
            for row in rows))

    def kpage(self, page: str) -> GameLoop | None:
        """Its MIXER on a page ("parts": the tone mixer, "stems": the stem mixer), as the Launchkey's mixer went to
        it (shown if it's open)."""
        return self._send(f"kpage {page}")

    def ktones(self, rows: Sequence[tuple[Any, ...]]) -> GameLoop | None:
        """Its TONES page: its rows, each an id, a label and the tone it plays (as shown: "NUMBER|GROUP|NAME"), and
        (laid out as the Launchkey's) its kind, whether it follows the song's tone changes (1, 0; -1 none), its
        column and the knobs' page (0 tone, 1 palm mutes, 2 harmonics); sent as they change."""
        def text(value: str, most: int = 40) -> str:
            return "".join(c for c in str(value) if c not in ",;\n").strip()[:most] or "?"
        return self._send("ktones " + ";".join(
            f"{row[0]},{text(row[1])},{text(row[2], 47)}" +
            (f",{text(row[3], 11)},{int(row[4])},{int(row[5])},{int(row[6])}" if len(row) > 6 else "") for row in rows))

    def kmixed(self) -> dict[str, float | str] | None:
        """What was done in the MIXER or TONES since it was last asked: {id: percent} for a fader moved,
        {id: "mute"}, {id: "lights"} or {id: "follow"} for a pad pressed (to turn over), {"page": "parts" or
        "stems"} for the MIXER's page turned (the last), {"field": N} for TONES' page (the last), {id: "tone:N"} for
        a TONES column's ▲ ▼ (its tone N steps on, the presses added up); None: no answer."""
        words = (self._ask("kmixed") or "").split()
        if not words or words[0] != "kmixed":
            return None
        moved: dict[str, float | str] = {}
        for word in words[1:]:
            key, _, value = word.partition("=")
            if value in ("mute", "lights", "follow") or key == "page" and value in ("parts", "stems"):
                moved[key] = value
                continue
            if value.startswith("tone:"):
                try:
                    steps = int(value[5:])
                except ValueError:
                    continue
                was = moved.get(key)
                moved[key] = f"tone:{steps + (int(was[5:]) if isinstance(was, str) and was.startswith('tone:') else 0)}"
                continue
            try:
                moved[key] = float(value)
            except ValueError:
                continue
        return moved

    def key(self, name: str) -> GameLoop | None:
        """A key typed in the game, as on the computer's keyboard: KEYS' (its menus' up and down, Escape, Enter...)."""
        return self._send(f"key {name}")

    def music(self, level: float) -> GameLoop | None:
        """The game's Music volume (its mixer's: the song), 0 to 100, at once."""
        return self._send(f"music {level:g}")

    def status(self) -> GameLoop | None:
        return self._send("status")

    def time(self, wait: float = TIME_WAIT) -> tuple[float | None, str | None, str | None] | None:
        """Where the game's song is, exactly (its playback's own time; None: no song's playback seen), its screen
        (None: not seen yet), and the arrangement playing (its .sng's name, e.g. amonamarthwayofvikings_lead; None:
        none; "": a mod that doesn't say), asked many times a second, following it: nothing else done. None: no
        answer (no game, or a mod without it)."""
        words = (self._ask_time(wait) or "").split()
        if len(words) not in (3, 4) or words[0] != "time":
            return None
        fields = dict(word.split("=", 1) for word in words[1:] if "=" in word)
        try:
            timer = None if fields.get("t", "-") == "-" else float(fields["t"])
        except ValueError:
            return None
        screen, arrangement = fields.get("screen", "-"), fields.get("arr", "")
        return timer, None if screen == "-" else screen, None if arrangement == "-" else arrangement

    def songs(self) -> GameSongs | None:
        """Learn a Song's song list (while it shows): its highlighted song; None without it (or the mod)."""
        words = (self._ask("songs") or "").split()
        if len(words) != 4 or words[0] != "songs":
            return None
        fields = dict(word.split("=", 1) for word in words[1:] if "=" in word)
        try:
            return GameSongs(int(fields["row"]), int(fields["of"]), fields["key"])
        except (KeyError, ValueError):
            return None

    def song_keys(self, start: int, count: int) -> tuple[int, list[str]] | None:
        """The song list's keys (in its order) from a row, and how many rows it has; None without it."""
        words = (self._ask(f"songs list {start} {count}") or "").split()
        if len(words) < 2 or words[0] != "songs" or not words[1].startswith("of="):
            return None
        return int(words[1][3:]), words[2:]

    def focus(self, key: str) -> bool:
        """The song with a key highlighted in the song list, as the game's own moves do; whether it answered."""
        return (self._ask(f"focus {key}") or "").startswith("songs focusing")

    def notes(self, since: int) -> NoteCounts | None:
        """The player's note counts, and their changes from the one numbered since (the mod keeps the last 4096);
        None: no answer (no game, or a mod without them)."""
        answer = self._ask(f"notes {since}")
        return parse_notes(answer) if answer is not None else None

    def _send(self, command: str) -> GameLoop | None:
        answer = self._ask(command)
        return parse(answer) if answer is not None else None

    def _ask_time(self, wait: float) -> str | None:
        """The time command asked on a socket kept for it (answers come late, after their wait, dropped first)."""
        with self._time_lock:
            sock = self._time_socket
            try:
                if sock is None:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.connect(("127.0.0.1", self.port))
                    self._time_socket = sock
                sock.setblocking(False)
                while True:  # (late answers, and refusals of earlier asks with no listener: dropped)
                    try:
                        sock.recv(16384)
                    except (BlockingIOError, InterruptedError):
                        break
                    except OSError:
                        continue
                sock.settimeout(wait)
                sock.send(b"time")
                return sock.recv(16384).decode(errors="replace")
            except OSError:
                if sock is not None:
                    sock.close()
                self._time_socket = None
                return None

    def _ask(self, command: str, wait: float | None = None) -> str | None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self.wait if wait is None else wait)
                sock.connect(("127.0.0.1", self.port))  # (connected: no listener is refused at once)
                sock.send(command.encode())
                return sock.recv(16384).decode(errors="replace")
        except OSError:
            return None
