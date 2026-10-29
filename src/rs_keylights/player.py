from __future__ import annotations

import time
from bisect import bisect_right
from typing import Callable, Protocol, Sequence

from .lights import Light

SEEK_BACK = 0.25  # seconds; going back further than this is a seek (riff repeat, restart)
SEEK_AHEAD = 1.0  # going forward further than this is a seek too, not a burst of notes


class KeysProtocol(Protocol):
    def light(self, keys: tuple[int, ...]) -> None:
        ...

    def unlight(self, keys: tuple[int, ...]) -> None:
        ...

    def all_off(self) -> None:
        ...


class Player:
    """Lights the keys for a song position that moves forward (and now and then jumps)."""

    def __init__(self, lights: Sequence[Light], keys: KeysProtocol):
        self.lights = list(lights)
        self.keys = keys
        self._ons = [light.on for light in self.lights]
        # (time, 0 for off / 1 for on, keys): at the same time an off comes before an on
        self._actions = sorted(
            [(light.off, 0, light.keys) for light in self.lights] + [(light.on, 1, light.keys) for light in self.lights]
        )
        self._times = [action[0] for action in self._actions]
        self._next = 0
        self._position: float | None = None

    @property
    def position(self) -> float | None:
        return self._position

    def next_time(self) -> float | None:
        return self._times[self._next] if self._next < len(self._times) else None

    def advance(self, position: float) -> None:
        if self._position is None or position < self._position - SEEK_BACK or position > self._position + SEEK_AHEAD:
            self.seek(position)
            return
        if position <= self._position:
            return  # a little back: the clock's estimate moved, not the song
        while self._next < len(self._actions) and self._actions[self._next][0] <= position:
            _time, on, keys = self._actions[self._next]
            if on:
                self.keys.light(keys)
            else:
                self.keys.unlight(keys)
            self._next += 1
        self._position = position

    def seek(self, position: float) -> None:
        self.keys.all_off()
        self._next = bisect_right(self._times, position)
        current = bisect_right(self._ons, position) - 1
        if current >= 0 and self.lights[current].off > position:
            self.keys.light(self.lights[current].keys)
        self._position = position

    def stop(self) -> None:
        if self._position is not None:
            self.keys.all_off()
        self._position = None

    def done(self) -> bool:
        return self._next >= len(self._actions)


def play(
    player: Player,
    start: float = 0.0,
    speed: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Plays from a song position in real time (times speed) to the end."""
    began = clock()
    while True:
        position = start + (clock() - began) * speed
        player.advance(position)
        upcoming = player.next_time()
        if upcoming is None:
            break
        sleep(min(max(upcoming - position, 0.0) / speed, 0.05))
    player.stop()
