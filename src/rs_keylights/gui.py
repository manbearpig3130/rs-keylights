from __future__ import annotations

import copy
import hmac
import ipaddress
import json
import math
import os
from bisect import bisect_left, bisect_right
import queue
import re
import secrets
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import ParseResult, parse_qs, urlparse

from .follow import DEFAULT_OVERLAY_PORT, OverlayClient, PartsFor
from .keyboard import DEFAULT_CHANNEL, DEFAULT_PORT, NORMAL_VOLUME, Keyboard, KeyboardError, Synth
from .library import NO_NOTES, ArrangementRef, SongIndex
from .psarc import Archive, ArchiveError
from .lights import LIGHTS_EARLY, MIN_PERIOD, KeyRange
from .parts import Part, PlannedPart, VoicesFor, plan, player_for
from .session import Following, Playback
from .listen import BASS_LOWEST, DEVICE as GUITAR_INPUT, LOWEST, Listener
from .lyrics import Line, lyric_lines
from .pedal import SLOTS as PEDAL_SLOTS, Pedal
from .riffloop import GameLoop, GameSongs, RiffLoop
from .sng import BASS, GUITAR, Arrangement, Chord, Note, Section, note_name
from .songtones import FIRST_KINDS, KINDS as TONE_KINDS, kind_changes, song_tones, tone_kind
from .keyshow import KeyShow
from .songkey import Key, find_key, scale_show, shapes, tuning_name
from .sound import CHANNEL_SETS, SoundChoice, Voice, Voices, parse_note
from .tones import TONES, Tone, tone
from .watch import Watch

# A page in the browser for what the commands do: the song library, parts and their tones, playback
# with a time slider, following the game, and the keyboard's settings. The server answers on 127.0.0.1,
# to requests for 127.0.0.1 or localhost (not other names pointing there: DNS rebinding), and changes
# things only for JSON requests from its own page. With lan it answers on the network too, so a phone
# can be the remote while the game has the screen: to the Mac's own names and addresses, and to
# browsers that opened the link with its key once (then a cookie has it).

DEFAULT_GUI_PORT = 8040
PART_ORDER = ("lead", "rhythm", "combo", "bass")
KINDS = ("lead", "rhythm", "bass")  # each has default tones, for parts without their own
TONE_FIELDS = ("tone", "mute_tone", "low_tone")  # a set of tones: these tone numbers, and split (a note)
SAME_TONE = 0  # palm mutes or low notes with the tone the rest has (over a default for them)
SLOTS = 8  # a song's arrangements with volumes of their own, by their place in it (the same in every song)
GAME_TIME_EVERY = 0.05  # seconds: the game's time asked of its riffloop mod at most this often
GAME_SONGS_EVERY = 0.1  # seconds: following, its song list's highlight asked at most this often
GAME_SONGS_OWN = 0.6  # seconds after a remote moved the game's highlight its moves are taken as that one's
GAME_KEYS_AT_ONCE = 300  # the song list's keys asked for so many at a time
PEDAL_FAVOURITES, PEDAL_FAVOURITES_MOST = (51, 187), 8  # the pedal presets the Launchkey's pad goes through
WARM_AROUND = (0, 1, -1, 2, -2, 3, -3)  # the songs whose art gets ready around the one browsed or pointed at
ARRANGEMENT_ID = re.compile(r"[0-9A-Za-z-]{1,64}")
KEY_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"  # easy to type on a phone: no 0/o or 1/l/i
KEY_COOKIE = "rsk_key"
REMOTE_BUSY = 2.0  # seconds after a remote was last used that the page looks more often
KEYS_CHANNEL = 1  # a remote's keys: no song's part and no light uses channel 1 (nor the keyboard's own keys)
SECTION_NAMES = {"prechorus": "Pre-chorus", "postchorus": "Post-chorus", "preverse": "Pre-verse",
                 "postvs": "Post-verse", "postverse": "Post-verse", "noguitar": "No guitar", "buildup": "Build-up",
                 "fadein": "Fade in", "fadeout": "Fade out", "modverse": "Verse", "modchorus": "Chorus"}
KEYS_TONE = 1  # STEREO GRAND PIANO
KEY_RANGE = KeyRange()  # the keys that light
RING_LOOK = 6.0  # seconds back a held note's start is looked for (a guitar heard)
TONE_NUMBERS = frozenset(t.number for t in TONES)
COOKIE_AGE = 400 * 24 * 3600  # the most browsers keep one
NO_KEY_PAGE = (b"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
               b"<title>rs-keylights</title><p style='font:17px -apple-system,sans-serif;margin:2em'>Open the "
               b"link with the key once: the page on the Mac shows it under <b>Keyboard</b>, and "
               b"<code>rs-keylights gui --lan</code> prints it.</p>")


class GuiError(ValueError):
    """A request that can't be done: the page shows the message."""


def default_settings_file() -> Path:
    return Path.home() / "Library" / "Application Support" / "rs-keylights" / "gui.json"


def song_id(ref: ArrangementRef) -> str:
    return f"{ref.archive}#{ref.song_key}"


def part_order(ref: ArrangementRef) -> tuple[int, str]:
    return (PART_ORDER.index(ref.part) if ref.part in PART_ORDER else len(PART_ORDER), ref.part)


def kind(ref: ArrangementRef) -> str:
    """Whose default tones a part gets: bass, rhythm, or lead (combos too)."""
    if ref.bass:
        return "bass"
    return "rhythm" if ref.part.startswith("rhythm") or ref.name.lower() == "rhythm" else "lead"


def default_lights(kinds: list[str]) -> list[int]:
    """The keys each of a song's parts lights by default when it's played (0: it doesn't play): with 4 parts or
    fewer, a key each, and those left over to the lead parts and the rhythm parts in turn (to the bass only
    without them); with more, so for the first lead, rhythm and bass part."""
    chosen = list(range(len(kinds))) if len(kinds) <= 4 else [kinds.index(k) for k in KINDS if k in kinds]
    lights = [1 if i in chosen else 0 for i in range(len(kinds))]
    favoured = sorted([i for i in chosen if kinds[i] != "bass"] or chosen, key=lambda i: (kinds[i] != "lead", i))
    for n in range(4 - len(chosen) if favoured else 0):
        lights[favoured[n % len(favoured)]] += 1
    return lights


def default_follow() -> dict[str, Any]:
    """Following's settings: real pitch, the offset, the default tones for lead, rhythm and bass parts
    (playing songs uses them too), the tones of arrangements given their own, and the parts chosen to play
    while an arrangement is played (all by persistent ID: {played: {arrangement: {play, lights}}}, where
    only the one played plays on every key)."""
    return {"real_pitch": False, "offset_ms": 0, "show_key": True, "lead": {}, "rhythm": {},
            "bass": {}, "arrangements": {}, "parts": {}}


def changed_follow(settings: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Following's settings with a change to any of them, checked. Tones change field by field: an empty one
    goes back to its default (an arrangement's: those for its kind of part)."""
    out = copy.deepcopy(settings)
    for key in ("real_pitch", "show_key"):  # (sound: one switch for playing and following, play options' silent)
        if key in change:
            out[key] = bool(change[key])
    if "offset_ms" in change:
        out["offset_ms"] = _number(change["offset_ms"], "offset", -2000, 2000)
    for key in KINDS:
        if key in change:
            out[key] = _changed_tones(out[key], change[key])
    arrangements = change.get("arrangements", {})
    if not isinstance(arrangements, dict):
        raise GuiError("Expected tones for each arrangement")
    for arrangement_id, tones in arrangements.items():
        arrangement_id = _arrangement_id(arrangement_id)
        merged = _changed_tones(out["arrangements"].get(arrangement_id, {}), tones)
        if merged:
            out["arrangements"][arrangement_id] = merged
        else:
            out["arrangements"].pop(arrangement_id, None)
    parts = change.get("parts", {})
    if not isinstance(parts, dict):
        raise GuiError("Expected the parts to play for each arrangement played")
    for played, chosen in parts.items():
        played = _arrangement_id(played)
        if not isinstance(chosen, dict):
            raise GuiError("Expected the parts to play")
        along = out["parts"].setdefault(played, {})
        for arrangement_id, fields in chosen.items():
            arrangement_id = _arrangement_id(arrangement_id)
            if not isinstance(fields, dict):
                raise GuiError("Expected whether a part plays and its lit keys")
            merged = {**along.get(arrangement_id, {}), **{k: v for k, v in fields.items() if k in ("play", "lights")}}
            is_played, part = arrangement_id == played, {}
            if merged.get("play") is not None and bool(merged["play"]) != is_played:
                part["play"] = bool(merged["play"])
            if merged.get("lights") is not None:
                lights = int(_number(merged["lights"], "lit keys", 0, 4))
                if lights != (4 if is_played else 0):
                    part["lights"] = lights
            if part:  # only what differs from the default is kept
                along[arrangement_id] = part
            else:
                along.pop(arrangement_id, None)
        if not along:
            del out["parts"][played]
    return out


def _arrangement_id(value: str) -> str:
    if not ARRANGEMENT_ID.fullmatch(value):
        raise GuiError(f"Not an arrangement: {value!r}")
    return value.upper()


def _outputs(table: dict[str, Any], change: Any) -> dict[str, dict[str, Any]]:
    """The MIDI device sounding each kind of part (lead, rhythm, bass) other than the keyboard, with a change,
    checked: its port, channel, and whether it plays a note at a time (a change of None: the keyboard again)."""
    if not isinstance(change, dict):
        raise GuiError("Expected a MIDI device for each kind of part")
    out = dict(table)
    for part, chosen in change.items():
        if part not in KINDS:
            raise GuiError(f"No kind of part {part!r}")
        if not chosen:
            out.pop(part, None)
            continue
        if not isinstance(chosen, dict) or not str(chosen.get("port") or "").strip():
            raise GuiError("Choose a MIDI output for the part")
        program = chosen.get("program")
        out[part] = {"port": str(chosen["port"]).strip(),
                     "channel": int(_number(chosen.get("channel", 1), "channel", 1, 16)),
                     "mono": bool(chosen.get("mono", True)),
                     "program": None if program in (None, "") else int(_number(program, "program", 0, 127)),
                     "octave": int(_number(chosen.get("octave", 0), "octave", -2, 2))}
    return out


def _tone_map(table: dict[str, Any], change: Any) -> dict[str, int | None]:
    """The keyboard's tone for each kind of tone a song switches to (songtones' KINDS), with a change, checked
    (None: the tone as it is)."""
    if not isinstance(change, dict):
        raise GuiError("Expected a tone for each kind of tone")
    out = dict(table)
    for kind, number in change.items():
        if kind not in TONE_KINDS:
            raise GuiError(f"No kind of tone {kind!r}")
        if number in (None, "", 0):
            out[kind] = None
        else:
            found = _tone(number)
            out[kind] = found.number if found else None
    return out


def _initial(artist: str) -> str:
    """An artist's letter, going through the library a letter at a time (numbers and signs: "#")."""
    first = artist.strip().casefold()[:1]
    return first if first.isalpha() else "#"


def _sections(sections: tuple[Section, ...]) -> list[dict[str, Any]]:
    """A song's sections: each one's kind (as charted, without a number: chorus2 is a chorus), its name (the
    kind's, and how many of them so far: "Chorus 2"), and its start and end."""
    counts: dict[str, int] = {}
    out = []
    for section in sections:
        kind = re.sub(r"\d+$", "", section.name.lower()) or section.name.lower()
        counts[kind] = counts.get(kind, 0) + 1
        out.append({"kind": kind, "name": f"{SECTION_NAMES.get(kind, kind.capitalize())} {counts[kind]}",
                    "start": round(section.start, 3), "end": round(section.end, 3)})
    return out


def parts_chosen(settings: dict[str, Any], played: ArrangementRef,
                 refs: list[ArrangementRef]) -> list[tuple[ArrangementRef, bool, int]]:
    """Each arrangement of a song while one of them is played: whether it plays and the keys it lights (the
    one played: yes, every key; the others: no, none; unless chosen otherwise for the one played)."""
    chosen = settings["parts"].get(played.persistent_id, {})
    out = []
    for ref in refs:
        own, is_played = chosen.get(ref.persistent_id, {}), ref.persistent_id == played.persistent_id
        out.append((ref, own.get("play", is_played), own.get("lights", 4 if is_played else 0)))
    return out


def _changed_options(options: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Playing's options with a change, checked: speed, real_pitch, silent, and the song picked."""
    out = dict(options)
    if "speed" in change:
        out["speed"] = _number(change["speed"], "speed", 0.05, 4)
    for key in ("real_pitch", "silent"):
        if key in change:
            out[key] = bool(change[key])
    if "song" in change:
        out["song"] = str(change["song"]) if change["song"] else None
    return out


def _volumes(volumes: Any) -> list[int]:
    """Volumes kept by place (SLOTS of them, 0 to 127), checked."""
    out = [int(_number(v, "volume", 0, 127)) for v in list(volumes)[:SLOTS]]
    return out + [NORMAL_VOLUME] * (SLOTS - len(out))


def _changed_tones(tones: dict[str, Any], change: Any) -> dict[str, Any]:
    if not isinstance(change, dict):
        raise GuiError("Expected tones")
    merged = {**tones, **{k: v for k, v in change.items() if k in (*TONE_FIELDS, "split")}}
    out: dict[str, Any] = {}
    for field in TONE_FIELDS:
        if field != "tone" and merged.get(field) in (SAME_TONE, str(SAME_TONE)) and merged.get(field) is not False:
            out[field] = SAME_TONE  # (kept: it plays over a default)
            continue
        found = _tone(merged.get(field))
        if found is not None:
            out[field] = found.number
    split = str(merged.get("split") or "").strip()
    if split:
        _split(split)
        out["split"] = split
    return out


class Gui:
    def __init__(
        self,
        index: SongIndex,
        keyboard_factory: Callable[[str, int], Keyboard] = Keyboard,
        settings_file: Path | None = None,
        overlay_port: int = DEFAULT_OVERLAY_PORT,
        list_ports: Callable[[], list[str]] | None = None,
        other_follow: Callable[[], bool] | None = None,
        overrides: dict[str, Any] | None = None,
        watch_factory: Callable[[Gui], Watch] = Watch,
        trace: Callable[[dict[str, object]], None] | None = None,
        synth_factory: Callable[[str], Synth] = Synth,
        pedal_factory: Callable[[], Pedal] = Pedal,
        riff_loop: RiffLoop | None = None,
    ):
        self.index = index
        self._synth_factory = synth_factory  # other MIDI devices sounding parts (outputs), opened once they're used
        self._synths: dict[str, Synth] = {}
        self.pedal = pedal_factory()  # the guitar's pedal, its presets switched from the Launchkey and the page
        # Following, the loop is Rocksmith's: its Riff Repeater loops it (RS Re-ARMed's riffloop mod), for the song
        # it was set for (another followed, or following stopped: Riff Repeater's own again)
        self._riff_loop = riff_loop or RiffLoop()
        self._riff_song: str | None = None
        self._riff_said: GameLoop | None = None
        self._game_time: tuple[float, float | None] = (-math.inf, None)  # (when asked, the game's time then)
        self._game_said: GameLoop | None = None  # (and all the mod said then)
        # Following, Learn a Song's song list (the game's, through the mod): its highlight as last asked (when), its
        # keys in its order, the key last seen highlighted, and the one a remote last moved it to (when)
        self._game_songs: tuple[float, GameSongs | None] = (-math.inf, None)
        self._game_keys: list[str] = []
        self._game_seen: str | None = None
        self._game_sent: tuple[float, str | None] = (-math.inf, None)
        self._riff_settings: dict[str, Any] = {"speed": 100, "accelerator": False, "passes": 1}  # (as set here)
        self._listener: Listener | None = None  # the guitar heard (listen), its notes lighting the keys
        self._planned_now: list[PlannedPart] = []  # the song playing's parts, as planned
        self._note_times: dict[int, tuple[tuple[Note, ...], list[float]]] = {}  # (when its notes start, by them)
        self._trace = trace  # what RS Companion reports while following, for debugging (follow.Follower's)
        self._watch_factory = watch_factory
        self._watch: Watch | None = None  # the Launchkey, and each followed song's key as it starts
        self._keys: dict[str, dict[str, Any] | None] = {}  # each song's key, as the page shows it
        self._library: dict[str, Any] | None = None  # the library as the page lists it, until it's read again
        self._browse: dict[str, Any] | None = None  # a remote going through the library: where, and since when
        self._loop: dict[str, Any] = {"song": None, "a": None, "b": None, "on": False}  # the A-B loop, of a song
        self._dialling: dict[str, Any] = {"at": -math.inf}  # what a remote is setting (speed, position), and when it was last used
        self._art: OrderedDict[str, bytes | None] = OrderedDict()  # album art as PNG, the last ones asked for
        self._idle_show: KeyShow | None = None
        self._show_quit = threading.Event()  # a show on the keys while nothing plays
        self._show_thread: threading.Thread | None = None
        # A remote's keys play the keyboard from its own MIDI thread: on the song's keyboard while one plays or
        # is followed, else on one of their own (which a show shares while nothing plays)
        self._keys_lock = threading.RLock()  # after _lock, when both are taken
        self._idle_keyboard: Keyboard | None = None
        self._followed: ArrangementRef | None = None  # following: the arrangement last played (its song stays shown)
        self._keys_voiced: tuple[Keyboard, int] | None = None  # the keyboard given the keys' tone, and the tone
        # Played by a thread of their own: the remote's MIDI thread only hands them on (CoreMIDI can deadlock if
        # it waits, or opens ports); [] lets go of them all, None ends it
        self._keys_queue: queue.Queue[list[int] | None] = queue.Queue()
        self._keys_thread: threading.Thread | None = None
        self._replays: dict[str, Callable[[], None]] = {}  # the parts to play again once a remote's changes pause
        self._replay_timer: threading.Timer | None = None
        self._warm: list[str] = []  # songs whose album art to get ready next, in the background
        self._warm_ready = threading.Condition()
        self._warm_thread: threading.Thread | None = None
        self.keyboard_factory = keyboard_factory
        self.settings_file = settings_file or default_settings_file()
        self.overlay_port = overlay_port
        self._list_ports = list_ports or _midi_outputs
        self._other_follow = other_follow or _other_follow_running
        self._lock = threading.RLock()
        self._session: Playback | Following | None = None
        self._playing: dict[str, Any] | None = None  # the song being played: id, title, parts
        self._loaded: OrderedDict[str, list[tuple[ArrangementRef, Arrangement]]] = OrderedDict()
        self._lyric_lines: OrderedDict[str, tuple[Line, ...]] = OrderedDict()  # songs' lyrics, the last few
        self._other = (0.0, False)
        self.settings: dict[str, Any] = {"midi_port": DEFAULT_PORT, "channel": DEFAULT_CHANNEL,
                                         "lights_early_ms": LIGHTS_EARLY * 1000, "fastest_flash_ms": MIN_PERIOD * 1000,
                                         "launchkey": True, "keys_sound": True, "keys_lights": True, "ring": True,
                                         "overlap_ms": 120, "listen": False, "listen_device": GUITAR_INPUT,
                                         "tone_changes": True, "tone_map": dict(TONE_KINDS), "techniques": True,
                                         "outputs": {},
                                         "keys_tone": KEYS_TONE, "keys_volume": NORMAL_VOLUME,
                                         "volumes": [NORMAL_VOLUME] * SLOTS, "pedal_backup": "",
                                         "pedal_favourites": list(PEDAL_FAVOURITES)}
        self.follow_settings = default_follow()  # the same for every page showing them, phone or Mac
        self.follow_version = 0  # changes with them, so pages know to show them again
        self._follow_saved = False
        # Playing songs: the options, the song picked, and each song's parts, lit keys, tones and start
        self.play_options: dict[str, Any] = {"speed": 1.0, "real_pitch": False, "silent": False, "song": None}
        self.song_settings: dict[str, dict[str, Any]] = {}
        self.play_version = 0
        self._play_saved = False
        self.songs_file = self.settings_file.with_name("songs.json")
        self._lan_key: str | None = None
        try:
            songs = json.loads(self.songs_file.read_text())
        except (OSError, ValueError):
            songs = None
        if isinstance(songs, dict):
            self.song_settings = {sid: value for sid, value in songs.items() if isinstance(value, dict)}
        try:
            saved = json.loads(self.settings_file.read_text())
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict):
            if isinstance(saved.get("play"), dict):
                try:
                    self.play_options, self._play_saved = _changed_options(self.play_options, saved["play"]), True
                except GuiError:
                    pass
            self.settings.update({k: saved[k] for k in self.settings if k in saved})
            try:
                self.settings["volumes"] = _volumes(self.settings["volumes"])
                self.settings["keys_volume"] = int(_number(self.settings["keys_volume"], "volume", 0, 127))
            except (GuiError, TypeError, ValueError):
                self.settings.update(volumes=[NORMAL_VOLUME] * SLOTS, keys_volume=NORMAL_VOLUME)
            try:
                kept = _tone_map(dict(TONE_KINDS), self.settings["tone_map"])
                self.settings["tone_map"] = {kind: TONE_KINDS[kind] if number == FIRST_KINDS.get(kind) else number
                                             for kind, number in kept.items()}  # (the first defaults: the GM tones)
            except (GuiError, TypeError, ValueError, AttributeError):
                self.settings["tone_map"] = dict(TONE_KINDS)
            try:
                self.settings["pedal_favourites"] = _favourites(self.settings["pedal_favourites"])
            except (GuiError, TypeError, ValueError):
                self.settings["pedal_favourites"] = list(PEDAL_FAVOURITES)
            try:
                self.settings["outputs"] = _outputs({}, self.settings["outputs"])
            except (GuiError, TypeError, ValueError, AttributeError):
                self.settings["outputs"] = {}
            if isinstance(saved.get("follow"), dict):
                follow = dict(saved["follow"])
                if "guitar" in follow:  # before lead and rhythm parts had defaults of their own
                    guitar = follow.pop("guitar")
                    follow.setdefault("lead", guitar)
                    follow.setdefault("rhythm", guitar)
                try:
                    self.follow_settings, self._follow_saved = changed_follow(self.follow_settings, follow), True
                except GuiError:
                    pass
            if isinstance(saved.get("lan_key"), str) and len(saved["lan_key"]) >= 8:
                self._lan_key = saved["lan_key"]
        self._kept = dict(self.settings)  # what the file keeps: not the command line's overrides
        self.settings.update(overrides or {})  # from the command line: for this run, not saved
        self.pedal.use_backup(_backup(self.settings["pedal_backup"]))

    # Library and songs

    def library(self) -> dict[str, Any]:
        """Every song, by artist, with what the index knows of it (no notes read): for the list and its details."""
        with self._lock:
            if self._library is not None:
                return self._library
        songs: dict[str, list[ArrangementRef]] = {}
        for ref in self.index.arrangements():
            if ref.part not in NO_NOTES:
                songs.setdefault(song_id(ref), []).append(ref)
        out = []
        for sid, refs in songs.items():
            named = max(refs, key=lambda r: bool(r.artist))
            guitar = next((r for r in sorted(refs, key=part_order) if not r.bass), refs[0])
            out.append({
                "id": sid,
                "artist": named.artist,
                "song": named.song or named.song_key,
                "file": Path(named.archive).name,
                "parts": [r.name or r.part for r in sorted(refs, key=part_order)],
                "album": named.album,
                "year": named.year,
                "length": max(r.length for r in refs),
                "tempo": round(named.tempo),
                "tuning": tuning_name(guitar.tuning, guitar.capo),
            })
        out.sort(key=lambda s: (s["artist"].casefold(), s["song"].casefold(), s["file"]))
        library = {"game_dir": str(self.index.game_dir), "songs": out}
        with self._lock:
            self._library = library
        return library

    def song(self, sid: str) -> dict[str, Any]:
        """A song's details, with its parts as chosen to play (which, their lit keys and tones) and its start."""
        loaded = self._load(sid)
        first = loaded[0][0]
        named = next((r for r, _a in loaded if r.artist), first)
        chosen = {p["part"]: p for p in self._song_parts(sid, loaded)}
        return {
            "id": sid,
            "artist": named.artist,
            "song": named.song or named.song_key,
            "title": named.title,
            "file": Path(first.archive).name,
            "key": self.song_key(sid),
            "guitar": _guitar(loaded),
            "length": max(a.length for _r, a in loaded),
            "sections": _sections(next((a.sections for _r, a in loaded if a.sections), ())),
            "start": self.song_settings.get(sid, {}).get("start", 0),
            "saved": sid in self.song_settings,
            "version": self.play_version,
            "parts": [
                {
                    **chosen[ref.part],
                    "song_tones": [{"at": round(at, 3), "said": said, "known": known}
                                   for at, said, known in song_tones(a, ref.bass)],
                    "notes": len(a.notes),
                    "tuning": " ".join(f"{t:+d}" if t else "0" for t in a.tuning),
                    "arrangement": ref.persistent_id,
                }
                for ref, a in loaded
            ],
        }

    def fretboard(self, sid: str, part: str | None = None) -> dict[str, Any]:
        """An arrangement's notes and chord shapes where they're played, for the page's fretboard: each note's
        start, end and strings with their frets, and each chord's start, end, name and shape (a fret a string, -1
        not played); the one asked for (its part, or its persistent ID), else the song's first guitar part; with
        the guitar it's played on."""
        loaded = self._load(sid)
        ref, a = next(((r, a) for r, a in loaded if part in (r.part, r.persistent_id)), None) or \
            next(((r, a) for r, a in loaded if not r.bass), loaded[0])
        return {**(_guitar([(ref, a)]) or {}), "part": ref.part, "arrangement": ref.persistent_id,
                "name": ref.name or ref.part,
                "notes": [[round(n.time, 3), round(n.time + n.sustain, 3), [[s, f] for s, f in zip(n.strings, n.frets)]]
                          for n in a.notes],
                "chords": [[round(c.start, 3), round(c.end, 3), c.name, list(c.frets)] for c in a.chords]}

    def _song_parts(self, sid: str, loaded: list[tuple[ArrangementRef, Arrangement]]) -> list[dict[str, Any]]:
        """A song's parts as chosen to play: unless chosen otherwise, as default_lights has them."""
        saved = self.song_settings.get(sid, {}).get("parts", {})
        defaults = default_lights([kind(ref) for ref, _arrangement in loaded])
        out = []
        for i, (ref, _arrangement) in enumerate(loaded):
            own = saved.get(ref.part, {})
            out.append({"part": ref.part, "name": ref.name or ref.part, "kind": kind(ref),
                        "play": bool(own.get("play", defaults[i] > 0)), "lights": int(own.get("lights", defaults[i])),
                        "tones": {k: own[k] for k in (*TONE_FIELDS, "split") if own.get(k) not in (None, "")}})
        return out

    def set_song(self, body: dict[str, Any], later: float = 0.0) -> dict[str, Any]:
        """Changes to a song's parts (play, lights, and tone fields as following's) or its start, checked and
        kept; the song goes on with them if it's playing: at once, or once changes stop coming for later
        seconds (a remote's taps and turns)."""
        sid = str(body.get("song", ""))
        loaded = self._load(sid)
        change = body.get("parts") or {}
        if not isinstance(change, dict):
            raise GuiError("Expected each part's choices")
        with self._lock:
            parts = {p["part"]: {"play": p["play"], "lights": p["lights"], **p["tones"]}
                     for p in self._song_parts(sid, loaded)}
            for name, fields in change.items():
                if name not in parts:
                    raise GuiError(f"No part {name} in this song")
                if not isinstance(fields, dict):
                    raise GuiError("Expected a part's choices")
                part = parts[name]
                tones = _changed_tones({k: part[k] for k in (*TONE_FIELDS, "split") if k in part}, fields)
                play = bool(fields["play"]) if "play" in fields else part["play"]
                lights = int(_number(fields["lights"], "lit keys", 0, 4)) if "lights" in fields else part["lights"]
                parts[name] = {"play": play, "lights": lights, **tones}
            for name, fields in change.items():  # a part turned on takes the keys still free
                if fields.get("play") and "lights" not in fields:
                    others = sum(p["lights"] for n, p in parts.items() if p["play"] and n != name)
                    parts[name]["lights"] = min(parts[name]["lights"], max(0, 4 - others))
            on = [p for p in parts.values() if p["play"]]
            if len(on) > len(CHANNEL_SETS):
                raise GuiError(f"{len(CHANNEL_SETS)} parts at most")
            if sum(p["lights"] for p in on) > 4:
                raise GuiError("The keyboard lights 4 keys at most: give the parts fewer lit keys")
            kept = dict(self.song_settings.get(sid, {}), parts=parts)
            if "start" in body:
                kept["start"] = _number(body["start"], "start", 0, 24 * 3600)
            self.song_settings[sid] = kept
            self.play_version += 1
            self._save_songs()
            if any(field != "start" for field in body if field != "song"):
                self._replay("song", lambda: self._play_on(sid), later)
        return self.song(sid)

    def set_volumes(self, changes: dict[int, Any]) -> dict[str, Any]:
        """The volumes of songs' arrangements by their place in their song (0: the first; 0 to 127, NORMAL_VOLUME
        as they start), the same in every song, playing or following: kept, and at once on the keyboard, the notes
        going on. All of them. (An arrangement muted is one not played: a part's "play".)"""
        with self._lock:
            volumes = list(self.settings["volumes"])
            for slot in changes:
                if not 0 <= int(slot) < SLOTS:
                    raise GuiError(f"Volumes for the first {SLOTS} arrangements of a song")
            for slot, volume in changes.items():
                volumes[int(slot)] = int(_number(volume, "volume", 0, 127))
            self.settings["volumes"] = self._kept["volumes"] = volumes
            self._save()
            for slot in {int(slot) for slot in changes}:
                self._volume_now(slot, self._volume(slot))
        return {"volumes": volumes}

    def _volume_now(self, slot: int, volume: int) -> None:
        """(Holding _lock.) The volume of the arrangement in a place in its song at once, if it's sounding: on the
        keyboard, or the other MIDI device it sounds on."""
        session = self._session
        if isinstance(session, Playback) and session.running and self._playing:
            sounding = [(p["slot"], CHANNEL_SETS[i], track.voices)
                        for i, (p, track) in enumerate(zip(self._playing["parts"], session.player.tracks))]
        elif isinstance(session, Following) and session.running:
            sounding = [(self._slot(ref), channels, planned.voices)
                        for (ref, channels), planned in zip(session.follower.sounding(), session.follower.planned)]
        else:
            return
        for place, channels, voices in sounding:
            if place != slot:
                continue
            if isinstance(voices, Voices) and voices.output is not None:
                voices.output.set_volume(voices.main.channel, volume)
            else:
                for channel in channels:
                    session.keyboard.set_volume(channel, volume)

    def _slot(self, ref: ArrangementRef) -> int:
        """An arrangement's place in its song (as its parts are listed: lead first)."""
        return next((i for i, r in enumerate(self._song_refs(ref)) if r.persistent_id == ref.persistent_id), 0)

    def _volume(self, slot: int) -> int:
        """The volume of the arrangement in a place in its song."""
        volumes = self.settings["volumes"]
        return int(volumes[slot]) if slot < len(volumes) else NORMAL_VOLUME

    def switch(self, body: dict[str, Any]) -> dict[str, Any]:
        """Makes a song the active one: the one Play plays and a remote shows (another one playing stops; following
        the game goes on, the song waiting until it stops)."""
        sid = str(body.get("song", ""))
        self._load(sid)
        with self._lock:
            session = self._session
            if isinstance(session, Playback) and session.running and self._playing and self._playing["id"] != sid:
                self._stop()
            self.play_options["song"], self._play_saved = sid, True
            self.play_version += 1
            self._save()
        return self.state()

    def toggle_real_pitch(self) -> bool:
        """Real pitch on or off, for what's followed or played; whether it's on now."""
        with self._lock:
            following = isinstance(self._session, Following) and self._session.running
        if following:
            return bool(self.set_follow({"real_pitch": not self.follow_settings["real_pitch"]})["real_pitch"])
        return bool(self.set_play_options({"real_pitch": not self.play_options["real_pitch"]})["real_pitch"])

    # Going through the library with a remote

    # Following, on Learn a Song's song list, a remote goes through the game's list instead (its order, its
    # highlight moved to the song too) and the library follows it, as it does the game's own moves

    def game_songs(self) -> GameSongs | None:
        """Following, Learn a Song's song list while it shows: its highlighted song (asked at most every
        GAME_SONGS_EVERY); None otherwise, or without the mod."""
        with self._lock:
            session = self._session
        if not (isinstance(session, Following) and session.running):
            return None
        now = time.monotonic()
        asked, said = self._game_songs
        if now - asked >= GAME_SONGS_EVERY:
            said = self._riff_loop.songs()
            self._game_songs = (now, said)
        return said

    def game_browse(self) -> dict[str, Any] | None:
        """Following, the game's song list highlighting another song by itself (its keys, Rocksmith's up and down on
        a remote): the library's place moved to it too; the song, as browse_move has it. None otherwise."""
        said = self.game_songs()
        if said is None or said.key == self._game_seen:
            return None
        self._game_seen = said.key
        if time.monotonic() - self._game_sent[0] < GAME_SONGS_OWN:
            return None  # (a remote's move, or on its way there)
        return self._browse_game(float(said.row), said.count, said.key)

    def _game_song_keys(self, count: int) -> list[str]:
        """The song list's keys in its order (asked again when it has another number of rows)."""
        if len(self._game_keys) != count:
            keys: list[str] = []
            while len(keys) < count:
                said = self._riff_loop.song_keys(len(keys), GAME_KEYS_AT_ONCE)
                if said is None or not said[1]:
                    break
                keys += said[1]
            self._game_keys = keys
        return self._game_keys

    def _browse_game(self, row: float, count: int, key: str) -> dict[str, Any]:
        """The library's place on the game's song with a key (a row of its song list, maybe between two, as a slow
        strip has it); the song, as browse_move has it (one the library hasn't: its key)."""
        listed = self.library()["songs"]
        index = next((i for i, song in enumerate(listed) if song["id"].split("#", 1)[-1] == key), None)
        with self._lock:
            version = self._browse["version"] + 1 if self._browse is not None else 1
            here = index if index is not None else (self._browse or {}).get("index", 0.0)
            self._browse = {"index": float(here), "game_row": row, "version": version, "at": time.monotonic()}
        if index is None:
            return {"id": f"#{key}", "song": key, "artist": "", "place": round(row), "of": count}
        self.warm_around(listed[index]["id"])
        return {**listed[index], "place": round(row), "of": count}

    def _game_move(self, said: GameSongs, to: Callable[[float, list[str]], float]) -> dict[str, Any] | None:
        """A remote's move through the game's song list: from the row it's on (or the remote's, moving it still), to
        another; the game's highlight moved there, and the library's place."""
        keys = self._game_song_keys(said.count)
        if not keys:
            return None
        recent = time.monotonic() - self._game_sent[0] < GAME_SONGS_OWN
        with self._lock:
            row = (self._browse or {}).get("game_row") if recent else None
        start = float(row) if row is not None else float(said.row)
        row = min(max(to(start, keys), 0.0), len(keys) - 1.0)
        key = keys[round(row)]
        if key != self._game_sent[1] or not recent:
            self._riff_loop.focus(key)
        self._game_sent = (time.monotonic(), key)
        return self._browse_game(row, len(keys), key)

    def browse_move(self, songs: float) -> dict[str, Any] | None:
        """Moves a remote's place in the library by some songs (from the active song at first); the song there (its
        place in it too, and how many songs it has: place, of). Following, on the game's song list: through its."""
        said = self.game_songs()
        if said is not None:
            return self._game_move(said, lambda start, _keys: start + songs)
        listed = self.library()["songs"]
        if not listed:
            return None
        with self._lock:
            if self._browse is None:
                active = self.play_options.get("song")
                start = next((i for i, song in enumerate(listed) if song["id"] == active), 0)
                self._browse = {"index": float(start), "version": 0}
            index = min(max(self._browse["index"] + songs, 0.0), len(listed) - 1.0)
            self._browse = {"index": index, "version": self._browse["version"] + 1, "at": time.monotonic()}
        self.warm_around(listed[round(index)]["id"])
        return {**listed[round(index)], "place": round(index), "of": len(listed)}

    def browse_at(self, song_id: str | None) -> dict[str, Any] | None:
        """Moves a remote's place in the library to a song (the active one, if not given or not listed); the song
        there, as browse_move has it."""
        listed = self.library()["songs"]
        if not listed:
            return None
        ids = [song["id"] for song in listed]
        active = self.play_options.get("song")
        index = ids.index(song_id) if song_id in ids else ids.index(active) if active in ids else 0
        with self._lock:
            version = self._browse["version"] + 1 if self._browse is not None else 1
            self._browse = {"index": float(index), "version": version, "at": time.monotonic()}
        self.warm_around(listed[index]["id"])
        return {**listed[index], "place": index, "of": len(listed)}

    def browse_letter(self, steps: int) -> dict[str, Any] | None:
        """Moves a remote's place in the library a letter at a time (its artists': the library is in their order):
        on, to the first song of the next (past the last: the last song); back, to the first of the one it's on,
        then of the one before. The song there, as browse_move has it. Following, on the game's song list: through
        its (the library's artists for its keys)."""
        said = self.game_songs()
        if said is not None:
            artists = {song["id"].split("#", 1)[-1]: song["artist"] for song in self.library()["songs"]}

            def by_letter(start: float, keys: list[str]) -> float:
                letters = [_initial(artists.get(key, key)) for key in keys]
                firsts = [i for i in range(len(keys)) if i == 0 or letters[i] != letters[i - 1]]
                index = round(start)
                for _step in range(abs(steps)):
                    index = (next((i for i in firsts if i > index), len(keys) - 1) if steps > 0 else
                             max((i for i in firsts if i < index), default=0))
                return float(index)
            return self._game_move(said, by_letter)
        listed = self.library()["songs"]
        if not listed:
            return None
        initials = [_initial(song["artist"]) for song in listed]
        starts = [i for i in range(len(listed)) if i == 0 or initials[i] != initials[i - 1]]
        with self._lock:
            if self._browse is None:
                active = self.play_options.get("song")
                index = next((i for i, song in enumerate(listed) if song["id"] == active), 0)
            else:
                index = min(round(self._browse["index"]), len(listed) - 1)
            for _step in range(abs(steps)):
                if steps > 0:
                    index = next((i for i in starts if i > index), len(listed) - 1)
                else:
                    index = max((i for i in starts if i < index), default=0)
            version = self._browse["version"] + 1 if self._browse is not None else 1
            self._browse = {"index": float(index), "version": version, "at": time.monotonic()}
        self.warm_around(listed[index]["id"])
        return {**listed[index], "place": index, "of": len(listed)}

    def browse_select(self) -> dict[str, Any] | None:
        """Switches to the song a remote stopped at; it."""
        with self._lock:
            browse = self._browse
        listed = self.library()["songs"]
        if browse is None or not listed:
            return None
        song = listed[min(round(browse["index"]), len(listed) - 1)]
        self.switch({"song": song["id"]})
        with self._lock:
            if self._browse is not None:
                self._browse = {**self._browse, "version": self._browse["version"] + 1, "chosen": song["id"]}
        return song

    def _browse_state(self) -> dict[str, Any] | None:
        browse = self._browse
        listed = (self._library or {}).get("songs", [])
        if browse is None or "at" not in browse or not listed:
            return None
        return {"song": listed[min(round(browse["index"]), len(listed) - 1)]["id"], "version": browse["version"],
                "chosen": browse.get("chosen")}

    def _remote_state(self) -> dict[str, Any]:
        dialling = self._dialling
        return {"speed": dialling.get("speed"), "position": dialling.get("position"),
                "busy": time.monotonic() - dialling["at"] < REMOTE_BUSY}

    def play_options_info(self) -> dict[str, Any]:
        return {**self.play_options, "saved": self._play_saved, "version": self.play_version}

    def set_play_options(self, body: dict[str, Any]) -> dict[str, Any]:
        """Changes to the speed, real pitch, lights only (silent: following too), or the song picked, kept; a song
        playing takes them at once."""
        with self._lock:
            options = _changed_options(self.play_options, body)
            if not options["silent"] and self.play_options["silent"]:
                self._check_sound_channel()
            changed = {k for k in options if options[k] != self.play_options[k]}
            self.play_options, self._play_saved = options, True
            if changed - {"song"}:
                self.play_version += 1
            self._save()
            session = self._session
            if isinstance(session, Playback) and session.running and self._playing:
                if "speed" in changed:
                    session.set_speed(options["speed"])
                if changed & {"real_pitch", "silent"}:
                    self._play_on(self._playing["id"])
            elif isinstance(session, Following) and session.running and "silent" in changed:  # (one switch for both)
                self._follow_on(session)
        return self.play_options_info()

    def _play_on(self, sid: str) -> None:
        """The song, if it's playing, goes on with its parts as chosen now, from where it is."""
        session = self._session
        if not isinstance(session, Playback) or not session.running or not self._playing or self._playing["id"] != sid:
            return
        planned, length = self._plan(sid)
        session.replace(player_for(planned, session.keyboard, *self._light_timing()), length)
        self._playing, self._planned_now = self._describe(sid, planned), planned

    def _load(self, sid: str) -> list[tuple[ArrangementRef, Arrangement]]:
        with self._lock:
            if sid in self._loaded:
                self._loaded.move_to_end(sid)
                return self._loaded[sid]
        refs = sorted((r for r in self.index.arrangements() if song_id(r) == sid and r.part not in NO_NOTES),
                      key=part_order)
        if not refs:
            raise GuiError("No such song (is its drive connected? try Reindex)")
        loaded = [(ref, ref.load()) for ref in refs]
        with self._lock:
            self._loaded[sid] = loaded
            while len(self._loaded) > 8:
                self._loaded.popitem(last=False)
        return loaded

    def song_key(self, sid: str) -> dict[str, Any] | None:
        """A song's key from all its arrangements' notes, for the page: its name and scale, notes outside the
        scale it uses a lot, a key nearly as likely, and the key a guitarist's fingers play in its tuning."""
        if sid in self._keys:
            return self._keys[sid]
        loaded = self._load(sid)
        key = find_key([a for _r, a in loaded], [r.bass for r, _a in loaded])
        info = None
        if key is not None:
            guitar = next((a for r, a in loaded if not r.bass), None)
            played = shapes(key, guitar) if guitar else None
            info = {"name": key.name, "tonic": key.tonic, "mode": key.mode, "scale": list(key.scale),
                    "notes": list(key.notes), "other": list(key.other), "other_notes": [key.spell(pc) for pc in key.other],
                    "alternative": key.alternative, "played_as": f"{played[0].name} in {played[1]}" if played else None}
        self._keys[sid] = info
        return info

    def warm_around(self, sid: str) -> None:
        """Gets the album art of the songs listed around one ready in the background (the one browsed with a
        remote, or pointed at), so it's there at once going on through them: each is read from its archive
        and converted, which takes a while (more on a slow drive)."""
        listed = (self._library or {}).get("songs", [])
        index = next((i for i, song in enumerate(listed) if song["id"] == sid), None)
        if index is None:
            return
        with self._warm_ready:
            self._warm = [listed[index + d]["id"] for d in WARM_AROUND
                          if 0 <= index + d < len(listed) and listed[index + d]["id"] not in self._art]
            if self._warm_thread is None:
                self._warm_thread = threading.Thread(target=self._warm_run, name="rs-keylights art", daemon=True)
                self._warm_thread.start()
            self._warm_ready.notify()

    def _warm_run(self) -> None:
        while True:
            with self._warm_ready:
                while not self._warm:
                    self._warm_ready.wait()
                sid = self._warm.pop(0)
            try:
                self.album_art(sid)
            except Exception:  # gone from the library: the next one
                pass

    def album_art(self, sid: str) -> bytes | None:
        """A song's album art as PNG (its archive's texture, converted by macOS's sips), or None."""
        with self._lock:
            if sid in self._art:
                self._art.move_to_end(sid)
                return self._art[sid]
        refs = [r for r in self.index.arrangements() if song_id(r) == sid]
        if not refs:
            raise GuiError("No such song")
        art = None
        try:
            with Archive(Path(refs[0].archive)) as archive:
                key = refs[0].song_key.lower()
                names = sorted((n for n in archive.names if n.startswith("gfxassets/album_art/") and n.endswith(".dds")),
                               key=lambda n: (key not in n.lower(), not n.endswith("_256.dds")))  # its own, biggest
                if names:
                    art = _png(archive.read(names[0]))
        except (OSError, ArchiveError):
            art = None
        with self._lock:
            self._art[sid] = art
            while len(self._art) > 128:
                self._art.popitem(last=False)
        return art

    def tones(self) -> list[dict[str, Any]]:
        return [{"number": t.number, "name": t.name, "group": t.group} for t in TONES]

    # Playing

    def _plan(self, sid: str) -> tuple[list[PlannedPart], float]:
        """A song's parts as chosen, planned, and the song's length."""
        loaded = self._load(sid)
        by_part = {ref.part: (ref, a) for ref, a in loaded}
        silent = bool(self.play_options["silent"])
        main: dict[str | None, Tone | None] = {}
        low: dict[str | None, Tone | None] = {}
        mute: dict[str | None, Tone | None] = {}
        split: dict[str | None, int | None] = {}
        volume: dict[str, int] = {}
        parts = []
        for slot, chosen in enumerate(self._song_parts(sid, loaded)):
            if not chosen["play"]:
                continue
            name = chosen["part"]
            ref, arrangement = by_part[name]
            parts.append(Part(ref, arrangement, chosen["lights"]))
            own, defaults = chosen["tones"], self.follow_settings[kind(ref)]

            def pick(field: str) -> Any:  # (SAME_TONE: over a default)
                return own.get(field, defaults.get(field))

            main[name], low[name], mute[name] = _tone(pick("tone")), _tone(pick("low_tone")), _tone(pick("mute_tone"))
            split[name], volume[name] = _split(pick("split")), self._volume(slot)
        if not silent and parts:
            self._check_sound_channel()
        choice = None if silent else SoundChoice(main, low, mute, split, bool(self.play_options["real_pitch"]),
                                                 ring=bool(self.settings["ring"]), tone_map=self._tones_for_kinds(),
                                                 techniques=bool(self.settings["techniques"]),
                                                 overlap=self.settings["overlap_ms"] / 1000)

        def voices_for(ref: ArrangementRef, channels: tuple[int, int, int]) -> Voices:
            assert choice is not None
            return self._routed(replace(choice.voices(ref, channels), volume=volume[ref.part]), ref)

        planned = plan(parts, voices_for if choice else None)
        length = max([a.length for _r, a in loaded] + [l.off for pp in planned for l in pp.lights[-1:]])
        return planned, length

    def _describe(self, sid: str, planned: list[PlannedPart]) -> dict[str, Any]:
        loaded = self._load(sid)
        first, order = loaded[0][0], [ref.part for ref, _a in loaded]
        return {
            "id": sid,
            "title": first.title,
            "parts": [{"part": pp.part.ref.part, "name": pp.part.ref.name, "slot": order.index(pp.part.ref.part),
                       "sound": pp.voices.describe() if pp.voices else "silent",
                       "lights": pp.part.lights} for pp in planned],
        }

    def play(self, body: dict[str, Any]) -> dict[str, Any]:
        """Plays a song with its parts as chosen, from a start (or its own) at a speed (or the one chosen)."""
        sid = str(body.get("song", ""))
        planned, length = self._plan(sid)  # (every part muted, it plays on: they can be played again as it goes)
        start = _number(body.get("start", self.song_settings.get(sid, {}).get("start", 0)), "start", 0, 24 * 3600)
        speed = _number(body.get("speed", self.play_options["speed"]), "speed", 0.05, 4)
        with self._lock:
            self._end_idle_show()
            with self._keys_lock:  # a remote's keys wait, then play on its keyboard
                self._stop()
                keyboard = self._open_keyboard()
                try:
                    playback = Playback(player_for(planned, keyboard, *self._light_timing()), keyboard, length, start,
                                        speed)
                except Exception:
                    keyboard.close()
                    raise
                playback.set_loop(self._loop_for(sid))
                playback.start()
                self._session = playback
            self._playing, self._planned_now = self._describe(sid, planned), planned
            self.play_options["song"] = sid
        return self.state()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if isinstance(self._session, Playback):
                self._session.pause()
        return self.state()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if isinstance(self._session, Playback):
                if self._session.ended:
                    self._session.seek(0)
                self._session.resume()
        return self.state()

    def seek(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if isinstance(self._session, Playback):
                self._session.seek(_number(body.get("position"), "position", 0, 24 * 3600))
        return self.state()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop()
        return self.state()

    # Following the game

    def follow_info(self) -> dict[str, Any]:
        """Following's settings, and the song being followed (once it ends, until the next, the one last
        followed: ended): its arrangements, each with its own tones."""
        with self._lock:
            settings, version, session = self.follow_settings, self.follow_version, self._session
            shown, ended = self._shown_followed(session)
        song = None
        if shown is not None:
            song = {"id": song_id(shown), "title": shown.title, "played": shown.persistent_id, "ended": ended,
                    "key": self.song_key(song_id(shown)), "arrangements": [
                {"id": r.persistent_id, "name": r.name or r.part, "kind": kind(r),
                 "playing": not ended and r.persistent_id == shown.persistent_id, "play": play, "lights": lights,
                 "tones": settings["arrangements"].get(r.persistent_id, {})}
                for r, play, lights in parts_chosen(settings, shown, self._song_refs(shown))]}
        return {"version": version, "saved": self._follow_saved,
                **{k: v for k, v in settings.items() if k not in ("arrangements", "parts")}, "song": song}

    def _shown_followed(self, session: Any) -> tuple[ArrangementRef | None, bool]:
        """(Holding _lock.) While following: the arrangement played, or once its song ends (until the next) the one
        last played; and whether its song ended."""
        if not (isinstance(session, Following) and session.running):
            return None, False
        current = session.follower.current
        if current is not None:
            self._followed = current
        return self._followed, current is None

    def set_follow(self, body: dict[str, Any], later: float = 0.0) -> dict[str, Any]:
        """Changes to following's settings (changed_follow), kept, and applied while following: the arrangement
        playing goes on with other tones from where it is, at once or once changes stop coming for later
        seconds."""
        with self._lock:
            settings = changed_follow(self.follow_settings, body)
            for played_id, chosen in (body.get("parts") or {}).items():
                played = self.index.find(played_id)
                if played is None:
                    raise GuiError(f"No arrangement {played_id} in the library")
                refs = self._song_refs(played)
                for arrangement_id, fields in chosen.items():  # one turned on takes the keys still free
                    if isinstance(fields, dict) and fields.get("play") and "lights" not in fields:
                        others = sum(lights for r, play, lights in parts_chosen(settings, played, refs)
                                     if play and r.persistent_id != arrangement_id.upper())
                        mine = next((l for r, _p, l in parts_chosen(settings, played, refs)
                                     if r.persistent_id == arrangement_id.upper()), 0)
                        if mine > 4 - others:
                            settings = changed_follow(settings, {"parts": {played_id: {arrangement_id: {
                                "lights": max(0, 4 - others)}}}})
                on = [lights for _r, play, lights in parts_chosen(settings, played, refs) if play]
                if len(on) > len(CHANNEL_SETS):
                    raise GuiError(f"{len(CHANNEL_SETS)} parts at most")
                if sum(on) > 4:
                    raise GuiError("The keyboard lights 4 keys at most: give the parts fewer lit keys")
            self.follow_settings, self._follow_saved = settings, True
            self.follow_version += 1
            self._save()
            session = self._session
            if isinstance(session, Following) and session.running:
                session.set_offset(settings["offset_ms"] / 1000)
                self._replay("follow", lambda: self._follow_on(session), later)
            if any(kind_ in body for kind_ in KINDS) and self._playing:  # a song playing takes new defaults
                self._play_on(self._playing["id"])
        return self.follow_info()

    def _follow_on(self, session: Following) -> None:
        """The arrangement played goes on with the parts and tones as chosen now."""
        if session is self._session and session.running:
            session.set_parts(self._follow_voices(), self._follow_parts())

    def _replay(self, key: str, action: Callable[[], None], later: float) -> None:
        """(Holding _lock.) Plays the parts again with changes: now, or once they stop coming for later seconds
        (each time stops the notes sounding)."""
        self._replays.pop(key, None)
        if later <= 0:
            action()
            return
        self._replays[key] = action
        if self._replay_timer is not None:
            self._replay_timer.cancel()
        timer = threading.Timer(later, lambda: self._replay_due(timer))
        timer.daemon = True
        self._replay_timer = timer
        timer.start()

    def _replay_due(self, timer: threading.Timer) -> None:
        with self._lock:
            if timer is not self._replay_timer:  # more changes came
                return
            actions, self._replays, self._replay_timer = list(self._replays.values()), {}, None
            for action in actions:
                try:
                    action()
                except Exception:  # (the song stopped meanwhile)
                    pass

    def follow(self, body: dict[str, Any]) -> dict[str, Any]:
        """Starts following the game with the settings kept (after any changes to them in the request)."""
        if body:
            self.set_follow(body)
        with self._lock:
            if not self.play_options["silent"]:
                self._check_sound_channel()
            self._end_idle_show()
            with self._keys_lock:  # a remote's keys wait, then play on its keyboard
                self._stop()
                keyboard = self._open_keyboard()
                following = Following(OverlayClient(self.overlay_port), self.index, keyboard, self._follow_voices(),
                                      self.follow_settings["offset_ms"] / 1000, *self._light_timing(),
                                      parts_for=self._follow_parts(), trace=self._trace)
                following.start()
                self._session, self._followed = following, None
        return self.state()

    def show_key(self) -> None:
        """The followed song's scale on the keys, where the song leaves them free."""
        with self._lock:
            session = self._session
            current = session.follower.current if isinstance(session, Following) and session.running else None
        key = self.song_key(song_id(current)) if current else None
        if isinstance(session, Following) and key:
            session.show(scale_show(_key_of(key)))

    def show_scale(self, body: dict[str, Any]) -> dict[str, Any]:
        """A song's scale on the keys, over and over: the song followed's or played's, else the one asked for (or
        picked). It gives way to the song's lights, and stops for a key pressed on the keyboard."""
        with self._lock:
            session = self._session
            running = session is not None and session.running
            if isinstance(session, Following) and running:
                current = session.follower.current
                sid = song_id(current) if current else None
            elif isinstance(session, Playback) and running and self._playing:
                sid = self._playing["id"]
            else:
                session, sid = None, str(body.get("song") or self.play_options.get("song") or "")
        key = self.song_key(sid) if sid else None
        if key is None:
            raise GuiError("No song to show the key of" if not sid else "This song has no notes to find its key from")
        frames = scale_show(_key_of(key))
        with self._lock:
            if session is not None:
                session.show(frames)
                return self.state()
            self._end_idle_show()
            with self._keys_lock:
                if self._idle_keyboard is None:
                    self._idle_keyboard = self._open_keyboard()
                keyboard = self._idle_keyboard
            self._show_quit = quit_ = threading.Event()
            self._idle_show = show = KeyShow(frames)
            self._show_thread = threading.Thread(target=_play_show, args=(keyboard, show, quit_, self._show_done),
                                                 daemon=True)
            self._show_thread.start()
        return self.state()

    def stop_scale(self) -> dict[str, Any]:
        with self._lock:
            if isinstance(self._session, (Following, Playback)):
                self._session.stop_show()
            self._end_idle_show()
        return self.state()

    def showing(self) -> bool:
        with self._lock:
            if isinstance(self._session, (Following, Playback)) and self._session.running:
                return self._session.showing
            return self._show_thread is not None and self._show_thread.is_alive()

    def _show_done(self, keyboard: Keyboard) -> None:
        """A show over while nothing plays: its keyboard closes, unless a remote's keys are down on it."""
        with self._keys_lock:
            if keyboard.holding:
                return
            if self._idle_keyboard is keyboard:
                self._idle_keyboard = None
        keyboard.close()

    def _end_idle_show(self) -> None:
        self._show_quit.set()
        if self._show_thread is not None:
            self._show_thread.join(timeout=2)
        self._show_thread = None

    def remote_info(self) -> dict[str, Any]:
        """What a remote (the Launchkey) shows: the song followed, else the one playing, else the one picked on
        the page, with its arrangements as chosen; whether they sound; and the default tones (following, whether
        Rocksmith is reached too)."""
        with self._lock:
            session = self._session
            following = isinstance(session, Following) and session.running
            playing = self._playing["id"] if isinstance(session, Playback) and session.running and self._playing else None
        defaults = {k: self.follow_settings[k] for k in KINDS}
        if following:
            info = self.follow_info()
            connected = session.follower.connected if isinstance(session, Following) else None
            followed = info["song"]
            if followed is not None:  # (each arrangement's chords and tone changes too)
                chords = self._chords(followed["id"], lambda ref: ref.persistent_id)
                changes = self._tone_changes(followed["id"], lambda ref: ref.persistent_id)
                notes = self._notes(followed["id"], lambda ref: ref.persistent_id)
                followed = {**followed, "lyrics": self._lyrics(followed["id"]),
                            "arrangements": [{**a, "chords": chords.get(a["id"], ()),
                                              "tone_changes": changes.get(a["id"], ()), "notes": notes.get(a["id"], ()),
                                              "output": self._output_of(a["kind"])}
                                             for a in followed["arrangements"]]}
            self._riff_sync(followed["id"] if followed else None)
            length = None
            if followed is not None:
                try:
                    length = max(a.length for _r, a in self._load(followed["id"]))
                except (GuiError, OSError, ValueError):
                    length = None
            state = self.game_state()  # (its song playing or paused: None, neither, or not known)
            return {"mode": "follow", "sound": not self.play_options["silent"], "real_pitch": info["real_pitch"], **defaults,
                    "song": followed, "position": None, "length": length, "paused": state == "paused",
                    "state": state, "connected": connected,
                    "loop": self.loop_info(followed["id"] if followed else None),
                    "speed": self._riff_settings["speed"] / 100, "riff": dict(self._riff_settings),
                    "volumes": list(self.settings["volumes"]),
                    "keys": self.keys_info(), "listen": bool(self.settings["listen"]), "pedal": self.pedal_info()}
        sid, song = playing or self.play_options.get("song"), None
        position = length = None
        if sid:
            try:
                details = self.song(sid)
            except (GuiError, OSError, ValueError):  # gone from the library, or its drive unplugged
                details = None
            if details is not None:
                chords = self._chords(sid, lambda ref: ref.part)
                changes = self._tone_changes(sid, lambda ref: ref.part)
                notes = self._notes(sid, lambda ref: ref.part)
                song = {"id": sid, "title": details["title"], "key": details["key"], "played": None,
                        "sections": details["sections"], "lyrics": self._lyrics(sid),
                        "arrangements": [{"id": p["part"], "name": p["name"], "kind": p["kind"], "playing": False,
                                          "play": p["play"], "lights": p["lights"], "tones": p["tones"],
                                          "chords": chords.get(p["part"], ()), "tone_changes": changes.get(p["part"], ()),
                                          "notes": notes.get(p["part"], ()), "output": self._output_of(p["kind"])}
                                         for p in details["parts"]]}
                position, length = details["start"], details["length"]
        paused = False
        session = self._session
        if playing and isinstance(session, Playback):
            position, length, paused = session.position, session.length, session.paused
        return {"mode": "play" if playing else "idle", "sound": not self.play_options["silent"],
                "real_pitch": self.play_options["real_pitch"], **defaults, "song": song, "position": position,
                "length": length, "paused": paused, "speed": self.play_options["speed"],
                "volumes": list(self.settings["volumes"]), "loop": self.loop_info(sid),
                "keys": self.keys_info(), "listen": bool(self.settings["listen"]), "pedal": self.pedal_info()}

    def _chords(self, sid: str, key: Callable[[ArrangementRef], str]) -> dict[str, tuple[Chord, ...]]:
        """A song's arrangements' chords, by their key (the part, or its persistent ID); none if it's gone."""
        try:
            return {key(ref): a.chords for ref, a in self._load(sid)}
        except (GuiError, OSError, ValueError):
            return {}

    def _notes(self, sid: str, key: Callable[[ArrangementRef], str]) -> dict[str, tuple[Note, ...]]:
        """A song's arrangements' notes (a remote shows the one played, picked with no chords), by their key."""
        try:
            return {key(ref): a.notes for ref, a in self._load(sid)}
        except (GuiError, OSError, ValueError):
            return {}

    def _tone_changes(self, sid: str, key: Callable[[ArrangementRef], str]) -> dict[str, tuple[tuple[float, str], ...]]:
        """A song's arrangements' tone changes the keyboard follows, by their key (the part, or its persistent ID):
        from the start (-inf) the kind of tone it starts with ("its own tone" if that isn't known), then when it
        switches, and what to (a kind of tone, or "its own tone"); none without changes, or while they're not
        followed."""
        tones = self._tones_for_kinds()
        if not tones:
            return {}
        try:
            loaded = self._load(sid)
        except (GuiError, OSError, ValueError):
            return {}
        out = {}
        for ref, a in loaded:
            changes = [(at, kind or "its own tone") for at, kind in kind_changes(a, ref.bass)
                       if kind is None or kind in tones]
            if changes and a.base_tone is not None:
                out[key(ref)] = ((-math.inf, tone_kind(a.base_tone, ref.bass) or "its own tone"), *changes)
        return out

    def _lyrics(self, sid: str) -> tuple[Line, ...]:
        """A song's lyrics in lines, from its vocals part (none without one, or if it's gone)."""
        with self._lock:
            if sid in self._lyric_lines:
                self._lyric_lines.move_to_end(sid)
                return self._lyric_lines[sid]
        vocals = next((r for r in self.index.arrangements() if song_id(r) == sid and r.part == "vocals"), None)
        try:
            lines = lyric_lines(vocals.load().lyrics) if vocals is not None else ()
        except (ArchiveError, OSError, ValueError):
            lines = ()
        with self._lock:
            self._lyric_lines[sid] = lines
            while len(self._lyric_lines) > 8:
                self._lyric_lines.popitem(last=False)
        return lines

    def song_position(self) -> float | None:
        """Where the song a remote shows is now: the one playing (or paused), or the one followed (None: nothing
        plays, or the game's paused)."""
        with self._lock:
            session = self._session
        if isinstance(session, Playback) and session.running:
            return session.position
        if isinstance(session, Following) and session.running:
            exact = self.game_time()
            return exact if exact is not None else session.follower.position(time.monotonic())
        return None

    def game_time(self) -> float | None:
        """Following, where the game's song is, exactly, as its riffloop mod sees it (asked at most every
        GAME_TIME_EVERY); None without the mod (or before it's seen the song play)."""
        now = time.monotonic()
        asked, at = self._game_time
        if now - asked >= GAME_TIME_EVERY:
            said = self._riff_loop.status()
            at = said.time if said is not None else None
            self._game_time, self._game_said = (now, at), said
            sent_for = self._riff_song
            if said is not None and said.loop is None and sent_for is not None:  # (the game started again since:
                # the mod's lost it)
                on = self._loop_for(sent_for)
                if on:
                    self._riff_said = self._riff_loop.loop(*on)
        return at

    def game_state(self) -> str | None:
        """Following, whether the game's song is "playing" or "paused", as its riffloop mod sees the game's screen
        (asked at most every GAME_TIME_EVERY); None on neither (a menu), or without the mod."""
        self.game_time()
        said = self._game_said
        return "playing" if said is not None and said.playing else "paused" if said is not None and said.paused else None

    def remote_dialling(self, dialling: dict[str, Any]) -> None:
        """A remote was used: what it's setting and will apply when it stops (speed, position), for the page."""
        self._dialling = {**dialling, "at": time.monotonic()}

    def remote_seek(self, position: float) -> None:
        """A remote's move in the song: where it plays, or, while nothing plays, where the song picked starts."""
        with self._lock:
            session = self._session
            if isinstance(session, Following) and session.running:
                return
            if isinstance(session, Playback) and session.running:
                session.seek(min(max(position, 0.0), session.length))
                return
        sid = self.play_options.get("song")
        if sid:
            self.set_song({"song": sid, "start": min(max(position, 0.0), self.song(sid)["length"])})

    def set_loop(self, body: dict[str, Any]) -> dict[str, Any]:
        """The A-B loop of the song playing (else the active one), its ends on the song's beats: "a" and "b" each a
        time (seconds), "here" (where the song is) or None (none); "move_a" and "move_b" beats to move one by (from
        where the song is, without it), "move_a_bars" and "move_b_bars" bars (to the bar lines); "section" loops the section the song is in (or, looping it already, stops);
        and "on". The end without the start: the start is its section's. The start moved to or past the end, the
        end goes (moved by beats, it stops a beat before). Playing, the song goes back to a little before the
        start each time it reaches the end. Its ends, and whether it's on."""
        sid = self._loop_song()
        if not sid:
            raise GuiError("Pick a song on the page first")
        loaded = self._load(sid)
        length = max(a.length for _r, a in loaded)
        beats = sorted(next((a.beats for _r, a in loaded if a.beats), ()))
        bars = sorted(next((a.bars for _r, a in loaded if a.bars), ()))
        sections = next((a.sections for _r, a in loaded if a.sections), ())
        here = self._here(sid)

        def nearest(at: float) -> int:
            i = bisect_left(beats, at)
            return min((j for j in (i - 1, i) if 0 <= j < len(beats)), key=lambda j: abs(beats[j] - at))

        def at(value: Any) -> float:
            if value == "here":
                return beats[nearest(here)] if beats else here
            return float(_number(value, "loop end", 0, length))

        with self._lock:
            loop = dict(self._loop) if self._loop["song"] == sid else {"song": sid, "a": None, "b": None, "on": False}
            if body.get("section"):
                found = next((x for x in sections if x.start <= here < x.end), None)
                if found is None:
                    raise GuiError("No section here")
                wanted = {"a": round(found.start, 3), "b": round(found.end, 3)}
                looping = loop["on"] and (loop["a"], loop["b"]) == (wanted["a"], wanted["b"])
                loop.update(wanted, on=not looping)
            for end, other in (("a", "b"), ("b", "a")):
                if end in body:
                    loop[end] = None if body[end] is None else round(at(body[end]), 3)
                    if loop[end] is not None and loop[other] is not None and (loop["a"] >= loop["b"]):
                        loop[other] = None  # (the start past the end: the end goes; the end before the start:
                        # the start, its section's, below)
                if f"move_{end}" in body and beats:
                    steps = int(_number(body[f"move_{end}"], "beats", -1000, 1000))
                    moved = round(beats[min(max(nearest(loop[end] if loop[end] is not None else here) + steps, 0),
                                            len(beats) - 1)], 3)
                    if loop[other] is None or (moved < loop["b"] if end == "a" else moved > loop["a"]):
                        loop[end] = moved
                if f"move_{end}_bars" in body and bars:  # (to the next bar line on, or back, then a bar a step)
                    steps = int(_number(body[f"move_{end}_bars"], "bars", -1000, 1000))
                    from_ = loop[end] if loop[end] is not None else here
                    i = (bisect_right(bars, from_ + 0.001) + steps - 1 if steps > 0 else
                         bisect_left(bars, from_ - 0.001) + steps)
                    moved = round(bars[min(max(i, 0), len(bars) - 1)], 3)
                    if steps and (loop[other] is None or (moved < loop["b"] if end == "a" else moved > loop["a"])):
                        loop[end] = moved
            if loop["b"] is not None and loop["a"] is None:
                starts = [x.start for x in sections if x.start < loop["b"]]
                loop["a"] = round(max(starts), 3) if starts else 0.0
                if loop["a"] >= loop["b"]:
                    loop["a"] = loop["b"] = None
            if "on" in body:
                loop["on"] = bool(body["on"])
            loop["on"] = bool(loop["on"]) and loop["a"] is not None and loop["b"] is not None
            self._loop = loop
            session = self._session
            if isinstance(session, Playback) and session.running and self._playing and self._playing["id"] == sid:
                session.set_loop(self._loop_for(sid))
            following = isinstance(session, Following) and session.running
        if following:  # (the game's Riff Repeater loops it)
            on = self._loop_for(sid)
            self._riff_said = self._riff_loop.loop(*on) if on else self._riff_loop.off()
            self._riff_song = sid if on else None
        return self.loop_info(sid)

    def loop_info(self, sid: str | None) -> dict[str, Any]:
        """The loop of a song (None: the song followed, else playing, else the active one): its start, end, and
        whether it's on; following, what the game's riffloop mod said last too (game: answered, it has the hook
        point it needs, Riff Repeater was on)."""
        with self._lock:
            loop = self._loop
            session = self._session
            following = isinstance(session, Following) and session.running
            said = self._riff_said
            game = ({"game": {"answered": said is not None, "hook": bool(said and said.hook),
                              "riff": bool(said and said.riff)}} if following else {})
            if loop["song"] != (sid or self._loop_song()):
                return {"a": None, "b": None, "on": False, **game}
            return {"a": loop["a"], "b": loop["b"], "on": loop["on"], **game}

    def set_game_riff(self, body: dict[str, Any]) -> dict[str, Any]:
        """Following, Riff Repeater's settings, through the game's riffloop mod: "speed" (percent: its
        Accelerator's start, and where it is), "accelerator" (on: a little faster each time through) and "passes"
        (times through before each speed-up). The settings as set here, and whether the game answered."""
        with self._lock:
            session = self._session
        if not (isinstance(session, Following) and session.running):
            raise GuiError("Follow Rocksmith first: these are its Riff Repeater's")
        said: GameLoop | None = None
        settings = dict(self._riff_settings)
        if "speed" in body:
            settings["speed"] = int(_number(body["speed"], "speed", 10, 200))
            said = self._riff_loop.speed(settings["speed"])
        if "accelerator" in body:
            settings["accelerator"] = bool(body["accelerator"])
            said = self._riff_loop.accelerator(settings["accelerator"])
        if "passes" in body:
            settings["passes"] = int(_number(body["passes"], "passes", 1, 20))
            said = self._riff_loop.passes(settings["passes"])
        self._riff_settings = settings
        return {**settings, "answered": said is not None}

    def _riff_sync(self, followed: str | None) -> None:
        """Rocksmith's Riff Repeater looping a range for a song not followed now (or not following): its own again."""
        if self._riff_song is not None and followed != self._riff_song:
            self._riff_song = None
            self._riff_said = self._riff_loop.off()
        elif self._riff_song is not None:
            self.game_time()  # (and the mod given it again, if the game's started again since)

    def _loop_for(self, sid: str) -> tuple[float, float] | None:
        loop = self.loop_info(sid)
        return (loop["a"], loop["b"]) if loop["on"] else None

    def _loop_song(self) -> str | None:
        with self._lock:
            session = self._session
            if isinstance(session, Following) and session.running:
                shown, _ended = self._shown_followed(session)
                return song_id(shown) if shown is not None else None
            if isinstance(session, Playback) and session.running and self._playing:
                return str(self._playing["id"])
            return self.play_options.get("song")

    def _here(self, sid: str) -> float:
        """Where a song is: following, where the game is; playing, where it plays; else where it starts."""
        with self._lock:
            session = self._session
            following = isinstance(session, Following) and session.running
        if following:  # (the game's exact time, else RS Companion's)
            at = self.game_time()
            if at is None and isinstance(session, Following):
                at = session.follower.position(time.monotonic())
            if at is not None:
                return at
        with self._lock:
            if isinstance(session, Playback) and session.running and self._playing and self._playing["id"] == sid:
                return session.position
        return float(self.song_settings.get(sid, {}).get("start", 0))

    def remote_play(self) -> str:
        """A remote's Play: pauses or goes on with the song playing (following, the game's, through its riffloop
        mod), or plays the one picked; what it does now."""
        with self._lock:
            session = self._session
            following = isinstance(session, Following) and session.running
        if following:
            said = self._riff_loop.status()
            if said is None:
                raise GuiError("Rocksmith didn't answer: needs its riffloop mod")
            if said.screen is None:
                raise GuiError("Rocksmith's riffloop mod is too old: update it")
            if not (said.playing or said.paused):
                raise GuiError(f"Rocksmith isn't playing a song ({said.screen})")
            (self._riff_loop.resume if said.paused else self._riff_loop.pause)()
            self._game_time = (-math.inf, None)  # (its screen asked again)
            return "playing" if said.paused else "paused"
        with self._lock:
            session = self._session
            if isinstance(session, Playback) and session.running:
                if session.paused:
                    if session.ended:
                        session.seek(0)
                    session.resume()
                    return "playing"
                session.pause()
                return "paused"
        sid = self.play_options.get("song")
        if not sid:
            raise GuiError("Pick a song on the page first")
        self.play({"song": sid})
        return "playing"

    def game_key(self, name: str) -> int:
        """Following, a key typed in Rocksmith (its riffloop mod's: up, down, enter, escape...), as on the computer's
        keyboard: its menus from a remote. How many keys it has still to type (this one too)."""
        with self._lock:
            session = self._session
        if not (isinstance(session, Following) and session.running):
            raise GuiError("Follow Rocksmith first")
        said = self._riff_loop.key(name)
        if said is None:
            raise GuiError("Rocksmith didn't answer: needs its riffloop mod")
        if said.screen is None:
            raise GuiError("Rocksmith's riffloop mod is too old: update it")
        return said.keys

    def remote_stop(self) -> str:
        """A remote's Stop: the song playing stops (following goes on: it has its own pad)."""
        with self._lock:
            if isinstance(self._session, Playback) and self._session.running:
                self._stop()
                return "stopped"
        return "nothing playing"

    def toggle_follow(self) -> bool:
        """A remote's follow button: following the game, or stopping; whether it's following now."""
        with self._lock:
            following = isinstance(self._session, Following) and self._session.running
        if following:
            self.stop()
            return False
        self.follow({})
        return True

    def change_part(self, arrangement: str, fields: dict[str, Any], later: float = 0.0) -> None:
        """A remote's change to an arrangement of its song (play, lights, tone fields or volume), kept at once (the
        page shows it); the song playing takes it then too, or once changes stop coming for later seconds."""
        self.change_parts({arrangement: fields}, later)

    def change_parts(self, changes: dict[str, dict[str, Any]], later: float = 0.0) -> None:
        """A remote's changes to arrangements of its song together (change_part's, each by its ID)."""
        info = self.remote_info()
        song = info["song"]
        if song is None or not changes:
            return
        if info["mode"] == "follow":
            body: dict[str, Any] = {}
            for arrangement, fields in changes.items():
                along = {k: v for k, v in fields.items() if k in ("play", "lights")}
                tones = {k: v for k, v in fields.items() if k in (*TONE_FIELDS, "split")}
                if along:
                    body.setdefault("parts", {song["played"]: {}})[song["played"]][arrangement] = along
                if tones:
                    body.setdefault("arrangements", {})[arrangement] = tones
            self.set_follow(body, later)
        else:
            self.set_song({"song": song["id"], "parts": changes}, later)

    def toggle_sound(self) -> bool:
        """Sound on or off (off: just the lights), playing or following; whether it's on now."""
        return not self.set_play_options({"silent": not self.play_options["silent"]})["silent"]

    def start_watch(self) -> None:
        """The Launchkey as a remote, and each followed song's key as it starts, while this runs (and the guitar
        heard, if it's to be)."""
        with self._lock:
            if self._watch is None:
                self._watch = self._watch_factory(self)
                self._watch.start()
        self.listen_now()

    # The guitar, heard

    def listen_now(self) -> None:
        """Hears the guitar on its input, its notes lighting the keys, or stops (as the settings have it)."""
        wanted, device = bool(self.settings["listen"]), str(self.settings["listen_device"])
        listener = self._listener
        if listener is not None and (not wanted or listener.device != device):
            listener.stop()
            self._listener = listener = None
        if wanted and (listener is None or not listener.running):
            self._listener = Listener(self._hear, self._expected_now, device, self._lowest_now)
            self._listener.start()

    def _hear(self, pitches: tuple[int, ...]) -> None:
        """(The listener's thread.) The notes the guitar plays, on the keys: lit first, the song's in the room
        left."""
        keys = tuple(dict.fromkeys(KEY_RANGE.fit(p) for p in pitches))[:KEY_RANGE.max_lit]
        with self._keys_lock:
            try:
                keyboard = self._keys_keyboard(open_one=bool(keys))
                if keyboard is not None:
                    keyboard.hear(keys)
            except (KeyboardError, OSError, RuntimeError):  # no keyboard: the next notes try again
                pass

    def _played(self) -> tuple[tuple[Note, ...], bool] | None:
        """The notes of the arrangement the guitar plays, and whether it's a bass: following, the one played in the
        game; playing, the one lighting the most keys. None while nothing plays."""
        session = self._session
        if isinstance(session, Following) and session.running:
            current = session.follower.current
            for planned in session.follower.planned:
                if current is not None and planned.part.ref.persistent_id == current.persistent_id:
                    return planned.part.arrangement.notes, planned.part.ref.bass
            return None
        if isinstance(session, Playback) and session.running and self._planned_now:
            best = max(self._planned_now, key=lambda planned: planned.part.lights)
            return best.part.arrangement.notes, best.part.ref.bass
        return None

    def _expected_now(self) -> tuple[int, ...]:
        """(The listener's thread.) The pitches the song expects the guitar to sound now: the notes struck a little
        before or after (as it's heard late), and the ones held on."""
        played, at = self._played(), self.song_position()
        if played is None or at is None:
            return ()
        notes = played[0]
        cached = self._note_times.get(id(notes))
        if cached is None or cached[0] is not notes:
            if len(self._note_times) > 8:
                self._note_times.clear()
            cached = self._note_times[id(notes)] = (notes, [note.time for note in notes])
        times = cached[1]
        out = {p for note in notes[bisect_left(times, at - 0.35):bisect_right(times, at + 0.05)] for p in note.pitches}
        out |= {p for note in notes[bisect_left(times, at - RING_LOOK):bisect_left(times, at - 0.35)]
                if note.time + note.sustain >= at for p in note.pitches}
        return tuple(sorted(out))

    def _lowest_now(self) -> float:
        """(The listener's thread.) The lowest note to listen for: a bass's, while it's played."""
        played = self._played()
        return BASS_LOWEST if played is not None and played[1] else LOWEST

    def toggle_listen(self) -> bool:
        """The guitar heard (its notes lighting the keys) or not; whether it is now."""
        return bool(self.set_settings({"listen": not self.settings["listen"]})["listen"])

    def listening(self) -> dict[str, Any]:
        """For the page: whether the guitar's heard, on which input, what it plays now, how loud, and why not."""
        listener = self._listener
        return {"on": bool(self.settings["listen"]), "device": self.settings["listen_device"],
                "heard": [note_name(p) for p in listener.now] if listener else [],
                "level": round(listener.level, 4) if listener else 0.0,
                "error": listener.error if listener else None}

    # The guitar's pedal

    def pedal_info(self) -> dict[str, Any]:
        """The pedal's preset as far as it's known (None: not known) and its name, the one before, why it couldn't
        be switched (None: it could), and the favourites the Launchkey's pad goes through."""
        pedal = self.pedal
        slot, previous = pedal.slot, pedal.previous
        return {"slot": slot, "name": pedal.name(slot), "previous": previous, "previous_name": pedal.name(previous),
                "error": pedal.error, "favourites": list(self.settings["pedal_favourites"])}

    def pedal_name(self, slot: int | None) -> str:
        return self.pedal.name(slot)

    def set_pedal(self, body: dict[str, Any], later: float = 0.0) -> dict[str, Any]:
        """Switches the pedal: to a slot ("slot"), some on from the one it's on ("step", round from the last to the
        first), or back to the one before ("back"); after later seconds, if it's not switched again by then."""
        if body.get("back"):
            if self.pedal.back() is None:
                raise GuiError("No preset to go back to")
        elif "step" in body:
            steps, slot = int(_number(body["step"], "step", -PEDAL_SLOTS, PEDAL_SLOTS)), self.pedal.slot
            base = slot if slot is not None else 0 if steps > 0 else 1
            self.pedal.switch((base - 1 + steps) % PEDAL_SLOTS + 1, later)
        else:
            self.pedal.switch(int(_number(body.get("slot"), "preset", 1, PEDAL_SLOTS)), later)
        return self.pedal_info()

    def _follow_parts(self) -> PartsFor:
        """The parts to play while an arrangement is played, and their lit keys."""
        settings = self.follow_settings

        def parts_for(played: ArrangementRef) -> list[tuple[ArrangementRef, int]]:
            return [(r, lights) for r, play, lights in parts_chosen(settings, played, self._song_refs(played)) if play]

        return parts_for

    def _song_refs(self, ref: ArrangementRef) -> list[ArrangementRef]:
        """The arrangements of a song with notes, lead first."""
        sid = song_id(ref)
        return sorted((r for r in self.index.arrangements() if song_id(r) == sid and r.part not in NO_NOTES),
                      key=part_order)

    def _follow_voices(self) -> VoicesFor | None:
        """The voices for each arrangement played: its own tones, else the defaults for its kind of part, else the
        built-in ones (None: silent lights)."""
        settings = self.follow_settings
        if self.play_options["silent"]:
            return None

        def voices_for(ref: ArrangementRef, channels: tuple[int, int, int]) -> Voices:
            own, defaults = settings["arrangements"].get(ref.persistent_id, {}), settings[kind(ref)]

            def pick(field: str) -> Any:
                return own.get(field, defaults.get(field))

            voices = SoundChoice({None: _tone(pick("tone"))}, {None: _tone(pick("low_tone"))},
                                 {None: _tone(pick("mute_tone"))}, {None: _split(pick("split"))},
                                 settings["real_pitch"], ring=bool(self.settings["ring"]),
                                 tone_map=self._tones_for_kinds(), techniques=bool(self.settings["techniques"]),
                                 overlap=self.settings["overlap_ms"] / 1000).voices(ref, channels)
            return self._routed(replace(voices, volume=self._volume(self._slot(ref))), ref)

        return voices_for

    def _check_sound_channel(self) -> None:
        channel = int(self.settings["channel"])
        if channel in [c for channels in CHANNEL_SETS for c in channels]:
            raise GuiError(f"The lights' channel ({channel}) is one the sound plays on: choose another in Keyboard")

    # A remote's keys (the Launchkey's), played on the keyboard

    def keys_info(self) -> dict[str, Any]:
        number = self._keys_tone()
        found = tone(number)
        return {"sound": bool(self.settings["keys_sound"]), "lights": bool(self.settings["keys_lights"]),
                "tone": number, "name": f"{found.number:03d} {found.name}", "volume": int(self.settings["keys_volume"])}

    def set_keys(self, body: dict[str, Any]) -> dict[str, Any]:
        """Whether a remote's keys sound on the keyboard, with which tone and at what volume (at once), and light
        its keys; kept."""
        changed: dict[str, Any] = {}
        if "sound" in body:
            changed["keys_sound"] = bool(body["sound"])
        if "lights" in body:
            changed["keys_lights"] = bool(body["lights"])
        if "tone" in body:
            number = int(_number(body["tone"], "tone", 0, 1000))
            if number not in TONE_NUMBERS:
                raise GuiError(f"No tone {number}")
            changed["keys_tone"] = number
        if "volume" in body:
            changed["keys_volume"] = int(_number(body["volume"], "volume", 0, 127))
        with self._keys_lock:
            self.settings.update(changed)
            keyboard = self._keys_keyboard(open_one=False)
            if "keys_volume" in changed and keyboard is not None and self._keys_voiced is not None:
                keyboard.set_volume(KEYS_CHANNEL, changed["keys_volume"])
        self._kept.update(changed)
        self._save()
        return self.keys_info()

    def play_keys(self, message: list[int]) -> None:
        """A remote's key going down or up, its sustain pedal or its modulation, from its MIDI thread: handed on to
        the keys' own thread."""
        with self._keys_lock:
            if self._keys_thread is None:
                self._keys_thread = threading.Thread(target=self._keys_run, name="rs-keylights keys", daemon=True)
                self._keys_thread.start()
        self._keys_queue.put(list(message))

    def release_keys(self) -> None:
        """A remote gone: its keys let go, its notes stopped (after the ones it sent)."""
        if self._keys_thread is not None:
            self._keys_queue.put([])

    def _keys_run(self) -> None:
        while True:
            message = self._keys_queue.get()
            try:
                if message is None:
                    return
                self._play_key(message) if message else self._release_keys()
            except Exception:  # no keyboard (unplugged, or the lights on channel 1): the next key tries again
                pass
            finally:
                self._keys_queue.task_done()

    def _play_key(self, message: list[int]) -> None:
        """A key sounds on a channel of its own with the keys' tone, and lights before the song's lights (which
        get the room left until it's let go)."""
        status, number, value = message[0] & 0xF0, message[1], message[2]
        down = status == 0x90 and value > 0
        sound = bool(self.settings["keys_sound"]) and not self.play_options["silent"]  # (the one mute: theirs too)
        lights = bool(self.settings["keys_lights"])
        with self._keys_lock:
            keyboard = self._keys_keyboard(open_one=down and (sound or lights))
            if keyboard is None:
                return
            if status == 0xB0:  # the sustain pedal, or modulation
                if sound or value == 0:
                    keyboard.control(KEYS_CHANNEL, number, value)
            elif down:
                if lights and KEY_RANGE.lowest <= number <= KEY_RANGE.highest:
                    keyboard.hold((number,))
                if sound:
                    self._voice_keys(keyboard)
                    keyboard.sound(KEYS_CHANNEL, number, value)
            else:
                keyboard.let_go((number,))
                keyboard.sound(KEYS_CHANNEL, number, 0)

    def _release_keys(self) -> None:
        with self._keys_lock:
            keyboard = self._keys_keyboard(open_one=False)
            if keyboard is not None:
                keyboard.let_go_all()

    def _keys_keyboard(self, open_one: bool) -> Keyboard | None:
        """The keyboard a remote's keys play: the song's while one plays or is followed, else their own (or a
        show's while nothing plays), opened if asked."""
        session = self._session
        if session is not None and session.running:
            return session.keyboard
        if self._idle_keyboard is None and open_one:
            self._idle_keyboard = self._open_keyboard()
        return self._idle_keyboard

    def _voice_keys(self, keyboard: Keyboard) -> None:
        number = self._keys_tone()
        voiced = self._keys_voiced
        if voiced is None or voiced[0] is not keyboard or voiced[1] != number:
            found = tone(number)
            keyboard.set_voice(KEYS_CHANNEL, found.program, found.bank, ours=False,
                               volume=int(self.settings["keys_volume"]))
            self._keys_voiced = (keyboard, number)

    def _keys_tone(self) -> int:
        number = self.settings.get("keys_tone")
        return number if isinstance(number, int) and number in TONE_NUMBERS else KEYS_TONE

    # State and settings

    def state(self) -> dict[str, Any]:
        with self._lock:
            session = self._session
            out: dict[str, Any] = {"mode": "idle", "sound": not self.play_options["silent"],
                                   "volumes": list(self.settings["volumes"]),
                                   "other_follow": self._other_follow_cached(),
                                   "follow_version": self.follow_version, "play_version": self.play_version,
                                   "showing": self.showing(), "active": self.play_options.get("song"),
                                   "browse": self._browse_state(), "remote": self._remote_state(),
                                   "keys": self.keys_info(), "loop": self.loop_info(None),
                                   "listening": self.listening(), "pedal": self.pedal_info(),
                                   "launchkey": self._watch.status if self._watch is not None else None}
            if isinstance(session, Playback):
                if not session.running:
                    self._session = None
                else:
                    out.update(
                        mode="ended" if session.ended else "paused" if session.paused else "playing",
                        position=session.position,
                        length=session.length,
                        speed=session.speed,
                        song=self._playing,
                    )
            elif isinstance(session, Following):
                if not session.running:
                    self._session = None
                    out["follow"] = {"messages": list(session.messages)}
                else:
                    current = session.follower.current
                    shown, _ended = self._shown_followed(session)
                    out.update(mode="following", follow={
                        "parts": session.follower.playing() if current else [],
                        "song": f"{current.title} ({current.name})" if current else None,
                        "song_id": song_id(shown) if shown else None,  # (once its song ends, the last one)
                        "arrangement": shown.persistent_id if shown else None,
                        "position": session.follower.position(time.monotonic()),
                        "messages": list(session.messages),
                    })
        self._riff_sync((out.get("follow") or {}).get("song_id") if out["mode"] == "following" else None)
        return out

    def get_settings(self) -> dict[str, Any]:
        try:
            ports = self._list_ports()
        except Exception:
            ports = []
        return {**self.settings, "ports": ports, "game_dir": str(self.index.game_dir), "overlay_port": self.overlay_port,
                "outputs_there": self.output_status(), "inputs": _audio_inputs(),
                "pedal_backup_used": str(self.pedal.backup or ""),
                "pedal_names": [self.pedal.name(slot) for slot in range(1, PEDAL_SLOTS + 1)]}

    def set_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        port = str(body.get("midi_port", self.settings["midi_port"])).strip()
        channel = int(_number(body.get("channel", self.settings["channel"]), "lights channel", 1, 16))
        early = _number(body.get("lights_early_ms", self.settings["lights_early_ms"]), "lights early", 0, 200)
        flash = _number(body.get("fastest_flash_ms", self.settings["fastest_flash_ms"]), "fastest flash", 0, 300)
        launchkey = bool(body.get("launchkey", self.settings["launchkey"]))
        ring = bool(body.get("ring", self.settings["ring"]))
        overlap = _number(body.get("overlap_ms", self.settings["overlap_ms"]), "overlap", 0, 500)
        tone_changes = bool(body.get("tone_changes", self.settings["tone_changes"]))
        played = bool(body.get("techniques", self.settings["techniques"]))
        listen = bool(body.get("listen", self.settings["listen"]))
        listen_device = str(body.get("listen_device", self.settings["listen_device"]) or GUITAR_INPUT)
        backup = str(body.get("pedal_backup", self.settings["pedal_backup"]) or "").strip()
        favourites = _favourites(body.get("pedal_favourites", self.settings["pedal_favourites"]))
        outputs = _outputs(self.settings["outputs"], body.get("outputs") or {})
        tone_map = _tone_map(self.settings["tone_map"], body.get("tone_map") or {})
        if not port:
            raise GuiError("Choose the keyboard's MIDI output")
        sounded = (self.settings["ring"], self.settings["tone_changes"], self.settings["tone_map"],
                   self.settings["techniques"], self.settings["outputs"], self.settings["overlap_ms"])
        changed = dict(midi_port=port, channel=channel, lights_early_ms=early, fastest_flash_ms=flash, launchkey=launchkey,
                       ring=ring, tone_changes=tone_changes, tone_map=tone_map, techniques=played, outputs=outputs,
                       overlap_ms=overlap, listen=listen, listen_device=listen_device, pedal_backup=backup,
                       pedal_favourites=favourites)
        if backup != self.settings["pedal_backup"]:
            self.pedal.use_backup(_backup(backup))
        self.settings.update(changed)
        self._kept.update(changed)
        self._save()
        with self._lock:  # the lights' timing applies at once (and the notes ringing); the output and channel from
            # the next song
            session = self._session
            if isinstance(session, (Playback, Following)) and session.running:
                session.retime_lights(*self._light_timing())
        self.listen_now()  # (the guitar heard, or not)
        if (ring, tone_changes, tone_map, played, outputs, overlap) != sounded:  # (the notes ringing, tone changes,
            # techniques and other MIDI devices: at once)
            if isinstance(session, Following):
                self._follow_on(session)
            elif self._playing:
                self._play_on(self._playing["id"])
        return self.get_settings()

    def _routed(self, voices: Voices, ref: ArrangementRef) -> Voices:
        """A part's voices on the MIDI device chosen for its kind of part (outputs), if it's there: all its notes on
        its channel, with the device's own sound, a note at a time if so chosen; else on the keyboard."""
        chosen = self.settings["outputs"].get(kind(ref))
        synth = self._synth(chosen["port"]) if chosen else None
        if chosen is None or synth is None:
            return voices
        return replace(voices, main=Voice(chosen["channel"], voices.main.tone), low=None, mute=None, output=synth,
                       mono=chosen["mono"], program=chosen.get("program"), transpose=12 * chosen.get("octave", 0))

    def set_program(self, part: str, program: int) -> None:
        """A remote's knob on an arrangement sounding on another MIDI device (its kind's: part): one of the
        device's own programs (0: its first), kept and sent at once."""
        with self._lock:
            chosen = self.settings["outputs"].get(part)
            if chosen is None:
                raise GuiError(f"The {part} parts sound on the keyboard")
            chosen = {**chosen, "program": int(_number(program, "program", 0, 127))}
            self.settings["outputs"] = self._kept["outputs"] = {**self.settings["outputs"], part: chosen}
            self._save()
        synth = self._synth(chosen["port"])
        if synth is not None:
            synth.program(chosen["channel"], chosen["program"])

    def _output_of(self, part: str) -> dict[str, Any] | None:
        """For a remote: the MIDI device a kind of part sounds on (its name, and program), if it isn't the keyboard."""
        chosen = self.settings["outputs"].get(part)
        return {"port": chosen["port"], "program": chosen.get("program")} if chosen else None

    def _synth(self, port: str) -> Synth | None:
        """Another MIDI device, opened (once: it stays open); None if it isn't there (or it's the keyboard)."""
        if port == self.settings["midi_port"]:
            return None
        with self._lock:
            synth = self._synths.get(port)
        if synth is not None and synth.is_open:
            return synth
        synth = self._synth_factory(port)
        try:
            synth.open()
        except (KeyboardError, OSError, RuntimeError):
            return None
        with self._lock:
            self._synths[port] = synth
        return synth

    def output_status(self) -> dict[str, bool]:
        """Each kind of part's MIDI device, chosen: whether it's there."""
        return {part: self._synth(chosen["port"]) is not None for part, chosen in self.settings["outputs"].items()}

    def _tones_for_kinds(self) -> dict[str, Tone]:
        """The main tone for each kind of tone a song switches to (none: they're not followed)."""
        if not self.settings["tone_changes"]:
            return {}
        return {kind: tone(number) for kind, number in self.settings["tone_map"].items() if number}

    def lan_key(self) -> str:
        """The key a browser on the network needs (in the link the first time, then a cookie): made once, kept."""
        with self._lock:
            if not self._lan_key:
                self._lan_key = "".join(secrets.choice(KEY_LETTERS) for _ in range(12))
                self._save()
            return self._lan_key

    def _save(self) -> None:
        """Keeps the settings (not the command line's overrides), following's once changed, and the key."""
        with self._lock:
            data: dict[str, Any] = dict(self._kept)
            if self._follow_saved:
                data["follow"] = self.follow_settings
            if self._play_saved:
                data["play"] = self.play_options
            if self._lan_key:
                data["lan_key"] = self._lan_key
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.settings_file.with_suffix(".tmp")
            temp.write_text(json.dumps(data, indent=2) + "\n")
            temp.replace(self.settings_file)

    def _save_songs(self) -> None:
        with self._lock:
            self.songs_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.songs_file.with_suffix(".tmp")
            temp.write_text(json.dumps(self.song_settings, indent=1) + "\n")
            temp.replace(self.songs_file)

    def _light_timing(self) -> tuple[float, float]:
        """(lights early, fastest flash) in seconds."""
        return float(self.settings["lights_early_ms"]) / 1000, float(self.settings["fastest_flash_ms"]) / 1000

    def reindex(self) -> dict[str, Any]:
        self.index.refresh()
        with self._lock:
            self._loaded.clear()
            self._library = None
        return self.library()

    def close(self) -> None:
        watch, self._watch = self._watch, None
        if watch is not None:  # first: the Launchkey given back even if something else is stuck
            watch.stop()
            watch.join()
        if self._lock.acquire(timeout=5):  # (something stuck mustn't keep it from quitting)
            try:
                self._stop()
            finally:
                self._lock.release()
        if self._keys_thread is not None:
            self._keys_queue.put(None)
        self.pedal.close()
        for synth in list(self._synths.values()):  # (other MIDI devices: their notes stopped)
            synth.close()
        if self._listener is not None:
            self._listener.stop()

    def _stop(self) -> None:
        """Stops what plays, and closes the keyboard a remote's keys had (before another opens: closing one
        turns the lights' channel up again)."""
        self._end_idle_show()
        with self._keys_lock:
            if self._session is not None:
                self._session.stop()
            self._session = None
            self._playing = None
            idle, self._idle_keyboard = self._idle_keyboard, None
            if idle is not None:
                idle.close()

    def _open_keyboard(self) -> Keyboard:
        keyboard = self.keyboard_factory(str(self.settings["midi_port"]), int(self.settings["channel"]))
        keyboard.open()
        return keyboard

    def _other_follow_cached(self) -> bool:
        checked, running = self._other
        if time.monotonic() - checked > 3:
            try:
                running = self._other_follow()
            except Exception:
                running = False
            self._other = (time.monotonic(), running)
        return running


def _guitar(loaded: list[tuple[ArrangementRef, Arrangement]]) -> dict[str, Any] | None:
    """The guitar a song's played on, for the page's fretboard (its first guitar part's; a bass's, with none): its
    strings' open pitches, low to high, its capo, and its tuning's name."""
    if not loaded:
        return None
    ref, arrangement = next(((r, a) for r, a in loaded if not r.bass), loaded[0])
    strings = BASS if ref.bass else GUITAR
    tuning = (tuple(arrangement.tuning) + (0,) * len(strings))[:len(strings)]
    return {"strings": [pitch + move for pitch, move in zip(strings, tuning)], "capo": max(arrangement.capo, 0),
            "tuning": tuning_name(tuning, arrangement.capo), "bass": ref.bass}


def _favourites(value: Any) -> list[int]:
    """The pedal's favourite presets, checked: slots (1 to 200), PEDAL_FAVOURITES_MOST at most, each once."""
    if not isinstance(value, (list, tuple)):
        raise GuiError("The pedal's favourites are a list of presets")
    out = list(dict.fromkeys(int(_number(slot, "preset", 1, PEDAL_SLOTS)) for slot in value))
    if len(out) > PEDAL_FAVOURITES_MOST:
        raise GuiError(f"The pedal's favourites: {PEDAL_FAVOURITES_MOST} at most")
    return out


def _backup(path: str) -> Path | None:
    """The pedal's backup, as the settings have it (None: the newest found)."""
    return Path(path).expanduser() if path else None


def _number(value: Any, what: str, lowest: float, highest: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise GuiError(f"Not a number for {what}: {value!r}") from None
    if not lowest <= number <= highest:
        raise GuiError(f"The {what} must be {lowest:g} to {highest:g}")
    return number


def _tone(value: Any) -> Tone | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return tone(int(value))
    except (KeyError, TypeError, ValueError):
        raise GuiError(f"No tone {value!r}") from None


def _split(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return parse_note(str(value))
    except ValueError as exc:
        raise GuiError(str(exc)) from None


def _audio_inputs() -> list[str]:
    """The audio inputs there are (to hear a guitar on), if sounddevice is installed."""
    try:
        import sounddevice
    except ImportError:
        return []
    try:
        return [str(d["name"]) for d in sounddevice.query_devices() if d["max_input_channels"] > 0]
    except Exception:  # (no audio)
        return []


def _midi_outputs() -> list[str]:
    import rtmidi

    out = getattr(rtmidi, "MidiOut")()
    try:
        return list(out.get_ports())
    finally:
        del out


def _png(dds: bytes) -> bytes | None:
    with tempfile.TemporaryDirectory(prefix="rs-keylights-art") as folder:
        source, target = Path(folder) / "art.dds", Path(folder) / "art.png"
        source.write_bytes(dds)
        try:
            subprocess.run(["sips", "-s", "format", "png", str(source), "--out", str(target)], capture_output=True,
                           timeout=10, check=True)
            return target.read_bytes()
        except (OSError, subprocess.SubprocessError):
            return None


def _key_of(info: dict[str, Any]) -> Key:
    return Key(info["tonic"], info["mode"])


def _play_show(keyboard: Keyboard, show: KeyShow, quit_: threading.Event, done: Callable[[Keyboard], None]) -> None:
    """A show on the keys while nothing plays."""
    try:
        while not quit_.is_set() and not show.done:
            show.step(keyboard, time.monotonic())
            quit_.wait(0.02)
    finally:
        show.end(keyboard)
        done(keyboard)


def _bonjour_name() -> str | None:
    """This Mac's name on the local network (NAME.local, as System Settings > Sharing shows it)."""
    try:
        found = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    name = found.stdout.strip()
    return f"{name}.local" if name else None


def _lan_address() -> str | None:
    """This Mac's address on the network it reaches others through (nothing is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: picks the route, not a destination
            return probe.getsockname()[0]
        except OSError:
            return None


def _is_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _other_follow_running() -> bool:
    """Another `rs-keylights follow` (e.g. the RS Re-ARMed sidekick) would light the keys too."""
    found = subprocess.run(["pgrep", "-f", "rs-keylights follow"], capture_output=True, text=True)
    return any(pid.strip() and int(pid) != os.getpid() for pid in found.stdout.split())


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, gui: Gui, port: int = DEFAULT_GUI_PORT, lan: bool = False, address: str | None = None):
        self.gui = gui
        self.lan_key = gui.lan_key() if lan else None  # on the network: the key browsers there need
        self.bonjour = _bonjour_name() if lan else None
        self.names = {n.lower() for n in (socket.gethostname(), self.bonjour) if n} if lan else set()
        super().__init__((address or ("0.0.0.0" if lan else "127.0.0.1"), port), _Handler)

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)  # without HTTPServer's name lookup, which can be slow
        self.server_name, self.server_port = "rs-keylights", self.port

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def phone_urls(self) -> list[str]:
        """Links for a browser on the network (a phone on the same Wi-Fi), with the key; none without lan."""
        if not self.lan_key:
            return []
        return [f"http://{host}:{self.port}/?key={self.lan_key}" for host in (self.bonjour, _lan_address()) if host]

    def trusted(self, client: str) -> bool:
        """A request from this Mac: no key needed."""
        return _is_address(client) and ipaddress.ip_address(client).is_loopback


class _Handler(BaseHTTPRequestHandler):
    @property
    def _server(self) -> GuiServer:
        return cast(GuiServer, self.server)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (the base class's name)
        pass

    def _send(self, status: int, body: bytes, content_type: str, cookie: str | None = None,
              cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):  # the page went (reloaded or closed) first: no one to answer
            self.close_connection = True

    def _json(self, status: int, value: Any) -> None:
        self._send(status, json.dumps(value).encode(), "application/json")

    def _host_allowed(self) -> bool:
        """For this server's own names and addresses (not other names pointing at it: DNS rebinding)."""
        name, _, port = (self.headers.get("Host") or "").lower().rpartition(":")
        server = self._server
        if port != str(server.port):
            return False
        if name in ("127.0.0.1", "localhost"):
            return True
        return bool(server.lan_key) and (name in server.names or _is_address(name.strip("[]")))

    def _link_key(self, url: ParseResult) -> str | None:
        return parse_qs(url.query).get("key", [None])[0] if url.path == "/" else None

    def _has_key(self, url: ParseResult) -> bool:
        """The key, in the link to the page or in the cookie."""
        given = self._link_key(url)
        if given is None:
            try:
                cookie = SimpleCookie(self.headers.get("Cookie") or "")
            except CookieError:
                return False
            given = cookie[KEY_COOKIE].value if KEY_COOKIE in cookie else None
        key = self._server.lan_key
        return bool(key and given) and hmac.compare_digest(str(given).encode(), str(key).encode())

    def _allowed(self, url: ParseResult) -> bool:
        """For this server's names, and from this Mac or a browser with the key; anything else is refused here."""
        if not self._host_allowed():
            self._json(403, {"error": f"Use http://127.0.0.1:{self._server.port}/"})
            return False
        if self._server.trusted(self.client_address[0]) or self._has_key(url):
            return True
        if url.path == "/":
            self._send(403, NO_KEY_PAGE, "text/html; charset=utf-8")
        else:
            self._json(403, {"error": "Open the link with the key first: the page on the Mac shows it, under Keyboard"})
        return False

    def do_GET(self) -> None:  # noqa: N802 (the base class's name)
        url = urlparse(self.path)
        if not self._allowed(url):
            return
        gui = self._server.gui
        try:
            if url.path == "/":
                page = files("rs_keylights").joinpath("gui.html").read_bytes()
                key, given = self._server.lan_key, self._link_key(url)
                cookie = None
                if key and given and hmac.compare_digest(given.encode(), key.encode()):  # remembered from now on
                    cookie = f"{KEY_COOKIE}={key}; Max-Age={COOKIE_AGE}; Path=/; HttpOnly; SameSite=Lax"
                self._send(200, page, "text/html; charset=utf-8", cookie)
            elif url.path == "/api/library":
                self._json(200, gui.library())
            elif url.path == "/api/song":
                self._json(200, gui.song(parse_qs(url.query).get("id", [""])[0]))
            elif url.path == "/api/fretboard":
                query = parse_qs(url.query)
                self._json(200, gui.fretboard(query.get("id", [""])[0], query.get("part", [None])[0]))
            elif url.path == "/api/art":
                sid = parse_qs(url.query).get("id", [""])[0]
                art = gui.album_art(sid)
                gui.warm_around(sid)  # the songs around it next, in the background
                if art is None:
                    self._json(404, {"error": "No album art"})
                else:
                    self._send(200, art, "image/png", cache="private, max-age=86400")
            elif url.path == "/api/key":
                self._json(200, gui.song_key(parse_qs(url.query).get("id", [""])[0]))
            elif url.path == "/api/tones":
                self._json(200, gui.tones())
            elif url.path == "/api/state":
                self._json(200, gui.state())
            elif url.path == "/api/follow":
                self._json(200, gui.follow_info())
            elif url.path == "/api/play/options":
                self._json(200, gui.play_options_info())
            elif url.path == "/api/settings":
                self._json(200, {**gui.get_settings(), "lan": bool(self._server.lan_key),
                                 "phone": self._server.phone_urls()})
            else:
                self._json(404, {"error": "Not found"})
        except Exception as exc:  # shown on the page
            self._json(400 if isinstance(exc, (GuiError, ValueError, OSError)) else 500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802 (the base class's name)
        url = urlparse(self.path)
        if not self._allowed(url):
            return
        origin = self.headers.get("Origin")
        if origin and origin.lower() != f"http://{(self.headers.get('Host') or '').lower()}":
            self._json(403, {"error": "Not from this page"})
            return
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            self._json(415, {"error": "JSON only"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise GuiError("Expected a JSON object")
        except (ValueError, GuiError) as exc:
            self._json(400, {"error": f"Bad request: {exc}"})
            return
        gui = self._server.gui
        routes: dict[str, Callable[[], Any]] = {
            "/api/play": lambda: gui.play(body),
            "/api/pause": gui.pause,
            "/api/resume": gui.resume,
            "/api/seek": lambda: gui.seek(body),
            "/api/play/options": lambda: gui.set_play_options(body),
            "/api/song/settings": lambda: gui.set_song(body),
            "/api/switch": lambda: gui.switch(body),
            "/api/stop": gui.stop,
            "/api/follow": lambda: gui.follow(body),
            "/api/follow/settings": lambda: gui.set_follow(body),
            "/api/scale": lambda: gui.show_scale(body),
            "/api/scale/stop": gui.stop_scale,
            "/api/settings": lambda: gui.set_settings(body),
            "/api/keys": lambda: gui.set_keys(body),
            "/api/volumes": lambda: gui.set_volumes(body.get("volumes") or {}),
            "/api/loop": lambda: gui.set_loop(body),
            "/api/pedal": lambda: gui.set_pedal(body),
            "/api/riff": lambda: gui.set_game_riff(body),
            "/api/reindex": gui.reindex,
        }
        route = routes.get(url.path)
        if route is None:
            self._json(404, {"error": "Not found"})
            return
        try:
            self._json(200, route())
        except Exception as exc:  # shown on the page
            self._json(400 if isinstance(exc, (GuiError, ValueError, OSError)) else 500, {"error": str(exc)})
