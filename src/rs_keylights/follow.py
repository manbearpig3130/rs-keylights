from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .keyboard import Keyboard
from .library import ArrangementRef
from .keyshow import KeyShow
from .lights import LIGHTS_EARLY, MIN_PERIOD
from .parts import Part, PlannedPart, VoicesFor, plan, player_for
from .player import Player
from .sng import Arrangement, note_name
from .sound import CHANNEL_SETS
from .tones import Tone

# Follows the song Rocksmith plays through RS Companion's stream overlay API (RockSniffer's API,
# 127.0.0.1:9938, Settings > Stream overlays): the song timer, the song and the selected
# arrangement. RS Companion updates the timer about 4 times a second; SongClock fills in between.

DEFAULT_OVERLAY_PORT = 9938
POLL_INTERVAL = 0.1
TICK = 0.005
MENUS = (0, 1)  # currentState: no game, menus

# The parts to play while an arrangement is played: arrangements of its song, each with the keys it lights
PartsFor = Callable[[ArrangementRef], Sequence[tuple[ArrangementRef, int]]]


class OverlayError(RuntimeError):
    pass


@dataclass(frozen=True)
class GameState:
    state: int
    timer: float
    song_key: str
    arrangement_id: str
    stage: str = ""  # its gameStage (traced, to learn what it says, e.g. while paused)


class OverlayClient:
    def __init__(self, port: int = DEFAULT_OVERLAY_PORT, timeout: float = 0.5):
        self.url = f"http://127.0.0.1:{port}/"
        self.timeout = timeout

    def read(self) -> GameState:
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as response:
                body = response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise OverlayError(f"No overlay API at {self.url}: {exc}") from exc
        return parse_state(body)


def parse_state(body: bytes) -> GameState:
    # RS Companion appends the song details (with album art) last; the state comes before them
    cut = body.find(b',"songDetails":')
    try:
        data = json.loads(body[:cut] + b"}" if cut > 0 else body)
    except ValueError as exc:
        raise OverlayError(f"Unexpected overlay API response: {exc}") from exc
    readout = data.get("memoryReadout") if isinstance(data, dict) else None
    if not isinstance(readout, dict):
        raise OverlayError("The overlay API response has no memoryReadout")
    timer = readout.get("songTimer")
    state = data.get("currentState")
    return GameState(
        state=state if isinstance(state, int) else 0,
        timer=float(timer) if isinstance(timer, (int, float)) else 0.0,
        song_key=str(readout.get("songID") or ""),
        arrangement_id=str(readout.get("arrangementID") or ""),
        stage=str(readout.get("gameStage") or ""),
    )


class SongClock:
    """The song position between the timer's updates.

    Each timer value is seen a little after the game read it, never before, so the freshest
    estimate is the largest among recent samples. The timer's rate comes from the samples too
    (Riff Repeater can slow a song down). Besides the plain cases (a stopped timer is a pause, a
    value far from where the last one predicts is a seek) the game shows a few of its own:
    - pausing, the timer's last step is very short (it stopped between two updates): dark at once,
    - Riff Repeater rewinds the timer quickly back to the section's start: dark while it goes back,
    - after a new speed is picked, steps are clearly short or long: three of them in a row are the
      new speed, and the rate is measured again from them.
    """

    WINDOW = 5.0  # seconds of samples for the rate
    RECENT = 2.5  # seconds of samples for the position
    JUMP = 0.5
    MIN_RATE_SPAN = 3.0  # seconds of samples for an exact rate
    QUICK_RATE_SPAN = 1.5  # seconds of samples for a rate clearly off the one in use
    QUICK_RATE_DIFF = 0.15
    NEW_SPEED_SPAN = 0.4  # seconds of samples for the rate after a speed change
    SNAP = 0.03  # a rate this close to 1 is 1
    PAUSING = 0.35  # a step this much shorter than expected: the timer stopped between updates
    SLOWER, FASTER = 0.65, 1.5  # a step this much shorter or longer than expected is off speed
    NEW_SPEED = 3  # off-speed steps in a row that make a new speed

    def __init__(self) -> None:
        self._samples: deque[tuple[float, float]] = deque()  # (timer, seen at)
        self._off: list[tuple[float, float]] = []  # off-speed samples in a row, not yet in _samples
        self._off_lost_time = False  # one of them looked like pausing
        self._pausing = False
        self._last_value: float | None = None
        self._changed_at = 0.0
        self._interval = 0.25
        self._going_back = False
        self._new_speed = False  # measuring the rate after a speed change
        self.rate = 1.0

    def reset(self) -> None:
        self._clear()
        self._last_value = None
        self._new_speed = False
        self.rate = 1.0

    def update(self, timer: float, now: float) -> None:
        if timer == self._last_value:
            return
        previous, gap = self._last_value, now - self._changed_at
        self._last_value, self._changed_at = timer, now
        if previous is not None and timer < previous:
            # Going back: Riff Repeater's rewind, a restart or a seek. Keep the rate: same speed.
            self._clear()
            self._going_back = True
            return
        if previous is not None and gap < 1.5:
            self._interval = 0.8 * self._interval + 0.2 * gap
        if self._going_back or not self._samples:
            self._clear()
            self._samples.append((timer, now))
            return

        last_timer, last_seen = self._off[-1] if self._off else self._samples[-1]
        expected, step = self.rate * (now - last_seen), timer - last_timer
        if abs(step - expected) > self.JUMP:  # a seek ahead, or playing again after a pause
            self._clear()
            self._samples.append((timer, now))
            return
        ratio = step / expected if expected > 0.05 else 1.0
        self._pausing = ratio < self.PAUSING
        if not self.SLOWER <= ratio <= self.FASTER:
            self._off.append((timer, now))
            self._off_lost_time |= self._pausing
            if len(self._off) >= self.NEW_SPEED:  # a new speed, not a pause
                self._samples = deque(self._off)
                self._off.clear()
                self._off_lost_time = self._pausing = False
                self._new_speed = True
                self._measure_rate()
            return
        if self._off:  # back to the expected speed
            if self._off_lost_time:  # the timer stopped for a moment: go on from there
                self._samples = deque(self._off)
            else:
                self._samples.extend(self._off)
            self._off.clear()
            self._off_lost_time = False
        self._samples.append((timer, now))
        while self._samples[0][1] < now - self.WINDOW:
            self._samples.popleft()
        self._measure_rate()

    def position(self, now: float) -> float | None:
        if not self._samples or self._going_back or self._pausing or now - self._changed_at > self.stale_after:
            return None
        return self._estimate(now)

    @property
    def stale_after(self) -> float:
        return min(max(0.6, 2.5 * self._interval), 2.5)

    def _clear(self) -> None:
        self._samples.clear()
        self._off.clear()
        self._off_lost_time = False
        self._pausing = False
        self._going_back = False

    def _measure_rate(self) -> None:
        # Least squares over the window: each sample is up to a poll interval late, at random
        span = self._samples[-1][1] - self._samples[0][1]
        if span < (self.NEW_SPEED_SPAN if self._new_speed else self.QUICK_RATE_SPAN):
            return
        n = len(self._samples)
        mean_seen = sum(seen for _timer, seen in self._samples) / n
        mean_timer = sum(timer for timer, _seen in self._samples) / n
        spread = sum((seen - mean_seen) ** 2 for _timer, seen in self._samples)
        rate = sum((seen - mean_seen) * (timer - mean_timer) for timer, seen in self._samples) / spread
        if span < self.MIN_RATE_SPAN and not self._new_speed and abs(rate - self.rate) < self.QUICK_RATE_DIFF:
            return  # too few samples to adjust a rate that is about right
        self.rate = 1.0 if abs(rate - 1.0) < self.SNAP else min(max(rate, 0.1), 2.0)
        if span >= self.MIN_RATE_SPAN:
            self._new_speed = False

    def _estimate(self, now: float) -> float | None:
        if not self._samples:
            return None
        newest = self._samples[-1][1]
        return max(timer + self.rate * (now - seen) for timer, seen in self._samples if seen >= newest - self.RECENT)


class IndexProtocol(Protocol):
    def find(self, persistent_id: str) -> ArrangementRef | None:
        ...

    def refresh(self, progress: Callable[[int, int], None] | None = None) -> int:
        ...


class Follower:
    def __init__(
        self,
        client: OverlayClient,
        index: IndexProtocol,
        keyboard: Keyboard,
        offset: float = 0.0,
        status: Callable[[str], None] = print,
        load: Callable[[ArrangementRef], Arrangement] = lambda ref: ref.load(),
        voices_for: VoicesFor | None = None,
        parts_for: PartsFor | None = None,
        trace: Callable[[dict[str, object]], None] | None = None,
        lights_early: float = LIGHTS_EARLY,
        fastest_flash: float = MIN_PERIOD,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = client
        self.index = index
        self.keyboard = keyboard
        self.offset = offset
        self._status = status
        self._load = load
        self._voices_for = voices_for  # with sound: the voices (channels and tones) for each arrangement
        self._parts_for = parts_for  # None: just the arrangement played, on every key
        self.lights_early = lights_early  # seconds the keys light before the notes
        self.fastest_flash = fastest_flash  # a key's repeats closer than this flash on every other note
        self._trace = trace  # what RS Companion reports and what the clock makes of it, for debugging
        self._clock = clock
        self._sleep = sleep
        self._song_clock = SongClock()
        self._arrangement_id = ""
        self._player: Player | None = None
        self.current: ArrangementRef | None = None  # the arrangement being followed
        self._loaded: dict[str, Arrangement] = {}  # its song's arrangements read so far, by persistent ID
        self._planned: list[PlannedPart] = []  # what plays
        self._show: KeyShow | None = None  # a show on the keys while the song leaves them free
        self._next_poll = 0.0
        self._connected: bool | None = None
        self._playing = False

    def position(self, now: float) -> float | None:
        """The song position, while the song plays."""
        return self._song_clock.position(now) if self._player is not None else None

    def stop(self) -> None:
        """Turns everything off (until the next step)."""
        self.stop_show()
        self._unload()

    @property
    def showing(self) -> bool:
        return self._show is not None

    def sounding(self) -> list[tuple[ArrangementRef, tuple[int, int, int]]]:
        """The arrangements played, and the channels each sounds on."""
        return [(planned.part.ref, CHANNEL_SETS[i]) for i, planned in enumerate(self._planned)]

    @property
    def connected(self) -> bool | None:
        """It reaches RS Companion's overlay API (None: not tried yet)."""
        return self._connected

    def show(self, frames: Sequence[tuple[float, tuple[int, ...]]], repeat: bool = True) -> None:
        """Lights a show on the keys (frames: seconds into it and the keys lit; the last one's time is its length)
        where the song leaves them free, over and over (repeat) until it needs them or a key is pressed."""
        self.stop_show()
        self._show = KeyShow(frames, repeat)

    def stop_show(self) -> None:
        if self._show is not None:
            self._show.end(self.keyboard)
        self._show = None

    def set_lights(self, lights_early: float, fastest_flash: float) -> None:
        """Other lights early and fastest flash, also for the arrangement playing."""
        self.lights_early, self.fastest_flash = lights_early, fastest_flash
        if self._player is not None:
            self._player.retime(lead=lights_early, min_period=fastest_flash)

    def set_parts(self, voices_for: VoicesFor | None, parts_for: PartsFor | None) -> None:
        """Other voices (None: silent) or parts, also for the song playing: it goes on with them from where it
        is, without reading the arrangement again."""
        self._voices_for, self._parts_for = voices_for, parts_for
        if self._player is None or self.current is None:
            return
        planned = self._plan(self.current)
        if _sound_of(planned) == _sound_of(self._planned):
            return
        player = self._player_with(planned)
        player.retime(rate=self._player.rate)
        self._player.stop()
        self._player = player  # the next step seeks it to where the song is
        self._status(f"{self.current.title} ({self.current.name}): {self._describe(planned) or 'lights only'}")

    def run(self) -> None:
        while True:
            self.step(self._clock())
            self._sleep(TICK)

    def step(self, now: float) -> None:
        if now >= self._next_poll:
            self._next_poll = now + POLL_INTERVAL
            self._poll(now)
        position = self._song_clock.position(now) if self._player is not None else None
        if self._show is not None:
            self._show.step(self.keyboard, now, self._player, None if position is None else position + self.offset)
            if self._show.done:
                self._show = None
        if self._player is None:
            return
        if position is None:
            if self._playing:
                self._player.stop()
                self._playing = False
                self._record({"t": now, "stopped": True})
            return
        if not self._playing:
            self._record({"t": now, "playing": round(position, 3), "rate": self._song_clock.rate})
        self._player.retime(rate=self._song_clock.rate)  # Riff Repeater's speed: the keys flash for it
        self._player.advance(position + self.offset)
        self._playing = True

    def _poll(self, now: float) -> None:
        try:
            state = self.client.read()
        except OverlayError as exc:
            self._record({"t": now, "error": str(exc)})
            if self._connected is not False:
                self._status("Waiting for RS Companion's overlay API (Settings > Stream overlays)")
                self._connected = False
            self._unload()
            return
        if not self._connected:
            self._status("Connected to RS Companion")
            self._connected = True
        estimate = self._song_clock.position(now)
        self._record({"t": now, "state": state.state, "stage": state.stage, "timer": state.timer,
                      "song": state.song_key, "arrangement": state.arrangement_id,
                      "estimate": None if estimate is None else round(estimate, 3)})

        arrangement_id = state.arrangement_id if state.state not in MENUS else ""
        if arrangement_id != self._arrangement_id:
            self._unload()
            self._arrangement_id = arrangement_id
            if arrangement_id:
                self._player = self._open(arrangement_id, state.song_key)
        if self._player is not None and state.timer > 0:
            self._song_clock.update(state.timer, now)

    def _open(self, arrangement_id: str, song_key: str) -> Player | None:
        ref = self.index.find(arrangement_id)
        if ref is None:
            self._status("Looking for new songs...")
            self.index.refresh()
            ref = self.index.find(arrangement_id)
        if ref is None:
            self._status(f"Song {song_key}: arrangement {arrangement_id} isn't in the game's archives")
            return None
        try:
            arrangement = self._load(ref)
        except Exception as exc:  # a broken or unreadable song file: keep following the game
            self._status(f"{ref.title} ({ref.name}): can't read its notes: {exc}")
            return None
        self.current, self._loaded = ref, {ref.persistent_id: arrangement}
        planned = self._plan(ref)
        notes = ""
        for p in planned:
            if p.part.ref == ref:
                moved = ", an octave up" if p.shift == 12 else f", {p.shift // 12} octaves up" if p.shift else ""
                notes = f": {len(p.lights)} notes{moved}"
        described = self._describe(planned)
        self._status(f"{ref.title} ({ref.name}){notes}" + (f"; {described}" if described else ""))
        return self._player_with(planned)

    def _plan(self, current: ArrangementRef) -> list[PlannedPart]:
        """The parts to play while this arrangement is played, read when first needed."""
        chosen = self._parts_for(current) if self._parts_for else [(current, 4)]
        parts = []
        for ref, lights in list(chosen)[: len(CHANNEL_SETS)]:
            arrangement = self._loaded.get(ref.persistent_id)
            if arrangement is None:
                try:
                    arrangement = self._loaded[ref.persistent_id] = self._load(ref)
                except Exception as exc:  # a broken song file: the other parts play on
                    self._status(f"{ref.title} ({ref.name}): can't read its notes: {exc}")
                    continue
            parts.append(Part(ref, arrangement, lights))
        return plan(parts, self._voices_for)

    def _player_with(self, planned: list[PlannedPart]) -> Player:
        self._planned = planned
        return player_for(planned, self.keyboard, self.lights_early, self.fastest_flash)

    def _describe(self, planned: list[PlannedPart]) -> str:
        """How the parts play; for the arrangement played alone on every key, just its voices."""
        if [(p.part.ref, p.part.lights) for p in planned] == [(self.current, 4)]:
            return planned[0].voices.describe() if planned[0].voices else ""
        return "; ".join(
            f"{p.part.ref.name or p.part.ref.part} lighting {_keys(p.part.lights)}, "
            f"{p.voices.describe() if p.voices else 'silent'}"
            for p in planned
        ) or "nothing plays"

    def playing(self) -> list[dict[str, object]]:
        """What plays, part by part: its lit keys, notes, octaves up, and tones (None: silent)."""
        out: list[dict[str, object]] = []
        for p in self._planned:
            voices = p.voices
            out.append({
                "name": p.part.ref.name or p.part.ref.part, "lights": p.part.lights, "notes": len(p.lights),
                "octaves": p.shift // 12,
                "tone": _tone_text(voices.main.tone) if voices else None,
                "mute": _tone_text(voices.mute.tone) if voices and voices.mute else None,
                "low": f"{_tone_text(voices.low.tone)} below {note_name(voices.split)}" if voices and voices.low else None,
            })
        return out

    def _record(self, entry: dict[str, object]) -> None:
        if self._trace is not None:
            self._trace(entry)

    def _unload(self) -> None:
        if self._player is not None:
            self._player.stop()
        self._player = None
        self.current = None
        self._loaded, self._planned = {}, []
        self._playing = False
        self._arrangement_id = ""
        self._song_clock.reset()


def _sound_of(planned: list[PlannedPart]) -> list[tuple[str, int, int, object]]:
    """What makes parts play differently: which, their lit keys, octave shift and voices."""
    return [(p.part.ref.persistent_id, p.part.lights, p.shift, p.voices) for p in planned]


def _tone_text(tone: Tone) -> str:
    return f"{tone.number:03d} {tone.name}"


def _keys(count: int) -> str:
    return "no keys" if count == 0 else "1 key" if count == 1 else f"{count} keys"
