from __future__ import annotations

import sys
import threading
import time
from collections import deque
from typing import Callable, Collection, Sequence

from .follow import TICK, Follower, GameTime, OverlayClient, PartsFor
from .library import ArrangementRef
from .keyboard import Keyboard
from .keyshow import KeyShow
from .lights import LIGHTS_EARLY, MIN_PERIOD
from .parts import VoicesFor
from .player import Player, Track
from .priority import Activity, playing_thread

# Sessions that run in the background and can be stopped: playing a song (pause, seek, speed and an A-B loop
# while it plays) and following the game.

LOOP_LEAD_IN = 1.0  # seconds of the song before a loop's start that it goes back to, to come in on it


# Python runs one thread at a time, handing over every so often: a change being worked out (a song's parts planned
# again, tens of milliseconds) could keep the song's thread waiting that long, its notes late. Handed over this
# often instead (Python's default: 5 ms), it waits a millisecond or two
SWITCH_INTERVAL = 0.001


def _quick_switches() -> None:
    if sys.getswitchinterval() > SWITCH_INTERVAL:
        sys.setswitchinterval(SWITCH_INTERVAL)


# And macOS told: the song's thread the most urgent kind, and the process latency-critical while it runs (no App Nap
# for the Dock app, its window closed or hidden; timers as precise as they go: priority's)
PLAYING = "Playing a song's notes in time"


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
        self._timed()
        self.loop: tuple[float, float] | None = None  # from, to: reaching its end, back to a little before its start
        self._last = self._from  # the position last played to (passing the loop's end from there: back)
        player.retime(rate=speed)
        self._show: KeyShow | None = None  # a show on the keys where the song leaves them free
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._activity = Activity(PLAYING)

    @property
    def position(self) -> float:
        with self._lock:
            return self._now()

    def where_at(self, at: float) -> tuple[float | None, float]:
        """Where the song is at a time on its clock (None: paused, or ended) and how fast it goes, without waiting for
        its lock: the song's audio asks from its sound device's thread, which can't wait while this one plays notes or
        another player is put in (the sound it was to make missed)."""
        start, since, speed, paused = self._timing
        if paused:
            return None, speed
        return min(start + (at - since) * speed, self.length), speed

    def _timed(self) -> None:
        """(Its timing changed: kept together for where_at, swapped whole.)"""
        self._timing = (self._from, self._since, self.speed, self.paused)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        _quick_switches()
        self._activity.begin()
        self._since = self._clock()
        self._timed()
        self._thread = threading.Thread(target=self._run, name="rs-keylights playback", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if not self.paused:
                self._from, self._since = self._now(), self._clock()
                self.paused = True
                self._timed()
                self.player.stop()

    def resume(self) -> None:
        with self._lock:
            if self.paused:
                self._since = self._clock()
                self.paused = False
                self._timed()

    def seek(self, position: float) -> None:
        with self._lock:
            self._seek(position)

    def _seek(self, position: float) -> None:
        self._from, self._since = min(max(position, 0.0), self.length), self._clock()
        self._last = self._from  # (moved past the loop's end: it plays on, until it's back before it)
        self.ended = False
        self._timed()
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
            self._timed()
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

    def mute(self, names: Collection[str]) -> None:
        """Parts muted by name, the rest not, as it plays (Player.mute): nothing else stopped. (Made first, the notes
        going on meanwhile; then taken at once.)"""
        self.player.prepare_mute(names)
        with self._lock:
            self.player.mute(names)

    def relight(self, tracks: dict[str, Track]) -> None:
        """Parts lighting other keys, as it plays (Player.relight): what sounds left as it is. (Made first, the notes
        going on meanwhile; then taken at once.)"""
        self.player.prepare_relight(tracks)
        with self._lock:
            self.player.relight(tracks)

    def replace(self, player: Player, length: float | None = None) -> None:
        """Plays on with another player (other parts, tones or lights), from where the song is."""
        with self._lock:
            measured = self._clock()  # (where the song is then: the time stopping and retiming take isn't lost to it,
            position = self._from if self.paused else min(  # the song's audio and the game ahead of it after)
                self._from + (measured - self._since) * self.speed, self.length)
            self.player.stop()
            self.player = player
            player.retime(rate=self.speed)
            if length is not None:
                self.length = length
                if position > length:
                    position, measured = length, self._clock()
                self._from, self._since = position, measured
                self._timed()
            if not self.paused:
                player.seek(self._now())  # (where it is now: on from it)

    def stop(self) -> None:
        self._quit.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _now(self) -> float:
        if self.paused:
            return self._from
        return min(self._from + (self._clock() - self._since) * self.speed, self.length)

    def _run(self) -> None:
        playing_thread()
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
                            self._timed()
                            self.player.stop()
                        upcoming = self.player.next_time()
                        wait = 0.05 if upcoming is None else min(max(upcoming - position, 0.0) / self.speed, 0.05)
                self._sleep(wait)
        finally:
            self._activity.end()
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
        game_time: GameTime | None = None,
    ):
        self.messages: deque[str] = deque(maxlen=40)
        self.keyboard = keyboard
        self.follower = Follower(client, index, keyboard, offset=offset, status=self.messages.append,
                                 voices_for=voices_for, parts_for=parts_for, lights_early=lights_early,
                                 fastest_flash=fastest_flash, clock=clock, sleep=sleep, trace=trace,
                                 game_time=game_time, overlay_thread=True)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._activity = Activity(PLAYING)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_parts(self, voices_for: VoicesFor | None, parts_for: PartsFor | None) -> None:
        """Other tones (or none) or parts: what plays goes on with them from where the song is. Planned and made
        first, while the song's steps go on (tens of milliseconds: the notes would wait), then taken at once."""
        prepared = self.follower.prepare_parts(voices_for, parts_for)
        with self._lock:
            self.follower.use_parts(prepared, voices_for, parts_for)

    def set_extras(self, extras_for: Callable[[ArrangementRef], Sequence[Track]] | None,
                   muted_for: Callable[[ArrangementRef], bool] | None = None) -> None:
        """Tracks besides the parts (a song's drum part), and whether they're muted: what plays goes on with them
        from where the song is."""
        with self._lock:
            self.follower.set_extras(extras_for, muted_for)

    def relight_parts(self, parts_for: PartsFor | None) -> bool:
        """The parts' lit keys as chosen now, at once, if they can be (Follower.relight_parts; planned and made first,
        the notes going on meanwhile)."""
        prepared = self.follower.prepare_relight(parts_for)
        if prepared is None:
            return False
        with self._lock:
            return self.follower.relight_parts(prepared)

    def mute_parts(self, parts_for: PartsFor | None) -> bool:
        """The parts muted and unmuted as chosen now, at once, if they can be (Follower.mute_parts; made first, the
        notes going on meanwhile)."""
        self.follower.prepare_mutes(parts_for)
        with self._lock:
            return self.follower.mute_parts(parts_for)

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
        _quick_switches()
        self._activity.begin()
        self._thread = threading.Thread(target=self._run, name="rs-keylights follow", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._quit.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        playing_thread()
        try:
            while not self._quit.is_set():
                with self._lock:
                    self.follower.step(self._clock())
                self._sleep(TICK)
        finally:
            self._activity.end()
            with self._lock:
                self.follower.close()
            self.keyboard.close()
