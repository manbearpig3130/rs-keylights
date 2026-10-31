from __future__ import annotations

from typing import Iterable, Protocol, Sequence

from .player import Player

# A show on the keys (a scale climbing them) where a song leaves them free: it gives way a moment before the
# song's next lights, and stops for a key pressed on the keyboard.

SHOW_GAP = 0.15  # seconds before the song's next lights that a show gives way


class ShowKeys(Protocol):
    @property
    def lit(self) -> Iterable[int]:
        ...

    def light(self, keys: tuple[int, ...]) -> None:
        ...

    def unlight(self, keys: tuple[int, ...]) -> None:
        ...


class KeyShow:
    def __init__(self, frames: Sequence[tuple[float, tuple[int, ...]]], repeat: bool = True):
        self.frames = list(frames)  # (seconds into it, the keys lit); the last one's time is its length
        self.repeat = repeat
        self.done = not self.frames
        self._began: float | None = None
        self._lit: tuple[int, ...] = ()

    def step(self, keys: ShowKeys, now: float, player: Player | None = None, song: float | None = None) -> None:
        """Lights the frame due (song: the song's position while it plays, to give way to its lights)."""
        if self.done:
            return
        if self._began is None:
            self._began = now
            _pressed(keys)  # keys pressed before it began don't stop it
        elif _pressed(keys):
            self.end(keys)
            return
        if player is not None and song is not None and self._song_needs_keys(keys, player, song):
            self.end(keys)
            return
        elapsed, length = now - self._began, self.frames[-1][0]
        if elapsed >= length:
            if not self.repeat or length <= 0:
                self.end(keys)
                return
            elapsed %= length
        frame = next((lit for at, lit in reversed(self.frames) if at <= elapsed), ())
        if frame != self._lit:
            keys.unlight(tuple(k for k in self._lit if k not in frame))
            keys.light(tuple(k for k in frame if k not in self._lit))
            self._lit = frame

    def end(self, keys: ShowKeys) -> None:
        if self._lit:
            keys.unlight(self._lit)
        self._lit, self.done = (), True

    def _song_needs_keys(self, keys: ShowKeys, player: Player, song: float) -> bool:
        """The song lights keys now or in a moment (or will, as it plays on from a pause)."""
        upcoming = player.next_time()
        if upcoming is not None and upcoming - song < SHOW_GAP:
            return True
        if set(keys.lit) - set(self._lit):
            return True
        return player.position is None and bool(player.keys_at(song))


def _pressed(keys: ShowKeys) -> bool:
    pressed = getattr(keys, "pressed", None)
    return bool(pressed()) if callable(pressed) else False
