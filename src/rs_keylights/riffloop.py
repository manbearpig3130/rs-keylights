from __future__ import annotations

import os
import socket
from dataclasses import dataclass

# Rocksmith's Riff Repeater looping a range of our own (the A-B loop, following the game), through RS Re-ARMed's
# riffloop mod (rearmed-sidekicks), which makes it phrases of their own in the song for Riff Repeater to pick (and
# pauses the game's song, or has it go on, types its keys, and reads and moves its song list's highlight): a command a
# datagram on 127.0.0.1, answered with its state.

PORT = int(os.environ.get("RS_RIFFLOOP_PORT", "9939"))
WAIT = 0.25  # seconds an answer's waited for (none: the game isn't running, or hasn't the mod)
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
    return GameLoop(fields.get("hook") == "1", fields.get("riff") == "1", (ends[0], ends[1]) if len(ends) == 2 else None,
                    at, screen if screen and screen != "-" else None, int(keys) if keys.isdigit() else 0)


class RiffLoop:
    """The game's riffloop mod: loop(a, b) has Riff Repeater loop that range, off() its own phrases again; each
    returns what the mod said (None: no answer)."""

    def __init__(self, port: int = PORT, wait: float = WAIT) -> None:
        self.port, self.wait = port, wait

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

    def key(self, name: str) -> GameLoop | None:
        """A key typed in the game, as on the computer's keyboard: KEYS' (its menus' up and down, Escape, Enter...)."""
        return self._send(f"key {name}")

    def status(self) -> GameLoop | None:
        return self._send("status")

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

    def _send(self, command: str) -> GameLoop | None:
        answer = self._ask(command)
        return parse(answer) if answer is not None else None

    def _ask(self, command: str) -> str | None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self.wait)
                sock.connect(("127.0.0.1", self.port))  # (connected: no listener is refused at once)
                sock.send(command.encode())
                return sock.recv(16384).decode(errors="replace")
        except OSError:
            return None
