from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Sequence

from .follow import TICK, Follower, OverlayClient
from .keyboard import Keyboard
from .library import ArrangementRef
from .lights import LIGHTS_EARLY, MIN_PERIOD, KeyRange, Light, schedule
from .player import Player, Track
from .sng import Arrangement
from .sound import CHANNEL_SETS, SoundChoice, Voices

# What play and the GUI share: the parts of a song made into tracks, and sessions that run in the
# background (play with pause, seek and speed; follow) and can be stopped.


@dataclass(frozen=True)
class Part:
    ref: ArrangementRef
    arrangement: Arrangement
    lights: int  # keys it lights (0: none); the keyboard lights 4 in all


@dataclass(frozen=True)
class PlannedPart:
    part: Part
    shift: int  # octaves (in semitones) its lit keys are moved up
    lights: list[Light]
    voices: Voices | None


def plan(parts: Sequence[Part], choice: SoundChoice | None) -> list[PlannedPart]:
    """Lights and voices for each part. The lit parts share an octave shift, so their keys line up; each
    part sounds on channels of its own (CHANNEL_SETS, in order)."""
    if len(parts) > len(CHANNEL_SETS):
        raise ValueError(f"{len(CHANNEL_SETS)} parts at most")
    full = KeyRange()
    lit_shift = full.octave_shift([n for p in parts if p.lights for n in p.arrangement.notes])
    planned = []
    for i, part in enumerate(parts):
        shift = lit_shift if part.lights else full.octave_shift(part.arrangement.notes)
        lights = schedule(part.arrangement.notes, KeyRange(max_lit=part.lights or 4), shift)
        voices = choice.voices(part.ref, CHANNEL_SETS[i]).for_arrangement(shift) if choice else None
        planned.append(PlannedPart(part, shift, lights, voices))
    return planned


def player_for(planned: Sequence[PlannedPart], keyboard: Keyboard, lead: float = LIGHTS_EARLY,
               min_period: float = MIN_PERIOD) -> Player:
    """Sets the parts' tones on the keyboard and makes their player (lead: seconds the keys light early;
    min_period: repeats closer than this flash on every other note)."""
    for p in planned:
        for voice in p.voices.all() if p.voices else []:
            keyboard.set_voice(voice.channel, voice.tone.program, voice.tone.bank)
    return Player([Track(p.lights, p.voices, lit=bool(p.part.lights)) for p in planned], keyboard,
                  lead=lead, min_period=min_period)


class Playback:
    """Plays a song in real time in a thread: pause, resume, seek and speed while it plays. It closes the
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
        player.retime(rate=speed)
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
            self._from, self._since = min(max(position, 0.0), self.length), self._clock()
            self.ended = False
            if not self.paused:
                self.player.seek(self._from)

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("The speed must be more than 0")
        with self._lock:
            self._from, self._since = self._now(), self._clock()
            self.speed = speed
            self.player.retime(rate=speed)

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
                    if self.paused:
                        wait = 0.05
                    else:
                        position = self._now()
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
                self.player.stop()
            self.keyboard.close()


class Following:
    """Follows the game in a thread (follow.Follower), keeping its last messages."""

    def __init__(
        self,
        client: OverlayClient,
        index,
        keyboard: Keyboard,
        voices_for: Callable[[ArrangementRef], Voices] | None = None,
        offset: float = 0.0,
        lights_early: float = LIGHTS_EARLY,
        fastest_flash: float = MIN_PERIOD,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.messages: deque[str] = deque(maxlen=40)
        self.keyboard = keyboard
        self.follower = Follower(client, index, keyboard, offset=offset, status=self.messages.append,
                                 voices_for=voices_for, lights_early=lights_early, fastest_flash=fastest_flash,
                                 clock=clock, sleep=sleep)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_voices_for(self, voices_for: Callable[[ArrangementRef], Voices] | None) -> None:
        """Other tones (or none): the arrangement being followed is loaded again with them."""
        with self._lock:
            self.follower.set_voices_for(voices_for)

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
