from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from .follow import TICK, Follower, OverlayClient, PartsFor
from .keyboard import Keyboard
from .keyshow import KeyShow
from .lights import LIGHTS_EARLY, MIN_PERIOD
from .parts import VoicesFor
from .player import Player

# Sessions that run in the background and can be stopped: playing a song (pause, seek, speed and an A-B loop
# while it plays) and following the game.

LOOP_LEAD_IN = 1.0  # seconds of the song before a loop's start that it goes back to, to come in on it


class Playback:
    """Plays a song in real time in a thread: pause, resume, seek, speed and a loop while it plays. It closes the
    keyboard when it stops, or at the song's end."""

    def __init__(
        self,
        player: Player,
        keyboard: Keyboard,
        length: float,
        start: float = 0.0,
        speed: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if speed <= 0:
            raise ValueError("The speed must be more than 0")
        self.player = player
        self.keyboard = keyboard
        self.length = length
        self.speed = speed
        self.paused = False
        self.ended = False
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._from = min(max(start, 0.0), length)  # song position at _since
        self._since = clock()
        self.loop: tuple[float, float] | None = None  # from, to: reaching its end, back to a little before its start
        self._last = self._from  # the position last played to (passing the loop's end from there: back)
        player.retime(rate=speed)
        self._show: KeyShow | None = None  # a show on the keys where the song leaves them free
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def position(self) -> float:
        with self._lock:
            return self._now()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._since = self._clock()
        self._thread = threading.Thread(target=self._run, name="rs-keylights playback", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if not self.paused:
                self._from, self._since = self._now(), self._clock()
                self.paused = True
                self.player.stop()

    def resume(self) -> None:
        with self._lock:
            if self.paused:
                self._since = self._clock()
                self.paused = False

    def seek(self, position: float) -> None:
        with self._lock:
            self._seek(position)

    def _seek(self, position: float) -> None:
        self._from, self._since = min(max(position, 0.0), self.length), self._clock()
        self._last = self._from  # (moved past the loop's end: it plays on, until it's back before it)
        self.ended = False
        if not self.paused:
            self.player.seek(self._from)

    def set_loop(self, loop: tuple[float, float] | None) -> None:
        """An A-B loop (None: none): past its end already, back to its start at once."""
        with self._lock:
            self.loop = loop
            if loop is not None and self._now() > loop[1]:
                self._seek(max(loop[0] - LOOP_LEAD_IN, 0.0))

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("The speed must be more than 0")
        with self._lock:
            self._from, self._since = self._now(), self._clock()
            self.speed = speed
            self.player.retime(rate=speed)

    @property
    def showing(self) -> bool:
        return self._show is not None

    def show(self, frames, repeat: bool = True) -> None:
        """A show on the keys where the song leaves them free (keyshow.KeyShow)."""
        with self._lock:
            self._end_show()
            self._show = KeyShow(frames, repeat)

    def stop_show(self) -> None:
        with self._lock:
            self._end_show()

    def _end_show(self) -> None:
        if self._show is not None:
            self._show.end(self.keyboard)
        self._show = None

    def retime_lights(self, lead: float, min_period: float) -> None:
        """Other lights early and fastest flash, from where the song is."""
        with self._lock:
            self.player.retime(lead=lead, min_period=min_period)

    def replace(self, player: Player, length: float | None = None) -> None:
        """Plays on with another player (other parts, tones or lights), from where the song is."""
        with self._lock:
            position = self._now()
            self.player.stop()
            self.player = player
            player.retime(rate=self.speed)
            if length is not None:
                self.length = length
                position = min(position, length)
                self._from, self._since = position, self._clock()
            if not self.paused:
                player.seek(position)

    def stop(self) -> None:
        self._quit.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _now(self) -> float:
        if self.paused:
            return self._from
        return min(self._from + (self._clock() - self._since) * self.speed, self.length)

    def _run(self) -> None:
        try:
            while not self._quit.is_set():
                with self._lock:
                    show = self._show
                    if show is not None:
                        show.step(self.keyboard, self._clock(), self.player, None if self.paused else self._now())
                        if show.done:
                            self._show = None
                    if self.paused:
                        wait = 0.02 if self._show is not None else 0.05
                    else:
                        position = self._now()
                        loop = self.loop
                        if loop is not None and self._last < loop[1] <= position:  # its end reached: back
                            self._seek(max(loop[0] - LOOP_LEAD_IN, 0.0))
                            position = self._from
                        self._last = position
                        self.player.advance(position)
                        if position >= self.length:
                            self.ended = True
                            self.paused = True
                            self._from = self.length
                            self.player.stop()
                        upcoming = self.player.next_time()
                        wait = 0.05 if upcoming is None else min(max(upcoming - position, 0.0) / self.speed, 0.05)
                self._sleep(wait)
        finally:
            with self._lock:
                self._end_show()
                self.player.stop()
            self.keyboard.close()


class Following:
    """Follows the game in a thread (follow.Follower), keeping its last messages."""

    def __init__(
        self,
        client: OverlayClient,
        index,
        keyboard: Keyboard,
        voices_for: VoicesFor | None = None,
        offset: float = 0.0,
        lights_early: float = LIGHTS_EARLY,
        fastest_flash: float = MIN_PERIOD,
        parts_for: PartsFor | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        trace: Callable[[dict[str, object]], None] | None = None,
    ):
        self.messages: deque[str] = deque(maxlen=40)
        self.keyboard = keyboard
        self.follower = Follower(client, index, keyboard, offset=offset, status=self.messages.append,
                                 voices_for=voices_for, parts_for=parts_for, lights_early=lights_early,
                                 fastest_flash=fastest_flash, clock=clock, sleep=sleep, trace=trace)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_parts(self, voices_for: VoicesFor | None, parts_for: PartsFor | None) -> None:
        """Other tones (or none) or parts: what plays goes on with them from where the song is."""
        with self._lock:
            self.follower.set_parts(voices_for, parts_for)

    @property
    def showing(self) -> bool:
        return self.follower.showing

    def show(self, frames, repeat: bool = True) -> None:
        """A show on the keys where the song leaves them free (follow.Follower.show)."""
        with self._lock:
            self.follower.show(frames, repeat)

    def stop_show(self) -> None:
        with self._lock:
            self.follower.stop_show()

    def set_offset(self, offset: float) -> None:
        with self._lock:
            self.follower.offset = offset

    def retime_lights(self, lead: float, min_period: float) -> None:
        with self._lock:
            self.follower.set_lights(lead, min_period)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rs-keylights follow", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._quit.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            while not self._quit.is_set():
                with self._lock:
                    self.follower.step(self._clock())
                self._sleep(TICK)
        finally:
            with self._lock:
                self.follower.stop()
            self.keyboard.close()
