from __future__ import annotations

import math
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol, Sequence

from .sng import HAMMER_ON, PALM_MUTE, PULL_OFF
from .songkey import SHARPS, Key
from .lights import KeyRange
from .tones import DRUM_SET_BANK, tone

# A warm-up before playing: six short drills in turn, about five minutes, with a click on the keyboard's drums.
# Each drill is a pattern of notes in beats (a pass), played round again (some move up a fret each pass). Each is
# read first, then shown (the keyboard plays its first notes, on a guitar), then counted in and played. Its speed is
# in notes a minute: SLOWEST at first, a click each note; faster, two notes a click, then four, so the click stays
# one to follow; kept as it's left. In E standard, as the guitar is with nothing between it and the amp; the scale
# is the song's (as its fingers play it), else A minor.

E_STANDARD = (40, 45, 50, 55, 59, 64)  # each string's open pitch, low to high
DRILL_SECONDS = 30.0  # each drill's playing, after it's shown (then on to the next as a group of clicks ends)
READY_SECONDS = 4.0  # before a drill's shown, to read it
COUNT_IN = 4  # clicks before your turn
GROUP = 4  # clicks a group, the first accented
SLOWEST = 40  # notes a minute: each drill's speed at first, a click each note
SPEED_STEP = 4  # clicks a minute, a speed's change
CLICK_MOST = 120  # clicks a minute: faster, two notes a click (then four: a click a beat), the click half as fast
CLICK_FASTEST = 240  # clicks a minute at the most notes a click
DRUM_CHANNEL = 10
CLICK_KIT = 0  # the keyboard's Standard drum set
CLICK = 77  # Low Wood Block: each click...
ACCENT = 76  # ...and a group's first, Hi Wood Block
CLICK_VELOCITY, ACCENT_VELOCITY = 90, 120
SHOW_CHANNEL, SHOW_MUTE_CHANNEL = 11, 12  # (song parts' channels: no song plays meanwhile)
SHOW_TONE, SHOW_MUTE_TONE = 75, 77  # CLEAN GUITAR 1, and MUTE GUITAR for palm mutes
SHOW_VELOCITY = 100
LATE = 0.1  # clicks: a click or note this late (the thread held up) is left out, not played in a burst
WAIT_MOST = 0.02  # seconds the thread waits at most (a change heard at once)
MINOR_MODES = ("minor", "Dorian", "Phrygian")  # (the minor pentatonic; else the major)
STANDARD = (0, 5, 10, 15, 19, 24)  # (a standard tuning's strings, from its lowest)
TUNINGS = tuple((f"{name} standard", tuple(40 - down + s for s in STANDARD)) for down, name in
                enumerate(("E", "Eb", "D", "C#", "C", "B", "Bb", "A"))) + \
    tuple((f"Drop {name}", (40 - down - 2,) + tuple(40 - down + s for s in STANDARD[1:])) for down, name in
          enumerate(("D", "C#", "C", "B", "A#", "A")))  # (name, each string's open pitch)
SCALES = {  # a scale's steps from its root (pentatonic: minor or major, by the song's key)
    "minor pentatonic": (0, 3, 5, 7, 10), "major pentatonic": (0, 2, 4, 7, 9), "blues": (0, 3, 5, 6, 7, 10),
    "natural minor": (0, 2, 3, 5, 7, 8, 10), "major": (0, 2, 4, 5, 7, 9, 11), "harmonic minor": (0, 2, 3, 5, 7, 8, 11),
    "Dorian": (0, 2, 3, 5, 7, 9, 10), "Phrygian": (0, 1, 3, 5, 7, 8, 10), "Lydian": (0, 2, 4, 6, 7, 9, 11),
    "Mixolydian": (0, 2, 4, 5, 7, 9, 10),
}
ROOT_UNDER_TWO = ("major pentatonic", "major", "Lydian", "Mixolydian")  # (their shape: the root under finger 2)
LIGHT_FOR = 0.8  # of a note's length its key's lit (the same key again: it flashes)
# Your playing heard (listen's Onsets), each note judged once its time's passed: the start nearest its time (the
# click heard, and your own usual timing) with its pitch (or an octave off: YIN's slip), else one with no pitch heard
# (given the benefit of the doubt: a low note with another string ringing on can't be told from its neighbours),
# else the nearest (wrong); in time if it's near your usual timing. A round's notes (ROUND_CLICKS clicks' worth) judged together: clean, faster; sloppy
# twice in a row, slower (the speed ladder); nothing heard, as if not played (not judged).
CLICK_HEARD = 0.01  # seconds the click takes to sound (MIDI, the keyboard)
JUDGE_WINDOW = 0.2  # seconds either side of a note's time its start's looked for (at most half the gap between notes)
PITCH_WAIT = 0.15  # seconds after that a start's pitch can still come
IN_TIME = 0.08  # seconds off your usual timing that's in time, at most (a quarter of the gap between notes)...
IN_TIME_LEAST = 0.03  # ...and at least
USUAL_MOST = 0.08  # seconds late (or early) your usual timing's taken as at most
USUAL_NOTES = 24  # the notes it's from
ROUND_CLICKS = 8
CLEAN, SLOPPY = 0.9, 0.6  # a round's notes right and in time: this much, faster; less than this, sloppy
RIGHT = ("ok", "early", "late")  # (the right note)


@dataclass(frozen=True)
class Step:
    """A drill's note: its beat in the pass, its string (0: the lowest) and fret, the finger that frets it (0:
    open) and how long it is (beats); its techniques as charted songs have them (sng's: hammer-on, palm mute...)."""

    beat: float
    string: int
    fret: int
    finger: int
    length: float = 0.25
    mask: int = 0


@dataclass(frozen=True)
class Drill:
    key: str  # its name for the speed kept
    name: str
    works: str  # what it works on
    how: str  # how to play it well
    grid: float  # beats between its notes (0.25: sixteenths; 0.5: eighths), the notes its speed counts
    beats: float  # a pass's
    passes: tuple[tuple[Step, ...], ...]  # each pass's notes, in turn, then round again
    demo: int = 8  # its first notes, shown before it's played
    scale: tuple[int, ...] = ()  # the scale drill: its pitch classes (from the root), for the fretboard
    names: tuple[str, ...] = ()  # and their names
    tuning: tuple[int, ...] = E_STANDARD  # each string's open pitch, low to high

    def pitch(self, step: Step) -> int:
        return self.tuning[step.string] + step.fret


def per_click(drill: Drill, speed: int) -> int:
    """Notes a click at a speed (notes a minute): one while the click's CLICK_MOST a minute at most, then two,
    then four (a click a beat, at most)."""
    most, per = round(1 / drill.grid), 1
    while speed / per > CLICK_MOST and per < most:
        per *= 2
    return per


def fastest(drill: Drill) -> int:
    return CLICK_FASTEST * round(1 / drill.grid)


def kept_speed(drill: Drill, speed: Any) -> int:
    """A speed kept for a drill, if it's one (SLOWEST up, SPEED_STEP at a time); else SLOWEST."""
    if isinstance(speed, int) and not isinstance(speed, bool) and SLOWEST <= speed <= fastest(drill) \
            and speed % SPEED_STEP == 0:
        return speed
    return SLOWEST


def stepped(drill: Drill, speed: int, step: int) -> int:
    """A speed SPEED_STEP clicks a minute faster (step 1), at the notes a click it has; or slower (-1): the speed a
    step faster goes from to here (a click each note, or two, or four), else as many fewer as it has."""
    if step > 0:
        speed += SPEED_STEP * per_click(drill, speed)
    elif step < 0:
        below = next((speed - SPEED_STEP * per for per in (1, 2, 4)
                      if per_click(drill, speed - SPEED_STEP * per) == per), None)
        speed = below if below is not None else speed - SPEED_STEP * per_click(drill, speed)
    return min(max(speed, SLOWEST), fastest(drill))


def _sixteenths(places: Sequence[tuple[int, int, int]], start: float = 0.0, mask: Callable[[int], int] | None = None,
                each: float = 0.25) -> list[Step]:
    """Notes one after another (string, fret, finger), each a sixteenth (or each beats) long."""
    return [Step(start + i * each, string, fret, finger, each, mask(i) if mask else 0)
            for i, (string, fret, finger) in enumerate(places)]


def chromatic(positions: Sequence[int] = (5, 6, 7, 8, 7, 6)) -> Drill:
    """1-2-3-4 up the strings and 4-3-2-1 down, one finger a fret, a fret higher each pass (then back down)."""
    passes = []
    for at in positions:
        up = [(string, at + f, f + 1) for string in range(6) for f in range(4)]
        down = [(string, at + f, f + 1) for string in reversed(range(6)) for f in reversed(range(4))]
        passes.append(tuple(_sixteenths(up + down)))
    return Drill("chromatic", "Chromatic 1-2-3-4", "alternate picking, both hands in time",
                 "One finger a fret. Pick down, up, down, up. Keep the fingers close to the strings.",
                 0.25, 12, tuple(passes), demo=8)


def spider(positions: Sequence[int] = (5, 6, 7, 6)) -> Drill:
    """Fingers 1 and 3 on two strings, then 2 and 4, string pair by string pair up, then the same back down; a
    beat's rest at the top and at the bottom."""
    passes = []
    for at in positions:
        up = [place for low in range(5) for place in
              ((low, at, 1), (low + 1, at + 2, 3), (low, at + 1, 2), (low + 1, at + 3, 4))]
        passes.append(tuple(_sixteenths(up) + _sixteenths(up[::-1], start=6.0)))
    return Drill("spider", "Spider (1-3, 2-4)", "finger independence and stretch",
                 "Fingers 1 and 3, then 2 and 4, on neighbouring strings. Keep each finger near its fret until it "
                 "plays.", 0.25, 12, tuple(passes), demo=8)


def string_skipping() -> Drill:
    """Octaves a string apart (the string between skipped): A, D, G, C up the neck's strings and back down, each
    pair twice, in eighths."""
    pairs = [((0, 5, 1), (2, 7, 3)), ((1, 5, 1), (3, 7, 3)), ((2, 5, 1), (4, 8, 4)), ((3, 5, 1), (5, 8, 4))]
    places = [place for pair in pairs + pairs[::-1] for place in (*pair, *pair)]
    return Drill("skipping", "String skipping (octaves)", "picking accuracy: the right string every time",
                 "Pick down on the low note, up on the high one. The low note's finger lightly mutes the string "
                 "between.", 0.5, 16, (tuple(_sixteenths(places, each=0.5)),), demo=8)


def legato() -> Drill:
    """Hammer-ons and pull-offs, picking only each string's first note: 5 h7 p5 h7, p5 h8 p5 h8, up the strings
    one pass and down the next."""
    passes = []
    for strings in (range(6), reversed(range(6))):
        places = [place for string in strings for place in
                  ((string, 5, 1), (string, 7, 3), (string, 5, 1), (string, 7, 3),
                   (string, 5, 1), (string, 8, 4), (string, 5, 1), (string, 8, 4))]
        passes.append(tuple(_sixteenths(places, mask=lambda i: 0 if i % 8 == 0 else
                                        HAMMER_ON if i % 2 else PULL_OFF)))
    return Drill("legato", "Legato trills", "fretting-hand strength: hammer-ons and pull-offs",
                 "Pick only the first note on each string. Hammer down with the fingertip; pull off a little "
                 "downwards, so the string sounds.", 0.25, 12, tuple(passes), demo=8)


def tuning_named(name: str | None) -> tuple[int, ...]:
    return dict(TUNINGS).get(name or "", E_STANDARD)


def tuning_name(tuning: Sequence[int]) -> str:
    return next((name for name, strings in TUNINGS if strings == tuple(tuning)), "its own tuning")


def scale_shape(steps: Sequence[int], tonic: int, tuning: Sequence[int] = E_STANDARD,
                start: int | None = None) -> tuple[tuple[tuple[int, int, int], ...], int]:
    """A scale in one place on the neck, low to high: its notes (string, fret, finger), each string's in four frets
    from start (a drop tuning's low string: two higher, as its notes are), stretching a fret further for a note the
    next string can't reach there; and the fret it's from (none given: its root on the lowest string under the first
    finger, an open string in open position; or a major one's under the second)."""
    scale = {(tonic + step) % 12 for step in steps}
    base = tuning[-1] - STANDARD[-1]  # (the lowest string's pitch, tuned as a standard tuning's)
    root = (tonic - base) % 12
    if start is None:  # (the root under finger 1, an open string's: open position; a major's under 2, up the neck
        # if that's behind the nut)
        start = (root - 1) % 12 if tuple(steps) in (SCALES[k] for k in ROOT_UNDER_TWO) else root
    lows = [max(start + (base + STANDARD[i] - tuning[i] if i < len(STANDARD) else 0), 0) for i in range(len(tuning))]
    places: list[tuple[int, int, int]] = []
    last = -1
    for string, open_ in enumerate(tuning):
        low = lows[string]
        reach = range(low, low + 5)  # (four frets, and a stretch)
        for fret in reach:
            pitch = open_ + fret
            if pitch % 12 not in scale or pitch <= last:
                continue
            if fret == low + 4 and string + 1 < len(tuning) and pitch - tuning[string + 1] >= lows[string + 1]:
                continue  # (the next string has it, in its own four frets)
            finger = 0 if fret == 0 else min(fret, 4) if low == 0 else min(max(fret - low + 1, 1), 4)
            places.append((string, fret, finger))
            last = pitch
    return tuple(places), start


def scale_drill(key: Key | None = None, kind: str = "pentatonic", tonic: int | None = None,
                tuning: Sequence[int] = E_STANDARD, start: int | None = None) -> Drill:
    """A scale up and down in one place on the neck, in eighths: the song's key's (its root, and for a pentatonic
    whether minor or major), else A minor; or a kind and key of its own (SCALES)."""
    song_tonic, mode = (key.tonic, key.mode) if key is not None else (9, "minor")
    tonic = song_tonic if tonic is None else tonic
    if kind not in SCALES:  # (a pentatonic: the song's minor or major)
        kind = "minor pentatonic" if key is None or mode in MINOR_MODES else "major pentatonic"
    steps = SCALES[kind]
    places, start = scale_shape(steps, tonic, tuning, start)
    spell = key.spell if key is not None and tonic == key.tonic else (lambda pc: SHARPS[pc % 12])
    scale = tuple((tonic + step) % 12 for step in steps)
    names = tuple(spell(pc) for pc in scale)
    roots = [f for s, f, _ in places if (tuning[s] + f) % 12 == tonic]
    where = f"from fret {start}" if start else "in open position"
    played = _sixteenths(list(places) + list(places[::-1]), each=0.5)
    return Drill("scale", f"{names[0]} {kind}", "the scale your song is made of" if key is not None and
                 tonic == key.tonic else f"the {names[0]} {kind} scale, {where}",
                 f"Root {names[0]} ringed red (fret {roots[0]} on the {_string_name(tuning, places[0][0])} string). Now "
                 f"and then, say each note's name as you play it.", 0.5, len(played) * 0.5, (tuple(played),),
                 demo=min(len(places), 12), scale=scale, names=names, tuning=tuple(tuning))


def _string_name(tuning: Sequence[int], string: int) -> str:
    return "low " + SHARPS[tuning[0] % 12] if string == 0 else SHARPS[tuning[string] % 12]


def gallops() -> Drill:
    """Palm-muted gallops (an eighth and two sixteenths) on the low string, then tremolo picking (sixteenths): a
    bar on the open string, a bar moving to the 3rd and 5th frets."""
    def gallop(beat: float, fret: int) -> list[Step]:
        finger = {0: 0, 3: 1, 5: 3}[fret]
        return [Step(beat, 0, fret, finger, 0.5, PALM_MUTE), Step(beat + 0.5, 0, fret, finger, 0.25, PALM_MUTE),
                Step(beat + 0.75, 0, fret, finger, 0.25, PALM_MUTE)]

    def tremolo(beat: float, fret: int) -> list[Step]:
        finger = {0: 0, 3: 1, 5: 3}[fret]
        return [Step(beat + i * 0.25, 0, fret, finger, 0.25, PALM_MUTE) for i in range(4)]

    frets = (0, 0, 0, 0, 0, 0, 3, 5)
    return Drill("gallops", "Gallops and tremolo picking", "picking-hand stamina and palm muting",
                 "Rest the side of your picking hand on the strings by the bridge. Small, loose wrist movements: "
                 "tense is slow.", 0.25, 8,
                 (tuple(s for beat, fret in enumerate(frets) for s in gallop(beat, fret)),
                  tuple(s for beat, fret in enumerate(frets) for s in tremolo(beat, fret))), demo=6)


def routine(key: Key | None = None, tuning: Sequence[int] = E_STANDARD, scale: dict[str, Any] | None = None,
            what: str = "routine") -> tuple[Drill, ...]:
    """The warm-up's drills (what: "routine"), or one of them on its own (its key), in a tuning; its scale the song's
    pentatonic, or one chosen (scale: kind, tonic, start)."""
    scale = scale or {}
    drills = tuple(replace(d, tuning=tuple(tuning)) for d in (chromatic(), spider(), string_skipping(), legato())) + \
        (scale_drill(key, str(scale.get("kind") or "pentatonic"), scale.get("tonic"), tuning, scale.get("start")),
         replace(gallops(), tuning=tuple(tuning)))
    chosen = tuple(d for d in drills if d.key == what)
    return chosen or drills


DRILL_KEYS = tuple(d.key for d in routine())


def _beats_a_second(drill: Drill, speed: int) -> float:
    return speed * drill.grid / 60


def _shown_until(drill: Drill, speed: int) -> float:
    """The beat a drill's showing ends at: its last note shown, to the end of that group of clicks."""
    steps = drill.passes[0]
    until = steps[drill.demo].beat if drill.demo < len(steps) else float(drill.beats)
    group = GROUP * per_click(drill, speed) * drill.grid
    return math.ceil(until / group - 1e-9) * group


def played_seconds(drill: Drill, speed: int, seconds: float | None = None) -> float:
    """A drill's playing: its passes, until seconds (DRILL_SECONDS) are up, the last one played to its end."""
    whole = drill.beats / _beats_a_second(drill, speed)
    return max(math.ceil((DRILL_SECONDS if seconds is None else seconds) / whole - 1e-9), 1) * whole


def drill_seconds(drill: Drill, speed: int, shown: bool, seconds: float | None = None,
                  ready: float | None = None) -> float:
    """About how long a drill takes: read, shown, counted in and played."""
    show = _shown_until(drill, speed) / _beats_a_second(drill, speed) if shown and drill.demo else 0.0
    count = COUNT_IN * per_click(drill, speed) * 60 / speed
    return (READY_SECONDS if ready is None else ready) + show + count + played_seconds(drill, speed, seconds)


def drill_data(drill: Drill, speed: int) -> dict[str, Any]:
    """A drill for the page: its speed, and each pass's notes as the tab and fretboard have a song's ([beat, end,
    [[string, fret]], [mask, 0, 0], finger])."""
    return {"key": drill.key, "name": drill.name, "works": drill.works, "how": drill.how, "speed": speed,
            "click": speed // per_click(drill, speed), "per": per_click(drill, speed), "demo": drill.demo,
            "grid": drill.grid,
            "beats": drill.beats, "strings": list(drill.tuning), "scale": list(drill.scale), "names": list(drill.names),
            "passes": [[[s.beat, s.beat + s.length, [[s.string, s.fret]], [s.mask, 0, 0], s.finger] for s in steps]
                       for steps in drill.passes]}


class ClickKeys(Protocol):
    def set_voice(self, channel: int, program: int, bank: int | None = None, ours: bool = True,
                  volume: int | None = ...) -> None:
        ...

    def play(self, channel: int, notes: tuple[int, ...], velocity: int = ...) -> None:
        ...

    def stop(self, channel: int, notes: tuple[int, ...]) -> None:
        ...

    def light(self, keys: tuple[int, ...]) -> None:
        ...

    def unlight(self, keys: tuple[int, ...]) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass
class Due:
    """A note of yours, due: when (on the clock), its pitch and techniques, where it is in the drill (its pass and
    place), the speed then; and, judged, what it was (RIGHT, "wrong", "missed") and how far off (seconds, from the
    click)."""

    time: float
    pitch: int
    mask: int
    passed: int
    index: int
    speed: int
    verdict: str | None = None
    off: float | None = None


class Warmup:
    """A warm-up's drills, in turn, in a thread of its own: a few seconds to read each (ready), its first notes
    played on the keyboard's guitar (shown, unless not wanted), its count-in, then your turn, its passes, for
    DRILL_SECONDS (on to the next as the pass then ends; one drill on its own, on until it's stopped); a click all
    along, and each note's key lit as it comes.
    Your playing heard (heard: each note's start; hearing(): whether it's listening), each note judged, and the
    speed changed as it goes (on_speed: told). Paused, skipped back and on, and faster or slower as it goes. It
    closes the keyboard when it's stopped, or done."""

    def __init__(self, drills: Sequence[Drill], keyboard: ClickKeys, speeds: dict[str, int] | None = None,
                 shown: bool = True, clock: Callable[[], float] = time.monotonic, seconds: float | None = None,
                 ready: float | None = None, ladder: bool = True, hearing: Callable[[], bool] | None = None,
                 on_speed: Callable[[str, int], None] | None = None, until_stopped: bool = False,
                 on_drill: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.drills = tuple(drills)
        self.keyboard = keyboard
        self.speeds = {d.key: kept_speed(d, (speeds or {}).get(d.key)) for d in self.drills}
        self.shown = shown  # (each drill shown first)
        self.ladder = ladder  # (faster as you get it right, slower as you don't)
        self.seconds = DRILL_SECONDS if seconds is None else seconds  # (each drill's playing)
        self.ready = READY_SECONDS if ready is None else ready
        self.until_stopped = until_stopped  # (played on and on: one drill on its own)
        self.index = 0
        self.paused = False
        self.done = False
        self.tallies: dict[str, list[int]] = {}  # each drill's notes judged, and right and in time
        self._hearing = hearing or (lambda: False)
        self._on_speed = on_speed
        self._on_drill = on_drill  # (told how each drill went, as it's over: for the progress log)
        self._finished: list[dict[str, Any]] = []  # (drills over, to tell)
        self._clock = clock
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._clicking: int | None = None  # the click sounding
        self._sounding: tuple[int, int] | None = None  # the note shown sounding: its channel, pitch
        self._lit: int | None = None  # the key lit
        self._light_until = 0.0  # (the beat it goes out)
        self._starts: deque[list[Any]] = deque(maxlen=256)  # your notes' starts heard: [time, pitch, used]
        self._offs: deque[float] = deque(maxlen=USUAL_NOTES)  # your right notes' timings (seconds from the click)
        self._changed: list[tuple[str, int]] = []  # (speeds the ladder changed, to tell)
        keyboard.set_voice(DRUM_CHANNEL, CLICK_KIT, DRUM_SET_BANK)
        for channel, number in ((SHOW_CHANNEL, SHOW_TONE), (SHOW_MUTE_CHANNEL, SHOW_MUTE_TONE)):
            keyboard.set_voice(channel, tone(number).program, tone(number).bank)
        self._begin(clock())

    @property
    def drill(self) -> Drill:
        return self.drills[min(self.index, len(self.drills) - 1)]

    @property
    def speed(self) -> int:
        return self.speeds[self.drill.key]

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rs-keylights warm-up", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._quit.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        elif self._thread is None:
            self.keyboard.close()

    def pause(self) -> None:
        with self._lock:
            if not self.paused and not self.done:
                self._ran += self._clock() - self._since
                self.paused = True
                self._sound(None)
                self._light(None)
                self._forget_due()

    def resume(self) -> None:
        with self._lock:
            if self.paused:
                self._since = self._clock()
                self.paused = False

    def go(self, index: int) -> None:
        """Another drill (skipped on, or back), from its start."""
        with self._lock:
            if 0 <= index < len(self.drills) and not self.done:
                now = self._clock()
                self._finish(now)
                self.index = index
                self._begin(now)
        self._tell()

    def your_turn(self) -> None:
        """Seen enough of the drill shown: counted in for it at once."""
        with self._lock:
            if self._phase == "show" and not self.done:
                self._play(self._clock())

    def change_speed(self, step: int) -> int:
        """The drill's speed a step faster or slower, at once (from the beat it's on); the speed it has."""
        with self._lock:
            self._set_speed(stepped(self.drill, self.speed, step), self._clock())
            self._sloppy, self._pending = 0, None
            return self.speed

    def heard(self, at: float, pitch: int | None) -> None:
        """A note of yours starting (listen's Onsets): when, and its pitch (None: not heard)."""
        with self._lock:
            self._starts.append([at, pitch, False])

    def expected(self) -> tuple[int, ...]:
        """The notes due about now (for hearing which one sounds, with another string ringing on)."""
        with self._lock:
            now = self._clock()
            near = [d.pitch for d in self._due if now - 0.3 <= d.time <= now + 0.05]
            if self._phase == "play" and not self.paused:
                upcoming = self._upcoming()
                if upcoming is not None:
                    near.append(self.drill.pitch(upcoming))
            return tuple(near)

    def info(self) -> dict[str, Any]:
        """Where it is: the drill; ready (and the seconds before it's shown), being shown, counting in (the beat
        below 0) or played; its speed, its click and notes a click; the seconds left of it and of the warm-up; your
        notes judged (this pass's and the last's: [pass, place, verdict]), the last round's, and each drill's."""
        with self._lock:
            now = self._clock()
            ran, phase, beat = self._elapsed(now), self._phase, self._beat(now)
            count = COUNT_IN * self._step / _beats_a_second(self.drill, self.speed)
            length = count + played_seconds(self.drill, self.speed, self.seconds)
            if self._ends is not None and phase == "play":  # (its last pass: to its end)
                length = ran + max(self._ends - beat, 0.0) / _beats_a_second(self.drill, self.speed)
            drill_left = max(length - ran, 0.0) if phase == "play" else length
            before = (max(self.ready - ran, 0.0) + (self._shown_end / _beats_a_second(self.drill, self.speed)
                                                    if self.shown and self.drill.demo else 0.0)) \
                if phase == "ready" else max(self._shown_end - beat, 0.0) / _beats_a_second(self.drill, self.speed) \
                if phase == "show" else 0.0
            after = sum(drill_seconds(d, self.speeds[d.key], self.shown, self.seconds, self.ready)
                        for d in self.drills[self.index + 1:])
            passed = max(math.floor(beat / self.drill.beats), 0) if phase == "play" else 0
            return {"drill": self.index, "drills": len(self.drills), "done": self.done, "paused": self.paused,
                    "name": self.drill.name, "names": [d.name for d in self.drills],
                    "phase": "done" if self.done else "count" if phase == "play" and beat < 0 else phase,
                    "ready_left": round(max(self.ready - ran, 0.0), 2) if phase == "ready" else 0.0,
                    "beat": round(beat, 3), "speed": self.speed, "click": self.speed // per_click(self.drill, self.speed),
                    "per": per_click(self.drill, self.speed), "rate": _beats_a_second(self.drill, self.speed),
                    "shown": self.shown, "shown_end": self._shown_end, "ladder": self.ladder,
                    "drill_left": None if self.until_stopped else round(drill_left, 1),
                    "drill_length": None if self.until_stopped else round(length, 1), "beats": self.drill.beats,
                    "left": None if self.until_stopped else round(0.0 if self.done else before + drill_left + after, 1),
                    "ending": self._ends, "until_stopped": self.until_stopped,
                    "marks": [[d.passed, d.index, d.verdict] for d in self._judged if d.passed >= passed - 1],
                    "judged": self._judged_count, "round": self._round, "usual_ms": round(self._usual() * 1000),
                    "tallies": {k: list(v) for k, v in self.tallies.items()}}

    def step(self, now: float) -> float:
        """Clicks and notes due by now, your notes judged, and on to the next part (shown, played) or drill (or
        done) as one ends; the seconds until there's more to do (at most WAIT_MOST)."""
        with self._lock:
            wait = self._step_now(now)
            changed, self._changed = self._changed, []
        for key, speed in changed:  # (told with the lock let go: whoever's told can ask where it is)
            if self._on_speed is not None:
                self._on_speed(key, speed)
        self._tell()
        return wait

    def _tell(self) -> None:
        """The drills over, told how they went (with the lock let go)."""
        with self._lock:
            finished, self._finished = self._finished, []
        for result in finished:
            if self._on_drill is not None:
                self._on_drill(result)

    def _finish(self, now: float) -> None:
        """(Holding the lock.) The drill going, over: how it went kept to tell, if it was played (a few seconds,
        or a note heard)."""
        if self._reported:
            return
        self._reported = True
        played = max(self._elapsed(now) - self._count_seconds, 0.0) if self._phase == "play" else 0.0
        if played < 5.0 and not self._run_notes:
            return
        self._finished.append({"drill": self.drill.key, "name": self.drill.name,
                               "tuning": tuning_name(self.drill.tuning), "start": self._started_speed,
                               "speed": self.speed, "notes": self._run_notes, "ok": self._run_ok,
                               "seconds": round(played, 1)})

    def _step_now(self, now: float) -> float:
        if self.paused or self.done:
            return WAIT_MOST
        if self._phase == "ready":
            ran = self._elapsed(now)
            if ran < self.ready:
                return min(self.ready - ran, WAIT_MOST)
            if self.shown and self.drill.demo:
                self._show(now)
            else:
                self._play(now)
        beat = self._beat(now)
        if self._phase == "show" and beat >= self._shown_end:
            self._play(now)
            beat = self._beat(now)
        if self._phase == "play":
            self._judge_due(now)
            if self._ends is None and not self.until_stopped and self._elapsed(now) >= self._count_seconds + self.seconds:
                whole = self.drill.beats
                self._ends = max(math.ceil(beat / whole - 1e-9) * whole, whole)  # (done as its pass ends)
            if self._ends is not None and beat >= self._ends:
                self._tick(None)
                self._light(None)
                self._judge_due(math.inf)  # (its last notes: judged with what's been heard)
                self._finish(now)
                if self.index + 1 >= len(self.drills):
                    self.done = True
                    return WAIT_MOST
                self.index += 1
                self._begin(now)
                return 0.0
        while self._next <= beat:
            number = round(self._next / self._step)
            if number % GROUP == 0 and self._pending is not None and self._phase == "play" and self._next >= 0:
                speed, self._pending = self._pending, None  # (the ladder's: as a group starts)
                self._set_speed(speed, now, at=self._next)
                self._changed.append((self.drill.key, speed))
                number = round(self._next / self._step)
            if beat - self._next < LATE * self._step:
                self._tick(number % GROUP == 0)
            self._next = self._click_at(number + 1)
        if self._lit is not None and beat >= self._light_until:
            self._light(None)
        if self._phase == "show":
            self._show_notes(beat)  # (after its click: the click's the time)
        elif self._phase == "play":
            self._play_notes(beat, now)
        due = [self._next]
        if self._phase == "show":
            steps = self.drill.passes[0][:self.drill.demo]
            if self._shown < len(steps):
                due.append(steps[self._shown].beat)
            due.append(self._shown_end)
            if self._sounding is not None:
                due.append(self._sounding_until)
        elif self._phase == "play":
            upcoming = self._upcoming_beat()
            if upcoming is not None:
                due.append(upcoming)
        if self._lit is not None:
            due.append(self._light_until)
        return min(max(min(due) - beat, 0.0) / _beats_a_second(self.drill, self.speed), WAIT_MOST)

    @property
    def _step(self) -> float:
        """Beats between clicks."""
        return per_click(self.drill, self.speed) * self.drill.grid

    def _click_at(self, number: int) -> float:
        return number * self._step

    def _set_speed(self, speed: int, now: float, at: float | None = None) -> None:
        """A new speed from now (or from a beat just passed), its clicks where they fall at it."""
        self._rebase(now)
        if at is not None:  # (from that beat, as if then)
            self._at -= (self._from - at) / _beats_a_second(self.drill, self.speed)
            self._from = at
        self.speeds[self.drill.key] = speed
        self._next = self._click_at(math.ceil(self._from / self._step - 1e-9))

    def _begin(self, now: float) -> None:
        """A drill from its start: ready, then shown, then counted in (the first note's key lit: there it starts)."""
        self._tick(None)
        self._sound(None)
        self._phase = "ready"
        self._ran, self._since = 0.0, now  # (seconds run in this phase, before the last pause, and since)
        self._from, self._at = 0.0, 0.0  # the beat at a time run (as the speed last changed)
        self._next = 0.0  # the beat of the next click
        self._ends: float | None = None  # the beat it's done at, its time up
        self._count_seconds = 0.0
        self._shown = 0  # the notes shown so far
        self._sounding_until = 0.0
        self._shown_end = _shown_until(self.drill, self.speed)
        self._cursor = (0, 0)  # your turn: the next note's pass and place
        self._due: list[Due] = []  # your notes due, not judged yet
        self._judged: deque[Due] = deque(maxlen=200)
        self._judged_count = 0
        self._round_notes: list[Due] = []
        self._round: dict[str, Any] | None = None  # the last round's: how it went
        self._sloppy = 0  # sloppy rounds in a row
        self._pending: int | None = None  # a speed the ladder's changing to
        self._started_speed = self.speed  # (how it went, for the progress log: its speed as it started, its notes
        self._run_notes = self._run_ok = 0  # judged and right and in time, and whether it's been told)
        self._reported = False
        self._light(self.drill.pitch(self.drill.passes[0][0]), until=math.inf)

    def _show(self, now: float) -> None:
        """Its first notes, played on the keyboard's guitar, with the click."""
        self._phase = "show"
        self._ran, self._since = 0.0, now
        self._from, self._at = 0.0, 0.0
        self._next, self._shown = 0.0, 0
        self._shown_end = _shown_until(self.drill, self.speed)

    def _play(self, now: float) -> None:
        """Your turn: counted in, then its passes (the first note's key lit)."""
        self._sound(None)
        self._phase = "play"
        self._ran, self._since = 0.0, now
        self._from, self._at = -COUNT_IN * self._step, 0.0
        self._next = self._from
        self._count_seconds = COUNT_IN * self._step / _beats_a_second(self.drill, self.speed)
        self._cursor = (0, 0)
        self._light(self.drill.pitch(self.drill.passes[0][0]), until=math.inf)

    def _show_notes(self, beat: float) -> None:
        if self._sounding is not None and beat >= self._sounding_until:
            self._sound(None)
        steps = self.drill.passes[0][:self.drill.demo]
        while self._shown < len(steps) and steps[self._shown].beat <= beat:
            note = steps[self._shown]
            self._shown += 1
            if beat - note.beat < LATE * self._step:
                self._sound(note)
                self._light(self.drill.pitch(note), until=note.beat + LIGHT_FOR * note.length)

    def _upcoming(self) -> Step | None:
        passed, index = self._cursor
        steps = self.drill.passes[passed % len(self.drill.passes)]
        return steps[index] if index < len(steps) else None

    def _upcoming_beat(self) -> float | None:
        note = self._upcoming()
        return None if note is None else self._cursor[0] * self.drill.beats + note.beat

    def _play_notes(self, beat: float, now: float) -> None:
        """Your notes as they come: each key lit, each note due (to be judged)."""
        while True:
            note, at = self._upcoming(), self._upcoming_beat()
            if note is None or at is None or at > beat:
                return
            passed, index = self._cursor
            steps = self.drill.passes[passed % len(self.drill.passes)]
            self._cursor = (passed, index + 1) if index + 1 < len(steps) else (passed + 1, 0)
            if beat - at < LATE * self._step:
                self._light(self.drill.pitch(note), until=at + LIGHT_FOR * note.length)
            if self._hearing():
                when = now - (beat - at) / _beats_a_second(self.drill, self.speed)
                self._due.append(Due(when, self.drill.pitch(note), note.mask, passed, index, self.speed))

    def _usual(self) -> float:
        """Your usual timing: seconds after the click you play the right notes (early: below 0), as far as it's
        taken."""
        if len(self._offs) < 4:
            return 0.0
        return max(min(statistics.median(self._offs), USUAL_MOST), -USUAL_MOST)

    def _judge_due(self, now: float) -> None:
        """Your notes whose time (and a start's pitch's) has passed, each judged by the start nearest it."""
        while self._due:
            due = self._due[0]
            gap = 60 / due.speed
            window = min(JUDGE_WINDOW, gap / 2)
            if now < due.time + CLICK_HEARD + USUAL_MOST + window + PITCH_WAIT:
                return
            self._due.pop(0)
            self._judge(due, window, max(IN_TIME_LEAST, min(IN_TIME, gap / 4)))

    def _judge(self, due: Due, window: float, in_time: float) -> None:
        heard_at = due.time + CLICK_HEARD
        middle = heard_at + self._usual()
        near = [s for s in self._starts if not s[2] and abs(s[0] - middle) <= window]
        right = [s for s in near if s[1] is not None and abs(s[1] - due.pitch) in (0, 12)]
        unheard = [s for s in near if s[1] is None]
        start = min(right or unheard or near, key=lambda s: abs(s[0] - middle), default=None)
        if start is None:
            due.verdict = "missed"
        elif not right and not unheard:
            due.verdict = "wrong"
        else:
            off = due.off = start[0] - heard_at
            self._offs.append(off)
            off -= self._usual()
            due.verdict = "ok" if abs(off) <= in_time else "early" if off < 0 else "late"
        if start is not None:
            start[2] = True
        self._judged.append(due)
        self._judged_count += 1
        tally = self.tallies.setdefault(self.drill.key, [0, 0])
        tally[0] += 1
        tally[1] += due.verdict == "ok"
        self._run_notes += 1
        self._run_ok += due.verdict == "ok"
        self._round_notes.append(due)
        if len(self._round_notes) >= ROUND_CLICKS * per_click(self.drill, due.speed):
            self._end_round()

    def _end_round(self) -> None:
        """A round's notes, judged together: clean, faster; sloppy twice in a row, slower (the ladder, on: as the
        next group of clicks starts); nothing heard, not judged (not played, or the guitar's not heard)."""
        notes, self._round_notes = self._round_notes, []
        right = [d for d in notes if d.verdict in RIGHT]
        ok = sum(d.verdict == "ok" for d in notes)
        offs = [d.off for d in right if d.off is not None]
        heard = any(d.verdict != "missed" for d in notes)
        one_speed = len({d.speed for d in notes}) == 1
        clean, sloppy = ok >= CLEAN * len(notes), ok < SLOPPY * len(notes)
        verdict = "nothing heard" if not heard else "new speed" if not one_speed else \
            "clean" if clean else "sloppy" if sloppy else "steady"
        if verdict == "clean" and self.ladder:
            faster = stepped(self.drill, self.speed, 1)
            if faster != self.speed:
                self._pending, verdict = faster, "faster"
            self._sloppy = 0
        elif verdict == "sloppy":
            self._sloppy += 1
            if self._sloppy >= 2 and self.ladder:
                slower = stepped(self.drill, self.speed, -1)
                if slower != self.speed:
                    self._pending, verdict = slower, "slower"
                self._sloppy = 0
        elif verdict in ("steady", "clean"):
            self._sloppy = 0
        self._round = {"notes": len(notes), "right": len(right), "ok": ok, "verdict": verdict,
                       "off_ms": round(statistics.mean(offs) * 1000) if offs else None}

    def _forget_due(self) -> None:
        """Notes due as it's paused (or moved): not judged."""
        self._due.clear()
        self._round_notes.clear()

    def _sound(self, note: Step | None) -> None:
        """A note shown, on the keyboard's guitar (palm muted: its muted guitar), the last one stopped; None: just
        stopped."""
        if self._sounding is not None:
            channel, pitch = self._sounding
            self.keyboard.stop(channel, (pitch,))
            self._sounding = None
        if note is not None:
            channel = SHOW_MUTE_CHANNEL if note.mask & PALM_MUTE else SHOW_CHANNEL
            self._sounding = (channel, self.drill.pitch(note))
            self._sounding_until = note.beat + note.length
            self.keyboard.play(channel, (self.drill.pitch(note),), SHOW_VELOCITY)

    def _light(self, pitch: int | None, until: float = 0.0) -> None:
        """A note's key lit (the last one's out), until a beat; None: just out."""
        key = KeyRange().fit(pitch) if pitch is not None else None
        if self._lit is not None:  # (the same key again: it flashes)
            self.keyboard.unlight((self._lit,))
        self._lit = key
        self._light_until = until
        if key is not None:
            self.keyboard.light((key,))

    def _elapsed(self, now: float) -> float:
        return self._ran + (0.0 if self.paused else now - self._since)

    def _beat(self, now: float) -> float:
        if self._phase == "ready":
            return self._from
        return self._from + (self._elapsed(now) - self._at) * _beats_a_second(self.drill, self.speed)

    def _rebase(self, now: float) -> None:
        self._from, self._at = self._beat(now), self._elapsed(now)

    def _tick(self, accent: bool | None) -> None:
        """A click (accent: a group's first); None: the last one stopped."""
        if self._clicking is not None:
            self.keyboard.stop(DRUM_CHANNEL, (self._clicking,))
            self._clicking = None
        if accent is not None:
            self._clicking = ACCENT if accent else CLICK
            self.keyboard.play(DRUM_CHANNEL, (self._clicking,), ACCENT_VELOCITY if accent else CLICK_VELOCITY)

    def _run(self) -> None:
        try:
            while not self._quit.is_set():
                wait = self.step(self._clock())
                if self.done:
                    break
                self._quit.wait(wait)
        finally:
            with self._lock:
                self._tick(None)
                self._sound(None)
                self._light(None)
                if not self.done:
                    self._finish(self._clock())  # (stopped: the drill going, as far as it got)
            self.keyboard.close()
            self._tell()
