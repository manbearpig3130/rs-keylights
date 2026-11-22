from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Protocol, Sequence

from .keyboard import Keyboard
from .library import ArrangementRef
from .keyshow import KeyShow
from .lights import LIGHTS_EARLY, MIN_PERIOD
from .parts import DRUMS, Part, PlanCache, PlannedPart, VoicesFor, plan, player_for
from .player import SEEK_AHEAD, Player, Track
from .sng import Arrangement
from .tones import Tone

# Follows the song Rocksmith plays through RS Companion's stream overlay API (RockSniffer's API,
# 127.0.0.1:9938, Settings > Stream overlays): the song timer, the song and the selected
# arrangement. RS Companion updates the timer about 4 times a second; SongClock fills in between.
# With RS Re-ARMed's riffloop mod in the game, it all comes from the game itself instead: where the song is (its
# playback's own time, asked 50 times a second and answered in well under a millisecond: GameClock) and which
# arrangement plays (its playback's own copy of it, named as its .sng is), so RS Companion isn't needed: it's read
# only while the game doesn't say (no game yet, or a mod without it), on a thread of its own (OverlayReader), so its
# answers (a hundred KB, album art and all, now and then tens of milliseconds) never hold up the notes.

DEFAULT_OVERLAY_PORT = 9938
POLL_INTERVAL = 0.1
TICK = 0.005
GAME_TIME_EVERY = 0.02  # seconds between asks of the game's own time (riffloop's)
TRACE_AFTER_JUMP = 2.0  # seconds of the game's time reads traced after it jumps (a pause gone, a seek, a rewind)
STALL_TRACED = 0.15  # seconds between steps, while the notes play, that are traced: this process held up (the Mac
# busy, swapping), its notes coming late, all at once (more than SEEK_AHEAD: those in it skipped, traced too)
LATE_SUMMED = (10, 20, 50, 100)  # milliseconds a step comes after its TICK that the trace counts while the notes play,
LATE_SUMMARY = 30.0  # summed up every this many seconds (and as they stop): the process held up, short of a stall
JUST_STRUCK = 0.15  # seconds: starting to play mid-song, notes struck this little before sound; older ones still
# ringing don't (every part's would come at once: following again, or going on after Rocksmith's resume, 2 s back)
GAME_TIME_MISSES, GAME_TIME_BACK_OFF = 3, 2.0  # unanswered this many times (no game, or a mod without it): asked
# again this long after
# The game's own song time and screen (riffloop's time command: seconds, None without a song's playback seen; and
# its screen, "..._Pause" paused), and the arrangement playing (its .sng's name: None without a song's playback, ""
# from a mod that doesn't say); None: no answer
GameTime = Callable[[], "tuple[float | None, str | None] | tuple[float | None, str | None, str | None] | None"]
MENUS = (0, 1)  # currentState: no game, menus

# The parts to play while an arrangement is played: arrangements of its song, each with the keys it lights
PartsFor = Callable[[ArrangementRef], Sequence[tuple[ArrangementRef, int] | tuple[ArrangementRef, int, bool]]]
# (each part to play and its lit keys; a third, False: planned muted, so it can be unmuted at once: Player.mute)


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


@dataclass(frozen=True)
class Prepared:
    """What the song playing would go on with (Follower.prepare_parts): for the arrangement it was made for, the
    parts planned and their player (None: the same as now), and the tracks besides them."""
    current: ArrangementRef
    planned: list[PlannedPart] | None
    extras: list[Track]
    player: Player | None


class GameClock:
    """The song's time as the game itself has it (riffloop's: its playback controller's), read many times a second
    and at once: where the song is between reads (the freshest read, gone on at its rate), how fast it goes (Riff
    Repeater's speed: from the reads), and when it isn't going on (paused: the pause screen, or the time standing
    still; going back: Riff Repeater's rewind). Trusted while it's read (and agrees with RS Companion's timer: else
    another song's playback's, set aside a while)."""

    STILL = 0.3  # seconds of the same time: stopped (a frame's 1/60 s; a stutter shorter)
    FRESH = 0.25  # seconds a read's trusted for
    RECENT = 0.1  # seconds of reads for where it is
    WINDOW = 1.0  # seconds of reads for its rate
    RATE_SPAN = 0.4  # at least this long of them for a rate of their own
    SNAP = 0.02  # a rate this near 1 is 1
    JUMP = 0.5  # seconds a read is further on than its rate says: a seek
    STALL = 0.1  # seconds of the same time, then on: a stand (Rocksmith's resume stands a moment after going back),
    # its rate measured afresh from there rather than across it (else slow for a second: the keys and notes made again)
    SETTLE = 0.4  # seconds of reads after a jump on (going on after a pause, a seek ahead) before it's played from:
    # quiet till then, rather than the notes since in a rush while it finds where and how fast the song goes
    MISMATCH, DISTRUST = 5.0, 3.0  # seconds from RS Companion's timer: not this song's; set aside this long
    MISMATCHES = 4  # RS Companion's timers in a row that far off, first (its timer, a trace showed, now and then
    # reads 0, jumps half a minute on, or falls 2 s behind, for a few updates: the game's own is the one to go by)

    def __init__(self) -> None:
        self._distrusted_until = -math.inf
        self._mismatches = 0
        self.reset()

    def reset(self) -> None:
        self._samples: deque[tuple[float, float]] = deque()  # (time, read at)
        self._last: tuple[float, float] | None = None
        self._changed_at = self._read_at = self.jumped_at = -math.inf
        self._paused = self._going_back = self._settling = False
        self.rate = 1.0

    def update(self, timer: float, now: float, paused: bool = False) -> None:
        self._read_at, self._paused = now, paused
        last = self._last
        if last is not None and timer == last[0]:
            return
        if last is not None:
            step, expected = timer - last[0], self.rate * (now - last[1])
            if step < 0:  # back: Riff Repeater's rewind, a restart or a seek (dark till it goes on)
                self._samples.clear()
                self._going_back = True
                self.jumped_at = now
            elif abs(step - expected) > self.JUMP:  # on: a seek, or going on after a while
                self._samples.clear()
                self._going_back = False
                self._settling = True
                self.jumped_at = now
            else:
                if now - self._changed_at > self.STALL:  # (on after a stand: its rate from here)
                    self._samples.clear()
                self._going_back = False
        self._last, self._changed_at = (timer, now), now
        self._samples.append((timer, now))
        while self._samples[0][1] < now - self.WINDOW:
            self._samples.popleft()
        span = self._samples[-1][1] - self._samples[0][1]
        if span >= self.RATE_SPAN:
            rate = (self._samples[-1][0] - self._samples[0][0]) / span
            self.rate = 1.0 if abs(rate - 1.0) < self.SNAP else min(max(rate, 0.1), 2.0)

    def settled(self) -> bool:
        """Since a jump on (going on after a pause, a seek ahead), SETTLE of reads: where the song is and how fast,
        sure again (a song's start and a rewind, Riff Repeater's, are at once)."""
        if self._settling and self._samples and self._samples[-1][1] - self._samples[0][1] >= self.SETTLE:
            self._settling = False
        return not self._settling

    def trusted(self, now: float) -> bool:
        """Read lately (the game answering, with a song's playback), and not set aside."""
        return self._last is not None and now - self._read_at < self.FRESH and now >= self._distrusted_until

    def position(self, now: float) -> float | None:
        """Where the song is, while it goes on (None: paused, stopped or going back)."""
        if not self._samples or self._paused or self._going_back or now - self._changed_at > self.STILL:
            return None
        newest = self._samples[-1][1]
        return max(t + self.rate * (now - seen) for t, seen in self._samples if seen >= newest - self.RECENT)

    def check(self, timer: float, now: float) -> None:
        """RS Companion's timer (a little behind; each one read, as it changes): far from this, several in a row,
        this is another song's playback (the one before, still found): set aside a while. One or two far off are
        RS Companion's (it reads the game less surely)."""
        position = self.position(now) if self.trusted(now) else None
        if position is None:
            return
        self._mismatches = self._mismatches + 1 if abs(position - timer) > self.MISMATCH else 0
        if self._mismatches >= self.MISMATCHES:
            self._mismatches = 0
            self._distrusted_until = now + self.DISTRUST
            self.reset()


class OverlayReader:
    """RS Companion read on a thread of its own, every POLL_INTERVAL, so a slow answer never holds up the notes:
    each read (or why it failed) with when it was asked, taken by the follower's steps."""

    def __init__(self, client: OverlayClient, clock: Callable[[], float] = time.monotonic,
                 interval: float = POLL_INTERVAL) -> None:
        self._client, self._clock, self._interval = client, clock, interval
        self._got: deque[tuple[float, GameState | OverlayError]] = deque(maxlen=4)
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Reading (once started, it goes on till stop)."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="rs-keylights overlay", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._quit.set()

    def take(self) -> list[tuple[float, GameState | OverlayError]]:
        """The reads since last taken, oldest first."""
        with self._lock:
            got = list(self._got)
            self._got.clear()
        return got

    def _run(self) -> None:
        while not self._quit.is_set():
            asked = self._clock()
            try:
                got: GameState | OverlayError = self._client.read()
            except OverlayError as exc:
                got = exc
            with self._lock:
                self._got.append((asked, got))
            self._quit.wait(max(self._interval - (self._clock() - asked), 0.0))


class IndexProtocol(Protocol):
    def find(self, persistent_id: str) -> ArrangementRef | None:
        ...

    def by_sng(self, name: str) -> ArrangementRef | None:
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
        game_time: GameTime | None = None,
        overlay_thread: bool = False,
    ):
        self.client = client
        self.index = index
        self.keyboard = keyboard
        self.offset = offset
        self._status = status
        self._load = load
        self._voices_for = voices_for  # with sound: the voices (channels and tones) for each arrangement
        self._parts_for = parts_for  # None: just the arrangement played, on every key
        self._extras_for: Callable[[ArrangementRef], Sequence[Track]] | None = None  # tracks besides the parts (drums)
        self._extras_muted_for: Callable[[ArrangementRef], bool] | None = None  # (and whether they're muted)
        self._extras: list[Track] = []  # (and those playing)
        self._made = PlanCache()  # (the parts' lights and tracks as made: a change remakes just the part changed)
        self.lights_early = lights_early  # seconds the keys light before the notes
        self.fastest_flash = fastest_flash  # a key's repeats closer than this flash on every other note
        self._trace = trace  # what RS Companion reports and what the clock makes of it, for debugging
        self._clock = clock
        self._sleep = sleep
        self._song_clock = SongClock()
        self._game_time = game_time  # the game's own time (riffloop's), if it has the mod: where the song is
        self._game_clock = GameClock()
        self._companion_timer: float | None = None  # (RS Companion's timer as last read)
        self._next_game_time = 0.0
        self._game_misses = 0
        self._game_says = False  # the game says which arrangement plays (riffloop's): RS Companion isn't read
        self._game_unknown = ""  # (an arrangement it said that isn't in the library, even looked for again)
        self._overlay_thread = overlay_thread
        self._reader: OverlayReader | None = None  # (RS Companion on its own thread, while it's read)
        self._arrangement_id = ""
        self._player: Player | None = None
        self.current: ArrangementRef | None = None  # the arrangement being followed
        self._loaded: dict[str, Arrangement] = {}  # its song's arrangements read so far, by persistent ID
        self._planned: list[PlannedPart] = []  # what plays
        self._show: KeyShow | None = None  # a show on the keys while the song leaves them free
        self._next_poll = 0.0
        self._connected: bool | None = None
        self._playing = False
        self._stepped_at: float | None = None  # when it last stepped (a step long after: held up)
        self._late: dict[str, float] = {}  # (the steps' lateness since the last summary: _count_late)

    def position(self, now: float) -> float | None:
        """The song position, while the song plays: the game's own (riffloop's), while it's read; else as RS
        Companion's timer has it."""
        if self._player is None:
            return None
        if self._game_clock.trusted(now):
            return self._game_clock.position(now)
        return self._song_clock.position(now)

    def _rate(self, now: float) -> float:
        return self._game_clock.rate if self._game_clock.trusted(now) else self._song_clock.rate

    def close(self) -> None:
        """Stops reading RS Companion (its thread), and everything off."""
        self._stop_reader()
        self.stop()

    def _stop_reader(self) -> None:
        if self._reader is not None:
            self._reader.stop()
        self._reader = None

    def stop(self) -> None:
        """Turns everything off (until the next step)."""
        self.stop_show()
        self._unload()

    @property
    def showing(self) -> bool:
        return self._show is not None

    @property
    def planned(self) -> list[PlannedPart]:
        """The parts playing, as planned (their voices too)."""
        return list(self._planned)

    def sounding(self) -> list[tuple[ArrangementRef, tuple[int, ...]]]:
        """The arrangements played, and the keyboard's channels each sounds on."""
        return [(planned.part.ref, planned.channels) for planned in self._planned]

    @property
    def connected(self) -> bool | None:
        """It reaches the game (riffloop), or RS Companion's overlay API (None: not tried yet)."""
        return True if self._game_says else self._connected

    @property
    def from_game(self) -> bool:
        """The game itself says which arrangement plays (riffloop's), not RS Companion."""
        return self._game_says

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
        self.use_parts(self.prepare_parts(voices_for, parts_for), voices_for, parts_for)

    def prepare_parts(self, voices_for: VoicesFor | None, parts_for: PartsFor | None) -> Prepared | None:
        """What the song playing would go on with, with other voices or parts: planned, and its player made (tens of
        milliseconds), changing nothing yet, so it can be done while the song's steps go on (use_parts takes it
        then, at once); None without a song playing."""
        current, player = self.current, self._player
        if player is None or current is None:
            return None
        planned = self._plan(current, voices_for, parts_for, keep_muted=True)
        if _sound_of(planned) == _sound_of(self._planned):
            return Prepared(current, None, [], None)  # (the same: what plays goes on as it is)
        extras = list(self._extras_for(current)) if self._extras_for else []
        return Prepared(current, planned, extras,
                        player_for(planned, self.keyboard, self.lights_early, self.fastest_flash, extra=extras,
                                   cache=self._made, extra_muted=self._extras_muted()))

    def use_parts(self, prepared: Prepared | None, voices_for: VoicesFor | None, parts_for: PartsFor | None) -> None:
        """Other voices or parts from now, with what prepare_parts made of them (made for another song, or another
        player meanwhile: made again here)."""
        self._voices_for, self._parts_for = voices_for, parts_for
        if self._player is None or self.current is None:
            return
        if prepared is None or prepared.current is not self.current:
            prepared = self.prepare_parts(voices_for, parts_for)
        if prepared is None or prepared.planned is None or prepared.player is None:
            return
        self._planned, self._extras = prepared.planned, prepared.extras
        prepared.player.retime(rate=self._player.rate)
        self._player.stop()
        self._player = prepared.player  # the next step seeks it to where the song is
        self._status(f"{self.current.title} ({self.current.name}): {self._describe(prepared.planned) or 'lights only'}")

    def run(self) -> None:
        while True:
            self.step(self._clock())
            self._sleep(TICK)

    def step(self, now: float) -> None:
        late, self._stepped_at = now - (self._stepped_at if self._stepped_at is not None else now), now
        if self._game_time is not None and now >= self._next_game_time:
            self._ask_game_time(now)
        if self._game_says:
            self._stop_reader()  # (not needed)
        elif self._overlay_thread:  # (RS Companion's reads, as they've come)
            if self._reader is None:
                self._reader = OverlayReader(self.client, self._clock)
                self._reader.start()
            for asked, got in self._reader.take():
                self._polled(got, asked)
        elif now >= self._next_poll:
            self._next_poll = now + POLL_INTERVAL
            self._poll(now)
        position = self.position(now)
        if position is not None and self._game_says and self._game_clock.trusted(now) and not self._game_clock.settled():
            position = None  # (going on again after a pause, or after a seek ahead: quiet a moment, not in a rush)
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
                self._sum_late(now)
                self._record({"t": now, "stopped": True})
            return
        if not self._playing:
            self._record({"t": now, "playing": round(position, 3), "rate": self._rate(now),
                          "from": "game" if self._game_clock.trusted(now) else "companion"})
            self._player.seek(position + self.offset, sounding=JUST_STRUCK)  # (from about where it is)
        self._player.retime(rate=self._rate(now))  # Riff Repeater's speed: the keys flash for it
        was = self._player.position if self._playing else None
        if was is not None:
            self._count_late(now, late - TICK)
        if was is not None and late > STALL_TRACED:  # (held up: its notes late, or skipped)
            self._record({"t": now, "stall": round(late, 3), "at": round(position, 3)})
        if was is not None and position + self.offset > was + SEEK_AHEAD:
            self._record({"t": now, "skipped": round(was, 3), "to": round(position + self.offset, 3)})
        self._player.advance(position + self.offset)
        self._playing = True

    def _ask_game_time(self, now: float) -> None:
        """The game's own time read (riffloop's); unanswered a few times (no game, or a mod without it), asked
        again a while later."""
        assert self._game_time is not None
        got = self._game_time()
        if got is None:
            self._game_misses += 1
            again = GAME_TIME_BACK_OFF if self._game_misses >= GAME_TIME_MISSES else GAME_TIME_EVERY
            self._next_game_time = now + again
            if self._game_misses >= GAME_TIME_MISSES and self._game_says:  # (the game gone: RS Companion, if it's there)
                self._game_says = False
                self._unload()
            return
        self._game_misses = 0
        self._next_game_time = now + GAME_TIME_EVERY
        timer, screen = got[0], got[1]
        arrangement = got[2] if len(got) > 2 else ""
        if arrangement != "":
            if not self._game_says:
                self._status("Connected to Rocksmith")
                self._unload()  # (RS Companion's song, if any: the game's own from now)
                self._game_says = True
            self._game_arrangement(arrangement, now)
        elif self._game_says:  # (a mod that doesn't say, now: RS Companion's)
            self._game_says = False
            self._unload()
        if self._player is None:
            return
        if timer is None:  # (no song's playback seen: RS Companion's timer, till it is)
            self._game_clock.reset()
            return
        self._game_clock.update(timer, now, paused=bool(screen and screen.endswith("_Pause")))
        if self._trace is not None and now - self._game_clock.jumped_at < TRACE_AFTER_JUMP:  # (how it goes on)
            self._record({"t": round(now, 4), "read": timer, "screen": screen, "settled": self._game_clock.settled()})

    def _game_arrangement(self, name: str | None, now: float) -> None:
        """The arrangement the game says plays (its .sng's name; None: no song's playback, so none), opened as it
        changes."""
        if name is None:
            if self.current is not None or self._player is not None:
                self._record({"t": now, "game": None})
                self._unload()
            return
        if self.current is not None and _sng_name(self.current) == name.lower():
            return
        if name == self._game_unknown:
            return
        ref = self.index.by_sng(name)
        if ref is None:
            self._status("Looking for new songs...")
            self.index.refresh()
            ref = self.index.by_sng(name)
        self._unload()
        self._record({"t": now, "game": name})
        if ref is None:
            self._game_unknown = name
            self._status(f"Arrangement {name} isn't in the game's archives")
            return
        self._arrangement_id = ref.persistent_id
        self._player = self._open_ref(ref)
        if self._player is None:  # (its notes unreadable: not tried again till another plays)
            self._game_unknown = name

    def _poll(self, now: float) -> None:
        try:
            got: GameState | OverlayError = self.client.read()
        except OverlayError as exc:
            got = exc
        self._polled(got, now)

    def _polled(self, got: GameState | OverlayError, now: float) -> None:
        """RS Companion's state (or why it couldn't be read), as it was asked at now."""
        if isinstance(got, OverlayError):
            exc = got
            self._record({"t": now, "error": str(exc)})
            if self._connected is not False:
                self._status("Waiting for RS Companion's overlay API (Settings > Stream overlays)")
                self._connected = False
            self._unload()
            return
        state = got
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
            if state.timer != self._companion_timer:  # (a timer just read: the game's own time checked against it)
                self._game_clock.check(state.timer, now)
            self._companion_timer = state.timer
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
        return self._open_ref(ref)

    def _open_ref(self, ref: ArrangementRef) -> Player | None:
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

    def plan_now(self) -> list[PlannedPart]:
        """The parts as they'd be planned now for the arrangement played (the choices as they are: the ones in play
        can be a moment behind them, a remote's taps applying once they stop); none while nothing's played."""
        return self._plan(self.current) if self.current is not None and self._planned else []

    def _plan(self, current: ArrangementRef, voices_for: VoicesFor | None | bool = False,
              parts_for: PartsFor | None | bool = False, keep_muted: bool = False) -> list[PlannedPart]:
        """The parts to play while this arrangement is played, read when first needed (with voices and parts other
        than the ones it has, if they're given). Keeping those muted: the parts it plays now that aren't chosen any
        more stay in it muted (they can come back at once)."""
        voices = self._voices_for if voices_for is False else voices_for
        parts_of = self._parts_for if parts_for is False else parts_for
        chosen = list(parts_of(current) if callable(parts_of) else [(current, 4)])
        if keep_muted:
            have = {ref.persistent_id for ref, *_rest in chosen}
            chosen += [(p.part.ref, p.part.lights, False) for p in self._planned
                       if p.part.ref.persistent_id not in have and p.part.ref.persistent_id in self._loaded]
        parts = []
        for ref, lights, *played in chosen:
            arrangement = self._loaded.get(ref.persistent_id)
            if arrangement is None:
                try:
                    arrangement = self._loaded[ref.persistent_id] = self._load(ref)
                except Exception as exc:  # a broken song file: the other parts play on
                    self._status(f"{ref.title} ({ref.name}): can't read its notes: {exc}")
                    continue
            parts.append(Part(ref, arrangement, lights, muted=bool(played) and not played[0]))
        return plan(parts, voices if callable(voices) else None, strict=False,
                    first=current, cache=self._made)  # (one that doesn't fit the keyboard's channels: just lit)

    def set_extras(self, extras_for: Callable[[ArrangementRef], Sequence[Track]] | None,
                   muted_for: Callable[[ArrangementRef], bool] | None = None) -> None:
        """Tracks to play besides the parts, for the arrangement played (its song's drum part), or none, and whether
        they're muted (in the player: they can come back at once); the song playing goes on with them from where it
        is."""
        self._extras_for, self._extras_muted_for = extras_for, muted_for
        if self._player is None or self.current is None:
            return
        if (list(extras_for(self.current)) if extras_for else []) == self._extras:  # (the same: what plays goes on
            self._player.mute(self._player.muted - {DRUMS} | ({DRUMS} if self._extras_muted() else set()))  # as it
            return  # is, its notes sounding; muted or back, at once)
        player = self._player_with(self._planned)
        player.retime(rate=self._player.rate)
        self._player.stop()
        self._player = player  # the next step seeks it to where the song is

    def _extras_muted(self) -> bool:
        return bool(self._extras_muted_for and self.current is not None and self._extras_muted_for(self.current))

    def _mutes(self, parts_for: PartsFor | None) -> set[str] | None:
        """The parts to mute as parts_for has them now (the same parts, lighting the same keys) in the player as it
        is; None if it can't (a part to play that it hasn't, or has without a sound)."""
        current, player = self.current, self._player
        if player is None or current is None or not callable(parts_for):
            return None
        by_id = {p.part.ref.persistent_id: p for p in self._planned}
        chosen = {ref.persistent_id: (lights, bool(played[0]) if played else True)
                  for ref, lights, *played in parts_for(current)}
        if not set(chosen) <= set(by_id):  # (one to play it hasn't)
            return None
        muted = {DRUMS} if self._extras_muted() else set()
        for pid, p in by_id.items():
            lights, play = chosen.get(pid, (p.part.lights, False))  # (not chosen now: muted)
            if not play:
                muted.add(p.part.ref.part)
            elif p.part.lights != lights or (callable(self._voices_for) and p.voices is None):
                return None
        return muted

    def prepare_mutes(self, parts_for: PartsFor | None) -> None:
        """What mute_parts needs made first, changing nothing (Player.prepare_mute)."""
        muted, player = self._mutes(parts_for), self._player
        if muted is not None and player is not None:
            player.prepare_mute(muted)

    def mute_parts(self, parts_for: PartsFor | None) -> bool:
        """The parts muted and unmuted as parts_for has them now (the same parts, lighting the same keys), at once
        (Player.mute): nothing else stopped or struck again. False if the player can't (a part to play that it
        hasn't, or has without a sound): then set_parts."""
        muted, player, current = self._mutes(parts_for), self._player, self.current
        if muted is None or player is None or current is None:
            return False
        player.mute(muted)
        self._planned = [replace(p, part=replace(p.part, muted=p.part.ref.part in muted)) for p in self._planned]
        self._parts_for = parts_for
        self._status(f"{current.title} ({current.name}): {self._describe(self._planned) or 'lights only'}")
        return True

    def _player_with(self, planned: list[PlannedPart]) -> Player:
        self._planned = planned
        self._extras = list(self._extras_for(self.current)) if self._extras_for and self.current is not None else []
        return player_for(planned, self.keyboard, self.lights_early, self.fastest_flash, extra=self._extras,
                          extra_muted=self._extras_muted(),
                          cache=self._made)

    def _describe(self, planned: list[PlannedPart]) -> str:
        """How the parts play; for the arrangement played alone on every key, just its voices."""
        heard = [p for p in planned if not p.part.muted]
        if [(p.part.ref, p.part.lights) for p in heard] == [(self.current, 4)]:
            return heard[0].voices.describe() if heard[0].voices else ""
        return "; ".join(
            f"{p.part.ref.name or p.part.ref.part} lighting {_keys(p.part.lights)}, "
            f"{p.voices.describe() if p.voices else 'silent'}"
            for p in heard
        ) or "nothing plays"

    def playing(self) -> list[dict[str, object]]:
        """What plays, part by part: its lit keys, notes, octaves up, and tones (None: silent)."""
        out: list[dict[str, object]] = []
        for p in self._planned:
            if p.part.muted:
                continue
            voices = p.voices
            out.append({
                "name": p.part.ref.name or p.part.ref.part, "lights": p.part.lights, "notes": len(p.lights),
                "octaves": p.shift // 12,
                "tone": _tone_text(voices.main.tone) if voices else None,
                "mute": _tone_text(voices.mute.tone) if voices and voices.mute else None,
                "pinch": _tone_text(voices.squeal.tone) if voices and voices.squeal else None,
            })
        return out

    def _count_late(self, now: float, behind: float) -> None:
        """A step behind its time (seconds after its TICK, while the notes play): counted, summed up in the trace
        every LATE_SUMMARY seconds."""
        if self._trace is None:
            return
        late = self._late
        late.setdefault("since", now)
        late["steps"] = late.get("steps", 0) + 1
        late["worst"] = max(late.get("worst", 0.0), behind)
        for ms in LATE_SUMMED:
            if behind * 1000 > ms:
                late[f">{ms}ms"] = late.get(f">{ms}ms", 0) + 1
        if now - late["since"] >= LATE_SUMMARY:
            self._sum_late(now)

    def _sum_late(self, now: float) -> None:
        """The steps' lateness since the last summary, in the trace: how many, how many later than each of
        LATE_SUMMED, and the latest (seconds)."""
        late, self._late = self._late, {}
        if late.get("steps"):
            self._record({"t": now, "steps": int(late["steps"]), "worst": round(late["worst"], 3),
                          **{f">{ms}ms": int(late.get(f">{ms}ms", 0)) for ms in LATE_SUMMED}})

    def _record(self, entry: dict[str, object]) -> None:
        if self._trace is not None:
            self._trace(entry)

    def _unload(self) -> None:
        if self._player is not None:
            self._player.stop()
        self._player = None
        self.current = None
        self._loaded, self._planned = {}, []
        self._made = PlanCache()
        self._playing = False
        self._arrangement_id = ""
        self._song_clock.reset()
        self._game_clock.reset()


def _sng_name(ref: ArrangementRef) -> str:
    """An arrangement's .sng's name, as the game names it (songs/bin/macos/amonpurs_lead.sng: amonpurs_lead)."""
    return ref.sng.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()


def _sound_of(planned: list[PlannedPart]) -> list[tuple[str, int, bool, int, object, bool]]:
    """What makes parts play differently: which, their lit keys (pooled or not), octave shift, voices, muted."""
    return [(p.part.ref.persistent_id, p.part.lights, p.pooled, p.shift, p.voices, p.part.muted) for p in planned]


def _tone_text(tone: Tone) -> str:
    return f"{tone.number:03d} {tone.name}"


def _keys(count: int) -> str:
    return "no keys" if count == 0 else "1 key" if count == 1 else f"{count} keys"
