from __future__ import annotations

import time
from bisect import bisect_right
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .lights import MIN_PERIOD, KeyLight, Light, merge_keys

SEEK_BACK = 0.25  # seconds; going back further than this is a seek (riff repeat, restart)
SEEK_AHEAD = 1.0  # going forward further than this is a seek too, not a burst of notes
KEY = -1  # the track index of a key's light in the actions


class KeysProtocol(Protocol):
    def light(self, keys: tuple[int, ...]) -> None:
        ...

    def unlight(self, keys: tuple[int, ...]) -> None:
        ...

    def play(self, channel: int, notes: tuple[int, ...], velocity: int) -> None:
        ...

    def stop(self, channel: int, notes: tuple[int, ...]) -> None:
        ...

    def all_off(self) -> None:
        ...


class VoicesProtocol(Protocol):
    def pick(self, light: Light) -> tuple[int, tuple[int, ...], int]:
        ...


@dataclass(frozen=True)
class Track:
    """One part: its lights, the voices it sounds with (none: silent), and whether it lights keys."""

    lights: Sequence[Light]
    voices: VoicesProtocol | None = None
    lit: bool = True


class Player:
    """Lights the keys and plays the notes of one or more parts, for a song position that moves forward
    and now and then jumps. The keys follow one timeline per key made from every lit part (so two parts
    on the same key don't fight over it); each part's notes sound on their own."""

    def __init__(self, tracks: Sequence[Track], keys: KeysProtocol, rate: float = 1.0, lead: float = 0.0,
                 min_period: float = MIN_PERIOD):
        self.tracks = list(tracks)
        self.keys = keys
        self.rate = rate  # song seconds per second: the keys' flashing is timed for it
        self.lead = lead  # seconds the keys light before the notes
        self.min_period = min_period  # a key's repeats closer than this flash on every other one
        self._position: float | None = None
        self._sounding: dict[tuple[int, int], tuple[int, tuple[int, ...]]] = {}  # (track, light) -> (channel, notes)
        self._build()

    def _build(self) -> None:
        self.display: list[KeyLight] = merge_keys([t.lights for t in self.tracks if t.lit], self.rate, self.lead,
                                                  self.min_period)
        self._display_ons = [k.on for k in self.display]
        self._ons = [[light.on for light in t.lights] for t in self.tracks]
        # (time, 0 for off / 1 for on, KEY or a track, index): at the same time offs come first. A light
        # that ends as it starts would have its off before its on, so it isn't played.
        self._actions = sorted(
            [(time, kind, KEY, i) for i, k in enumerate(self.display) for time, kind in ((k.off, 0), (k.on, 1))]
            + [
                (time, kind, ti, li)
                for ti, t in enumerate(self.tracks)
                if t.voices is not None
                for li, light in enumerate(t.lights)
                if light.off > light.on
                for time, kind in ((light.off, 0), (light.on, 1))
            ]
        )
        self._times = [action[0] for action in self._actions]
        self._next = 0

    def retime(self, rate: float | None = None, lead: float | None = None, min_period: float | None = None) -> None:
        """Another playback speed, lead or fastest flash: the keys' timeline is made again, going on from
        where it is."""
        rate = self.rate if rate is None else rate
        lead = self.lead if lead is None else lead
        min_period = self.min_period if min_period is None else min_period
        if abs(rate - self.rate) < 0.005 and lead == self.lead and min_period == self.min_period:
            return
        self.rate, self.lead, self.min_period = rate, lead, min_period
        position = self._position
        self._build()
        if position is not None:
            self.seek(position)

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
            _time, on, ti, i = self._actions[self._next]
            if on:
                self._start(ti, i)
            else:
                self._end(ti, i)
            self._next += 1
        self._position = position

    def seek(self, position: float) -> None:
        self._all_off()
        self._next = bisect_right(self._times, position)
        for i in range(bisect_right(self._display_ons, position)):
            if self.display[i].off > position:
                self._start(KEY, i)
        for ti, t in enumerate(self.tracks):
            current = bisect_right(self._ons[ti], position) - 1
            if t.voices is not None and current >= 0 and t.lights[current].off > max(position, t.lights[current].on):
                self._start(ti, current)
        self._position = position

    def keys_at(self, position: float) -> list[int]:
        """The keys lit at a song position."""
        return [k.key for k in self.display[: bisect_right(self._display_ons, position)] if k.off > position]

    def stop(self) -> None:
        if self._position is not None:
            self._all_off()
        self._position = None

    def done(self) -> bool:
        return self._next >= len(self._actions)

    def _start(self, ti: int, i: int) -> None:
        if ti == KEY:
            self.keys.light((self.display[i].key,))
            return
        voices = self.tracks[ti].voices
        if voices is not None:
            channel, notes, velocity = voices.pick(self.tracks[ti].lights[i])
            self.keys.play(channel, notes, velocity)
            self._sounding[(ti, i)] = (channel, notes)

    def _end(self, ti: int, i: int) -> None:
        if ti == KEY:
            self.keys.unlight((self.display[i].key,))
        elif (ti, i) in self._sounding:
            self.keys.stop(*self._sounding.pop((ti, i)))

    def _all_off(self) -> None:
        self.keys.all_off()
        self._sounding.clear()


def play(
    player: Player,
    start: float = 0.0,
    speed: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Plays from a song position in real time (times speed) to the end."""
    player.retime(rate=speed)
    began = clock()
    while True:
        position = start + (clock() - began) * speed
        player.advance(position)
        upcoming = player.next_time()
        if upcoming is None:
            break
        sleep(min(max(upcoming - position, 0.0) / speed, 0.05))
    player.stop()
