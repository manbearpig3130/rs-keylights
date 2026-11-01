from __future__ import annotations

import threading
import time
from bisect import bisect_right
from collections import deque
from typing import Any, Callable, Protocol

import math

from .bitmap import (FASTEST, SLOWEST, WIDTH, Bitmap, arrangements_picture, keys_picture, lyrics_picture, mixer_picture,
                     parts_picture, pedal_picture, playback_picture, song_bar, song_picture, with_note, wrap)
from .launchkey import (ENCODER_BUTTONS, PLAY, RECORD, SIDE_BUTTONS, STATIONARY, TEMPORARY, TRACK_BUTTONS, Button,
                        Launchkey, Pad, ScaleMode, Strip, Turn, step_at)
from .chords import NAMES, chord_name
from .lyrics import Line
from .pedal import SLOTS as PEDAL_SLOTS
from .sng import Chord, note_name
from .tones import TONES, tone

# While the page runs: keeps a Launchkey MK4 as a remote, in step with the page, for the song followed, else
# the one playing, else the one picked; and shows each followed song's key as it starts.
#
# - The arrangements (the up arrow beside the knobs, lit): pads 1 to 6 on top, the song's arrangements (lit
#   when they play, the one you play in the game pulsing; a tap mutes one or plays it again, muted: as dim as
#   its colour goes); below them, their lit keys (a tap gives one more); knobs 1 to 6, each steps through its
#   arrangement's tones (or palm-mute or low-note tones: the up arrow again goes through them; on another MIDI device, its
#   programs). Column 7, its keys, a part of
#   their own playing the keyboard: the top pad turns them on or off, the bottom one whether they light the
#   keys, and knob 7 steps through their tone. While a tone shows on the screen, knob 8 steps through the groups
#   of tones; else it's all the volumes. Palm-mute and low-note tones start with the same tone as the rest (and
#   the default, when there is one), a group by itself. Turned to them, what they do shows for a while.
# - Playing the song (the down arrow, lit): knobs 1 and 2 move the A-B loop's start and end a beat at a time
#   (above the pads setting them; pad 1 tapped twice clears it), knob 3 moves through the song (or, while nothing
#   plays, where the song picked starts), knob 4 goes a section at a time (above the pad looping it), knob 5 sets
#   its speed; knob 8 all the volumes. Following, with Rocksmith paused, knob 3 steps through its song as its pause
#   screen's left and right arrows do, knob 4 a phrase at a time (its PageDown and PageUp). The pads on top set the loop: its start and end
#   where the song is, on or off, or the section it's in; below them, a pad for each kind of section (verse,
#   chorus...; the one it's in bright): a tap goes to the next one. They take the screen from the library. Pad 6
#   on top shows the lyrics instead of the song (again: the song), knob 6 and the pitch strip going through
#   them (Record: the song from the line shown; Track back: as they're sung, again the song); what the knobs and
#   pads do shows there too. Knob 7 picks whose chords show (auto, or an arrangement). The down arrow again shows
#   what they do (again: away).
# - The mixer (Func, lit; or knob 8 for a while, however it's used): knobs 1 to 6 are the arrangements'
#   volumes, knob 7 its keys', knob 8 all of them together, and the pads on top mute each (red) or play it
#   again, as they do outside it; the pitch strip moves the slider last used (a knob's, or a pad's on top; knob
#   8 or Mute: all of them). The pads below give lit keys, rails beside the sliders showing them. On the screen
#   until Func again or anything else is done (but the pads below and Rocksmith's keys). The pads are on the
#   arrangements while it's up, and back on playing the song after, if they were.
# - The right column: Mute (top pad: green sound, red just the lights; muted, every screen shows a crossed-out
#   speaker in its corner), and following the game (bottom pad).
# - Its Scale button: while its own Scale mode (which takes the song's key) is on, the song's scale is on the
#   pads as a piano octave (the tonic red), and climbs the keys as it goes on and as each song starts. Only
#   the button turns it off (the Launchkey's light and screen don't follow a message), so the pads stay too.
# - Play: plays and pauses (the screen shows which); following, Rocksmith's song (its riffloop mod: lit while it
#   plays, paused or playing on the screen, however it was paused).
# - Following, Rocksmith's menus (its riffloop mod typing its keys; lit): Track back and on are its up and down
#   arrows, the button right of the pads (top) its Escape (back), and Record its Enter. (Real pitch: on the page.)
# - The pitch strip goes through the library, a song at a time when slid slowly; flicked, it keeps going until
#   touched again; knob 8 goes a letter at a time (the artists'). The song it's on takes the screen while it's
#   in use; Record switches to it (on the song
#   already: stops it), but not while following (the game picks the song, its last staying shown until the
#   next). Not following, Track back is home: the screen back on the song (or waiting for one), the library too. The
#   modulation strip is its keys' modulation.
# - The screen: the song (with the chord being played in its corner: from the arrangement lighting the most keys,
#   the rhythm's first if they light as many, else from the next that has one there; whose, lit up before it: L,
#   R or B, numbered when a song has more than one; its tone changes said as they come, and the tone it's on lit
#   up at the right of the artist's line), and for a while what was
#   just done (its own display of the modulation strip aside);
#   what a tap turned on or off is said for a while at the bottom left of whatever it shows.

TICK = 0.03
INFO_EVERY = 0.25  # seconds between looks at what the page shows
LOOK_EVERY = 2.0  # seconds between looks for a Launchkey (plugged in, or out)
# Knob turns and taps are kept at once (the page shows them), but what they change plays this long after the
# last: the parts played again for each tone stepped through or lit key tapped would stop the notes sounding
# each time, and moving through the song or changing its speed starts it again
TURN_WAIT = 0.15
PAD_WAIT = 0.4  # lit keys
SECONDS_PER_STEP = 1.0  # knob 8
# The screen shows a picture of the song; for a while (this long after it's last asked for) with the speed (knob
# 7), real pitch on or off, or where knob 8 moves it, or instead the tone the knobs are on (an arrangement's, or
# its keys'); kept up to date, but sent at most this often
PICTURE_SHOWN, PICTURE_GAP = 3.0, 0.05
ARRANGEMENT_KNOBS = 5  # knobs (and pad columns) 1 to 5: the arrangements (one after the 5th has none: songs seldom
# have 6)
GUITAR_COLUMN = 5  # knob 6 and the pads under it: the guitar (its pedal's presets, whether it's heard)
KEYS_COLUMN = 6  # knob 7 and the pads under it: its keys, a part of their own
PEDAL_WAIT = 0.4  # seconds knob 6 rests on a preset before the pedal switches to it
PEDAL_JUMP = 10  # presets a step of knob 8, while the pedal's on the screen
LOOP_KNOBS, SEEK_KNOB = (0, 1), 2  # playing the song: knobs 1 and 2 the loop's start and end (beats: above their
# pads), knob 3 moves through it,
SECTION_KNOB, SPEED_KNOB, SPEED_STEP = 3, 4, 0.05  # knob 4 a section at a time (above the pad looping one), 5 its speed
# Following, paused, knobs 3 and 4 step through Rocksmith's song as its pause screen's keys do: a small step back
# or on, a phrase back or on (its PageDown and PageUp); at most GAME_STEPS a turn, and none while GAME_BEHIND keys
# are still to be typed (turned fast, the game doesn't fall behind)
GAME_SMALL_STEPS, GAME_PHRASES, GAME_STEPS, GAME_BEHIND = ("left", "right"), ("pagedown", "pageup"), 8, 4
GAME_KEY_TIME = 2 / 60  # seconds the game takes to type a key (pressed a frame, let go the next)
DOUBLE_TAP = 0.4  # seconds: pad 1 (the loop's start) tapped twice in this, the loop's cleared
SECTION_BACK = 2.0  # seconds into a section, knob 3 back goes to its start first
LOOP_PADS = ("a", "b", "on", "section")  # the pads on top: the loop's start and end here, on or off, the section
LYRICS_PAD, LYRICS_KNOB = 5, 5  # pad 6 on top: the lyrics on the screen (or the song); knob 6 goes through them
CHORDS_KNOB = 6  # knob 7: whose chords show (auto, or an arrangement's)
LYRICS_ON, LYRICS_OFF = 53, 55  # its pad: magenta, bright while they show
ACCELERATOR_PAD, ACCELERATOR_ON, ACCELERATOR_OFF = 0, 21, 23  # following, the pad below on the left: Riff Repeater's
# Accelerator (green, bright while it's on)
LYRICS_EARLY = 0.1  # seconds: a syllable lit this early
LYRICS_HOLD = 1.5  # seconds a line stays after its last syllable (then the next shows, waiting)
LYRICS_BACK = 4.0  # seconds after going through them, the lyrics are back with the song
LYRICS_LEAD_IN = 1.0  # seconds of the song before a line Record goes to
STRIP_LINE = 4  # the pitch strip's steps a line, going through the lyrics
SECTION_PADS = 7  # the pads below: a kind of section each, the song's most common
SECTION_COLOURS = {"intro": (100, 100, 100), "outro": (100, 100, 100), "verse": (0, 45, 127), "prechorus": (0, 90, 110),
                   "chorus": (127, 50, 0), "postchorus": (127, 90, 0), "hook": (127, 90, 0), "bridge": (80, 0, 127),
                   "solo": (127, 0, 0), "riff": (0, 127, 20), "interlude": (0, 110, 70), "breakdown": (127, 0, 70)}
OTHER_SECTION, SECTION_DIM = (70, 70, 110), 0.12  # (the others: grey-blue), and the kinds it's not in
LOOP_SET, LOOP_UNSET, LOOP_ON, LOOP_OFF, LOOPED, NOT_LOOPED = 9, 11, 21, 23, 37, 39  # the loop's pads: palette colours
VOLUME_STEP = 1  # the mixer's knobs: a step, of a volume (0 to 127, 100 as it starts)
FIELDS = (("tone", "tone"), ("mute_tone", "palm mutes"), ("low_tone", "low notes"))  # the tones knobs 1 to 6
# step through, the up arrow (again) going from one to the next
STRIP_MIDDLE = 8192  # the pitch strip springs back here when let go (and a slide can pass through it)
SONGS_PER_STEP = 0.25  # slid slowly (a step: a 128th of the strip)
# A finger put down again (maybe somewhere else) rather than sliding: a jump of more than NEW_TOUCH_STEPS after
# a gap of NEW_TOUCH seconds. A fast slide reports about every 9 ms, jumping up to 30-odd steps; a slow one
# can wait 100 ms and more between steps of 1 or 2.
NEW_TOUCH, NEW_TOUCH_STEPS = 0.06, 4
FLICK_SPEED = 180.0  # steps a second: let go this fast, it's a flick
FLICK_WINDOW = 0.1  # seconds of steps before letting go that make its speed
SETTLE = 0.1  # let go slowly: the steps this long before are undone (a finger coming off moves it a step or two)
LIFTED = 0.08  # seconds without a step: lifted off (or held still), when letting go isn't seen
BROWSE_SHOWN = 5.0  # seconds the screen shows the song the pitch strip is on, after it's last used
# Following, buttons that are Rocksmith's keys (its menus): Track back and on its up and down arrows, the button
# right of the pads (top) its Escape, Record its Enter
GAME_KEYS = {TRACK_BUTTONS[0]: "up", TRACK_BUTTONS[1]: "down", SIDE_BUTTONS[0]: "escape", RECORD: "enter"}
NOT_FOLLOWING_KEYS = ("Rocksmith's menus:", "follow it first")
FLICK_GAIN, FLICK_FADE = 1.5, 0.8  # a flick's songs a second for its steps a second, and how it slows (seconds)
BUILT_IN = {"lead": 79, "rhythm": 79, "bass": 85}
CHORD_EARLY = 0.15  # seconds: a chord's name shows this early, a little before it's played
CHORDS_FIRST = {"rhythm": 0, "lead": 1, "bass": 2}  # whose chords, of arrangements lighting as many keys
KIND_LETTERS = {"lead": "L", "rhythm": "R", "bass": "B"}  # whose chord it is, beside it
TONE_BADGES = {"clean lead": "Clean ld", "distortion": "Dist", "acoustic": "Acoustic", "electric piano": "E.piano",
               "driven bass": "Drv bass", "synth bass": "Syn bass", "its own tone": "Own"}  # the tone the song's on
# (the rest: capitalised)
COLOURS = {"lead": (9, (127, 45, 0)), "rhythm": (21, (0, 127, 20)), "bass": (45, (0, 45, 127))}
TONIC, IN_SCALE, ALSO = 5, 3, 49  # palette colours: the scale view
SOUND_ON, SOUND_OFF, FOLLOWING, NOT_FOLLOWING = 21, 5, 37, 39  # the right column's pads
KEYS_ON, KEYS_RGB = 49, (60, 0, 127)  # its keys' pads: purple
GUITAR_ON, GUITAR_RGB = 13, (127, 100, 0)  # the guitar's pads: yellow
MUTED_PAD = 5  # an arrangement (or its keys) muted, in the mixer: red
MUTED_LEVEL = 0.08  # and outside it: its colour, as dim as it goes
LIT, DIM = (3, 127), (1, 20)  # a button's light: palette colour and brightness
CONTROLS = 7  # the column of the right-hand pads and the last knob
WHITE_KEYS = (0, 2, 4, 5, 7, 9, 11, 12)  # the scale view: an octave from C, white keys on the bottom pads
BLACK_KEYS = (1, 3, None, 6, 8, 10, None, None)  # and the black keys above them
TIMED = ("speed", "position", "section", "loop", "chords", "keys", "parts", "pedal", "mixer", "playback",
         "arrangements")  # pictures
# shown for a while
ON_THE_SONG = (None, "speed", "position", "section", "loop", "chords")  # the song's screen (or the lyrics), what it
# shows
TONE_NUMBERS = [t.number for t in TONES]
GROUPS = [t.number for i, t in enumerate(TONES) if i == 0 or TONES[i - 1].group != t.group]  # each one's first
SAME, DEFAULT = 0, -1  # palm mutes and low notes: the tone the rest has (as the page keeps it: over a default), or
# none of their own (their kind's default, when it has one); first, a group by themselves


class GuiProtocol(Protocol):
    settings: dict[str, Any]
    follow_settings: dict[str, Any]

    def remote_info(self) -> dict[str, Any]:
        ...

    def change_part(self, arrangement: str, fields: dict[str, Any], later: float = 0.0) -> None:
        ...

    def set_volumes(self, changes: dict[int, Any]) -> dict[str, Any]:
        ...

    def toggle_sound(self) -> bool:
        ...

    def toggle_follow(self) -> bool:
        ...

    def remote_play(self) -> str:
        ...

    def remote_seek(self, position: float) -> None:
        ...

    def set_loop(self, body: dict[str, Any]) -> dict[str, Any]:
        ...

    def toggle_real_pitch(self) -> bool:
        ...

    def set_play_options(self, body: dict[str, Any]) -> dict[str, Any]:
        ...

    def remote_dialling(self, dialling: dict[str, Any]) -> None:
        ...

    def browse_move(self, songs: float) -> dict[str, Any] | None:
        ...

    def browse_select(self) -> dict[str, Any] | None:
        ...

    def browse_letter(self, steps: int) -> dict[str, Any] | None:
        ...

    def song_position(self) -> float | None:
        ...

    def set_program(self, part: str, program: int) -> None:
        ...

    def remote_stop(self) -> str:
        ...

    def browse_at(self, song_id: str | None) -> dict[str, Any] | None:
        ...

    def show_key(self) -> None:
        ...

    def show_scale(self, body: dict[str, Any]) -> dict[str, Any]:
        ...

    def stop_scale(self) -> dict[str, Any]:
        ...

    def set_keys(self, body: dict[str, Any]) -> dict[str, Any]:
        ...

    def play_keys(self, message: list[int]) -> None:
        ...

    def release_keys(self) -> None:
        ...

    def toggle_listen(self) -> bool:
        ...

    def set_pedal(self, body: dict[str, Any], later: float = 0.0) -> dict[str, Any]:
        ...

    def pedal_name(self, slot: int | None) -> str:
        ...

    def set_game_riff(self, body: dict[str, Any]) -> dict[str, Any]:
        ...


class Watch:
    def __init__(self, gui: GuiProtocol, launchkey_factory: Callable[[], Launchkey] = Launchkey,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.gui = gui
        self._factory = launchkey_factory
        self._clock = clock
        self._sleep = sleep
        self.launchkey: Launchkey | None = None
        self.scale_view = False  # its own Scale mode is on (its Scale button): the pads show the song's scale
        self.mode = "parts"  # what the knobs and pads are on: "parts" (the arrangements and its keys) or "playback"
        self.field = 0  # the tones knobs 1 to 6 step through (one of FIELDS)
        self.mix_knobs = False  # the knobs are the volumes (the mixer)
        self._mixer_kept = False  # the mixer stays until it's closed (else it's up for a while: knob 8)
        self._mode_after = "parts"  # the mode the knobs and pads go back to as it closes (it has the arrangements)
        self._mix_slider = CONTROLS  # the mixer's slider last used (its knob; knob 8: all of them), the pitch
        # strip's while it's up
        self._mix_strip: tuple[int, int, float] | None = None  # the pitch strip in it: where, its step, and when
        self._speed_to: float | None = None  # the speed the pitch strip is setting
        self._pitch: int | None = None  # the pitch strip's last place (not where it springs back to)
        self._pitch_step: int | None = None  # and its step
        self._slide: tuple[float, float] | None = None  # its last step, and when
        self._moves: deque[tuple[float, float]] = deque()  # its recent steps: when, and how many
        self._flick = 0.0  # songs a second, while a flick goes on
        self._browsed: str | None = None  # the song the screen shows, going through the library
        self._browsed_text: tuple[str, str] | None = None  # its name and artist
        self._browsed_place = (0, 1)  # and its place in the library, and how many songs it has
        self._browse_until = 0.0  # when the screen goes back to the song
        self._last_step = 0.0
        self._info: dict[str, Any] | None = None
        self._song: tuple[bool, str, str | None] | None = None  # following?, the song and arrangement played, last seen
        self._drawn: dict[Any, Any] = {}  # what the Launchkey shows, so only changes are sent
        self._pending: dict[tuple[str, str], int] = {}  # (arrangement, tone field or "lights"; or "volume", its place):
        # sent, until the page's state has them (several turns come at once)
        self._settling = False  # turned or tapped: where it stopped shown again once it stops
        self._moving_to: float | None = None  # where knob 8 is taking the song
        self._turned_at = 0.0
        self._wait = TURN_WAIT  # how long after the last turn or tap they apply
        self._note: tuple[str | None, tuple[str, str] | None] = (None, None)  # said for a while: (line, lines), as
        # _say has them
        self._note_until = 0.0  # until then
        self._loop_turned = "a"  # the loop's end last turned (knob 1 or 2)
        self._tapped_a = -math.inf  # when pad 1 (the loop's start) was last tapped
        self._game_busy_until = -math.inf  # following: until when the game's typing the keys sent it
        self._followed_at: float | None = None  # where the game was last (following: its chord stays while it pauses)
        self.lyrics_view = False  # the lyrics on the screen instead of the song (pad 6 on top, playing the song)
        self._lyrics_home = False  # Track back was the last pressed, the lyrics showing: again, the song
        self._chords_from: str | None = None  # whose chords show (an arrangement's ID; None: picked as they go)
        self._chords_song: str | None = None  # (for the song they're picked in)
        self._tone_at: float | None = None  # where the song was, last looked at (a tone change passed since: said)
        self._lyric_line: float | None = None  # the line gone to (knob 6, or the pitch strip), until _lyrics_until
        self._lyrics_until = 0.0
        self._lyric_strip: tuple[int, int, float] | None = None  # the pitch strip going through them: where, its
        # step, and when
        self._part_turned = 0  # the arrangement whose knob was last turned
        self._picture_of: str | None = None  # the picture on the screen for a while: "speed", "position",
        # "section", "loop", "keys", "parts", "mixer" or "playback"
        self._picture_until = 0.0  # until then
        self._screen_shown: tuple[Any, ...] | None = None  # what the screen shows
        self._screen_at = -math.inf  # when it was last sent a picture
        self._next_info = 0.0
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def status(self) -> str:
        if not self.gui.settings.get("launchkey", True):
            return "off"
        return f"{self.launchkey.name}" if self.launchkey is not None else "not found"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rs-keylights watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Asks it to stop (join waits for it)."""
        self._quit.set()

    def join(self, timeout: float = 2.0) -> None:
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def step(self) -> None:
        now = self._clock()
        launchkey = self.launchkey
        used = False
        if launchkey is not None and self._info is not None:
            for event in launchkey.events():
                self._try(lambda: self._on(event, now))
                self._next_info, used = 0.0, True  # show what it did at once
        self._glide(now)
        if (self._settling or self._moving_to is not None or self._speed_to is not None) \
                and now - self._turned_at >= self._wait:
            self._apply_turns()
            self._next_info, used = 0.0, True
        if used:  # the page shows it as it happens
            self.gui.remote_dialling({"speed": self._speed_to, "position": self._moving_to})
        if now >= self._next_info:
            self._next_info = now + INFO_EVERY
            self._refresh()
        self._show_screen(now)

    def look(self) -> None:
        """Takes over a Launchkey that's plugged in (and wanted), and lets go of one unplugged."""
        wanted = bool(self.gui.settings.get("launchkey", True))
        if self.launchkey is not None and (not wanted or not self.launchkey.connected()):
            self._let_go()
        if self.launchkey is None and wanted:
            launchkey = self._factory()
            try:
                found = launchkey.open()
            except Exception:  # a port that went away while opening it: try again later
                found = False
            if found:
                launchkey.on_keys = self.gui.play_keys  # at once, on its MIDI thread
                self.launchkey, self._drawn, self._screen_shown, self._next_info = launchkey, {}, None, 0.0

    def _run(self) -> None:
        next_look = 0.0
        try:
            while not self._quit.is_set():
                if self._clock() >= next_look:
                    next_look = self._clock() + LOOK_EVERY
                    self.look()
                self.step()
                self._sleep(TICK)
        finally:
            self._let_go()

    def _let_go(self) -> None:
        if self.launchkey is not None:
            try:
                self.launchkey.close()
            except Exception:  # unplugged: nothing to give back
                pass
            try:
                self.gui.release_keys()  # no notes left on (or keys lit) for keys held as it went
            except Exception:  # no keyboard
                pass
        self.launchkey = None

    def _try(self, action: Callable[[], object]) -> bool:
        try:
            action()
            return True
        except Exception as exc:  # refused, as the page would be, or no keyboard: say why
            said = wrap(str(exc), WIDTH - 2, 2)
            self._say(self._clock(), lines=(said[0], said[1] if len(said) > 1 else ""))
            return False

    def _apply_turns(self) -> None:
        """Turns and taps stopped: the song's position and speed set."""
        self._settling = False
        if self._moving_to is not None:
            position, self._moving_to = self._moving_to, None
            self._try(lambda: self.gui.remote_seek(position))
        if self._speed_to is not None:
            speed, self._speed_to = self._speed_to, None
            if (self._info or {}).get("mode") == "follow":  # (Riff Repeater's)
                self._try(lambda: self.gui.set_game_riff({"speed": round(speed * 100)}))
            else:
                self._try(lambda: self.gui.set_play_options({"speed": speed}))

    def _refresh(self) -> None:
        info = self.gui.remote_info()
        self._pending.clear()  # (what was sent is in it)
        song = info["song"]
        seen = (info["mode"] == "follow", song["id"], song["played"]) if song else None
        sid = song["id"] if song else None
        if sid != self._chords_song:  # (another song: its chords picked as they go)
            self._chords_from, self._chords_song = None, sid
        if seen != self._song:
            self._lyric_line = None  # (another song: its lyrics as it's sung)
            if seen and seen[0] and (self._song is None or not self._song[0] or self._song[1] != seen[1]):
                self.gui.browse_at(song["id"])  # following, a song started: the library goes to it
            self._song = seen
            if seen and song.get("key") and self.scale_view:  # its Scale mode on: the new song's scale climbs
                self._try(lambda: self.gui.show_scale({"song": song["id"]}))
            elif seen and seen[0] and song.get("key") and self.gui.follow_settings.get("show_key", True):
                self.gui.show_key()  # on the keys as the song starts in the game
        self._info = info
        self._tone_news(info, self._clock())
        self._draw()

    def _tone_news(self, info: dict[str, Any], now: float) -> None:
        """A tone change the song just passed (not jumped to), said: the arrangement whose chords show's."""
        song = info.get("song")
        at = None if self._moving_to is not None or song is None else self._song_at(info)
        last, self._tone_at = self._tone_at, at
        if song is None or at is None or last is None or not 0 < at - last < 1.0 or not song.get("arrangements"):
            return
        a = self._chords_order(song)[0]
        passed = [said for when, said in a.get("tone_changes") or () if last < when <= at]
        if passed:
            self._say(now, line=f"{a['name']}: {passed[-1]}")

    # The Launchkey

    def _draw(self) -> None:
        launchkey = self.launchkey
        if launchkey is None:
            return
        wanted = self._picture()
        for spot, look in wanted.items():
            if self._drawn.get(spot) == look:
                continue
            if spot == "scale":
                if look is not None:
                    launchkey.scale(*look)
            elif isinstance(spot, int):  # a button
                launchkey.button(spot, *look)
            elif look[0] == "rgb":
                row, column = spot
                _rgb, red, green, blue = look
                launchkey.pad_rgb(row, column, red, green, blue)
            else:
                row, column = spot
                launchkey.pad(row, column, look[1], pulse=look[2])
        self._drawn = wanted

    def _picture(self) -> dict[Any, Any]:
        """What the pads, the buttons and the Launchkey's own scale show."""
        info = self._info or {}
        song = info.get("song")
        key = song.get("key") if song else None
        picture: dict[Any, Any] = {}
        picture["scale"] = (key["tonic"], key["mode"]) if key else None
        if self.scale_view and key:
            scale, also = set(key["scale"]), set(key["other"])
            for column in range(8):
                for row, pitch in ((0, BLACK_KEYS[column]), (1, WHITE_KEYS[column])):
                    pc = None if pitch is None else pitch % 12
                    colour = (0 if pc is None else TONIC if pc == key["tonic"] else IN_SCALE if pc in scale
                              else ALSO if pc in also else 0)
                    picture[(row, column)] = ("palette", colour, False)
        else:
            parts = self.mode == "parts"
            arrangements = song["arrangements"][:ARRANGEMENT_KNOBS] if song and parts else []
            for column in range(ARRANGEMENT_KNOBS):
                if column >= len(arrangements):
                    picture[(0, column)], picture[(1, column)] = ("palette", 0, False), ("rgb", 0, 0, 0)
                    continue
                a = arrangements[column]
                bright, rgb = COLOURS.get(a["kind"], COLOURS["lead"])
                if a["play"]:
                    picture[(0, column)] = ("palette", bright, a["playing"] and not self.mix_knobs)
                elif self.mix_knobs:
                    picture[(0, column)] = ("palette", MUTED_PAD, False)
                else:
                    picture[(0, column)] = ("rgb", *(round(c * MUTED_LEVEL) for c in rgb))
                level = self._lights_of(a) / 4 * (1.0 if a["play"] else 0.25)
                picture[(1, column)] = ("rgb", *(round(c * level) for c in rgb))
            keys = info.get("keys") or {}
            if not parts:
                picture[(0, KEYS_COLUMN)] = ("palette", 0, False)
            elif keys.get("sound") or self.mix_knobs:
                picture[(0, KEYS_COLUMN)] = ("palette", KEYS_ON if keys.get("sound") else MUTED_PAD, False)
            else:
                picture[(0, KEYS_COLUMN)] = ("rgb", *(round(c * MUTED_LEVEL) for c in KEYS_RGB))
            lit = (1.0 if keys.get("lights") else 0.15) if parts else 0.0
            picture[(1, KEYS_COLUMN)] = ("rgb", *(round(c * lit) for c in KEYS_RGB))
            guitar = parts and not self.mix_knobs  # (the guitar: its pedal on a favourite or not, and heard or not)
            pedal = info.get("pedal") or {}
            if not guitar:
                picture[(0, GUITAR_COLUMN)] = ("palette", 0, False)
            elif pedal.get("slot") in (pedal.get("favourites") or ()):
                picture[(0, GUITAR_COLUMN)] = ("palette", GUITAR_ON, False)
            else:
                picture[(0, GUITAR_COLUMN)] = ("rgb", *(round(c * MUTED_LEVEL) for c in GUITAR_RGB))
            heard = (1.0 if info.get("listen") else 0.15) if guitar else 0.0
            picture[(1, GUITAR_COLUMN)] = ("rgb", *(round(c * heard) for c in GUITAR_RGB))
            if not parts:
                self._playback_pads(picture, info)
            picture[(0, CONTROLS)] = ("palette", SOUND_ON if info.get("sound") else SOUND_OFF, False)
            following = info.get("mode") == "follow"
            picture[(1, CONTROLS)] = ("palette", FOLLOWING if following else NOT_FOLLOWING, following)
        following = info.get("mode") == "follow"
        for number in (SIDE_BUTTONS[0], *TRACK_BUTTONS):  # (following: Rocksmith's keys)
            picture[number] = LIT if following else DIM
        picture[SIDE_BUTTONS[1]] = LIT if self.mix_knobs else DIM  # Func: the mixer
        picture[ENCODER_BUTTONS[0]] = LIT if self.mode == "parts" else DIM  # which the knobs are for: the parts
        picture[ENCODER_BUTTONS[1]] = LIT if self.mode == "playback" else DIM  # or playing the song
        picture[PLAY] = LIT if _sign(info) == "playing" else DIM
        other = self._browsed is not None and (song is None or self._browsed != song["id"])
        switches = info.get("mode") != "follow" and (other or info.get("mode") == "play")  # to another, or stopping
        picture[RECORD] = LIT if switches else DIM
        return picture

    def _playback_pads(self, picture: dict[Any, Any], info: dict[str, Any]) -> None:
        """Playing the song: the loop's pads on top (its start and end, lit when it has them; on, pulsing; the
        section it's in looped: following too, Riff Repeater looping it), and below them a kind of section each, the
        one it's in bright (not following: the game goes where it goes)."""
        song = info.get("song")
        if song is not None and song.get("lyrics"):  # (following too)
            picture[(0, LYRICS_PAD)] = ("palette", LYRICS_ON if self.lyrics_view else LYRICS_OFF, False)
        if song is None:
            return
        loop, sections = info.get("loop") or {}, song.get("sections") or []
        here = _section_at(sections, self._where(info))
        a, b, on = loop.get("a"), loop.get("b"), bool(loop.get("on"))
        picture[(0, 0)] = ("palette", LOOP_SET if a is not None else LOOP_UNSET, False)
        picture[(0, 1)] = ("palette", LOOP_SET if b is not None else LOOP_UNSET, False)
        picture[(0, 2)] = ("palette", LOOP_ON if on else LOOP_OFF, on)
        if sections:
            looped = on and here is not None and (a, b) == (here["start"], here["end"])
            picture[(0, 3)] = ("palette", LOOPED if looped else NOT_LOOPED, False)
        if info.get("mode") == "follow":  # (below, Riff Repeater's Accelerator)
            on = bool((info.get("riff") or {}).get("accelerator"))
            picture[(1, ACCELERATOR_PAD)] = ("palette", ACCELERATOR_ON if on else ACCELERATOR_OFF, False)
            return
        for column, kind in enumerate(_kinds(sections)):
            level = 1.0 if here is not None and here["kind"] == kind else SECTION_DIM
            picture[(1, column)] = ("rgb", *(round(c * level) for c in SECTION_COLOURS.get(kind, OTHER_SECTION)))

    def _on(self, event: Pad | Turn | Button | ScaleMode | Strip, now: float) -> None:
        info = self._info or {}
        song = info.get("song")
        if self.launchkey is None:
            return
        if not (isinstance(event, Button) and event.number == TRACK_BUTTONS[0]):
            self._lyrics_home = False  # (Track back, next: the lyrics as they're sung first)
        mixing = (isinstance(event, (Turn, Strip)) or isinstance(event, Pad) and event.row == 0 or
                  isinstance(event, Button) and event.number == SIDE_BUTTONS[1])
        game_key = isinstance(event, Button) and (event.number in (SIDE_BUTTONS[0], TRACK_BUTTONS[1]) or
                                                  event.number == TRACK_BUTTONS[0] and info.get("mode") == "follow")
        # (Rocksmith's keys, or said to be: the mixer stays up; Record, as ever, closes it)
        said = isinstance(event, Pad) and event.row == 1 or game_key
        if self.mix_knobs and not mixing and not said:  # something else done: the knobs back on the tones (lit
            # keys, keys lighting first and following are said over it; Rocksmith's keys go to the game)
            self._close_mixer()
        if self.mix_knobs and mixing:
            self._mix(event, info, now)
            return
        if isinstance(event, ScaleMode):  # its own display shows the scale; the pads and the keys show it too
            if event.on != self.scale_view:
                self.scale_view = event.on
                if not event.on:
                    self.gui.stop_scale()
                elif song is not None and song.get("key"):
                    self.gui.show_scale({"song": song["id"]})
            return
        if isinstance(event, Strip):  # (the pitch strip: the lyrics while they show, else the library)
            if self._lyrics_shown(info):
                self._slide_lyrics(event.value, event.at or now, info, now)
            else:
                self._browse_pitch(event.value, event.at or now)
            return
        if isinstance(event, Pad) and self.scale_view and song is not None and song.get("key"):
            self._say(now, lines=("Pads: the song's scale", "Scale: back to parts"))
            return
        if isinstance(event, Pad) and event.column == CONTROLS:
            if event.row == 0:
                self.gui.toggle_sound()  # (muted: every screen shows so, in a corner)
            else:
                following = self.gui.toggle_follow()
                self._say(now, line=f"Following: {'on' if following else 'off'}")
            return
        if isinstance(event, Button):
            self._button(event.number, song, now)
            return
        if isinstance(event, Turn) and event.encoder == CONTROLS:  # knob 8: the library a letter at a time, a group
            # of tones, or all the volumes
            if self._picture_of is None and self._browsed_text is not None and now < self._browse_until:
                self._flick = 0.0
                self._moves.clear()
                self._browsed_song(self.gui.browse_letter(event.steps))
                self._browse_until = now + BROWSE_SHOWN
            elif self.mode == "parts" and self._picture_of == "keys":
                self._turn_keys(event.steps, True, info, now)
            elif self.mode == "parts" and self._picture_of == "pedal":
                self._turn_pedal(event.steps, True, info, now)
            elif self._picture_of == "loop":
                self._turn_loop_bars(event.steps, now)
            elif self.mode == "parts" and self._picture_of == "parts":
                self._turn_tone(self._part_turned, event.steps, True, info, now)
            else:
                self._open_mixer(now, kept=False)
                self._turn_volume(CONTROLS, event.steps, info, now)
            return
        if self.mode == "playback":
            self._playback(event, info, now)
            return
        column = event.encoder if isinstance(event, Turn) else event.column
        if column == KEYS_COLUMN:
            self._keys_column(event, info, now)
            return
        if column == GUITAR_COLUMN:
            self._guitar_column(event, info, now)
            return
        arrangements = song["arrangements"][:ARRANGEMENT_KNOBS] if song else []
        if column >= len(arrangements):
            return
        a = arrangements[column]
        if isinstance(event, Turn):
            self._turn_tone(column, event.steps, False, info, now)
        elif event.row == 0:
            self._toggle_part(a, now)
        else:
            others = sum(self._lights_of(x) for x in arrangements if x["play"] and x["id"] != a["id"])
            options = list(range(max(0, 4 - others) + 1))  # up to the keys still free
            current = self._lights_of(a)
            if options == [0] and current == 0:
                self._said_by_pad(now, f"{a['name']}: no keys free")
                return
            lights = options[(options.index(current) + 1) % len(options)] if current in options else options[0]
            self._pending[(a["id"], "lights")] = lights
            self.gui.change_part(a["id"], {"lights": lights}, later=PAD_WAIT)
            self._turned_at, self._wait, self._settling = now, PAD_WAIT, True
            self._said_by_pad(now, f"{a['name']}: {lights or 'no'} lit key{'' if lights == 1 else 's'}")

    def _playback(self, event: Pad | Turn, info: dict[str, Any], now: float) -> None:
        """Playing the song: knobs 1 and 2 move the loop's start and end, 3 moves through it, 4 goes a section at
        a time, and 5 sets its speed; the pads on top set the loop, the ones below go to a kind of section. Anything
        else shows what they do."""
        if isinstance(event, Turn) and event.encoder in (SEEK_KNOB, SPEED_KNOB, SECTION_KNOB, *LOOP_KNOBS, LYRICS_KNOB,
                                                         CHORDS_KNOB) or \
                isinstance(event, Pad) and (event.row == 0 and (event.column < len(LOOP_PADS) or event.column == LYRICS_PAD)
                                            or event.row == 1 and event.column < SECTION_PADS):
            self._stop_browsing()  # (the song's line before the library)
            if not (isinstance(event, Turn) and event.encoder == LYRICS_KNOB):
                self._lyric_line = None  # (the lyrics as they're sung, or where it's going)
        if isinstance(event, Turn) and event.encoder == SEEK_KNOB:
            self._move(event.steps, info, now)
        elif isinstance(event, Turn) and event.encoder == SPEED_KNOB:
            self._turn_speed(event.steps, info, now)
        elif isinstance(event, Turn) and event.encoder == SECTION_KNOB:
            self._turn_section(event.steps, info, now)
        elif isinstance(event, Turn) and event.encoder in LOOP_KNOBS:
            self._turn_loop("ab"[LOOP_KNOBS.index(event.encoder)], event.steps, info, now)
        elif isinstance(event, Turn) and event.encoder == LYRICS_KNOB:
            self._scroll_lyrics(event.steps, info, now)
        elif isinstance(event, Turn) and event.encoder == CHORDS_KNOB:
            self._turn_chords(event.steps, info, now)
        elif isinstance(event, Pad) and event.row == 0 and event.column == LYRICS_PAD:
            self._toggle_lyrics(info, now)
        elif isinstance(event, Pad) and event.row == 0 and event.column < len(LOOP_PADS):
            self._loop_pad(LOOP_PADS[event.column], info, now)
        elif isinstance(event, Pad) and event.row == 1 and info.get("mode") == "follow":
            if event.column == ACCELERATOR_PAD:
                self._toggle_accelerator(info, now)
        elif isinstance(event, Pad) and event.row == 1 and event.column < SECTION_PADS:
            self._section_pad(event.column, info, now)

    def _stop_browsing(self) -> None:
        """The library's song off the screen (and a flick through it stopped), for the song's."""
        self._browse_until, self._flick = 0.0, 0.0
        self._moves.clear()

    def _toggle_part(self, a: dict[str, Any], now: float) -> None:
        """An arrangement's pad on top (the mixer's too): muted, or played again."""
        play = not a["play"]
        self.gui.change_part(a["id"], {"play": play})
        self._said_by_pad(now, f"{a['name']}: {'on' if play else 'off'}")

    def _keys_column(self, event: Pad | Turn, info: dict[str, Any], now: float) -> None:
        """Column 7, its keys (a song playing or not): knob 7 their tone, the top pad turns them on or off, the
        bottom one whether they light the keys first."""
        keys = info.get("keys") or {}
        if isinstance(event, Turn):
            self._turn_keys(event.steps, False, info, now)
        elif event.row == 0:
            self.gui.set_keys({"sound": not keys.get("sound")})
            self._said_by_pad(now, f"Launchkey keys: {'off' if keys.get('sound') else 'on'}")
        else:
            self.gui.set_keys({"lights": not keys.get("lights")})
            self._said_by_pad(now, f"Keys light first: {'off' if keys.get('lights') else 'on'}")

    def _guitar_column(self, event: Pad | Turn, info: dict[str, Any], now: float) -> None:
        """Column 6, the guitar: knob 6 its pedal's presets, the top pad the next of its favourites (the first, on
        none of them), the bottom one whether it's heard (its notes lighting the keys)."""
        if isinstance(event, Turn):
            self._turn_pedal(event.steps, False, info, now)
        elif event.row == 0:
            pedal = info.get("pedal") or {}
            favourites = list(pedal.get("favourites") or ())
            slot = self._pending.get(("pedal", "slot")) or pedal.get("slot")
            if not favourites:
                self._said_by_pad(now, "Pedal: no favourites")
                return
            slot = favourites[(favourites.index(slot) + 1) % len(favourites)] if slot in favourites else favourites[0]
            if self._try(lambda: self.gui.set_pedal({"slot": slot})):
                self._pending[("pedal", "slot")] = slot
                self._show_for_a_while("pedal", now)
        else:
            heard: list[bool] = []
            if self._try(lambda: heard.append(self.gui.toggle_listen())):
                self._said_by_pad(now, f"Guitar lights keys: {'on' if heard[0] else 'off'}")

    def _turn_pedal(self, steps: int, jump: bool, info: dict[str, Any], now: float) -> None:
        """Knob 6: the pedal's presets, one at a time (jump: knob 8, PEDAL_JUMP at a time; round from the last to
        the first), the screen showing the one it's on; the pedal switches to it once the knob rests."""
        slot = self._pending.get(("pedal", "slot")) or (info.get("pedal") or {}).get("slot")
        steps *= PEDAL_JUMP if jump else 1
        base = slot if slot is not None else 0 if steps > 0 else 1
        slot = (base - 1 + steps) % PEDAL_SLOTS + 1
        self._pending[("pedal", "slot")] = slot
        self._try(lambda: self.gui.set_pedal({"slot": slot}, later=PEDAL_WAIT))
        self._show_for_a_while("pedal", now)

    def _button(self, number: int, song: dict[str, Any] | None, now: float) -> None:
        following = (self._info or {}).get("mode") == "follow"
        if number == PLAY:  # the song's picture shows it playing or paused
            self.gui.remote_play()
            self._back_to_the_song()
        elif number in GAME_KEYS and following:  # Rocksmith's menus: up, down, Escape, Enter
            self.gui.game_key(GAME_KEYS[number])
        elif number == SIDE_BUTTONS[0]:  # (Rocksmith's Escape, following)
            self._say(now, lines=NOT_FOLLOWING_KEYS)
        elif number == RECORD and self._lyric_line is not None and now < self._lyrics_until:  # the line gone to
            self._lyrics_go(now)
        elif number == RECORD:  # the song the pitch strip is on switched to; on the song already, it stops
            if self._browsed is None or (song is not None and self._browsed == song["id"]):
                self.gui.remote_stop()
                self._back_to_the_song()
            elif self.gui.browse_select() is not None:  # (the screen shows it)
                self._browse_until = 0.0
                self._say(now, line="Switched to this song")
            self._browsed, self._browsed_text, self._flick = None, None, 0.0
            self._moves.clear()  # (no flick)
        elif number == TRACK_BUTTONS[0]:  # home: the screen on the song (or waiting for one), the library too; the
            # lyrics showing, on them as they're sung, then (pressed again) the song
            self._browsed, self._browsed_text, self._flick = None, None, 0.0
            self._moves.clear()
            self.gui.browse_at(song["id"] if song else None)
            lyrics = self.lyrics_view and bool((song or {}).get("lyrics"))
            if lyrics and self._lyrics_home:
                self.lyrics_view, self._lyrics_home = False, False
            else:
                self._lyrics_home = lyrics
            self._lyric_line = None
            self._back_to_the_song()
        elif number == TRACK_BUTTONS[1]:  # (Rocksmith's down arrow, following)
            self._say(now, lines=NOT_FOLLOWING_KEYS)
        elif number == SIDE_BUTTONS[1]:  # Func: the mixer
            self._open_mixer(now, kept=True)
        elif number == ENCODER_BUTTONS[0] and self.mode == "parts":  # again: the next of the arrangements' tones
            # (palm-mute, low-note; the screen shows which)
            self.field = (self.field + 1) % len(FIELDS)
            if ((self._info or {}).get("song") or {}).get("arrangements"):
                self._show_for_a_while("parts", now)
            else:
                self._say(now, line=f"Knobs 1-5: {FIELDS[self.field][1]}")
        elif number in ENCODER_BUTTONS:  # the knobs on the arrangements (what they do, for a while) or playing the
            # song; again, what they do
            mode, hint = ("parts", "arrangements") if number == ENCODER_BUTTONS[0] else ("playback", "playback")
            if self.mode != mode:
                self.mode = mode
                if self._picture_of in ("arrangements", "playback"):
                    self._picture_of = None
                if mode == "parts":
                    self._show_for_a_while(hint, now)
            elif self._picture_of == hint:
                self._picture_of = None
            else:
                self._show_for_a_while(hint, now)
                self._picture_until = math.inf  # (until it's pressed again, or something else shows)

    def _back_to_the_song(self) -> None:
        """The screen shows the song, over anything shown (or said) for a while."""
        if self.launchkey is not None:
            self.launchkey.hide(TEMPORARY)
        self._picture_of, self._browse_until, self._note_until = None, 0.0, 0.0

    # Going through the library with the modulation strip

    def _browse_pitch(self, value: int, at: float) -> None:
        """The pitch strip, slid: its steps, as a 7-bit strip's. Let go, it springs back to the middle: a jump
        there from elsewhere is that, and left out (a slide through it goes on from where it was)."""
        if value == STRIP_MIDDLE and self._pitch is not None and abs(self._pitch - STRIP_MIDDLE) > 64:
            self._released(at)
            return
        self._pitch, self._pitch_step = value, step_at(value, self._pitch_step)
        self._browse_strip(self._pitch_step, at)

    def _released(self, at: float) -> None:
        """Let go of the pitch strip: fast, a flick goes on through the library; else the step or two the finger
        made coming off is undone, so it stays on the song it was held on."""
        speed = self._speed_before(at)
        if abs(speed) >= FLICK_SPEED:
            self._flick, self._browse_until = speed * SONGS_PER_STEP * FLICK_GAIN, at + BROWSE_SHOWN
        else:
            undone = sum(steps for when, steps in self._moves if at - when <= SETTLE)
            if undone:
                self._browse(-undone * SONGS_PER_STEP)
        self._moves.clear()
        self._slide, self._pitch_step = None, None  # (put down again: from there)

    def _speed_before(self, at: float) -> float:
        """The strip's speed (steps a second) over the last FLICK_WINDOW before at, the way most of it went: a
        finger coming off it can throw a step or two the other way."""
        recent = [(when, steps) for when, steps in self._moves if at - when <= FLICK_WINDOW]
        net = sum(steps for _when, steps in recent)
        along = [(when, steps) for when, steps in recent if steps * net > 0]
        if len(along) < 2:
            return sum(steps for _when, steps in along) / FLICK_WINDOW
        return sum(steps for _when, steps in along[1:]) / max(along[-1][0] - along[0][0], 0.005)

    def _browse_strip(self, value: int, at: float) -> None:
        """The song it's on takes the screen, and gives it back BROWSE_SHOWN after."""
        self._slide_to(value, at)
        if self._browsed is None:
            self._browse(0.0)  # touched: the song it's on
        self._browse_until, self._picture_of = at + BROWSE_SHOWN, None

    def _slide_to(self, value: int, at: float) -> None:
        last, self._slide = self._slide, (value, at)
        self._flick = 0.0  # touched: a flick stops
        if last is None or (at - last[1] > NEW_TOUCH and abs(value - last[0]) > NEW_TOUCH_STEPS):  # put down again
            self._moves.clear()
            return
        steps = value - last[0]
        if steps:  # (it can go on reporting while held still)
            self._moves.append((at, steps))
            while at - self._moves[0][0] > FLICK_WINDOW + SETTLE:
                self._moves.popleft()
            self._browse(steps * SONGS_PER_STEP)

    def _glide(self, now: float) -> None:
        """A flick goes on, slowing, until the strip is touched again."""
        elapsed, self._last_step = now - self._last_step, now
        if self._slide is not None and self._moves and now - self._slide[1] >= LIFTED:  # let go, unseen
            speed = self._speed_before(self._slide[1])
            self._moves.clear()
            if abs(speed) >= FLICK_SPEED:
                self._flick = speed * SONGS_PER_STEP * FLICK_GAIN
        if abs(self._flick) < 1.0:
            self._flick = 0.0
            return
        elapsed = min(elapsed, 0.1)
        self._browse(self._flick * elapsed)
        self._flick *= math.exp(-elapsed / FLICK_FADE)
        self._browse_until = now + BROWSE_SHOWN

    def _browse(self, songs: float) -> None:
        self._browsed_song(self.gui.browse_move(songs))

    def _browsed_song(self, song: dict[str, Any] | None) -> None:
        if song is not None and song["id"] != self._browsed:
            self._browsed, self._browsed_text = song["id"], (song["song"], song["artist"])
            self._browsed_place = (song.get("place", 0), song.get("of", 1))

    def _turn_speed(self, steps: int, info: dict[str, Any], now: float) -> None:
        """Knob 5: the speed, SPEED_STEP a step from 25% to 150%, applied when it stops turning (following,
        Riff Repeater's)."""
        start = self._speed_to if self._speed_to is not None else info.get("speed", 1.0)
        self._speed_to = min(max(round((start + steps * SPEED_STEP) * 20) / 20, SLOWEST), FASTEST)
        self._turned_at, self._wait = now, TURN_WAIT
        self._show_for_a_while("speed", now)

    def _move(self, steps: int, info: dict[str, Any], now: float) -> None:
        """Knob 3: the song's position, applied when it stops turning (following, the game's, a step at a time)."""
        if info.get("mode") == "follow":
            self._game_steps(steps, GAME_SMALL_STEPS, "Step", info, now)
            return
        if info.get("position") is None:
            self._say(now, line="Position: no song")
            return
        start = self._moving_to if self._moving_to is not None else info["position"]
        self._moving_to = min(max(start + steps * SECONDS_PER_STEP, 0.0), info["length"])
        self._turned_at, self._wait = now, TURN_WAIT
        self._show_for_a_while("position", now)

    def _turn_section(self, steps: int, info: dict[str, Any], now: float) -> None:
        """Knob 4: a section at a time (back, to the start of the one it's in first), as knob 3 goes."""
        sections = (info.get("song") or {}).get("sections") or []
        if info.get("mode") == "follow":  # (the game's phrases)
            self._game_steps(steps, GAME_PHRASES, "Phrase", info, now)
            return
        if info.get("position") is None or not sections:
            self._say(now, line=f"Sections: {'no song' if not info.get('song') else 'no sections'}")
            return
        at = self._where(info)
        here = next((i for i in range(len(sections) - 1, -1, -1) if sections[i]["start"] <= at + 0.001), -1)
        if steps < 0 and here >= 0 and at - sections[here]["start"] > SECTION_BACK:
            here += 1
        self._go_to(sections[min(max(here + steps, 0), len(sections) - 1)]["start"], now)

    def _game_steps(self, steps: int, keys: tuple[str, str], said: str, info: dict[str, Any], now: float) -> None:
        """Following: Rocksmith's song stepped through as its pause screen's keys do (keys: back, on), a key a step
        (GAME_STEPS at most a turn, none while it has GAME_BEHIND still to type); not paused, said so."""
        if info.get("state") != "paused":
            self._say(now, lines=("Rocksmith: pause first", "(Play), then step"))
            return
        count = 0
        while count < min(abs(steps), GAME_STEPS) and now >= self._game_busy_until:
            waiting = self.gui.game_key(keys[steps > 0])
            self._game_busy_until = now + max(waiting - GAME_BEHIND + 1, 0) * GAME_KEY_TIME
            count += 1
        if not count:
            return  # (it's catching up)
        self._say(now, line=f"{said} {'on' if steps > 0 else 'back'}{f' x{count}' if count > 1 else ''}")

    def _section_pad(self, column: int, info: dict[str, Any], now: float) -> None:
        """A pad below, playing the song: the next section of its kind (after the last, the first)."""
        sections = (info.get("song") or {}).get("sections") or []
        kinds = _kinds(sections)
        if column >= len(kinds) or info.get("position") is None:
            return
        if info.get("mode") == "follow":
            self._say(now, line="Sections: the game's")
            return
        at = self._where(info)
        of_kind = [x for x in sections if x["kind"] == kinds[column]]
        self._go_to(next((x for x in of_kind if x["start"] > at + 0.05), of_kind[0])["start"], now)

    def _go_to(self, position: float, now: float) -> None:
        """A section's start: the song goes there once the turns and taps stop, the screen showing which."""
        self._moving_to = position
        self._turned_at, self._wait = now, TURN_WAIT
        self._show_for_a_while("section", now)

    def _loop_pad(self, which: str, info: dict[str, Any], now: float) -> None:
        """The loop's pads: its start where the song is ("a"), its end ("b", looping), on or off, or the section
        it's in (looping it already: off). What it is now is said (following, and the game can't loop it, why)."""
        loop = info.get("loop") or {}
        body = {"a": {"a": "here"}, "b": {"b": "here", "on": True}, "on": {"on": not loop.get("on")},
                "section": {"section": True}}[which]
        cleared = which == "a" and now - self._tapped_a < DOUBLE_TAP  # (tapped twice: none)
        self._tapped_a = -math.inf if cleared else now if which == "a" else self._tapped_a
        if cleared:
            body = {"a": None, "b": None, "on": False}
        now_set: dict[str, Any] = {}
        if not self._try(lambda: now_set.update(self.gui.set_loop(body))):
            return
        a, b, on = now_set.get("a"), now_set.get("b"), bool(now_set.get("on"))
        if cleared:
            said = "Loop cleared"
        elif a is None or b is None:
            said = f"Loop from {_exact(a)}" if a is not None else "Loop: A and B first"
        elif which == "section":
            found = _section_at((info.get("song") or {}).get("sections") or [], a)
            said = f"Loop: {found['name'] if found and on else 'off'}"
        else:
            said = f"Loop {_exact(a)}-{_exact(b)}: {'on' if on else 'off'}"
        game = now_set.get("game") if on else None  # (following: the game's)
        if game is not None:
            self._say(now, lines=(said, _riff_trouble(game) or "Pick it in Riff Repeater"))
        else:
            self._say(now, line=said)

    def _turn_loop(self, end: str, steps: int, info: dict[str, Any], now: float) -> None:
        """Knobs 1 and 2: the loop's start or end, a beat at a time (from where the song is, without it)."""
        if self._try(lambda: self.gui.set_loop({f"move_{end}": steps})):
            self._loop_turned = end
            self._show_for_a_while("loop", now)

    def _toggle_accelerator(self, info: dict[str, Any], now: float) -> None:
        """Following, the pad below on the left: Riff Repeater's Accelerator on or off."""
        on = not (info.get("riff") or {}).get("accelerator")
        said: dict[str, Any] = {}
        if self._try(lambda: said.update(self.gui.set_game_riff({"accelerator": on}))):
            self._say(now, lines=(f"Accelerator: {'on' if on else 'off'}",
                                  "In Riff Repeater" if said.get("answered") else "Game: no riffloop mod"))

    def _turn_loop_bars(self, steps: int, now: float) -> None:
        """Knob 8 while the loop shows: its end last turned (knob 1 or 2) a bar at a time, to the bar lines."""
        end = self._loop_turned
        if self._try(lambda: self.gui.set_loop({f"move_{end}_bars": steps})):
            self._show_for_a_while("loop", now)

    def _turn_chords(self, steps: int, info: dict[str, Any], now: float) -> None:
        """Knob 7: whose chords show, auto (picked as they go) then each arrangement in turn; the screen says."""
        song = info.get("song")
        if song is None:
            self._say(now, line="Chords: no song")
            return
        choices = [None, *(a["id"] for a in song.get("arrangements") or [])]
        at = choices.index(self._chords_from) if self._chords_from in choices else 0
        self._chords_from = choices[(at + steps) % len(choices)]
        self._show_for_a_while("chords", now)

    def _chords_said(self, song: dict[str, Any]) -> str:
        """Whose chords show, as said where the scale is."""
        chosen = next((a for a in song.get("arrangements") or [] if a["id"] == self._chords_from), None)
        return f"Chords: {chosen['name'] if chosen else 'auto'}"

    def _toggle_lyrics(self, info: dict[str, Any], now: float) -> None:
        """Pad 6 on top: the lyrics on the screen instead of the song, or the song again."""
        if not ((info.get("song") or {}).get("lyrics")):
            self._say(now, line="No lyrics in this song" if info.get("song") else "Lyrics: no song")
            return
        self.lyrics_view, self._lyric_line, self._picture_of = not self.lyrics_view, None, None

    def _scroll_lyrics(self, lines: float, info: dict[str, Any], now: float) -> None:
        """Knob 6 (a line a step) or the pitch strip: through the lyrics, from the line they're on (shown, if they
        weren't); back with the song LYRICS_BACK after."""
        lyrics = (info.get("song") or {}).get("lyrics") or ()
        if not lyrics:
            self._say(now, line="No lyrics in this song" if info.get("song") else "Lyrics: no song")
            return
        if self._lyric_line is None or now >= self._lyrics_until:
            self._lyric_line = float(self._lyric_now(lyrics, self._song_at(info))[0])
        self._lyric_line = min(max(self._lyric_line + lines, 0.0), len(lyrics) - 1.0)
        self.lyrics_view, self._lyrics_until, self._picture_of = True, now + LYRICS_BACK, None

    def _slide_lyrics(self, value: int, at: float, info: dict[str, Any], now: float) -> None:
        """The pitch strip while the lyrics show: through them, STRIP_LINE steps a line, as it slides; let go
        (springing back) or put down again, they stay."""
        last = self._lyric_strip
        if value == STRIP_MIDDLE and last is not None and abs(last[0] - STRIP_MIDDLE) > 64:  # let go
            self._lyric_strip = None
            return
        step = step_at(value, None if last is None else last[1])
        self._lyric_strip = (value, step, at)
        if last is None or (at - last[2] > NEW_TOUCH and abs(step - last[1]) > NEW_TOUCH_STEPS) or step == last[1]:
            return
        self._scroll_lyrics((step - last[1]) / STRIP_LINE, info, now)

    def _lyrics_go(self, now: float) -> None:
        """Record, on a line gone to: the song from a little before it (following: the game's)."""
        info = self._info or {}
        lyrics = (info.get("song") or {}).get("lyrics") or ()
        line, self._lyric_line = self._lyric_line, None
        if info.get("mode") == "follow" or line is None or not lyrics:
            self._say(now, line="Position: the game's")
            return
        self._moving_to = max(lyrics[round(line)].start - LYRICS_LEAD_IN, 0.0)
        self._turned_at, self._wait = now, TURN_WAIT

    def _lyrics_shown(self, info: dict[str, Any]) -> bool:
        """The lyrics view is up (and the song has lyrics): nothing over it for a while, or what's moving through the
        song, its speed or its loop, shown on it."""
        return (self.lyrics_view and self._picture_of in ON_THE_SONG and not self.mix_knobs and
                bool((info.get("song") or {}).get("lyrics")))

    def _lyric_now(self, lyrics: tuple[Line, ...], at: float | None) -> tuple[int, tuple[int, int] | None]:
        """The line being sung where the song is (LYRICS_EARLY ahead), and the syllable (where it is in the line;
        None: not yet, or it's over): before the first line, it; LYRICS_HOLD after one, the next."""
        if at is None:
            return 0, None
        at += LYRICS_EARLY
        i = bisect_right(lyrics, at, key=lambda line: line.start) - 1
        if i < 0:
            return 0, None
        if at > lyrics[i].end + LYRICS_HOLD:
            return min(i + 1, len(lyrics) - 1), None
        syllables = lyrics[i].syllables
        j = bisect_right(syllables, at, key=lambda syllable: syllable[0]) - 1
        return i, syllables[j][1:]

    def _where(self, info: dict[str, Any]) -> float:
        """Where the song is, or where it's being taken."""
        return self._moving_to if self._moving_to is not None else float(info.get("position") or 0.0)

    # The screen

    def _show_for_a_while(self, picture: str, now: float) -> None:
        """A picture on the screen ("speed", "position", "section", "loop", "keys", "parts", "mixer" or
        "playback") for PICTURE_SHOWN, over the song (or the library) and what was said, shown as the step ends."""
        self._picture_of, self._picture_until = picture, now + PICTURE_SHOWN
        self._browse_until = self._note_until = 0.0

    def _said_by_pad(self, now: float, line: str) -> None:
        """What an arrangement's or its keys' pad changed, said; but not in the mixer, where its sliders show it."""
        if not self.mix_knobs:
            self._say(now, line=line)

    def _say(self, now: float, line: str | None = None, lines: tuple[str, str] | None = None) -> None:
        """Something said for a while at the bottom left of whatever the screen shows: a line (on the song's, in its
        scale's place), or two (in its key's too)."""
        self._note, self._note_until = (line, lines), now + PICTURE_SHOWN

    def _show_screen(self, now: float) -> None:
        """The screen's stationary display: always a picture, but for the song the modulation strip is on, which
        is text (putting the strip's own display away brings that back). The Launchkey holds just one picture, so
        the others go there for a while too, rather than on its temporary display."""
        launchkey = self.launchkey
        if launchkey is None or self._info is None:
            return
        if self._picture_of is not None and now >= self._picture_until:
            if self._picture_of == "mixer":  # (up for a while: knob 8 alone)
                self._close_mixer()
            self._picture_of = None
        shows, draw = self._screen(now)
        if shows == self._screen_shown or (draw is not None and now - self._screen_at < PICTURE_GAP):
            return
        (picture, note), (was, said) = shows, self._screen_shown or (("",), None)
        if picture[0] in TIMED and was[0] != picture[0] or note is not None and note != said:
            launchkey.hide(TEMPORARY)  # just asked for: over anything shown for a while
        if draw is None:
            launchkey.show(STATIONARY, *picture[1:])
        else:
            launchkey.picture(STATIONARY, draw().data())
            self._screen_at = now
        self._screen_shown = shows

    def _screen(self, now: float) -> tuple[tuple[Any, ...], Callable[[], Bitmap] | None]:
        """What the screen shows now (the picture, and what's said over it), and how to draw it (None: text)."""
        picture, draw = self._picture_shown(now)
        if draw is None or now >= self._note_until:
            return (picture, None), draw
        line, lines = self._note
        said = lines if lines is not None else (line or "",)
        muted, bottom = not (self._info or {}).get("sound", True), 54 if picture[0] == "mixer" else 51
        drawn = draw
        return (picture, said), lambda: with_note(drawn(), said, muted, bottom)

    def _picture_shown(self, now: float) -> tuple[tuple[Any, ...], Callable[[], Bitmap] | None]:
        """The picture the screen shows now, and how to draw it (None: it's text)."""
        info = self._info or {}
        arrangements_now = ((info.get("song") or {}).get("arrangements") or [])[:ARRANGEMENT_KNOBS]  # (with knobs)
        muted = not info.get("sound", True)
        if self._picture_of == "mixer":  # the arrangements', then its keys'
            names = [a["name"] for a in arrangements_now] + ["Keys"]
            volumes = [self._volume_of(slot, info) for slot in range(len(arrangements_now))] + [self._keys_volume(info)]
            keys = info.get("keys") or {}
            quiet = [not a["play"] for a in arrangements_now] + [not keys.get("sound", True)]
            lights = [self._lights_of(a) / 4 for a in arrangements_now] + [1.0 if keys.get("lights") else 0.0]
            turned = (None if self._mix_slider == CONTROLS else len(arrangements_now) if self._mix_slider == KEYS_COLUMN
                      else min(self._mix_slider, len(names) - 1))  # (the keys' slider: after the arrangements')
            return (("mixer", *names, *volumes, *quiet, *lights, turned, muted),
                    lambda: mixer_picture(names, volumes, turned, muted=muted, quiet=quiet, lights=lights))
        if self._picture_of == "playback":
            return ("playback", muted), lambda: playback_picture(muted)
        if self._picture_of == "arrangements":
            return ("arrangements", muted), lambda: arrangements_picture(muted)
        if self._picture_of == "pedal":
            pedal = info.get("pedal") or {}
            slot = self._pending.get(("pedal", "slot")) or pedal.get("slot")
            name, favourites, error = self.gui.pedal_name(slot), pedal.get("favourites") or (), pedal.get("error")
            said = (_pedal_trouble(error) if error else
                    "Pad: " + " / ".join(f"{favourite:03d}" for favourite in favourites) if favourites else "")
            return ("pedal", slot, name, said, muted), lambda: pedal_picture(slot, name, said, muted)
        if self._picture_of == "keys":
            found = tone(self._keys_tone(info))
            group = found.group.title().replace("Gm ", "GM ")
            return ("keys", found.number, muted), lambda: keys_picture(found.number, group, found.name, muted)
        if self._picture_of == "parts" and arrangements_now:
            a = arrangements_now[min(self._part_turned, len(arrangements_now) - 1)]
            (field, said), which = FIELDS[self.field], self.field
            if a.get("output"):  # (on another MIDI device: its program)
                program, device = self._program_of(a), a["output"]["port"]
                shown = ((None, "Its own sound", device) if program is None else (program + 1, "Program", device))
                title, knobs = f"{a['name']} program", len(arrangements_now)
                return (("parts", title, *shown, knobs, which, muted),
                        lambda: parts_picture(title, *shown, knobs, which, muted))
            number, title, knobs = self._knob_tone(a, field), f"{a['name']} {said}", len(arrangements_now)
            default = self._default_of(a, field)
            if number == SAME:  # (the tone the rest plays with)
                shown = (None, "Same tone", tone(self._tone_of(a, "tone")).name)
            elif number == DEFAULT and default:  # (none of its own: its kind's)
                shown = (default, "Default", tone(default).name)
            else:
                found = tone(number)
                shown = (found.number, found.group.title().replace("Gm ", "GM "), found.name)
            return (("parts", title, *shown, knobs, which, muted),
                    lambda: parts_picture(title, *shown, knobs, which, muted))
        if self._browsed_text is not None and now < self._browse_until:
            return self._browsed_screen()
        if self._lyrics_shown(info):
            return self._lyrics_screen(info, now)
        position = (self._moving_to if self._moving_to is not None else
                    self._song_at(info) if info.get("mode") == "follow" else info.get("position"))  # (following: the
        # game's, as it plays)
        length = info.get("length")
        song = info.get("song")
        key = song.get("key") if song else None
        if song is None and info.get("mode") == "follow":  # whether the game's reached (by RS Companion)
            connected = info.get("connected")
            reached = ("Looking for Rocksmith" if connected is None else
                       "Connected to Rocksmith" if connected else "Can't reach Rocksmith")
            lines = (reached, "", "Following Rocksmith", "Waiting for a song")
        elif song is None:
            lines = ("rs-keylights", "", "Ready", "Pick one on the page")
        else:
            artist, _, title = song["title"].partition(" - ") if " - " in song["title"] else ("", "", song["title"])
            lines = (title, artist, key["name"] if key else "", " ".join(key["notes"]) if key else "")
        done = min(position / length, 1.0) if song is not None and position is not None and length else None
        sign = _sign(info) if song is not None else None
        loop, marks = info.get("loop") or {}, self._loop_marks(info)
        # For a while, after a control: what it changed, said in the scale's place (the corner left for muted)
        shows, handle, speed = "song", False, None
        if self._picture_of == "speed":  # (the line the speed)
            shows, speed = "speed", self._speed_to if self._speed_to is not None else info.get("speed", 1.0)
            lines = (*lines[:3], f"Speed {round(speed * 100)}%")
        elif self._picture_of in ("position", "section") and done is not None:  # (a handle on the line, the time
            # moving; or the section it's going to)
            shows, handle = self._picture_of, True
            if shows == "section":
                found = _section_at((song or {}).get("sections") or [], position or 0.0)
                lines = (*lines[:3], found["name"] if found else "")
        elif self._picture_of == "loop":
            shows, lines = "loop", (*lines[:3], self._loop_said(loop))
        elif self._picture_of == "chords" and song is not None:
            shows, lines = "chords", (*lines[:3], self._chords_said(song))
        time = f"{_time(position)} / {_time(length)}" if done is not None and position is not None and length else None
        at = self._song_at(info) if song is not None else None
        chord, tag = self._chord(song, at) if song is not None else (None, None)
        badge = self._tone_badge(song, at) if song is not None else None
        return ((shows, *lines, sign, None if done is None else song_bar(done, sign), speed, time, marks, chord, tag,
                 badge, muted),
                lambda: song_picture(*lines, done=done, sign=sign, handle=handle, speed=speed, beside=time,
                                     muted=muted, loop=marks, corner=chord, tag=tag, badge=badge))

    def _tone_badge(self, song: dict[str, Any], at: float | None) -> str | None:
        """The tone the song's on where it is, as the arrangement whose chords show switches tone (shortened, for
        its badge); None without changes (or while they're not followed)."""
        changes = (self._chords_order(song)[:1] or [{}])[0].get("tone_changes") or ()
        if not changes:
            return None
        i = bisect_right(changes, at if at is not None else 0.0, key=lambda change: change[0]) - 1
        said = changes[max(i, 0)][1]
        if said == "its own tone" and changes[0][1] != "its own tone":
            said = changes[0][1]  # (back to the one it starts with: its kind)
        return TONE_BADGES.get(said, said.capitalize())

    def _lyrics_screen(self, info: dict[str, Any], now: float) -> tuple[tuple[Any, ...], Callable[[], Bitmap] | None]:
        """The lyrics view: the lines before, being sung and after (or the one gone to, Record taking the song
        there), with the song's progress line and time above, the section (or what Record does) and chord below."""
        song = info["song"]
        lyrics, at, shown = song["lyrics"], self._song_at(info), self._picture_of
        if self._lyric_line is not None and now < self._lyrics_until:
            i, sung, said = round(self._lyric_line), None, "● = go here"
        else:
            (i, sung), found = self._lyric_now(lyrics, at), _section_at(song.get("sections") or [], at or 0.0)
            said = found["name"] if found and at is not None else ""
        speed = None
        if shown == "speed":  # (knob 5: its speed where the section was, the progress line turned into it)
            speed = self._speed_to if self._speed_to is not None else info.get("speed", 1.0)
            said = f"Speed {round(speed * 100)}%"
        elif shown == "loop":  # (knobs 1 and 2)
            said = self._loop_said(info.get("loop") or {})
        elif shown == "chords":  # (knob 7)
            said = self._chords_said(song)
        handle, marks = shown in ("position", "section"), self._loop_marks(info)
        before, line = lyrics[i - 1].text if i > 0 else "", lyrics[i].text
        after = lyrics[i + 1].text if i + 1 < len(lyrics) else ""
        length, sign = info.get("length"), _sign(info)
        done = min(at / length, 1.0) if at is not None and length else None
        time = _time(at) if at is not None else None
        (chord, tag), muted = self._chord(song, at), not info.get("sound", True)
        badge = self._tone_badge(song, at)
        return (("lyrics", before, line, after, sung, sign, None if done is None else round(done * WIDTH), time, said,
                 chord, tag, handle, marks, speed, badge, muted),
                lambda: lyrics_picture(before, line, after, sung=sung, done=done, sign=sign, time=time, said=said,
                                       chord=chord, muted=muted, tag=tag, handle=handle, loop=marks, speed=speed,
                                       badge=badge))

    def _loop_marks(self, info: dict[str, Any]) -> tuple[float | None, float | None, bool] | None:
        """The loop's brackets on the song's progress line: its start and end (0 to 1, each if it has one), and
        whether it's on; None without either, or a song (or its length)."""
        loop, length = info.get("loop") or {}, info.get("length")
        if info.get("song") is None or not length or (loop.get("a") is None and loop.get("b") is None):
            return None
        return (None if loop.get("a") is None else loop["a"] / length,
                None if loop.get("b") is None else loop["b"] / length, bool(loop.get("on")))

    def _loop_said(self, loop: dict[str, Any]) -> str:
        """The loop's end last turned (knob 1 or 2), as said where the scale is."""
        value = loop.get(self._loop_turned)
        return f"Loop {self._loop_turned.upper()} {_exact(value)}" if value is not None else "Loop: none"

    def _song_at(self, info: dict[str, Any]) -> float | None:
        """Where the song is now (or where knob 4, a section or a line takes it): playing, the song's place; following,
        the game's (where it was, while it's paused); else where it starts."""
        if self._moving_to is not None:
            return self._moving_to
        if info.get("mode") == "follow":
            live = self.gui.song_position()
            self._followed_at = live if live is not None else self._followed_at
            return self._followed_at
        live = self.gui.song_position() if info.get("mode") == "play" else None
        return live if live is not None else info.get("position")

    def _chord(self, song: dict[str, Any], at: float | None) -> tuple[str | None, str | None]:
        """The chord being played at a time (its name, CHORD_EARLY ahead), and whose (_tag): the arrangement's
        knob 7 picked, else the one of the arrangement lighting the most keys (rhythm, lead, then bass, lighting as
        many), else of the next with one there; between them, the last one. (None, None) before the first."""
        if at is None:
            return None, None
        at += CHORD_EARLY
        everyone = song.get("arrangements") or []
        arrangements = self._chords_order(song)
        if len(arrangements) == 1 and self._chords_from is not None and not arrangements[0].get("chords"):
            return self._note_at(arrangements[0], at), _tag(arrangements[0], everyone)  # (picked, with no chords: its
            # notes, a bass's)
        last: tuple[Chord, dict[str, Any]] | None = None
        for a in arrangements:
            chords = a.get("chords") or ()
            i = bisect_right(chords, at, key=lambda c: c.start) - 1
            if i >= 0 and at < chords[i].end:
                return chords[i].name, _tag(a, everyone)
            if i >= 0 and (last is None or chords[i].start > last[0].start):
                last = chords[i], a
        return (None, None) if last is None else (last[0].name, _tag(last[1], everyone))

    @staticmethod
    def _note_at(arrangement: dict[str, Any], at: float) -> str | None:
        """An arrangement's note being played at a time (held until the next): its name and octave ("G1"), or a
        chord's name made from its notes; None before the first."""
        notes = arrangement.get("notes") or ()
        i = bisect_right(notes, at, key=lambda note: note.time) - 1
        if i < 0:
            return None
        pitches = notes[i].pitches
        return note_name(pitches[0]) if len(pitches) == 1 else chord_name(pitches) or "+".join(
            NAMES[p % 12] for p in pitches)

    def _chords_order(self, song: dict[str, Any]) -> list[dict[str, Any]]:
        """Whose chords show, first to last: the arrangement knob 7 picked alone, else the arrangements lighting
        the most keys first (rhythm, lead, then bass, lighting as many)."""
        everyone = song.get("arrangements") or []
        chosen = [a for a in everyone if a["id"] == self._chords_from]
        return chosen or sorted(everyone, key=lambda a: (
            -(self._lights_of(a) if a["play"] else 0), CHORDS_FIRST.get(a["kind"], len(CHORDS_FIRST))))

    def _browsed_screen(self) -> tuple[tuple[Any, ...], Callable[[], Bitmap] | None]:
        """The song the pitch strip is on: its name, the line its place in the library (and the number, in the
        corner); Record switches to it, Track back (an up arrow) takes it back to the song, and knob 8 goes a
        letter at a time."""
        assert self._browsed_text is not None
        (title, artist), (place, of) = self._browsed_text, self._browsed_place
        muted = not (self._info or {}).get("sound", True)
        return (("browsed", title, artist, place, of, muted),
                lambda: song_picture(title, artist, "● = select", "", done=place / max(of - 1, 1), handle=True,
                                     beside="▲ = return", muted=muted, corner=f"{place + 1}/{of}", knob=("8", "A-Z")))

    def _open_mixer(self, now: float, kept: bool) -> None:
        """The mixer on the screen: kept, until it's closed; else for a while (knob 8 alone). The pads go on the
        arrangements until it closes."""
        if not self.mix_knobs:
            self._mode_after, self.mode = self.mode, "parts"
            self._mix_slider, self._mix_strip = CONTROLS, None
        self.mix_knobs, self._mixer_kept = True, kept
        self._show_for_a_while("mixer", now)
        if kept:
            self._picture_until = math.inf

    def _close_mixer(self) -> None:
        """The knobs back on the tones (or playing the song, as before), and the mixer off the screen."""
        if self.mix_knobs:
            self.mode = self._mode_after
        self.mix_knobs = self._mixer_kept = False
        if self._picture_of == "mixer":
            self._picture_of = None

    def _mix(self, event: Pad | Turn | Button | ScaleMode | Strip, info: dict[str, Any], now: float) -> None:
        """The mixer: knobs 1 to 7 the volumes, 8 all of them; the pads on top mute each or play it again (the
        right one: all of them), and Func keeps it up (opened by knob 8: up a while longer each time it's
        used), or closes it. What's said over it goes as it's used."""
        arrangements = ((info.get("song") or {}).get("arrangements") or [])[:ARRANGEMENT_KNOBS]
        self._note_until = 0.0
        if isinstance(event, Button):  # (Func)
            if self._mixer_kept:
                self._close_mixer()
            else:
                self._open_mixer(now, kept=True)
        elif isinstance(event, Turn):
            self._turn_volume(event.encoder, event.steps, info, now)
            self._open_mixer(now, kept=self._mixer_kept)
        elif isinstance(event, Strip):
            self._slide_volume(event.value, event.at or now, info, now)
            self._open_mixer(now, kept=self._mixer_kept)
        elif isinstance(event, Pad) and event.column == CONTROLS:
            self.gui.toggle_sound()
            self._mix_slider = CONTROLS
            self._open_mixer(now, kept=self._mixer_kept)
        elif isinstance(event, Pad) and event.column == KEYS_COLUMN:
            self._open_mixer(now, kept=self._mixer_kept)
            self._keys_column(event, info, now)
            self._mix_slider = KEYS_COLUMN
        elif isinstance(event, Pad) and event.column < len(arrangements):
            self._open_mixer(now, kept=self._mixer_kept)
            self._toggle_part(arrangements[event.column], now)
            self._mix_slider = event.column

    def _slide_volume(self, value: int, at: float, info: dict[str, Any], now: float) -> None:
        """The pitch strip in the mixer: the slider last used (all of them, without one) up or down as it slides,
        a step a step, as a knob turns it; let go (springing back) or put down again, nothing moves."""
        last = self._mix_strip
        if value == STRIP_MIDDLE and last is not None and abs(last[0] - STRIP_MIDDLE) > 64:  # let go
            self._mix_strip = None
            return
        step = step_at(value, None if last is None else last[1])
        self._mix_strip = (value, step, at)
        if last is None or (at - last[2] > NEW_TOUCH and abs(step - last[1]) > NEW_TOUCH_STEPS) or step == last[1]:
            return
        self._turn_volume(self._mix_slider, step - last[1], info, now)

    def _turn_volume(self, encoder: int, steps: int, info: dict[str, Any], now: float) -> None:
        """A mixer's knob: 1 to 6 the volume of the arrangement in its place (the same in every song), 7 its keys',
        8 all of them together; VOLUME_STEP a step, at once (the notes going on)."""
        arrangements = ((info.get("song") or {}).get("arrangements") or [])[:ARRANGEMENT_KNOBS]
        if encoder == CONTROLS:
            slots = range(len(info.get("volumes") or []))  # (all of them, in this song or not)
        else:
            slots = range(encoder, encoder + 1) if encoder < len(arrangements) else range(0)
        changes = {}
        for slot in slots:
            volume = min(max(self._volume_of(slot, info) + steps * VOLUME_STEP, 0), 127)
            self._pending[("volume", str(slot))] = volume
            changes[slot] = volume
        if changes:
            self.gui.set_volumes(changes)
        if encoder in (KEYS_COLUMN, CONTROLS):
            volume = min(max(self._keys_volume(info) + steps * VOLUME_STEP, 0), 127)
            self._pending[("volume", "keys")] = volume
            self.gui.set_keys({"volume": volume})
        if encoder in (CONTROLS, KEYS_COLUMN) or changes:
            self._mix_slider = encoder

    def _volume_of(self, slot: int, info: dict[str, Any]) -> int:
        """The volume of the arrangement in a place in its song (turns not in the page's state yet too)."""
        pending = self._pending.get(("volume", str(slot)))
        volumes = info.get("volumes") or []
        return pending if pending is not None else int(volumes[slot]) if slot < len(volumes) else 100

    def _keys_volume(self, info: dict[str, Any]) -> int:
        pending = self._pending.get(("volume", "keys"))
        return pending if pending is not None else int((info.get("keys") or {}).get("volume", 100))

    def _keys_tone(self, info: dict[str, Any]) -> int:
        """The tone its keys play the keyboard with (turns not in the page's state yet too)."""
        return self._pending.get(("keys", "tone")) or (info.get("keys") or {}).get("tone") or TONE_NUMBERS[0]

    def _turn_keys(self, steps: int, by_group: bool, info: dict[str, Any], now: float) -> None:
        """Knob 7: the tone its keys play the keyboard with, a tone at a time (by_group: knob 8, a group at a time);
        the screen shows it, and what the knobs do."""
        number = self._stepped(self._keys_tone(info), steps, by_group)
        self._pending[("keys", "tone")] = number
        self.gui.set_keys({"tone": number})
        self._show_for_a_while("keys", now)

    def _turn_tone(self, column: int, steps: int, by_group: bool, info: dict[str, Any], now: float) -> None:
        """Knobs 1 to 6: an arrangement's tone (or palm-mute or low-note tone, as the up arrow has it), a tone at a time
        (by_group: knob 8, a group at a time); kept at once, the song taking it once turns stop."""
        arrangements = ((info.get("song") or {}).get("arrangements") or [])[:ARRANGEMENT_KNOBS]
        if not arrangements:
            return
        column = min(column, len(arrangements) - 1)
        a, field = arrangements[column], FIELDS[self.field][0]
        if a.get("output"):  # (on another MIDI device: its programs, ten a step with knob 8)
            program = self._program_of(a)
            program = min(max((-1 if program is None else program) + steps * (10 if by_group else 1), 0), 127)
            self._pending[(a["id"], "program")] = program
            self._try(lambda: self.gui.set_program(a["kind"], program))
            self._part_turned = column
            self._show_for_a_while("parts", now)
            return
        number = self._stepped(self._knob_tone(a, field), steps, by_group, self._choices(a, field))
        self._pending[(a["id"], field)] = number
        self.gui.change_part(a["id"], {field: None if number == DEFAULT else number}, later=TURN_WAIT)
        self._turned_at, self._wait, self._settling = now, TURN_WAIT, True
        self._part_turned = column
        self._show_for_a_while("parts", now)

    def _stepped(self, number: int, steps: int, by_group: bool, choices: list[int] | None = None) -> int:
        """A tone some steps on: a tone at a time, or a group at a time (each group's first); the choices (SAME,
        DEFAULT) before them, a group by themselves."""
        choices = choices or []
        numbers = [*choices, *TONE_NUMBERS]
        starts = [0] * bool(choices) + [len(choices) + TONE_NUMBERS.index(first) for first in GROUPS]
        at = numbers.index(number) if number in numbers else len(choices)
        if not by_group:
            return numbers[(at + steps) % len(numbers)]
        group = max(i for i, start in enumerate(starts) if start <= at)
        return numbers[starts[(group + steps) % len(starts)]]

    def _program_of(self, arrangement: dict[str, Any]) -> int | None:
        """The program of the MIDI device an arrangement sounds on (turns not in the page's state yet too)."""
        pending = self._pending.get((arrangement["id"], "program"))
        return pending if pending is not None else (arrangement.get("output") or {}).get("program")

    def _choices(self, arrangement: dict[str, Any], field: str) -> list[int]:
        """What a knob has before the tones: palm mutes and low notes, the same tone as the rest, and their kind's
        default if it has one."""
        if field == "tone":
            return []
        return [SAME, DEFAULT] if self._default_of(arrangement, field) else [SAME]

    def _default_of(self, arrangement: dict[str, Any], field: str) -> int | None:
        """An arrangement's kind's default tone for a field (lead, rhythm or bass parts), if it has one."""
        return ((self._info or {}).get(arrangement["kind"]) or {}).get(field) or None

    def _knob_tone(self, arrangement: dict[str, Any], field: str) -> int:
        """Where an arrangement's knob is for a field (turns not applied yet too): its tone; palm mutes and low
        notes, SAME for the same tone as the rest, else with none of their own, DEFAULT (or SAME, without one)."""
        pending = self._pending.get((arrangement["id"], field))
        if pending is not None:
            return pending
        if field == "tone":
            return self._tone_of(arrangement, field)
        own = arrangement["tones"].get(field)
        if own is not None:
            return int(own)
        return DEFAULT if self._default_of(arrangement, field) else SAME

    def _lights_of(self, arrangement: dict[str, Any]) -> int:
        """Its lit keys, as the pads have them (taps not applied yet too)."""
        pending = self._pending.get((arrangement["id"], "lights"))
        return int(arrangement["lights"]) if pending is None else pending

    def _tone_of(self, arrangement: dict[str, Any], field: str) -> int:
        """The tone an arrangement plays with: its own, its kind's default, or the built-in one."""
        defaults = self._info[arrangement["kind"]] if self._info else {}
        own = arrangement["tones"].get(field) or defaults.get(field)
        if own:
            return own
        if field != "tone":  # no palm-mute tone yet: start from the tone it plays with
            return self._tone_of(arrangement, "tone")
        return BUILT_IN.get(arrangement["kind"], BUILT_IN["lead"])


def _time(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _exact(seconds: float) -> str:
    """A time to a tenth of a second (a loop's end)."""
    tenths = round(seconds * 10)
    return f"{tenths // 600}:{tenths % 600 // 10:02d}.{tenths % 10}"


def _tag(arrangement: dict[str, Any], arrangements: list[dict[str, Any]]) -> str:
    """An arrangement's letter beside its chord (L, R or B), numbered when the song has more than one of its kind."""
    kind = str(arrangement["kind"])
    same = [a["id"] for a in arrangements if a["kind"] == kind]
    letter = KIND_LETTERS.get(kind) or kind[:1].upper()
    return letter if len(same) < 2 else f"{letter}{same.index(arrangement['id']) + 1}"


def _section_at(sections: list[dict[str, Any]], at: float) -> dict[str, Any] | None:
    """The section a song is in at a time (None: before its first)."""
    return next((x for x in reversed(sections) if x["start"] <= at + 0.001), None)


def _sign(info: dict[str, Any]) -> str | None:
    """The song's sign: playing, paused or stopped; following, the game's song playing or paused (None: neither, a
    menu, or not known)."""
    if info.get("mode") == "follow":
        return info.get("state")
    return "stopped" if info.get("mode") != "play" else "paused" if info.get("paused") else "playing"


def _riff_trouble(game: dict[str, Any] | None) -> str | None:
    """Following, why the game can't loop the range (None: it can, or not following)."""
    if game is None:
        return None
    if not game.get("answered"):
        return "Game: no riffloop mod"
    if not game.get("hook"):
        return "Game: needs RS Re-ARMed 1.2+"
    return None


def _pedal_trouble(error: str) -> str:
    """Why the pedal couldn't be switched, short enough for the screen."""
    if "dnafx-preset" in error:
        return "Needs dnafx-preset"
    if "not found" in error or "could not be opened" in error:
        return "Pedal not connected"
    if "didn't answer" in error:
        return "Pedal didn't answer"
    return wrap(error, WIDTH - 2, 1)[0]


def _kinds(sections: list[dict[str, Any]]) -> list[str]:
    """The kinds of a song's sections with a pad each, in the order they come: the most common, if there are more
    than the pads (no guitar and silence left out)."""
    order: list[str] = []
    counts: dict[str, int] = {}
    for section in sections:
        kind = section["kind"]
        if kind in ("noguitar", "silence"):
            continue
        if kind not in counts:
            order.append(kind)
        counts[kind] = counts.get(kind, 0) + 1
    kept = sorted(order, key=lambda kind: (-counts[kind], order.index(kind)))[:SECTION_PADS]
    return [kind for kind in order if kind in kept]
