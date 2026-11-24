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
import shutil
import tempfile
import wave
import threading
import time
from collections import OrderedDict, deque
from datetime import date, datetime, timedelta
from dataclasses import asdict, replace
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import ParseResult, parse_qs, quote, urlparse

from .follow import DEFAULT_OVERLAY_PORT, OverlayClient, PartsFor
from .keyboard import (DEFAULT_CHANNEL, DEFAULT_PORT, NORMAL_VOLUME, PROGRAMS, Keyboard, KeyboardError, Synth,
                       keyboard_name)
from .library import NO_NOTES, ArrangementRef, SongIndex
from .psarc import Archive, ArchiveError
from .lights import LIGHTS_EARLY, MIN_PERIOD, KeyRange, Light
from . import drums, midiexport, theory
from .parts import (CHANNELS_USED_UP, DRUMS, DrumVoices, Part, PlanCache, PlannedPart, VoicesFor, channels_needed, plan,
                    player_for)
from .player import Track, squeal_bends
from .priority import settle
from .session import Following, Playback
from . import chartsync, gameparts, gamestems, melody, sides, splitserver, stems, tonecheck, warmup
from .songaudio import SongAudio, song_samples
from .listen import BASS_LOWEST, DEVICE as GUITAR_INPUT, LOWEST, Listener, OnsetListener
from .lyrics import Line, lyric_lines
from .pedal import SLOTS as PEDAL_SLOTS, Pedal, PedalError
from .pedaltones import PedalTones, PedalTonesError
from .plays import Recorder, judge, runs_of
from .riffloop import GameLoop, GameSongs, RiffLoop
from .sng import (BASS, BEND, GUITAR, HAMMER_ON, HARMONIC, PALM_MUTE, PINCH_HARMONIC, PULL_OFF, SLIDE, TAP, TREMOLO,
                  Arrangement, Chord, Note, Section, note_name)
from .songtones import FIRST_KINDS, KINDS as TONE_KINDS, kind_changes, song_tones, tone_kind
from .keyshow import KeyShow
from .songkey import Key, find_key, scale_show, shapes, tuning_name
from .sound import GUITAR_TONE, KEYBOARD_CHANNELS, PINCH_TONE, SoundChoice, Squeal, Voice, Voices, squeal_from
from .tones import DRUM_SET_BANK, DRUM_SETS, TONES, Tone, tone
from .warmup import Warmup
from .watch import (BUILT_IN, DEFAULT as DEFAULT_TONE, FIELDS as TONE_PAGES, TONE_NUMBERS as TONE_ORDER, Watch, knob_tone,
                    stepped_tone, tone_choices, tone_shown)

# A page in the browser for what the commands do: the song library, parts and their tones, playback
# with a time slider, following the game, and the keyboard's settings. The server answers on 127.0.0.1,
# to requests for 127.0.0.1 or localhost (not other names pointing there: DNS rebinding), and changes
# things only for JSON requests from its own page. With lan it answers on the network too, so a phone
# can be the remote while the game has the screen: to the Mac's own names and addresses, and to
# browsers that opened the link with its key once (then a cookie has it).

DEFAULT_GUI_PORT = 8040
PART_ORDER = ("lead", "rhythm", "combo", "bass", "vocals")
KIND_DEFAULTS = {"vocals": {"tone": 196}}  # (the vocal melody's own tone, unless it's given one: VOICE LEAD)
KINDS = ("lead", "rhythm", "bass")  # each has default tones, for parts without their own
TONE_FIELDS = ("tone", "mute_tone", "pinch_tone")  # a set of tones: these tone numbers
PART_FIELDS = (*TONE_FIELDS, "follow_tones")  # and whether its tone follows the song's (False: it stays)
SAME_TONE = 0  # palm mutes or pinch harmonics with the tone the rest has (over a default for them)
SLOTS = 8  # a song's arrangements with volumes of their own, by their place in it (the same in every song)
GAME_TIME_EVERY = 0.05  # seconds: the game's time asked of its riffloop mod at most this often
GAME_PARTS_AGAIN = 2.0  # seconds before the song's parts are handed to the game's mod again, if it's lost them
GAME_PART_LABELS = {"drums": "DRUMS", "bass": "BASS", "guitar": "GUITAR", "vocals": "VOCALS", "piano": "KEYS",
                    "other": "OTHER", "lead": "LEAD", "rhythm": "RHYTHM"}  # (the game's MIXER's stem page's columns, the song's parts)
GAME_PART_KINDS = {"drums": "drums", "bass": "bass", "guitar": "guitar", "vocals": "vocals", "piano": "keys",
                   "other": "other", "lead": "lead", "rhythm": "rhythm"}  # (their colours there)
GAME_MIXER_SCREEN = "RSKeylights_Mixer"  # the riffloop mod's MIXER in the game (from Learn a Song's pause menu: the
# Launchkey's mixer page, its own movie)
GAME_MIXER_SCREENS = (GAME_MIXER_SCREEN, "RSKeylights_Stems", "RSKeylights_Tones")  # (its STEM MIXER: the MIXER
# on its other page; and TONES: each part's tone)
GAME_MIXER_SLIDERS = 6  # its arrangements' sliders (the Launchkey's mixer knobs), then the Launchkey keys'
GAME_SONGS_EVERY = 0.1  # seconds: following, its song list's highlight asked at most this often
GAME_SONGS_OWN = 0.6  # seconds after a remote moved the game's highlight its moves are taken as that one's
GAME_KEYS_AT_ONCE = 300  # the song list's keys asked for so many at a time
PEDAL_FAVOURITES, PEDAL_FAVOURITES_MOST = (51, 187), 8  # the pedal presets the Launchkey's pad goes through
TAP_IDLE = 4.0  # seconds an input's meter isn't asked for before its input's closed (the page's gone, or folded)
BETAS = ("melody", "sides")  # beta features, each off till it's turned on (the rs-keylights card): a song's vocal
# melody as a part of its own (melody.py), and its guitar's lead and rhythm apart (sides.py)
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


def kept_id(sid: str, game_dir: Path) -> str:
    """A song's id as the files keep it: its archive in the game folder by its place there (dlc/x_m.psarc#Key), so
    its choices go with the game folder wherever that is; else as it is."""
    archive, _, key = sid.rpartition("#")
    folder = f"{str(game_dir).rstrip('/')}/"
    if archive.startswith(folder) and "/../" not in archive and "/./" not in archive:  # (the quick way: a save has
        # every song's)
        return f"{archive[len(folder):]}#{key}"
    try:
        return f"{Path(archive).relative_to(game_dir).as_posix()}#{key}" if archive else sid
    except ValueError:
        return sid


def place_in_game(archive: str) -> str | None:
    """An archive's place in its game folder, from its path (songs.psarc, or dlc/...); None if it isn't in one."""
    path = Path(archive)
    if not path.is_absolute():
        return path.as_posix()
    if path.name.lower() == "songs.psarc":
        return "songs.psarc"
    folders = [part.lower() for part in path.parts[:-1]]
    return Path(*path.parts[folders.index("dlc"):]).as_posix() if "dlc" in folders else None


def found_id(kept: str, game_dir: Path, by_key: Callable[[], dict[str, str]]) -> str:
    """The id of a song the files keep (kept_id's, or a whole path from an earlier game folder, such as one on a drive
    since given up), in this game folder; a song whose archive isn't there by the only song with its key (by_key: key
    -> id, for keys only one song has), as a song added while the game ran is copied in under another name."""
    archive, _, key = kept.rpartition("#")
    place = place_in_game(archive) if archive else None
    if place is None:
        return kept
    path = game_dir / place
    if not path.is_file() and key in by_key():
        return by_key()[key]
    return f"{path}#{key}"


def part_order(ref: ArrangementRef) -> tuple[int, str]:
    return (PART_ORDER.index(ref.part) if ref.part in PART_ORDER else len(PART_ORDER), ref.part)


def kind(ref: ArrangementRef) -> str:
    """Whose default tones a part gets: bass, rhythm, or lead (combos too); the vocal melody, vocals."""
    if ref.part == "vocals":
        return "vocals"
    if ref.bass:
        return "bass"
    return "rhythm" if ref.part.startswith("rhythm") or ref.name.lower() == "rhythm" else "lead"


def default_lights(kinds: list[str]) -> list[int]:
    """Whether each of a song's parts plays and lights keys by default (1), sharing the 4 lit keys, or not (0): with
    4 parts or fewer, all of them; with more, the first lead, rhythm and bass part."""
    played = [k for k in kinds if k != "vocals"]  # (the vocal melody: off, till it's turned on)
    chosen = list(range(len(played))) if len(played) <= 4 else [played.index(k) for k in KINDS if k in played]
    out, place = [], 0
    for k in kinds:
        out.append(0 if k == "vocals" else 1 if place in chosen else 0)
        place += k != "vocals"
    return out


def default_follow() -> dict[str, Any]:
    """Following's settings: real pitch, the offset, the default tones for lead, rhythm and bass parts
    (playing songs uses them too), the tones of arrangements given their own, and the parts chosen to play
    while an arrangement is played (all by persistent ID: {played: {arrangement: {play, lights}}}, where
    only the one played plays on every key)."""
    return {"real_pitch": True, "offset_ms": 0, "show_key": True, "lead": {}, "rhythm": {},
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
    """The MIDI device sounding each kind of part (lead, rhythm, bass; and a song's drums) other than the keyboard,
    with a change, checked: its port, channel, and whether it plays a note at a time; its program and how many it
    has (a remote's knob goes round them) (drums: its drums' notes, DRUM_MAPS) (a change of None: the keyboard
    again)."""
    if not isinstance(change, dict):
        raise GuiError("Expected a MIDI device for each kind of part")
    out = dict(table)
    for part, chosen in change.items():
        if part not in (*KINDS, "drums"):
            raise GuiError(f"No kind of part {part!r}")
        if not chosen:
            out.pop(part, None)
            continue
        if not isinstance(chosen, dict) or not str(chosen.get("port") or "").strip():
            raise GuiError("Choose a MIDI output for the part")
        programs = int(_number(chosen.get("programs") or PROGRAMS, "programs", 1, PROGRAMS))
        out[part] = {"port": str(chosen["port"]).strip(),
                     "channel": int(_number(chosen.get("channel", drums.DRUM_CHANNEL if part == "drums" else 1),
                                            "channel", 1, 16)),
                     "mono": bool(chosen.get("mono", True)),
                     "program": _program(chosen.get("program"), programs),
                     "programs": programs,
                     "octave": int(_number(chosen.get("octave", 0), "octave", -2, 2))}
        if part == "drums":
            if chosen.get("map", "gm") not in DRUM_MAPS:
                raise GuiError(f"No drum notes {chosen.get('map')!r}")
            out[part]["map"] = chosen.get("map", "gm")
            out[part]["kit"] = _circuit_kit(chosen.get("kit") or (table.get(part) or {}).get("kit") or {})
    return out


def _program(program: Any, programs: int) -> int | None:
    """A MIDI device's program (0: its first), checked against how many it has; None (or empty): its own sound."""
    if program in (None, ""):
        return None
    number = int(_number(program, "program", 0, PROGRAMS - 1))
    if number >= programs:
        raise GuiError(f"It has {programs} programs: 1 to {programs}")
    return number


def _recalled(table: dict[str, Any], change: Any, known: Any) -> tuple[Any, dict[str, dict[str, Any]]]:
    """A change to the MIDI devices sounding each kind of part, with what each device had for it before: a device
    chosen by itself ({"port": ...} alone) has its channel, its drums' notes and kit and so on back as they were
    (else the defaults); and each part's settings remembered for its device (chosen, or put back on the keyboard),
    for the next time it's chosen. (known: by part, then device.)"""
    remembered = {part: dict(ports) for part, ports in (known if isinstance(known, dict) else {}).items()
                  if part in (*KINDS, "drums") and isinstance(ports, dict)}
    if not isinstance(change, dict):
        return change, remembered
    filled = dict(change)
    for part, chosen in change.items():
        was = table.get(part)
        if isinstance(was, dict) and was.get("port"):
            remembered.setdefault(str(part), {})[was["port"]] = was
        if isinstance(chosen, dict) and set(chosen) == {"port"}:  # (just the device: as it was)
            filled[part] = {**remembered.get(str(part), {}).get(str(chosen["port"]).strip(), {}), "port": chosen["port"]}
    return filled, remembered


def _circuit_kit(change: Any) -> dict[str, list[int | None]]:
    """A Novation Circuit's drums for each General MIDI drum (CIRCUIT_KIT, changed), checked: which of its 4 drums
    plays it (1 to 4; 0, CIRCUIT_SHARED: whichever of those no drum's on suits it then), and its sound there (one of
    its samples, 1 to 64; None: the one that drum has, and a shared drum's not played)."""
    if not isinstance(change, dict):
        raise GuiError("Expected the Circuit's drum for each drum")
    out = {str(note): list(drum) for note, drum in CIRCUIT_KIT.items()}
    for note, drum in change.items():
        if str(note) not in out:
            raise GuiError(f"No drum {note!r}")
        if not isinstance(drum, (list, tuple)) or len(drum) != 2:
            raise GuiError("Expected a Circuit drum (1 to 4, or 0: shared) and its sample (1 to 64, or none)")
        sample = drum[1]
        out[str(note)] = [int(_number(drum[0], "Circuit drum", CIRCUIT_SHARED, 4)),
                          None if sample in (None, "") else int(_number(sample, "sample", 1, 64))]
    return out


# A song's drum part on another device: its drums' notes. General MIDI's (as the Casio's drum channel has them), or an
# original Novation Circuit's 4 drums on its channel 10 (its Programmer's Reference Guide 1.3: Drum 1 to 4 on notes 60,
# 62, 64 and 65, and each one's sample chosen with a Control Change, its patch select, 0 to 63): each drum of the part
# on one of them (CIRCUIT_KIT, changed on the page), with a sample of its own if it's given one (sent before its hits
# as it's needed, so one of the Circuit's drums plays several). Or shared (CIRCUIT_SHARED): those drums on the
# Circuit's drums no other's on, each hit on whichever suits it then (parts.place: so two at once both play, and a
# crash rings on), the kick and snare kept on drums of their own (their knobs on the Circuit theirs)
DRUM_MAPS = ("gm", "circuit")
CIRCUIT_NOTES = {1: 60, 2: 62, 3: 64, 4: 65}
CIRCUIT_PATCH_SELECT = {1: 8, 2: 18, 3: 44, 4: 50}
CIRCUIT_SHARED = 0
CIRCUIT_KIT: dict[int, tuple[int, int | None]] = {  # (Drum 1 the kick, 2 the snare, 3 the hi-hats and ride: time is
    # kept on one or the other; 4 the crash and toms)
    drums.KICK: (1, None), drums.SNARE: (2, None), drums.CLOSED_HAT: (3, None), drums.OPEN_HAT: (3, None),
    drums.RIDE: (3, None), drums.CRASH: (4, None), **{t: (4, None) for t in drums.TOMS}}


def _drum_voicing(output: dict[str, Any] | None) -> dict[str, Any]:
    """The drums' device's voicing (DrumVoices'): its notes for each General MIDI drum, the sounds chosen on it first
    (CC, value), and the drums shared and the Circuit drums they share (none: the drums no other's on are 4's)."""
    if not output or output.get("map") != "circuit":
        return {"notes": {}, "patches": {}, "shared": {}, "pool": {}}
    kit = {int(note): (int(drum or 0), sample) for note, (drum, sample) in _circuit_kit(output.get("kit") or {}).items()}
    notes: dict[int, int | None] = {}
    patches: dict[int, tuple[int, int]] = {}
    shared = {note: int(sample) - 1 for note, (drum, sample) in kit.items() if drum == CIRCUIT_SHARED and sample}
    own = {drum for drum, _sample in kit.values() if drum != CIRCUIT_SHARED}
    pool = {CIRCUIT_NOTES[d]: CIRCUIT_PATCH_SELECT[d] for d in sorted(set(CIRCUIT_NOTES) - own) or [4]}
    for note, (drum, sample) in kit.items():
        if drum == CIRCUIT_SHARED:
            notes[note] = next(iter(pool)) if sample else None  # (as it's struck on the page: its first drum)
            if sample:
                patches[note] = (next(iter(pool.values())), int(sample) - 1)
            continue
        notes[note] = CIRCUIT_NOTES[drum]
        if sample:
            patches[note] = (CIRCUIT_PATCH_SELECT[drum], int(sample) - 1)
    return {"notes": notes, "patches": patches, "shared": shared, "pool": pool}
DRUMS_VOLUME = 100  # a drum part's volume as it starts (0 to 127)
PRESSED_VELOCITY = 100  # a key clicked on the page: how hard it's struck
HELD_WAIT = 20.0  # seconds the page's ask for the keys held waits for a change, at most
SYNTH_LOOK_AGAIN = 2.0  # seconds before a MIDI device that wasn't there is looked for again
AUDIO_VOLUME = 80  # the song's own audio's, as it starts (0 to 100)
WARMUP_LOG = "warmups.jsonl"  # the warm-up's progress log, beside the settings: a line a drill played
PROGRESS_DAYS = 30  # the days of it shown
SERVER_SEEN_FOR = 30.0  # seconds what a split server said is shown before it's asked again
AUDIO_KEPT_FOR = 60.0  # seconds the last song's audio stays decoded once it's stopped (split: half a GB or more)
DRUM_HIT_LENGTH = 0.15  # seconds a drum struck on the page sounds (its sound decays by itself)
DRUMS_PLACE_MOST = 4  # for a remote, a song's drum part is an arrangement after its own, in this place at most (its
# fifth: a song with five arrangements has its drums there, its fifth on the page only)


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
    """Playing's options with a change, checked: speed, real_pitch, silent, the song's own audio (audio, and its
    volume: audio_volume, 0 to 100, and where it plays: audio_device), and the song picked."""
    out = dict(options)
    if "speed" in change:
        out["speed"] = _number(change["speed"], "speed", 0.05, 4)
    for key in ("real_pitch", "silent", "audio"):
        if key in change:
            out[key] = bool(change[key])
    if "audio_volume" in change:
        out["audio_volume"] = int(_number(change["audio_volume"], "the song's volume", 0, 100))
    if "stem_volumes" in change:  # (each of the song's parts', 0 to 100: those given)
        given = change["stem_volumes"]
        if not isinstance(given, dict) or any(k not in stems.ALL for k in given):
            raise GuiError(f"Expected the parts' volumes ({', '.join(stems.ALL)})")
        out["stem_volumes"] = {**{s: 100 for s in stems.ALL}, **out.get("stem_volumes", {}),
                               **{k: int(_number(v, f"the {k}' volume", 0, 100)) for k, v in given.items()}}
    if "stem_mutes" in change:  # (and each one muted or not: its volume kept)
        given = change["stem_mutes"]
        if not isinstance(given, dict) or any(k not in stems.ALL for k in given):
            raise GuiError(f"Expected the parts muted ({', '.join(stems.ALL)})")
        out["stem_mutes"] = {**{s: False for s in stems.ALL}, **out.get("stem_mutes", {}),
                             **{k: bool(v) for k, v in given.items()}}
    if "separator" in change:  # (which splits a song into its parts, and whose parts play: the other's if not made)
        if change["separator"] not in stems.SEPARATORS:
            raise GuiError(f"Expected a separator ({', '.join(stems.SEPARATORS)})")
        out["separator"] = change["separator"]
    if "audio_device" in change:  # (an audio output, by name; none: the Mac's own, as it is)
        device = change["audio_device"]
        if device is not None and not isinstance(device, str):
            raise GuiError("Expected an audio output's name")
        out["audio_device"] = device.strip() or None if isinstance(device, str) else None
    if "song" in change:
        out["song"] = str(change["song"]) if change["song"] else None
    return out


def _percent(volume: int) -> float:
    """A volume (0 to 127) as the game's sliders have it: percent, to a tenth."""
    return round(volume * 100 / 127, 1)


def _volume_of(percent: float) -> int:
    """A game slider's percent as a volume (0 to 127)."""
    return min(max(round(percent * 127 / 100), 0), 127)


def _volumes(volumes: Any) -> list[int]:
    """Volumes kept by place (SLOTS of them, 0 to 127), checked."""
    out = [int(_number(v, "volume", 0, 127)) for v in list(volumes)[:SLOTS]]
    return out + [NORMAL_VOLUME] * (SLOTS - len(out))


def _changed_tones(tones: dict[str, Any], change: Any) -> dict[str, Any]:
    if not isinstance(change, dict):
        raise GuiError("Expected tones")
    merged = {**tones, **{k: v for k, v in change.items() if k in PART_FIELDS}}
    out: dict[str, Any] = {}
    for field in TONE_FIELDS:
        if field != "tone" and merged.get(field) in (SAME_TONE, str(SAME_TONE)) and merged.get(field) is not False:
            out[field] = SAME_TONE  # (kept: it plays over a default)
            continue
        found = _tone(merged.get(field))
        if found is not None:
            out[field] = found.number
    if merged.get("follow_tones") is False:  # (kept only off: else it follows them as Tone changes has it)
        out["follow_tones"] = False
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
        pedal_tones_factory: Callable[[Callable[[int | None], str]], PedalTones] = PedalTones,
        audio_stream: Callable[..., Any] | None = None,
        onset_listener: Callable[..., OnsetListener] = OnsetListener,
    ):
        self.index = index
        self._onset_listener = onset_listener  # (the warm-up's: your playing heard; tests: their own)
        self._synth_factory = synth_factory  # other MIDI devices sounding parts (outputs), opened once they're used
        self._synths: dict[str, Synth] = {}
        self._synths_missing: dict[str, float] = {}  # (devices that weren't there: when they were looked for)
        self.pedal = pedal_factory()  # the guitar's pedal, its presets switched from the Launchkey and the page
        self._names_error: str | None = None  # (why the presets' names couldn't be read from it, last time)
        threading.Thread(target=self._read_pedal_names, name="rs-keylights pedal names", daemon=True).start()
        self.pedal_tones = pedal_tones_factory(self.pedal.name)  # and its preset for each of a song's tones
        # Following, the loop is Rocksmith's: its Riff Repeater loops it (RS Re-ARMed's riffloop mod), for the song
        # it was set for (another followed, or following stopped: Riff Repeater's own again)
        self._riff_loop = riff_loop or RiffLoop()
        self._recorder = Recorder(self._riff_loop)  # (the hits and misses counted as you play, kept)
        self._recorder.start()
        self._riff_song: str | None = None
        self._riff_said: GameLoop | None = None
        self._game_mixer_sent: tuple[tuple[Any, ...], ...] | None = None  # the MIXER's rows sent
        self._tone_field = {"turns": 0, "field": 0}  # the knobs' tones' page (tone, palm mutes, harmonics: the
        # Launchkey's up arrow's, and the game's TONES'), and the turns its TONES made (the Launchkey follows each)
        self._game_mixer_page = {"turns": 0, "page": None}  # the page the MIXER was turned to there last (each turn
        # counted: the Launchkey's mixer follows each)
        # last
        self._game_mixer_ids: dict[str, str] = {}  # its rows' parts (an arrangement's ID, "drums"), by row
        self._game_tones_sent: tuple[tuple[str, str, str], ...] | None = None  # its TONES' rows sent last
        self._game_tones: dict[str, int] = {}  # the tone each TONES row's part plays (stepped there: as it's now)
        self._game_mixer_lock = threading.RLock()  # (the Launchkey's loop and the page's both ask; held again as a
        # part's changed: change_parts looks at the state, which sends the rows)
        self._game_time: tuple[float, float | None] = (-math.inf, None)  # (when asked, the game's time then)
        self._game_said: GameLoop | None = None  # (and all the mod said then)
        # Following, Learn a Song's song list (the game's, through the mod): its highlight as last asked (when), its
        # keys in its order, the key last seen highlighted, and the one a remote last moved it to (when)
        self._game_songs: tuple[float, GameSongs | None] = (-math.inf, None)
        self._game_keys: list[str] = []
        self._game_seen: str | None = None
        self._game_sent: tuple[float, str | None] = (-math.inf, None)
        self._riff_settings: dict[str, Any] = {"speed": 100, "accelerator": False, "passes": 1}  # (as set here)
        self._game_stems_read: tuple[Any, dict[str, Any] | None] = (None, None)  # (RSMods.ini as last read)
        self._game_stems_was: dict[str, int] = {}  # (a part muted: its level before, to go back to)
        self._game_music_kept: int | None = None  # (the game's Music muted: at 0 there, its level kept here)
        # Following, the song's parts in the game's own song (gameparts, riffloop's): its song, file, the parts in it
        # and by which separator; what they're made for (the song, the separator chosen, the splits done), when they
        # were last handed to the mod, why not (the last time), and Song Stems' levels while they play (both at 100,
        # these kept, and its muted parts')
        self._game_parts: dict[str, Any] | None = None
        self._game_parts_wanted: tuple[Any, ...] | None = None
        self._game_parts_sent = -math.inf
        self._game_parts_error: str | None = None
        self._game_parts_lock = threading.Lock()
        self._song_stems_kept: tuple[dict[str, int], dict[str, int]] | None = None
        self._splits_done = 0
        # how far each song's audio is after its chart (chartsync: seconds; measured in the background as it's first
        # played or followed), and the songs being measured
        self._chart_offsets: dict[str, float] = {}
        self._measuring: set[str] = set()
        self._taps: dict[tuple[str, int], tonecheck.Tap] = {}  # (inputs read for their meters, or recorded)
        self._taps_lock = threading.Lock()
        self._tone_check: dict[str, Any] = {"state": "idle"}  # (the tone check: recording, analysing, or not)
        self._sides_measuring: dict[str, str] = {}  # (songs whose guitar's being measured for its lead and rhythm:
        # "measuring", or "failed": not again till rs-keylights starts again)
        self._melody_making: dict[str, dict[str, Any]] = {}  # (a song's vocal melody being made: its step, how far,
        # or why it failed)
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
        self._keys_out: dict[int, tuple[Synth, int]] = {}  # the keys' notes sounding on another device: its, channel
        self._song_audio: SongAudio | None = None  # the song's own audio, playing with it
        self._audio_token = 0  # (each start or stop: a song's decoded after, not played)
        self._audio_kept: tuple[str, Any, dict[str, Any] | None, int, str | None] | None = None  # the last song's
        # audio decoded: its id, samples, parts (if they're made), rate, and whose parts they are
        self._splitting: dict[str, dict[str, Any]] = {}  # songs' parts being made (or failed), by song
        self._split_queue: deque[tuple[str, ArrangementRef, str]] = deque()  # songs waiting to be split (and by
        # which separator), one at a time
        self._split_running = False  # (a thread splitting them)
        self._audio_error: str | None = None
        self._audio_stream = audio_stream  # (sounddevice's OutputStream; tests: their own)
        # Played by a thread of their own: the remote's MIDI thread only hands them on (CoreMIDI can deadlock if
        # it waits, or opens ports); [] lets go of them all, None ends it
        self._keys_queue: queue.Queue[list[int] | None] = queue.Queue()
        self._held: dict[int, int] = {}  # the keys held down (the remote's, the page's clicks): each one's velocity
        self._held_version = 0  # (one more each time they change)
        self._held_changed = threading.Condition()
        self._keys_thread: threading.Thread | None = None
        self._replays: dict[str, Callable[[], None]] = {}  # the parts to play again once a remote's changes pause
        self._replay_timer: threading.Timer | None = None
        self._made = PlanCache()  # (the parts' lights and tracks as made: a change remakes just the part changed)
        self._theories: dict[tuple[str, str | None], dict[str, Any]] = {}  # songs' theory (theory()), once worked out
        self._fit_before: dict[str, Any] = {}  # songs a remote's turning: their choices last checked to fit (None: none)
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
        self._warmup: Warmup | None = None  # the warm-up going (or done, until it's closed)
        self._warmup_song: str | None = None  # the song whose scale it has
        self._warmup_run = 0  # (each one started: a new number, for the page)
        self._warmup_hearing: OnsetListener | None = None  # (your playing, heard as it goes)
        self._playing: dict[str, Any] | None = None  # the song being played: id, title, parts
        self._loaded: OrderedDict[str, list[tuple[ArrangementRef, Arrangement]]] = OrderedDict()
        self._lyric_lines: OrderedDict[str, tuple[Line, ...]] = OrderedDict()  # songs' lyrics, the last few
        self._other = (0.0, False)
        self.settings: dict[str, Any] = {"midi_port": DEFAULT_PORT, "channel": DEFAULT_CHANNEL,
                                         "lights_early_ms": LIGHTS_EARLY * 1000, "fastest_flash_ms": MIN_PERIOD * 1000,
                                         "launchkey": True, "keys_sound": True, "keys_lights": True, "ring": True,
                                         "overlap_ms": 120, "listen": False, "listen_device": GUITAR_INPUT, "listen_channel": 1,
                                         "tone_changes": True, "tone_map": dict(TONE_KINDS), "techniques": True,
                                         "outputs": {}, "outputs_known": {}, "beat_lights": False,
                                         "keys_tone": KEYS_TONE, "keys_volume": NORMAL_VOLUME, "keys_output": None,
                                         "volumes": [NORMAL_VOLUME] * SLOTS, "pedal_backup": "",
                                         "pedal_favourites": list(PEDAL_FAVOURITES), "pinch": asdict(Squeal()),
                                         "drum_set": DRUM_SETS[0][1],  # (the keyboard's drum kit: its program)
                                         "warmup_speeds": {},  # (each warm-up drill's speed, as last left)
                                         "warmup_shown": True,  # (each drill shown first, on the keyboard)
                                         "warmup_ladder": True,  # (faster as you get it right: heard)
                                         "warmup_what": "routine",  # (the warm-up's drills, or one on its own)
                                         "warmup_tuning": warmup.TUNINGS[0][0],
                                         "warmup_scale": {"kind": "pentatonic", "tonic": None, "start": None},
                                         "beta": {name: False for name in BETAS},  # (beta features: each on or off)
                                         "pedal_input": None,  # (where the pedal's sound comes back in: {"device",
                                         # "channel"}, for its meter and the tone check)
                                         "split_server": None}  # (another computer splitting songs with
        # BS-RoFormer SW, tools/rs-keylights-split-server.py: {"url", "key"})
        self._server_seen: tuple[float, dict[str, Any]] | None = None  # (it, as last asked: when, and what it said)
        self._server_asking = False
        self._warmup_log_lock = threading.Lock()  # (the progress log: a drill's line at a time)
        self.follow_settings = default_follow()  # the same for every page showing them, phone or Mac
        self.follow_version = 0  # changes with them, so pages know to show them again
        self._follow_saved = False
        # Playing songs: the options, the song picked, and each song's parts, lit keys, tones and start
        self.play_options: dict[str, Any] = {"speed": 1.0, "real_pitch": True, "silent": False, "song": None,
                                             "audio": False, "audio_volume": AUDIO_VOLUME, "audio_device": None,
                                             "stem_volumes": {stem: 100 for stem in stems.ALL},
                                             "stem_mutes": {stem: False for stem in stems.ALL},
                                             "separator": stems.ROFORMER}  # (real pitch: the notes as they sound on
        # the guitar, at first)
        self.song_settings: dict[str, dict[str, Any]] = {}
        self.play_version = 0
        self._play_saved = False
        self.songs_file = self.settings_file.with_name("songs.json")
        self._by_key: dict[str, str] | None = None  # (song key -> id, for the keys only one song has)
        self._making: dict[str, dict[str, Any]] = {}  # drum parts being made (or that failed), by song: step, error
        # The game's song list sorted by artist, as last seen following (song keys in its order): where songs with the
        # same sort names (two copies of a song) go, as the game has them in the order it loaded them, not by a name.
        # Taken as the page starts (the library keeps its order while it runs: the Launchkey browses it by place)
        self.game_order_file = self.settings_file.with_name("game-order.json")
        try:
            order = json.loads(self.game_order_file.read_text())
        except (OSError, ValueError):
            order = []
        self._game_order_seen: list[str] = [k for k in order if isinstance(k, str)] if isinstance(order, list) else []
        self._game_order = {key: i for i, key in enumerate(self._game_order_seen)}
        self._lan_key: str | None = None
        try:
            songs = json.loads(self.songs_file.read_text())
        except (OSError, ValueError):
            songs = None
        moved = False  # (kept as whole paths, by an earlier version: kept again by their place in the game folder)
        if isinstance(songs, dict):
            for kept, value in songs.items():
                if isinstance(value, dict):
                    sid = self._found_id(kept)
                    moved |= kept_id(sid, index.game_dir) != kept
                    self.song_settings.setdefault(sid, value)
        try:
            saved = json.loads(self.settings_file.read_text())
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict):
            if isinstance(saved.get("play"), dict):
                try:
                    self.play_options, self._play_saved = _changed_options(self.play_options, saved["play"]), True
                    self.play_options["audio"] = False  # (the song's own audio: off as it starts, always)
                except GuiError:
                    pass
                kept = self.play_options["song"]
                if kept:
                    self.play_options["song"] = self._found_id(kept)
                    moved |= kept_id(self.play_options["song"], index.game_dir) != kept
            self.settings.update({k: saved[k] for k in self.settings if k in saved})
            if not _split_server_ok(self.settings["split_server"]):
                self.settings["split_server"] = None
            try:
                self.settings["beta"] = _betas({}, self.settings["beta"])
            except GuiError:
                self.settings["beta"] = {name: False for name in BETAS}
            try:
                self.settings["pedal_input"] = _pedal_input(self.settings["pedal_input"])
            except GuiError:
                self.settings["pedal_input"] = None
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
                self.settings["keys_output"] = _keys_output(self.settings["keys_output"])
            except (GuiError, TypeError, ValueError, AttributeError):
                self.settings["keys_output"] = None
            if self.settings["drum_set"] not in dict(DRUM_SETS).values():
                self.settings["drum_set"] = DRUM_SETS[0][1]
            self.settings["warmup_speeds"] = _warmup_speeds(self.settings["warmup_speeds"])
            self.settings["warmup_shown"] = self.settings["warmup_shown"] is not False
            self.settings["warmup_ladder"] = self.settings["warmup_ladder"] is not False
            self.settings.update(_warmup_choices(self.settings))
            try:
                self.settings["pinch"] = asdict(squeal_from(Squeal(), self.settings["pinch"]))
            except (ValueError, TypeError, AttributeError):
                self.settings["pinch"] = asdict(Squeal())
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
        if moved:
            self._save_songs()
            if self._play_saved:
                self._save()

    def _found_id(self, kept: str) -> str:
        """found_id in the index's game folder (the songs by key worked out once, if a song needs them)."""
        return found_id(kept, self.index.game_dir, self._songs_by_key)

    def _songs_by_key(self) -> dict[str, str]:
        if self._by_key is None:
            ids: dict[str, set[str]] = {}
            for ref in self.index.arrangements():
                ids.setdefault(ref.song_key, set()).add(song_id(ref))
            self._by_key = {key: next(iter(found)) for key, found in ids.items() if len(found) == 1}
        return self._by_key

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
                "sort": named.sort_key,
                "letter": _initial(named.artist_sort or named.artist),
                "file": Path(named.archive).name,
                "parts": [r.name or r.part for r in sorted(refs, key=part_order)],
                "album": named.album,
                "year": named.year,
                "length": max(r.length for r in refs),
                "tempo": round(named.tempo),
                "tuning": tuning_name(guitar.tuning, guitar.capo),
            })
        last = len(self._game_order)  # (as the game's song list, sorted by artist)
        out.sort(key=lambda s: (s["sort"], self._game_order.get(s["id"].rsplit("#", 1)[-1], last), s["file"]))
        library = {"game_dir": str(self.index.game_dir), "songs": out}
        with self._lock:
            self._library = library
        settle()
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
            "drums": self.drums_info(sid),
            "stems": self.stems_info(sid),
            "melody": self.melody_info(sid),
            "audio_export": drums.can_decode(),
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
        """An arrangement's notes and chord shapes where they're played, for the page's fretboard and tab: each
        note's start, end and strings with their frets, and each chord's start, end, name and shape (a fret a
        string, -1 not played); each note's techniques, the fret-hand mutes and the bars; the one asked for (its
        part, or its persistent ID), else the song's first guitar part; with the guitar it's played on."""
        loaded = self._load(sid)
        ref, a = next(((r, a) for r, a in loaded if part in (r.part, r.persistent_id)), None) or \
            next(((r, a) for r, a in loaded if not r.bass), loaded[0])
        bars = list(a.bars) or list(a.beats[::4])  # (a chart without bars: every 4th beat)
        return {**(_guitar([(ref, a)]) or {}), "part": ref.part, "arrangement": ref.persistent_id,
                "name": ref.name or ref.part,
                "notes": [[round(n.time, 3), round(n.time + n.sustain, 3), [[s, f] for s, f in zip(n.strings, n.frets)]]
                          for n in a.notes],
                "chords": [[round(c.start, 3), round(c.end, 3), c.name, list(c.frets)] for c in a.chords],
                # For its tab: each note's techniques (the chart's mask, the semitones it slides, as it's slid off
                # too, and the most it bends), as the notes are; its fret-hand mutes; when each bar starts
                "techniques": [[n.mask, n.slide or n.slide_off, max((up for _at, up in n.bends), default=0.0)]
                               for n in a.notes],
                "muted": [[round(n.time, 3), [[s, f] for s, f in zip(n.strings, n.frets)]] for n in a.muted],
                "bars": [round(t, 3) for t in bars], "length": round(a.length, 3)}

    def theory(self, sid: str, part: str | None = None) -> dict[str, Any]:
        """A song's theory, for the page's theory card (theory.py): its key; its chords as numbers in it (the
        guitar part with the most chords': the harmony), each section's progression and riffs' scale, and why a
        part's notes work (the one asked for, by its part or persistent ID; else the lead, or the first guitar
        part)."""
        cached = self._theories.get((sid, part))
        if cached is not None:
            return cached
        loaded = self._load(sid)
        info = self.song_key(sid)
        if info is None:
            return {"key": None}
        guitars = [(r, a) for r, a in loaded if not r.bass and a.notes]
        harmony = max(guitars or loaded, key=lambda ra: len(ra[1].chords))
        found = Key(info["tonic"], info["mode"])
        other = theory.key_named(info.get("alternative") or "")
        key = theory.chord_key([found, other] if other else [found], harmony[1], harmony[0].bass)  # (the chords say)
        melody = next(((r, a) for r, a in loaded if part in (r.part, r.persistent_id)), None) or \
            next(((r, a) for r, a in guitars if kind(r) == "lead"), None) or (guitars or loaded)[0]
        bass = next((a for r, a in loaded if r.bass and a.notes), None)
        chords = theory.chords_of(key, harmony[1], harmony[0].bass)
        raw = next((a.sections for _r, a in loaded if a.sections), ())
        out = {
            "key": {"name": key.name, "tonic": key.tonic, "mode": key.mode, "notes": list(key.notes)},
            "harmony": harmony[0].name or harmony[0].part,
            "part": melody[0].part, "parts": [{"part": r.part, "name": r.name or r.part} for r, _a in guitars],
            "chords": [[round(c.start, 3), round(c.end, 3), c.name, c.numeral, c.function, c.tag] for c in chords],
            "sections": theory.sections_of(key, raw, _sections(raw), chords, [a for _r, a in loaded]),
            "notes": theory.notes_why(key, melody[1], chords, bass),
        }
        self._theories[(sid, part)] = out
        return out

    def roll(self, sid: str, part: str | None = None) -> dict[str, Any]:
        """What the keyboard plays of a song, for the page's keys card (its notes falling onto the keys): the parts
        playing as planned (following this song, the game's arrangement's plan; playing it, the song's; else its
        parts as chosen: what plays takes them a moment later), or one arrangement alone (its part, or its persistent ID) as it would light 4 keys. Each
        note on a key (moved into the keys' range, as its lights and sound are): its start, its end (as it rings),
        its key, its part (an index of parts) and whether it lights (1) or only sounds (0)."""
        loaded = self._load(sid)
        with self._lock:
            session = self._session
        planned: list[PlannedPart] | None = None
        pooled = True  # (the lit parts share the 4 keys)
        if part:
            one = next(((r, a) for r, a in loaded if part in (r.part, r.persistent_id)), None)
            if one is None:
                raise GuiError(f"No part {part} in this song")
            planned, pooled = plan([Part(one[0], one[1], 4)], None, pooled=False), False
        elif isinstance(session, Following) and session.running:  # (as chosen now: what plays takes the choices a
            # moment later, a remote's taps once they stop)
            followed = session.follower.plan_now()
            if followed and song_id(followed[0].part.ref) == sid:
                planned = [pp for pp in followed if not pp.part.muted or pp.part.lights]  # (what's heard or lit:
                # a part muted that lights its keys still lights them)
        if planned is None:  # (playing it, or not: its parts as chosen now)
            parts = [Part(ref, a, chosen["lights"], muted=not chosen["play"])
                     for (ref, a), chosen in zip(loaded, self._song_parts(sid, loaded)) if chosen["play"] or
                     chosen["lights"]]  # (muted, lit: its lit notes alone)
            planned = plan(parts, None, pooled=pooled)
        keys = KeyRange()

        def fold(key: int) -> int:  # (into the keys' range, an octave at a time)
            while key < keys.lowest:
                key += 12
            while key > keys.highest:
                key -= 12
            return key

        notes: list[list[float | int]] = []
        for i, pp in enumerate(planned):
            for light in pp.lights:
                if light.extra:
                    continue
                lit = set(light.keys) if pp.part.lights else set()
                ends: dict[int, float] = {key: light.off for key in light.keys}
                for j, pitch in enumerate(light.pitches):
                    key = fold(pitch + pp.shift)
                    ring = light.rings[j] if j < len(light.rings) else light.off
                    ends[key] = max(ends.get(key, light.off), ring if key not in lit else light.off)
                for key, end in sorted(ends.items()):
                    if pp.part.muted and key not in lit:
                        continue  # (muted: not heard, just lit)
                    notes.append([round(light.on, 3), round(max(end, light.on + 0.05), 3), key, i, int(key in lit)])
        notes.sort(key=lambda n: (n[0], n[2]))
        return {"parts": [{"part": pp.part.ref.part, "name": pp.part.ref.name or pp.part.ref.part,
                           "kind": kind(pp.part.ref), "lights": pp.part.lights, "priority": pp.priority,
                           "muted": pp.part.muted} for pp in planned],
                "notes": notes, "lowest": keys.lowest, "highest": keys.highest, "most_lit": keys.max_lit,
                "pooled": pooled and len(planned) > 1}

    # A song's drum part (drums.py): made from its audio, kept, and played along with its arrangements

    def _drum_settings(self, sid: str) -> dict[str, Any]:
        own = self.song_settings.get(sid, {}).get("drums", {})
        return {"play": bool(own.get("play", False)), "volume": int(own.get("volume", DRUMS_VOLUME))}

    def drums_info(self, sid: str) -> dict[str, Any]:
        """A song's drum part, for the page: whether it's made (its hits), played and how loud, being made (the
        step) or failed (why), and whether the tools to make one are there; the keyboard's drum kit and its kits,
        the device the drums play on instead (none: the keyboard, its kit), and whether a remote's lights pulse to
        the beat (lights: its drums' lights pad's, every song's)."""
        ref = self._load(sid)[0][0]
        hits = drums.kept(ref)
        making = self._making.get(sid, {})
        output = self.settings["outputs"].get("drums")
        elsewhere = output["port"] if output and self._synth(output["port"]) is not None else None
        return {**self._drum_settings(sid), "made": hits is not None, "hits": len(hits) if hits else 0,
                "making": making.get("step"), "progress": making.get("progress") if making.get("step") else None,
                "error": making.get("error"), "tools": drums.setup_done(), "kit": int(self.settings["drum_set"]),
                "drum_sets": [[program, name.title()] for name, program in DRUM_SETS], "device": elsewhere,
                "lights": bool(self.settings["beat_lights"])}

    def _with_drums(self, sid: str, arrangements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A remote's arrangements of a song, with its drum part as one more: after them, in DRUMS_PLACE_MOST at
        most (its mute and its volume are the drum part's own; no lit keys or tones). Not made yet, it's there to be
        made (made: whether it is; making: while it's being made), if the tools to make one are."""
        try:
            made = drums.kept(self._load(sid)[0][0]) is not None
        except (GuiError, OSError, ValueError):
            return arrangements
        if not made and not drums.setup_done():
            return arrangements
        chosen = self._drum_settings(sid)
        output = self.settings["outputs"].get("drums")
        elsewhere = output["port"] if output and self._synth(output["port"]) is not None else None
        entry = {"id": "drums", "name": "Drums", "kind": "drums", "playing": False, "play": made and chosen["play"],
                 "drum_set": None if elsewhere else self.settings["drum_set"], "drums_on": elsewhere,
                 "lights": 0, "tones": {}, "chords": (), "tone_changes": (), "notes": (), "output": None,
                 "volume": chosen["volume"], "made": made, "making": bool(self._making.get(sid, {}).get("step")),
                 "progress": self._making.get(sid, {}).get("progress") if self._making.get(sid, {}).get("step") else None,
                 "error": self._making.get(sid, {}).get("error")}
        place = min(len(arrangements), DRUMS_PLACE_MOST)
        return [*arrangements[:place], entry, *arrangements[place:]]

    def _drum_hits(self, sid: str) -> list[drums.Hit] | None:
        """A song's drum part, if it's made: on its chart (made before that was: aligned now, and kept so)."""
        loaded = self._load(sid)
        beats = next((a.beats for _r, a in loaded if a.beats), ())
        return drums.kept(loaded[0][0], beats=beats)

    def _drum_tracks(self, sid: str, muted_too: bool = False) -> list[Track]:
        """A song's drum part as a track, if it's made and played (or muted, muted_too: a player mutes it) and
        there's sound: on the keyboard's drum channel, or the device chosen for drums (outputs), its notes mapped."""
        chosen = self._drum_settings(sid)
        if self.play_options["silent"] or not (chosen["play"] or muted_too):
            return []
        try:
            hits = self._drum_hits(sid)
        except (GuiError, OSError, ValueError):
            return []
        if not hits:
            return []
        output = self.settings["outputs"].get("drums")
        synth = self._synth(output["port"]) if output else None
        voicing = _drum_voicing(output if output and synth else None)
        voices = DrumVoices(channel=output["channel"] if output and synth else drums.DRUM_CHANNEL,
                            volume=chosen["volume"], kit=None if synth else self.settings["drum_set"], **voicing)
        return self._made.drums(hits, voices, synth)

    def set_drums(self, body: dict[str, Any]) -> dict[str, Any]:
        """A song's drum part played or not ("play"), how loud ("volume", 0 to 127), or made ("make": again, if it
        is), in the background (a minute or so); what plays goes on with it at once. Or the keyboard's drum kit
        ("kit": its program; every song's), or a remote's lights pulsing to the beat ("lights": its drums' lights
        pad's; every song's)."""
        sid = str(body.get("song", ""))
        loaded = self._load(sid)
        if "lights" in body and bool(body["lights"]) != bool(self.settings["beat_lights"]):
            self.toggle_beat_lights()
            self.play_version += 1  # (every page shows it, as the Launchkey's pad does)
        if "kit" in body:
            kit = int(_number(body["kit"], "drum kit", 0, 127))
            if kit not in dict(DRUM_SETS).values():
                raise GuiError(f"No drum kit {kit}")
            with self._lock:
                changed = kit != self.settings["drum_set"]
                self.settings["drum_set"] = kit
                if changed:
                    self.play_version += 1  # (every page shows it: the Launchkey's knob, the page's)
            if changed:
                self._save()
                self._drums_on(sid)
        if body.get("make"):
            self._make_drums(sid, loaded)
        with self._lock:
            kept = dict(self.song_settings.get(sid, {}))
            own = dict(kept.get("drums", {}))
            if "play" in body:
                own["play"] = bool(body["play"])
            if "volume" in body:
                own["volume"] = int(_number(body["volume"], "volume", 0, 127))
            was = kept.get("drums", {})
            if own != was:
                self.song_settings[sid] = {**kept, "drums": own}
                self._save_songs()
                self.play_version += 1
        if own.get("play") != was.get("play"):  # (played or not: what plays goes on with it, or without)
            self._drums_on(sid)
        elif own.get("volume") != was.get("volume"):  # (its volume: at once, on its channel, as the parts' are)
            self._drums_volume_now(sid)
        return self.drums_info(sid)

    def drum_part(self, sid: str) -> dict[str, Any]:
        """A song's drum part for the page's drums card: its hits ([seconds, General MIDI note, velocity], none if it
        isn't made), and where it plays: the keyboard's drum channel, or the device chosen for drums (its port, channel
        and its notes for each drum)."""
        hits = self._drum_hits(sid) or []
        chosen = self.settings["outputs"].get("drums")
        there = chosen is not None and self._synth(chosen["port"]) is not None
        voicing = _drum_voicing(chosen if chosen and there else None)
        mapped = voicing["notes"]
        drum_of = {note: drum for drum, note in CIRCUIT_NOTES.items()}
        shared = {str(n): [drum_of[p] for p in voicing["pool"]] for n in voicing["shared"]}  # (a Circuit's drums
        # each shared drum goes on, hit by hit)
        return {"made": bool(hits), "hits": [[round(h.time, 3), h.note, h.velocity] for h in hits],
                "device": chosen["port"] if chosen and there else None,
                "channel": chosen["channel"] if chosen and there else drums.DRUM_CHANNEL,
                "notes": {str(n): mapped.get(n, n) for n in drums.NAMES}, "names": {str(n): name for n, name in drums.NAMES.items()},
                "missing": chosen["port"] if chosen and not there else None,
                "output": chosen, "kit": _circuit_kit((chosen or {}).get("kit") or {}), "shared": shared,
                "drum_set": self.settings["drum_set"],
                "drum_sets": [[program, name.title()] for name, program in DRUM_SETS]}

    def hit_drum(self, body: dict[str, Any]) -> dict[str, Any]:
        """A drum struck (from the page's drums card: to hear it, and tune its sound): its General MIDI note
        ("note"), on the drums' keyboard channel or device as a drum part would play it, at a velocity (100)."""
        note = int(_number(body.get("note"), "drum", 0, 127))
        velocity = int(_number(body.get("velocity", 100), "velocity", 1, 127))
        chosen = self.settings["outputs"].get("drums")
        synth = self._synth(chosen["port"]) if chosen else None
        if synth is not None:
            voicing = _drum_voicing(chosen)
            notes, patches = voicing["notes"], voicing["patches"]
            played = notes.get(note, note)
            if played is None:
                return {"played": None}
            if note in patches:  # (its sound chosen on the device first)
                synth.control(chosen["channel"], *patches[note])
            synth.play(chosen["channel"], (played,), velocity)
            threading.Timer(DRUM_HIT_LENGTH, lambda: synth.stop(chosen["channel"], (played,))).start()
            return {"played": played}
        with self._keys_lock:
            keyboard = self._keys_keyboard(open_one=True)
            if keyboard is None:
                raise GuiError(f"No {self._keyboard_name} to play it on")
            keyboard.set_voice(drums.DRUM_CHANNEL, self.settings["drum_set"], DRUM_SET_BANK,
                               volume=None)  # (its kit: as set; its volume kept, a drum part's playing)
            keyboard.sound(drums.DRUM_CHANNEL, note, velocity)
        threading.Timer(DRUM_HIT_LENGTH, lambda: keyboard.sound(drums.DRUM_CHANNEL, note, 0)).start()
        return {"played": note}

    def _drums_volume_now(self, sid: str) -> None:
        """A song's drum part, playing or followed, at its volume now (its channel's, on its keyboard or device)."""
        with self._lock:
            session = self._session
        if not (isinstance(session, (Playback, Following)) and session.running):
            return
        if isinstance(session, Playback) and not (self._playing and self._playing["id"] == sid):
            return
        for track in self._drum_tracks(sid):
            if isinstance(track.voices, DrumVoices):
                sound: Any = track.sound or session.keyboard
                sound.set_volume(track.voices.channel, track.voices.volume)

    def _drums_on(self, sid: str) -> None:
        """What plays (this song, playing or followed) goes on with its drum part as it is now."""
        with self._lock:
            session = self._session
        if isinstance(session, Following) and session.running:
            self._follow_extras(session)
        elif self._playing and self._playing["id"] == sid and not self._mute_now(sid):  # (muted or back at once,
            self._play_on(sid)  # if it can be; else played again with it)

    def _make_drums(self, sid: str, loaded: list[tuple[ArrangementRef, Arrangement]],
                    pieces: dict[str, Path] | None = None, kit: Path | None = None) -> None:
        """A song's drum part made, on a thread of its own; once it's made, played (and what plays goes on with it).
        From the kit's pieces if they're given (their WAVs: a split server's, with the song's parts, in kit, which goes
        once it's made), else from the split server's if its drums are BS-RoFormer SW's (drums.make)."""
        with self._lock:
            if self._making.get(sid, {}).get("step"):
                if kit is not None:
                    shutil.rmtree(kit, ignore_errors=True)
                return  # (being made)
            self._making[sid] = {"step": "starting", "error": None, "progress": 0.0}
            self.play_version += 1  # (every page shows it's being made, however it was asked for)
        ref = loaded[0][0]
        beats = next((a.beats for _r, a in loaded if a.beats), ())

        def step(text: str) -> None:
            self._making[sid] = {**self._making.get(sid, {}), "step": text, "error": None}

        def far(done: float) -> None:  # (how far it is: the page's bar, and the Launchkey's screen)
            self._making[sid] = {**self._making.get(sid, {}), "progress": round(done, 3)}

        def run() -> None:
            try:
                drums.make(ref, beats, self.index.game_dir, say=step, progress=far, pieces=pieces,
                           server=self.settings["split_server"])
            except Exception as exc:  # (a tool missing, or failing: said on the page)
                self._making[sid] = {"step": None, "error": str(exc)}
                with self._lock:
                    self.play_version += 1
                return
            finally:
                if kit is not None:
                    shutil.rmtree(kit, ignore_errors=True)
            self._making.pop(sid, None)
            with self._lock:
                kept = dict(self.song_settings.get(sid, {}))
                if "play" not in kept.get("drums", {}):  # (made: played, unless it was turned off)
                    self.song_settings[sid] = {**kept, "drums": {**kept.get("drums", {}), "play": True}}
                    self._save_songs()
                self.play_version += 1
            self._drums_on(sid)

        threading.Thread(target=run, name="drums", daemon=True).start()

    def _song_parts(self, sid: str, loaded: list[tuple[ArrangementRef, Arrangement]]) -> list[dict[str, Any]]:
        """A song's parts as chosen to play: unless chosen otherwise, as default_lights has them."""
        saved = self.song_settings.get(sid, {}).get("parts", {})
        defaults = default_lights([kind(ref) for ref, _arrangement in loaded])
        out = []
        for i, (ref, _arrangement) in enumerate(loaded):
            own = saved.get(ref.part, {})
            out.append({"part": ref.part, "name": ref.name or ref.part, "kind": kind(ref),
                        "play": bool(own.get("play", defaults[i] > 0)), "lights": int(own.get("lights", defaults[i])),
                        "tones": {k: own[k] for k in PART_FIELDS if own.get(k) not in (None, "")}})
        return out

    def set_song(self, body: dict[str, Any], later: float = 0.0, answer: bool = True) -> dict[str, Any]:
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
                tones = _changed_tones({k: part[k] for k in PART_FIELDS if k in part}, fields)
                play = bool(fields["play"]) if "play" in fields else part["play"]
                lights = int(_number(fields["lights"], "lit keys", 0, 4)) if "lights" in fields else part["lights"]
                parts[name] = {"play": play, "lights": lights, **tones}
            on = [p for p in parts.values() if p["play"]]
            kept = dict(self.song_settings.get(sid, {}), parts=parts)
            if "start" in body:
                kept["start"] = _number(body["start"], "start", 0, 24 * 3600)
            was = self.song_settings.get(sid)
            self.song_settings[sid] = kept
            self.play_version += 1
            replays = any(field != "start" for field in body if field != "song")
            if change and "start" not in body and all(set(fields) <= {"play"} for fields in change.values()) and \
                    self._mute_now(sid):  # (parts muted or back, alone: at once, the rest going on untouched)
                self._save_songs()
                return self.song(sid) if answer else {}
            if change and "start" not in body and all(set(fields) <= {"lights"} for fields in change.values()) and \
                    self._relight_now(sid):  # (other lit keys, alone: at once, nothing sounding touched)
                self._save_songs()
                return self.song(sid) if answer else {}
            if replays and later > 0:  # (a remote's turns, coming quickly: each kept as it comes, and checked, saved
                # and played once they stop, as the last that fit if they don't; each step's planning would lag it)
                self._fit_before.setdefault(sid, was)
                self._replay("song", lambda: self._fitted_play_on(sid), later)
                return self.song(sid) if answer else {}
            if len(on) > 1:  # (the parts' sounds fit the keyboard's channels: else as it was)
                try:
                    self._plan(sid)
                except ValueError as exc:
                    if was is None:
                        del self.song_settings[sid]
                    else:
                        self.song_settings[sid] = was
                    raise GuiError(str(exc)) from exc
            self._save_songs()
            if replays:
                self._replay("song", lambda: self._play_on(sid), later)
        return self.song(sid) if answer else {}

    def _fitted_play_on(self, sid: str) -> None:
        """(Holding _lock.) A remote's turns stopped: the song's parts as they're chosen now if their sounds fit the
        keyboard's channels (else as they were last that did), kept, and the song goes on with them."""
        was = self._fit_before.pop(sid, None)
        try:
            self._plan(sid)
        except ValueError:
            if was is None:
                self.song_settings.pop(sid, None)
            else:
                self.song_settings[sid] = was
            self.play_version += 1
        self._save_songs()
        self._play_on(sid)

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
            sounding = [(self._slot(pp.part.ref), track.voices)
                        for pp, track in zip(self._planned_now, session.player.tracks)]
        elif isinstance(session, Following) and session.running:
            sounding = [(self._slot(ref), planned.voices)
                        for (ref, _channels), planned in zip(session.follower.sounding(), session.follower.planned)]
        else:
            return
        for place, voices in sounding:
            if place != slot or not isinstance(voices, Voices):
                continue
            if voices.output is not None:
                voices.output.set_volume(voices.main.channel, volume)
            else:
                for channel in {v.channel for v in voices.all()}:
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

    def toggle_beat_lights(self) -> bool:
        """A remote's lights pulsing to the song's beat (its drums' pad below: a metronome you see), on or off; kept."""
        with self._lock:
            on = not self.settings["beat_lights"]
            self.settings["beat_lights"] = self._kept["beat_lights"] = on
            self._save()
        return on

    def _beats_of(self, sid: str) -> dict[str, Any]:
        """A song's beats and the bars' first beats, as its chart has them (its first arrangement with them), for a
        remote's beat lights."""
        try:
            loaded = self._load(sid)
        except (GuiError, OSError, ValueError):
            return {"beats": (), "bars": ()}
        a = next((a for _r, a in loaded if a.beats), None)
        return {"beats": a.beats if a else (), "bars": a.bars if a else ()}

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

    def game_focus(self, body: dict[str, Any]) -> dict[str, Any]:
        """Following, on the game's song list: a song of the library ("song": its id) highlighted there (as a
        remote's moves are), the library's place on it; the song, as browse_move has it."""
        said = self.game_songs()
        if said is None:
            raise GuiError("Rocksmith's song list isn't showing (following, Learn a Song)")
        key = str(body.get("song", "")).split("#", 1)[-1]
        keys = self._game_song_keys(said.count)
        if key not in keys:
            raise GuiError("Rocksmith's song list hasn't this song (filtered out, or not in the game)")
        row = float(keys.index(key))
        moved = self._game_move(said, lambda _start, _keys: row)
        if moved is None:
            raise GuiError("Rocksmith didn't answer")
        return moved

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
            if len(keys) == count:
                self._keep_game_order(keys)
        return self._game_keys

    def _keep_game_order(self, keys: list[str]) -> None:
        """The game's list order kept for the next start, when it's sorted by artist (every song after the one before
        by their sort names; songs with the same ones in its own order) and it has changed."""
        if keys == self._game_order_seen:
            return
        named: dict[str, set[str]] = {}
        for song in self.library()["songs"]:
            named.setdefault(song["id"].rsplit("#", 1)[-1], set()).add(song["sort"])
        sorts = {key: next(iter(found)) for key, found in named.items() if len(found) == 1}
        # (A few out of order are copies of a song with the same key, or arrangement ids, the game showing one copy
        # and the library maybe another; sorted some other way in the game, by song or year, half of them are.)
        if sum(a in sorts and b in sorts and sorts[a] > sorts[b] for a, b in zip(keys, keys[1:])) * 100 > len(keys):
            return
        self._game_order_seen = list(keys)
        try:
            self.game_order_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.game_order_file.with_suffix(".tmp")
            temp.write_text(json.dumps(keys) + "\n")
            temp.replace(self.game_order_file)
        except OSError:
            pass

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
            letters_of = {song["id"].split("#", 1)[-1]: song["letter"] for song in self.library()["songs"]}

            def by_letter(start: float, keys: list[str]) -> float:
                letters = [letters_of.get(key) or _initial(key) for key in keys]
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
        initials = [song["letter"] for song in listed]
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
        return {**self.play_options, "saved": self._play_saved, "version": self.play_version,
                "audio_error": self._audio_error, "audio_there": drums.can_decode(), "audio_outputs": _audio_outputs()}

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
            audio = self._song_audio
            if audio is not None and "audio_volume" in changed:
                audio.volume = options["audio_volume"] / 100
            if audio is not None and changed & {"stem_volumes", "stem_mutes"} and len(audio.weights) > 1:  # (heard
                # at once; a song not split, the song alone: as it is)
                audio.weights.update(stems.mix_weights(_stem_weights(options, stems.ALL)))
            if changed & {"stem_volumes", "stem_mutes"} and self._game_parts is not None:  # (the game's song's, at once)
                self._game_parts_gains()
            if isinstance(session, Playback) and session.running and self._playing:
                if changed & {"audio", "audio_device", "separator"}:  # (another separator: its parts, if made)
                    self._audio_on(self._playing["id"])
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
        started = time.monotonic()
        planned, length = self._plan(sid)
        session.replace(player_for(planned, session.keyboard, *self._light_timing(), **self._drums_extra(sid),
                                  cache=self._made),
                        length)
        if self._trace is not None:  # (played again: everything sounding stopped and struck again)
            self._trace({"t": round(started, 3), "replayed": sid, "took": round(time.monotonic() - started, 4)})
        self._playing, self._planned_now = self._describe(sid, planned), planned

    def _drums_extra(self, sid: str) -> dict[str, Any]:
        """A player's drum part (player_for's extra), muted in it if it isn't played."""
        return {"extra": self._drum_tracks(sid, muted_too=True), "extra_muted": not self._drum_settings(sid)["play"]}

    def _relight_now(self, sid: str) -> bool:
        """The song playing's parts lighting the keys as they're chosen now, at once (Player.relight): what sounds left
        as it is. False if more than their lit keys changed in planning them again (another part, sound or mute);
        then it's played again with them."""
        with self._lock:
            session = self._session
            if not isinstance(session, Playback) or not session.running or not self._playing or \
                    self._playing["id"] != sid:
                return False
            planned, _length = self._plan(sid)
            was = self._planned_now
            if [pp.part.ref.part for pp in planned] != [pp.part.ref.part for pp in was] or \
                    any(new.voices != old.voices or new.part.muted != old.part.muted for new, old in zip(planned, was)):
                return False
            session.relight({pp.part.ref.part: self._made.track(pp) for pp in planned})
            self._playing, self._planned_now = self._describe(sid, planned), planned
            self.play_version += 1
            return True

    def _mute_now(self, sid: str) -> bool:
        """The song playing's parts (and its drum part) muted and unmuted as they're chosen now, at once: nothing
        else stopped or struck again (Player.mute). False if its player can't: a part played that it doesn't have
        (or has without the keyboard's channels), or a drum part made since; then it's played again with them."""
        with self._lock:
            session = self._session
            if not isinstance(session, Playback) or not session.running or not self._playing or \
                    self._playing["id"] != sid:
                return False
            by_name = {pp.part.ref.part: pp for pp in self._planned_now}
            silent = bool(self.play_options["silent"])
            muted = set()
            for chosen in self._song_parts(sid, self._load(sid)):
                pp = by_name.get(chosen["part"])
                if pp is None or pp.part.lights != chosen["lights"] or \
                        (chosen["play"] and not silent and pp.voices is None):
                    return False
                if not chosen["play"]:
                    muted.add(chosen["part"])
            tracks = self._drum_tracks(sid, muted_too=True)  # (the drum part: the same tracks, else made again since)
            have = [t for t, name in zip(session.player.tracks, session.player.names) if name == DRUMS]
            if len(have) != len(tracks) or any(a is not b for a, b in zip(have, tracks)):
                return False
            if not self._drum_settings(sid)["play"]:
                muted.add(DRUMS)
            started = time.monotonic()
            session.mute(muted)
            if self._trace is not None:
                self._trace({"t": round(started, 3), "muted": sorted(muted),
                             "took": round(time.monotonic() - started, 4)})
            planned = [replace(pp, part=replace(pp.part, muted=pp.part.ref.part in muted)) for pp in self._planned_now]
            self._playing, self._planned_now = self._describe(sid, planned), planned
            self.play_version += 1
            return True

    def _load(self, sid: str) -> list[tuple[ArrangementRef, Arrangement]]:
        with self._lock:
            if sid in self._loaded:
                self._loaded.move_to_end(sid)
                return self._loaded[sid]
        refs = sorted((r for r in self.index.arrangements() if song_id(r) == sid and self._playable(r)),
                      key=part_order)
        if not refs:
            raise GuiError("No such song (is its drive connected? try Reindex)")
        loaded = [(ref, self.arrangement_of(ref)) for ref in refs]
        with self._lock:
            self._loaded[sid] = loaded
            while len(self._loaded) > 8:
                self._loaded.popitem(last=False)
        settle()  # (kept a while: out of the full collections' way)
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
        pinch: dict[str | None, Tone | None] = {}
        mute: dict[str | None, Tone | None] = {}
        volume: dict[str, int] = {}
        fixed: set[str] = set()  # parts whose tone stays as chosen when the song's switches
        parts = []
        for slot, chosen in enumerate(self._song_parts(sid, loaded)):  # (muted ones too: they can come back at once)
            name = chosen["part"]
            ref, arrangement = by_part[name]
            parts.append(Part(ref, arrangement, chosen["lights"], muted=not chosen["play"]))
            own, defaults = chosen["tones"], self.follow_settings.get(kind(ref), KIND_DEFAULTS.get(kind(ref), {}))

            def pick(field: str) -> Any:  # (SAME_TONE: over a default)
                return own.get(field, defaults.get(field))

            main[name], mute[name] = _tone(pick("tone")), _tone(pick("mute_tone"))
            pinch[name] = _pinch(pick("pinch_tone"))
            volume[name] = self._volume(slot)
            if pick("follow_tones") is False:
                fixed.add(name)
        if not silent and any(not p.muted for p in parts):
            self._check_sound_channel()
        choice = None if silent else SoundChoice(main, pinch, mute, bool(self.play_options["real_pitch"]),
                                                 ring=bool(self.settings["ring"]), tone_map=self._tones_for_kinds(),
                                                 techniques=bool(self.settings["techniques"]),
                                                 overlap=self.settings["overlap_ms"] / 1000, squeal=self._squeal())

        def voices_for(ref: ArrangementRef, channels: tuple[int, int, int]) -> Voices:
            assert choice is not None
            voices = replace(choice.voices(ref, channels), volume=volume[ref.part])
            return self._routed(replace(voices, tone_map=()) if ref.part in fixed else voices, ref)

        planned = plan(parts, voices_for if choice else None,
                       cache=self._made)
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
                       "lights": pp.part.lights} for pp in planned if not pp.part.muted],
        }

    # A song's vocal melody (melody): its vocals' pitch tracked on the split server, played as one more of its parts

    def _vocals_ref(self, sid: str) -> ArrangementRef | None:
        refs = [r for r in self.index.arrangements() if song_id(r) == sid and r.part == "vocals"]
        return refs[0] if refs else None

    def melody_info(self, sid: str) -> dict[str, Any]:
        """A song's vocal melody, for the page: made (how many notes), being made (the step, how far) or failed
        (why), and whether it can be (a split server set)."""
        ref = self._load(sid)[0][0]
        notes = melody.kept(ref)
        making = self._melody_making.get(sid, {})
        return {"made": notes is not None, "notes": len(notes or []), "making": making.get("step"),
                "progress": making.get("progress") if making.get("step") else None, "error": making.get("error"),
                "can": self.settings["split_server"] is not None, "beta": self._beta("melody")}

    def set_melody(self, body: dict[str, Any]) -> dict[str, Any]:
        """A song's vocal melody made ("make": again, if it is), in the background (a minute or so: on the split
        server); once it's made it's one of the song's parts (its vocals), played or not as the others are."""
        sid = str(body.get("song", ""))
        loaded = self._load(sid)
        if body.get("make") and not self._beta("melody"):
            raise GuiError("The vocal melody is a beta feature: turn it on in the rs-keylights card")
        if body.get("make"):
            with self._lock:
                if self._melody_making.get(sid, {}).get("step"):
                    return self.melody_info(sid)
                self._melody_making[sid] = {"step": "starting", "progress": 0.0, "error": None}
                self.play_version += 1
            ref = next((r for r, _a in loaded if r.artist), loaded[0][0])
            beats = next((a.beats for _r, a in loaded if a.beats), ())

            def step(text: str) -> None:
                self._melody_making[sid] = {**self._melody_making.get(sid, {}), "step": text}

            def far(done: float) -> None:
                self._melody_making[sid] = {**self._melody_making.get(sid, {}), "progress": round(done, 3)}

            def run() -> None:
                try:
                    server = self.settings["split_server"]
                    if server is None:  # (said before anything's measured)
                        raise stems.StemsError("A vocal melody needs a split server (Split on another computer): "
                                               "RMVPE runs there")
                    try:  # (its chart's offset: the melody's times lined up with its parts)
                        offset = chartsync.offset_for(ref, beats, self.index.game_dir).offset
                        self._chart_offsets[sid] = offset
                    except Exception:  # (not measured: as it is)
                        offset = 0.0
                    melody.make(ref, self._vocals_ref(sid), self.index.game_dir, server, offset, progress=far,
                                say=step)
                    self._melody_making.pop(sid, None)
                except Exception as exc:  # (no split server, or an older one, or failing: said on the page)
                    self._melody_making[sid] = {"step": None, "progress": None, "error": str(exc)}
                self._melody_made(sid)

            threading.Thread(target=run, name="rs-keylights melody", daemon=True).start()
        return self.melody_info(sid)

    def _melody_made(self, sid: str) -> None:
        """A song's melody made (or not): its parts read again (its vocals among them), and what plays goes on with
        them."""
        with self._lock:
            self._loaded.pop(sid, None)
            self.play_version += 1
        if self._playing and self._playing["id"] == sid:
            try:
                self._play_on(sid)
            except Exception:  # (the song stopped meanwhile)
                pass

    def _melody_from_split(self, sid: str, ref: ArrangementRef, path: Path) -> None:
        """A BS-RoFormer SW split's melody (melody.json, the split server's) made into the song's vocal melody."""
        loaded = self._load(sid)
        beats = next((a.beats for _r, a in loaded if a.beats), ())
        try:
            try:
                offset = chartsync.offset_for(ref, beats, self.index.game_dir).offset
                self._chart_offsets[sid] = offset
            except Exception:  # (not measured: as it is)
                offset = 0.0
            melody.keep(ref, melody.notes_from_file(path, self._vocals_ref(sid), offset))
        except Exception as exc:  # (said on the page)
            self._melody_making[sid] = {"step": None, "progress": None, "error": str(exc)}
        self._melody_made(sid)

    def chart_offset(self, sid: str) -> float:
        """How far a song's audio is after its chart (seconds; 0: in time, or not known yet): measured in the
        background the first time it's asked for (chartsync: a second or two; kept), the page told once it is."""
        got = self._chart_offsets.get(sid)
        if got is not None:
            return got
        with self._lock:
            if sid in self._measuring or sid in self._chart_offsets:
                return self._chart_offsets.get(sid, 0.0)
            self._measuring.add(sid)

        def run() -> None:
            found = 0.0
            try:
                loaded = self._load(sid)
                ref = next((r for r, _a in loaded if r.artist), loaded[0][0])
                beats = next((a.beats for _r, a in loaded if a.beats), ())
                found = chartsync.offset_for(ref, beats, self.index.game_dir).offset
            except Exception:  # (no decoder, a song gone: in time, as it is)
                found = 0.0
            with self._lock:
                self._chart_offsets[sid] = found
                self._measuring.discard(sid)
                if found:
                    self.play_version += 1

        threading.Thread(target=run, name="rs-keylights chart offset", daemon=True).start()
        return 0.0

    def play(self, body: dict[str, Any]) -> dict[str, Any]:
        """Plays a song with its parts as chosen, from a start (or its own) at a speed (or the one chosen)."""
        sid = str(body.get("song", ""))
        self.chart_offset(sid)  # (measured meanwhile, if it isn't yet: its audio lined up with it once it is)
        planned, length = self._plan(sid)  # (every part muted, it plays on: they can be played again as it goes)
        start = _number(body.get("start", self.song_settings.get(sid, {}).get("start", 0)), "start", 0, 24 * 3600)
        speed = _number(body.get("speed", self.play_options["speed"]), "speed", 0.05, 4)
        with self._lock:
            self._end_idle_show()
            with self._keys_lock:  # a remote's keys wait, then play on its keyboard
                self._stop()
                keyboard = self._open_keyboard()
                try:
                    playback = Playback(player_for(planned, keyboard, *self._light_timing(),
                                                   **self._drums_extra(sid), cache=self._made),
                                        keyboard, length, start, speed)
                except Exception:
                    keyboard.close()
                    raise
                playback.set_loop(self._loop_for(sid))
                playback.start()
                self._session = playback
            self._playing, self._planned_now = self._describe(sid, planned), planned
            self.play_options["song"] = sid
            self._audio_on(sid)
        return self.state()

    # The song's own audio, played with it

    def _audio_on(self, sid: str) -> None:
        """The song's own audio played with it (if it's chosen), where it is; decoded first, on a thread of its own
        (a fraction of a second; until then, the song plays without it). Another song's: stopped."""
        self._audio_off()
        self._audio_error = None
        if not self.play_options["audio"]:
            return
        self._audio_token += 1
        token = self._audio_token

        def where(at: float) -> tuple[float | None, float]:  # (the sound device's thread: nothing waited for)
            session = self._session
            if not isinstance(session, Playback) or not session.running or not self._playing \
                    or self._playing["id"] != sid:
                return None, 1.0
            position, speed = session.where_at(at)  # (the chart's: the audio that far on from it, to line up)
            return (None if position is None else position + self._chart_offsets.get(sid, 0.0)), speed

        def run() -> None:
            try:
                loaded = self._load(sid)
                named = next((r for r, _a in loaded if r.artist), loaded[0][0])
                samples, parts, rate = self._song_samples(sid, named)
                weights = stems.mix_weights(_stem_weights(self.play_options, stems.ALL)) if parts else None
                audio = SongAudio({"song": samples, **parts} if parts else samples, rate, where,
                                  self.play_options["audio_volume"] / 100,
                                  stream_factory=self._audio_stream, weights=weights, search=samples)
                with self._lock:
                    if token != self._audio_token:
                        self._forget_audio_later()  # (another song, or none, since: kept a while, as if it played)
                        return
                    audio.start(self.play_options["audio_device"])
                    self._song_audio = audio
            except Exception as exc:  # (no decoder, no sound device: said on the page)
                self._audio_error = str(exc) or type(exc).__name__
                self.play_version += 1

        threading.Thread(target=run, name="rs-keylights song audio", daemon=True).start()

    def _song_samples(self, sid: str, ref: ArrangementRef) -> tuple[Any, dict[str, Any] | None, int]:
        """A song's audio, decoded, and its parts if they're made (the separator chosen's; not made, the other's):
        the last song's kept, played again, it's there."""
        which = self._parts_by(ref)
        names = self._part_names(ref, which)
        kept = self._audio_kept
        if kept is not None and kept[0] == sid and kept[4] == ((which, names) if which else None):
            return kept[1], kept[2], kept[3]
        self._audio_kept = None  # (its memory back first)
        samples, rate = song_samples(ref, self.index.game_dir)
        parts = stems.load(ref, samples, separator=which, parts=[n for n in names if n != "other"]) if which else None
        self._audio_kept = (sid, samples, parts, rate, (which, names) if parts is not None else None)
        return samples, parts, rate

    def _part_names(self, ref: ArrangementRef, which: str | None) -> tuple[str, ...]:
        """A split song's parts (stems.STEMS; its guitar's lead and rhythm apart, a beta feature, if they're on
        either side of its stereo: stems.WITH_SIDES)."""
        return stems.WITH_SIDES if which and self._beta("sides") and sides.made(ref, which) else stems.STEMS

    def _sides_info(self, sid: str, ref: ArrangementRef, which: str | None) -> dict[str, Any] | None:
        """Whether a split song's guitar is its lead and rhythm apart (a beta feature: none, if it's off), for the
        page: measured ("apart" or not, how far to each side), being measured (in the background, the first time:
        a few seconds), or not to be (no lead and rhythm charts)."""
        if not which or not self._beta("sides"):
            return None
        found = sides.kept(ref, which)
        if found is not None:
            return {"state": "apart" if found.apart else "together", "lead": found.lead, "rhythm": found.rhythm}
        guitars = self._guitar_charts(sid)
        if guitars is None:
            return {"state": "no charts"}
        with self._lock:
            if sid in self._sides_measuring:
                return {"state": self._sides_measuring[sid]}
            self._sides_measuring[sid] = "measuring"

        def run() -> None:
            try:
                sides.make(ref, which, guitars[0], guitars[1], self._chart_offsets.get(sid, 0.0))
                failed = False
            except Exception:  # (its parts gone meanwhile, say: one guitar, as it is)
                failed = True
            with self._lock:
                if failed:
                    self._sides_measuring[sid] = "failed"
                else:
                    self._sides_measuring.pop(sid, None)
                self.play_version += 1
                self._splits_done += 1  # (following it: its parts to the game's song again)
                again = self._playing is not None and self._playing["id"] == sid and self._song_audio is not None
            if again and self._beta("sides") and sides.made(ref, which):
                self._audio_on(sid)  # (its lead and rhythm, from now)

        threading.Thread(target=run, name="rs-keylights sides", daemon=True).start()
        return {"state": "measuring"}

    def _guitar_charts(self, sid: str) -> tuple[Arrangement, Arrangement] | None:
        """A song's lead and rhythm charts (the first of each), or None if it hasn't both."""
        loaded = self._load(sid)
        lead = next((a for r, a in loaded if r.part == "lead"), None)
        rhythm = next((a for r, a in loaded if r.part == "rhythm"), None)
        return (lead, rhythm) if lead is not None and rhythm is not None else None

    def _parts_by(self, ref: ArrangementRef) -> str | None:
        """Whose parts of a song play: the separator chosen's, else the other's (none made: None)."""
        split = stems.split_with(ref)
        chosen = self.play_options["separator"]
        return chosen if chosen in split else (split[0] if split else None)

    # The song's parts (stems), each at a volume of its own in its audio

    def stems_info(self, sid: str) -> dict[str, Any]:
        """A song's parts: made (by which separators, and whose play: "by"), being made (by which, how far; or waiting
        for another song's to be), failed (why), and whether the separator chosen can make them ("can"; each's:
        "can_by")."""
        ref = self._load(sid)[0][0]
        making = self._splitting.get(sid, {})
        split = stems.split_with(ref)
        chosen = self.play_options["separator"]
        can = {s: stems.can_make(separator=s) or (s == stems.ROFORMER and self.settings["split_server"] is not None)
               for s in stems.SEPARATORS}  # (BS-RoFormer SW: here, or on a split server)
        offset = self.chart_offset(sid)  # (measured, the first time: the page told once it is)
        which = self._parts_by(ref)
        return {"made": bool(split), "made_by": split, "by": which, "separator": chosen,
                "sides": self._sides_info(sid, ref, which),
                "chart_offset": round(offset * 1000) if offset else None,
                "making": bool(making.get("making")), "making_by": making.get("separator"),
                "progress": making.get("progress"), "waiting": bool(making.get("making") and making.get("waiting")),
                "error": making.get("error"), "can": can[chosen], "can_by": can,
                "names": list(self._part_names(ref, which)),
                "separators": [{"id": s, "name": stems.SEPARATOR_NAMES[s]} for s in stems.SEPARATORS],
                "server": self.split_server_info()}

    # A split server: BS-RoFormer SW on another computer's NVIDIA GPU (splitserver)

    def split_server_log(self, since: int = 0) -> dict[str, Any]:
        """What the split server's printed since line since, and what it's splitting now (splitserver.log), for the
        page's view of its output; or why not ("error": none set, not answering, or a copy from before it)."""
        server = self.settings["split_server"]
        if server is None:
            return {"lines": [], "next": since, "working": None, "error": "No split server set"}
        try:
            return {**splitserver.log(server["url"], server["key"], since), "error": None}
        except splitserver.Unreachable as exc:
            return {"lines": [], "next": since, "working": None, "error": f"It isn't answering ({exc})"}
        except splitserver.SplitServerError as exc:
            said = str(exc)
            return {"lines": [], "next": since, "working": None,
                    "error": "Its copy of the script is older: copy the new one to it to see its output here"
                    if said.startswith("404") or "No such thing" in said else said}

    def split_server_info(self) -> dict[str, Any] | None:
        """The split server set (None: none): its address, whether it answers ("ok": None while it's first asked),
        what it runs on and how many songs it has waiting, or why not. Asked again in the background once what it
        said is SERVER_SEEN_FOR old (a page asking never waits for it)."""
        server = self.settings["split_server"]
        if server is None:
            return None
        seen = self._server_seen
        if seen is None or seen[1].get("url") != server["url"] or time.monotonic() - seen[0] > SERVER_SEEN_FOR:
            with self._lock:
                start, self._server_asking = not self._server_asking, True
            if start:
                threading.Thread(target=self._ask_split_server, name="rs-keylights split server", daemon=True).start()
        if seen is None or seen[1].get("url") != server["url"]:
            return {"url": server["url"], "ok": None, "device": None, "waiting": 0, "error": None}
        return dict(seen[1])

    def _ask_split_server(self) -> dict[str, Any]:
        server = self.settings["split_server"]
        info: dict[str, Any] = {"url": server["url"] if server else None, "ok": False, "device": None, "waiting": 0,
                                "error": None}
        try:
            if server is not None:
                said = splitserver.status(server["url"], server["key"])
                info.update(ok=True, device=said.get("device"), waiting=int(said.get("waiting") or 0))
        except splitserver.SplitServerError as exc:
            info["error"] = str(exc)
        finally:
            self._server_asking = False
        seen, self._server_seen = self._server_seen, (time.monotonic(), info)
        if seen is None or {k: seen[1].get(k) for k in ("url", "ok", "device", "waiting")} != \
                {k: info.get(k) for k in ("url", "ok", "device", "waiting")}:
            with self._lock:
                self.play_version += 1  # (pages show it again)
        return info

    def set_split_server(self, body: dict[str, Any]) -> dict[str, Any]:
        """The split server to split songs with BS-RoFormer SW on ("url": its address, as it prints it, or just the
        computer's name; empty: none, here again; "key": the key it prints, kept if not given again), kept, and asked
        at once: what it said."""
        url = str(body.get("url") or "").strip()
        if not url:
            server = None
        else:
            if "://" not in url:
                url = "http://" + url
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise GuiError("Expected the split server's address, as it prints it (http://name.local:8042)")
            if parsed.port is None:
                url = f"{parsed.scheme}://{parsed.hostname}:{splitserver.PORT}"
            else:
                url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            kept = self.settings["split_server"] or {}
            key = str(body.get("key") or "").strip() or kept.get("key", "")
            if not key:
                raise GuiError("Expected its key too (it prints it as it starts)")
            server = {"url": url, "key": key}
        self.settings["split_server"] = self._kept["split_server"] = server
        self._save()
        with self._lock:
            self.play_version += 1
        if self.settings["split_server"] is None:
            self._server_seen = None
            return {"server": None}
        return {"server": self._ask_split_server()}

    def audio_info(self, sid: str | None) -> dict[str, Any]:
        """The song's own audio, for a remote (the Launchkey's stem mixer): on or off, its volume, its parts' volumes
        and which are muted, and whether the song (if there's one) is split into them (being split: how far; whether
        it can be)."""
        options = self.play_options
        split: dict[str, Any] = {}
        if sid:
            try:
                split = self.stems_info(sid)
            except (GuiError, OSError, ValueError):
                split = {}
        offset = self._chart_offsets.get(sid) if sid else None
        return {"on": bool(options["audio"]), "volume": int(options["audio_volume"]),
                "chart_offset": round(offset * 1000) if offset else None,
                "volumes": dict(options["stem_volumes"]), "mutes": dict(options["stem_mutes"]),
                "names": split.get("names") or list(stems.STEMS),
                "made": bool(split.get("made")), "making": bool(split.get("making")),
                "waiting": bool(split.get("waiting")), "progress": split.get("progress"), "can": bool(split.get("can")), "there": drums.can_decode()}

    def set_stems(self, body: dict[str, Any]) -> dict[str, Any]:
        """A song's parts made ("make"), by the separator chosen (or "separator"), in the background (Demucs: a minute
        or two; BS-RoFormer SW: about the song's length); played with its audio once they are. One song's split at a
        time (two at once would share the Mac's GPU or Neural Engine, and the cache's pruning): another asked for
        meanwhile waits its turn ("waiting")."""
        sid = str(body.get("song", ""))
        loaded = self._load(sid)
        if body.get("make"):
            separator = body.get("separator", self.play_options["separator"])
            if separator not in stems.SEPARATORS:
                raise GuiError(f"Expected a separator ({', '.join(stems.SEPARATORS)})")
            ref = next((r for r, _a in loaded if r.artist), loaded[0][0])
            with self._lock:
                if self._splitting.get(sid, {}).get("making"):
                    return self.stems_info(sid)
                self._splitting[sid] = {"making": True, "waiting": True, "progress": 0.0, "error": None,
                                        "separator": separator}
                self._split_queue.append((sid, ref, separator))
                start, self._split_running = not self._split_running, True
                self.play_version += 1
            if start:
                threading.Thread(target=self._split_songs, name="rs-keylights stems", daemon=True).start()
        return self.stems_info(sid)

    def _split_songs(self) -> None:
        """The songs waiting to be split, split in turn, until none are waiting."""
        while True:
            with self._lock:
                if not self._split_queue:
                    self._split_running = False
                    return
                sid, ref, separator = self._split_queue.popleft()
                self._splitting[sid] = {**self._splitting.get(sid, {}), "waiting": False}
                self.play_version += 1

            def far(done: float, sid: str = sid) -> None:
                self._splitting[sid] = {**self._splitting.get(sid, {}), "progress": round(done, 3)}

            kit = Path(tempfile.mkdtemp(prefix="rs-keylights-kit-")) if separator == stems.ROFORMER else None
            try:
                server = self.settings["split_server"] if separator == stems.ROFORMER else None
                pieces = stems.make(ref, self.index.game_dir, progress=far, separator=separator, server=server,
                                    kit_out=kit, melody=self._beta("melody"))
                self._splitting[sid] = {"making": False, "progress": None, "error": None}
                if kit is not None and "melody" in (pieces or []):  # (its vocals' melody too: made into its own)
                    self._melody_from_split(sid, ref, kit / "melody.json")
                pieces = [p for p in pieces or [] if p != "melody"]
                if kit is not None:  # (BS-RoFormer SW's: its drum part made again from them, from its kit's pieces
                    # too if the split server made them)
                    self._make_drums(sid, self._load(sid), {p: kit / f"{p}.wav" for p in pieces} or None, kit)
                    kit = None
            except Exception as exc:  # (the separator missing, or failing: said on the page)
                self._splitting[sid] = {"making": False, "progress": None, "error": str(exc)}
            finally:
                if kit is not None:
                    shutil.rmtree(kit, ignore_errors=True)
            with self._lock:
                self.play_version += 1
                self._splits_done += 1  # (following it: its parts to the game's song)
                if self._playing and self._playing["id"] == sid and self._song_audio is not None:
                    self._audio_on(sid)  # (its parts, at their volumes, from now)

    def _audio_off(self) -> None:
        self._audio_token += 1
        audio, self._song_audio = self._song_audio, None
        if audio is not None:
            audio.close()
        if self._audio_kept is not None:
            self._forget_audio_later()

    def _forget_audio_later(self) -> None:
        """The last song's audio, kept decoded (played again soon, it's there at once): its memory back in
        AUDIO_KEPT_FOR, if no audio's started or playing by then."""
        timer = threading.Timer(AUDIO_KEPT_FOR, self._audio_forgotten, (self._audio_token,))
        timer.daemon = True
        timer.start()

    def _audio_forgotten(self, token: int) -> None:
        with self._lock:
            if token == self._audio_token and self._song_audio is None:
                self._audio_kept = None

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

    # Warming up: drills with a click on the keyboard's drums, before playing (warmup)

    def warmup_info(self, sid: str | None = None) -> dict[str, Any]:
        """The warm-up going (or done): its drills, at their speeds, and where it is ("now"); else the drills it
        would have, with the song's scale (the one shown, if any) and the speeds they were last left at; and
        whether each is shown first."""
        with self._lock:
            session, song = self._warmup, self._warmup_song
        shown = bool(self.settings["warmup_shown"])
        if session is not None:
            drills, speeds, now = session.drills, dict(session.speeds), session.info()
        else:
            song = sid or None
            drills, speeds, now = self._warmup_drills(song), self.settings["warmup_speeds"], None
        speeds = {d.key: warmup.kept_speed(d, speeds.get(d.key)) for d in drills}
        settings = self.settings
        return {"drills": [warmup.drill_data(d, speeds[d.key]) for d in drills], "now": now, "shown": shown,
                "ladder": bool(settings["warmup_ladder"]),
                "seconds": None if settings["warmup_what"] != "routine" else
                round(sum(warmup.drill_seconds(d, speeds[d.key], shown) for d in drills)),
                "song": self._warmup_song_info(song),
                "what": settings["warmup_what"], "tuning": settings["warmup_tuning"], "scale": settings["warmup_scale"],
                "choices": {"what": [["routine", "The warm-up (all six)"]] +
                            [[d.key, d.name] for d in warmup.routine()],
                            "tunings": [name for name, _t in warmup.TUNINGS],
                            "scales": ["pentatonic", *warmup.SCALES]}}

    def _warmup_drills(self, song: str | None) -> tuple[warmup.Drill, ...]:
        """The drills chosen (the warm-up's, or one), in the tuning chosen, the scale's the song's or one chosen."""
        settings = self.settings
        return warmup.routine(self._warmup_key(song), warmup.tuning_named(settings["warmup_tuning"]),
                              settings["warmup_scale"], settings["warmup_what"])

    def set_warmup(self, body: dict[str, Any]) -> dict[str, Any]:
        """The warm-up started ("song": the one whose scale it has), stopped (or closed, done), paused or resumed,
        on to another drill ("go": its number), to your turn at the one shown ("your_turn"), or a step faster or
        slower ("step": 1 or -1; kept for that drill); and whether each drill's shown first ("shown"). Starting it
        stops what plays (or follows), and a song played stops it."""
        sid = str(body.get("song") or "") or None
        chosen = {f"warmup_{name}": body[name] for name in ("what", "tuning", "scale") if name in body}
        if chosen:
            kept = _warmup_choices({**self.settings, **chosen}, strict=True)
            with self._lock:
                self.settings.update(kept)
                self._kept.update(kept)
            self._save()
        for name, setting in (("shown", "warmup_shown"), ("ladder", "warmup_ladder")):
            if name in body:
                with self._lock:
                    self.settings[setting] = self._kept[setting] = bool(body[name])
                    if self._warmup is not None:
                        setattr(self._warmup, name, bool(body[name]))
                self._save()
        if body.get("start"):
            drills = self._warmup_drills(sid)
            lowest = warmup.tuning_named(self.settings["warmup_tuning"])[0]
            with self._lock:
                self._end_idle_show()
                with self._keys_lock:
                    self._stop()
                    keyboard = self._open_keyboard()
                    try:
                        session = Warmup(drills, keyboard, self.settings["warmup_speeds"],
                                         bool(self.settings["warmup_shown"]),
                                         ladder=bool(self.settings["warmup_ladder"]),
                                         hearing=lambda: self._warmup_heard()["on"], on_speed=self._warmup_sped,
                                         until_stopped=self.settings["warmup_what"] != "routine",
                                         on_drill=self._warmup_drilled)
                        hearing = self._onset_listener(session.heard, device=str(self.settings["listen_device"]),
                                                       channel=int(self.settings["listen_channel"]),
                                                       expected=session.expected,
                                                       lowest=0.85 * 440.0 * 2 ** ((lowest - 69) / 12))
                    except Exception:
                        keyboard.close()
                        raise
                    session.start()
                    hearing.start()
                    self._warmup, self._warmup_song, self._warmup_hearing = session, sid, hearing
                    self._warmup_run += 1
            return self.warmup_info(sid)
        with self._lock:
            session = self._warmup
            if session is None:
                pass
            elif body.get("stop"):
                with self._keys_lock:
                    self._warmup = None
                    session.stop()
                    self._stop_hearing()
            elif body.get("pause"):
                session.pause()
            elif body.get("resume"):
                session.resume()
            elif "go" in body:
                session.go(int(_number(body["go"], "drill", 0, len(session.drills) - 1)))
            elif body.get("your_turn"):
                session.your_turn()
            elif "step" in body:
                speed = session.change_speed(int(_number(body["step"], "step", -1, 1)))
                speeds = {**self.settings["warmup_speeds"], session.drill.key: speed}
                self.settings["warmup_speeds"] = self._kept["warmup_speeds"] = speeds
                self._save()
        return self.warmup_info(sid)

    def _warmup_sped(self, key: str, speed: int) -> None:
        """A drill's speed the ladder changed (from the warm-up's thread): kept, saved by a thread of its own (the
        warm-up's mustn't wait on the page's lock)."""
        self.settings["warmup_speeds"] = self._kept["warmup_speeds"] = {**self.settings["warmup_speeds"], key: speed}
        threading.Thread(target=self._save, name="rs-keylights save", daemon=True).start()

    def _warmup_drilled(self, result: dict[str, Any]) -> None:
        """A drill played (from the warm-up's thread): a line of the progress log (warmups.jsonl, beside the
        settings), with when."""
        line = json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **result})
        try:
            with self._warmup_log_lock:
                self.settings_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.settings_file.parent / WARMUP_LOG, "a", encoding="utf-8") as log:
                    log.write(line + "\n")
        except OSError:  # (a log: never in the way)
            pass

    def warmup_progress(self, days: int = PROGRESS_DAYS) -> dict[str, Any]:
        """The progress log, for the page: each drill's speed a day (the fastest it was left at that day; a scale's
        by its name and tuning), the last days of them, with how much of it was right and in time; the days in a
        row practised (to today, or yesterday), and today's minutes."""
        entries: list[dict[str, Any]] = []
        try:
            for line in (self.settings_file.parent / WARMUP_LOG).read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    if isinstance(entry, dict) and isinstance(entry.get("speed"), int) and isinstance(entry.get("at"), str):
                        entries.append(entry)
                except ValueError:
                    continue
        except OSError:
            pass
        today = date.today()
        first_day = today - timedelta(days=days - 1)
        groups: dict[str, dict[str, Any]] = {}
        practised: set[date] = set()
        today_seconds = 0.0
        for entry in entries:
            try:
                day = datetime.fromisoformat(entry["at"]).date()
            except ValueError:
                continue
            practised.add(day)
            if day == today:
                today_seconds += float(entry.get("seconds") or 0)
            scale = entry.get("drill") == "scale"
            key = f"scale|{entry.get('name')}|{entry.get('tuning')}" if scale else str(entry.get("drill"))
            group = groups.setdefault(key, {"key": key, "drill": entry.get("drill"), "name": entry.get("name"),
                                            "tuning": entry.get("tuning") if scale else None, "days": {},
                                            "first": entry["start"] if isinstance(entry.get("start"), int)
                                            else entry["speed"], "since": day.isoformat()})  # (where it began)
            if not scale:
                group["name"] = entry.get("name")  # (the latest)
            had = group["days"].setdefault(day.isoformat(), {"speed": 0, "notes": 0, "ok": 0})
            had["speed"] = max(had["speed"], entry["speed"])
            had["notes"] += int(entry.get("notes") or 0)
            had["ok"] += int(entry.get("ok") or 0)
        order = {key: i for i, key in enumerate(warmup.DRILL_KEYS)}
        rows = []
        for group in sorted(groups.values(), key=lambda g: (order.get(g["drill"], len(order)), g["key"])):
            shown = sorted(day for day in group["days"] if day >= first_day.isoformat())
            if not shown:
                continue
            points = [[day, group["days"][day]["speed"],
                       round(100 * group["days"][day]["ok"] / group["days"][day]["notes"])
                       if group["days"][day]["notes"] else None] for day in shown]
            rows.append({"key": group["key"], "name": group["name"], "tuning": group["tuning"], "points": points,
                         "speed": points[-1][1], "first": group["first"], "since": group["since"]})
        streak, day = 0, today if today in practised else today - timedelta(days=1)
        while day in practised:
            streak, day = streak + 1, day - timedelta(days=1)
        return {"rows": rows, "streak": streak, "today_minutes": round(today_seconds / 60, 1),
                "today": today.isoformat(), "days": days}

    def _warmup_heard(self) -> dict[str, Any]:
        """Whether your playing's heard: the guitar's input open (or why not), and how loud it is."""
        hearing = self._warmup_hearing
        if hearing is None:
            return {"on": False, "error": None, "level": 0.0}
        return {"on": bool(hearing.hearing), "error": hearing.error, "level": round(float(hearing.level), 4)}

    def _stop_hearing(self) -> None:
        hearing, self._warmup_hearing = self._warmup_hearing, None
        if hearing is not None:
            hearing.stop()

    def _warmup_key(self, sid: str | None) -> Key | None:
        """A song's key as its fingers play it (tuned down, say: as in E standard), for the warm-up's scale."""
        if not sid:
            return None
        try:
            info = self.song_key(sid)
            loaded = self._load(sid)
        except (GuiError, OSError, ValueError):
            return None
        if info is None:
            return None
        key = Key(info["tonic"], info["mode"])
        guitar = next((a for r, a in loaded if not r.bass), None)
        played = shapes(key, guitar) if guitar is not None else None
        return played[0] if played else key

    def _warmup_song_info(self, sid: str | None) -> dict[str, Any] | None:
        if not sid:
            return None
        try:
            loaded = self._load(sid)
        except (GuiError, OSError, ValueError):
            return None
        named = next((r for r, _a in loaded if r.artist), loaded[0][0])
        return {"id": sid, "title": named.title or named.song_key, "tempo": round(named.tempo) or None}

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
            was, self.follow_settings = self.follow_settings, settings
            voices_for = self._follow_voices()  # (the parts' sounds fit the keyboard's channels: else as it was)
            if voices_for is not None:
                for played_id in body.get("parts") or {}:
                    played = self.index.find(played_id)
                    refs = self._song_refs(played) if played is not None else []
                    needed = sum(channels_needed(voices_for(r, (0, 0, 0)))
                                 for r, play, _lights in parts_chosen(settings, played, refs) if play) if played else 0
                    if needed > len(KEYBOARD_CHANNELS):
                        self.follow_settings = was
                        raise GuiError(CHANNELS_USED_UP)
            self.follow_settings, self._follow_saved = settings, True
            self.follow_version += 1
            self._save()
            session = self._session
            if isinstance(session, Following) and session.running:
                session.set_offset(settings["offset_ms"] / 1000)
                fields = [set(each) for chosen in (body.get("parts") or {}).values() for each in chosen.values()] \
                    if set(body) == {"parts"} else []
                if not (fields and all(each <= {"play"} for each in fields)
                        and session.mute_parts(self._follow_parts())) and \
                        not (fields and all(each <= {"lights"} for each in fields)
                             and session.relight_parts(self._follow_parts())):  # (parts muted or back, or lighting
                    self._replay("follow", lambda: self._follow_on(session), later)  # other keys, alone: at once)
            if any(kind_ in body for kind_ in KINDS) and self._playing:  # a song playing takes new defaults
                self._play_on(self._playing["id"])
        return self.follow_info()

    def _follow_on(self, session: Following) -> None:
        """The arrangement played goes on with the parts and tones as chosen now."""
        if session is self._session and session.running:
            session.set_parts(self._follow_voices(), self._follow_parts())
            self._follow_extras(session)

    def _follow_extras(self, session: Following) -> None:
        """Following, the drum part of the song played: there if it's made (muted if it isn't played: it can come
        back at once)."""
        session.set_extras(lambda ref: self._drum_tracks(song_id(ref), muted_too=True),
                           lambda ref: not self._drum_settings(song_id(ref))["play"])

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
                                      parts_for=self._follow_parts(), trace=self._trace,
                                      game_time=self._riff_loop.time,
                                      song_offset=lambda ref: self.chart_offset(song_id(ref)), load=self.arrangement_of)
                self._follow_extras(following)  # (its song's drum part)
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
        Rocksmith is reached too); and the warm-up, while there's one (it takes the remote)."""
        info = self._remote_song_info()
        with self._lock:
            warming = self._warmup
            info["warmup"] = {**warming.info(), "hearing": self._warmup_heard()} if warming is not None else None
        return info

    def _remote_song_info(self) -> dict[str, Any]:
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
                changes = self._tone_changes(followed["id"], lambda ref: ref.persistent_id,
                                             lambda ref: self.follow_settings["arrangements"].get(ref.persistent_id, {}))
                every = self._tone_changes(followed["id"], lambda ref: ref.persistent_id, lambda ref: {})
                notes = self._notes(followed["id"], lambda ref: ref.persistent_id)
                followed = {**followed, "lyrics": self._lyrics(followed["id"]),
                            "arrangements": self._with_drums(followed["id"], [
                                {**a, "chords": chords.get(a["id"], ()), "tone_changes": changes.get(a["id"], ()),
                                 "tone_changes_known": bool(every.get(a["id"])),
                                 "notes": notes.get(a["id"], ()), "output": self._output_of(a["kind"])}
                                for a in followed["arrangements"]])}
            self._riff_sync(followed["id"] if followed else None)
            length = None
            if followed is not None:
                try:
                    length = max(a.length for _r, a in self._load(followed["id"]))
                except (GuiError, OSError, ValueError):
                    length = None
            state = self.game_state()  # (its song playing or paused: None, neither, or not known)
            said = self._game_said
            self._game_parts_follow(followed, said)
            self._game_mixer(followed, said)
            if said is not None and said.speed is not None:  # (Riff Repeater's speed as the game has it: its own
                # slider's moves too)
                self._riff_settings = {**self._riff_settings, "speed": round(said.speed)}
            if said is not None and said.accelerator is not None:  # (and its Accelerator: its menu's switch)
                self._riff_settings = {**self._riff_settings, "accelerator": said.accelerator}
            return {"mode": "follow", "sound": not self.play_options["silent"], "real_pitch": info["real_pitch"], **defaults,
                    "song": followed and {**followed, **self._beats_of(followed["id"])}, "position": None, "length": length,
                    "paused": state == "paused",
                    "state": state, "connected": connected, "game_screen": said.screen if said is not None else None,
                    "game_mixer_page": dict(self._game_mixer_page), "tone_field": dict(self._tone_field),
                    "loop": self.loop_info(followed["id"] if followed else None),
                    "speed": self._riff_settings["speed"] / 100, "riff": dict(self._riff_settings),
                    "music": self.game_music(), "music_muted": self.game_music_muted(),
                    "volumes": list(self.settings["volumes"]),
                    "game_stems": self.game_stems_info(),
                    "game_parts": self._game_parts_now(followed),
                    "audio": self.audio_info(followed["id"] if followed else None),
                    "keys": self.keys_info(), "listen": bool(self.settings["listen"]), "pedal": self.pedal_info(),
                    "beat_lights": bool(self.settings["beat_lights"])}
        self._game_parts_follow(None, None)  # (not following: none in the game's song)
        sid, song = playing or self.play_options.get("song"), None
        position = length = None
        if sid:
            try:
                details = self.song(sid)
            except (GuiError, OSError, ValueError):  # gone from the library, or its drive unplugged
                details = None
            if details is not None:
                chords = self._chords(sid, lambda ref: ref.part)
                changes = self._tone_changes(sid, lambda ref: ref.part,
                                             lambda ref: self.song_settings.get(sid, {}).get("parts", {}).get(ref.part, {}))
                every = self._tone_changes(sid, lambda ref: ref.part, lambda ref: {})
                notes = self._notes(sid, lambda ref: ref.part)
                song = {"id": sid, "title": details["title"], "key": details["key"], "played": None,
                        "sections": details["sections"], "lyrics": self._lyrics(sid), **self._beats_of(sid),
                        "arrangements": self._with_drums(sid, [
                            {"id": p["part"], "name": p["name"], "kind": p["kind"], "playing": False,
                             "play": p["play"], "lights": p["lights"], "tones": p["tones"],
                             "chords": chords.get(p["part"], ()), "tone_changes": changes.get(p["part"], ()),
                             "tone_changes_known": bool(every.get(p["part"])),
                             "notes": notes.get(p["part"], ()), "output": self._output_of(p["kind"])}
                            for p in details["parts"]])}
                position, length = details["start"], details["length"]
        paused = False
        session = self._session
        if playing and isinstance(session, Playback):
            position, length, paused = session.position, session.length, session.paused
        return {"mode": "play" if playing else "idle", "sound": not self.play_options["silent"],
                "real_pitch": self.play_options["real_pitch"], **defaults, "song": song, "position": position,
                "length": length, "paused": paused, "speed": self.play_options["speed"],
                "volumes": list(self.settings["volumes"]), "loop": self.loop_info(sid),
                "audio": self.audio_info(sid if song is not None else None),
                "keys": self.keys_info(), "listen": bool(self.settings["listen"]), "pedal": self.pedal_info(),
                    "beat_lights": bool(self.settings["beat_lights"])}

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

    def _tone_changes(self, sid: str, key: Callable[[ArrangementRef], str],
                      own: Callable[[ArrangementRef], dict[str, Any]]) -> dict[str, tuple[tuple[float, str], ...]]:
        """A song's arrangements' tone changes the keyboard follows, by their key (the part, or its persistent ID):
        from the start (-inf) the kind of tone it starts with ("its own tone" if that isn't known), then when it
        switches, and what to (a kind of tone, or "its own tone"); none without changes, or while they're not
        followed (at all, or by the arrangement: its own choices, own)."""
        tones = self._tones_for_kinds()
        if not tones:
            return {}
        try:
            loaded = self._load(sid)
        except (GuiError, OSError, ValueError):
            return {}
        out = {}
        for ref, a in loaded:
            if own(ref).get("follow_tones") is False:
                continue
            changes = [(at, kind or "its own tone") for at, kind in kind_changes(a, ref.bass)
                       if kind is None or kind in tones]
            if changes and a.base_tone is not None:
                out[key(ref)] = ((-math.inf, tone_kind(a.base_tone, ref.bass) or "its own tone"), *changes)
        return out

    def lyrics(self, sid: str) -> dict[str, Any]:
        """A song's lyrics, for the page's lyrics card: each line's start, end, text, and its syllables (each one's
        start, and where it is in the text: from, to); and the song's sections (name, start), to head the lines."""
        loaded = self._load(sid)
        raw = next((a.sections for _r, a in loaded if a.sections), ())
        return {
            "lines": [[round(line.start, 3), round(line.end, 3), line.text,
                       [[round(at, 3), begin, end] for at, begin, end in line.syllables]] for line in self._lyrics(sid)],
            "sections": [[s["name"], s["start"]] for s in _sections(raw)],
        }

    def plays_info(self, sid: str, which: str | None = None) -> dict[str, Any]:
        """A song's runs as the game counted them (plays.py), the newest first, and one of them (which: its id;
        else the newest) note by note: its counts, each section's hits and misses, and the notes missed (each one's
        time, section, what it is, and how often it was missed and hit: Riff Repeater's passes count each time)."""
        loaded = self._load(sid)
        by_name = {Path(r.sng).stem.lower(): (r, a) for r, a in loaded}
        runs = [r for r in runs_of(list(by_name)) if r.file is not None and (r.arrangement or "").lower() in by_name]

        def run_id(run: Any) -> str:
            return f"{run.file.parent.name}/{run.file.stem}"
        def part_of(run: Any) -> str:
            ref = by_name[(run.arrangement or "").lower()][0]
            return ref.name or ref.part
        listed = [{"id": run_id(r), "started": r.started, "part": part_of(r), "hits": r.counts[0], "misses": r.counts[1]}
                  for r in runs]
        run = next((r for r in runs if run_id(r) == which), runs[0] if runs else None)
        if run is None:
            return {"runs": [], "run": None}
        ref, a = by_name[(run.arrangement or "").lower()]
        judgement = judge(a.notes, run.changes)
        raw = a.sections or next((x.sections for _r, x in loaded if x.sections), ())
        sections = _sections(raw)
        starts = [x["start"] for x in sections]
        tally: dict[int, list[int]] = {}
        for judged in judgement.judged:
            tally.setdefault(judged.note, [0, 0])[0 if judged.hit else 1] += 1
        by_section = [[x["name"], x["start"], 0, 0] for x in sections]

        def section_of(at: float) -> int:
            return max(0, bisect_right(starts, at + 1e-3) - 1)
        for i, (hits, misses) in tally.items():
            if by_section:
                row = by_section[section_of(a.notes[i].time)]
                row[2] += hits
                row[3] += misses
        missed = [[round(a.notes[i].time, 3), sections[section_of(a.notes[i].time)]["name"] if sections else "",
                   _note_said(a, a.notes[i], ref.bass), misses, hits]
                  for i, (hits, misses) in sorted(tally.items(), key=lambda x: a.notes[x[0]].time) if misses]
        counted = judgement.hits + judgement.misses
        return {"runs": listed, "run": {
            "id": run_id(run), "part": ref.name or ref.part, "started": run.started, "hits": judgement.hits,
            "misses": judgement.misses, "accuracy": round(100 * judgement.hits / counted, 1) if counted else None,
            "passes": judgement.passes, "unmatched": judgement.unmatched,
            "sections": [row for row in by_section if row[2] or row[3]], "missed": missed}}

    def export_midi(self, sid: str) -> tuple[str, bytes]:
        """A song as a MIDI file, for a DAW (song_export): its name and the file."""
        loaded = self._load(sid)
        try:
            vocals = next((r for r in self.index.of_song(loaded[0][0]) if r.part == "vocals"), None)
            sung = vocals.load() if vocals is not None else None
        except (ArchiveError, OSError, ValueError):  # (without its vocals, then)
            sung = None
        return song_export(loaded, sung)

    def export_audio(self, sid: str) -> tuple[str, bytes]:
        """A song's audio as a WAV file, lined up with its MIDI file (the drum tools decode it: a few seconds)."""
        loaded = self._load(sid)
        named = next((r for r, _a in loaded if r.artist), loaded[0][0])
        with tempfile.TemporaryDirectory(prefix="rs-keylights-audio-") as work:
            out = Path(work) / "song.wav"
            try:
                drums.decode(named, self.index.game_dir, out)
            except drums.DrumsError as exc:
                raise GuiError(str(exc)) from exc
            return midiexport.file_name(named.title, ".wav"), out.read_bytes()

    def _lyrics(self, sid: str) -> tuple[Line, ...]:
        """A song's lyrics in lines, from its vocals part (none without one, or if it's gone)."""
        with self._lock:
            if sid in self._lyric_lines:
                self._lyric_lines.move_to_end(sid)
                return self._lyric_lines[sid]
        try:
            refs = self.index.of_song(self._load(sid)[0][0])  # (its song's: not every song's looked through)
            vocals = next((r for r in refs if r.part == "vocals"), None)
            lines = lyric_lines(vocals.load().lyrics) if vocals is not None else ()
        except (ArchiveError, GuiError, OSError, ValueError):
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

    def _game_mixer(self, followed: dict[str, Any] | None, said: GameLoop | None) -> None:
        """Following, RS-Keylights' MIXER and TONES in the game (the riffloop mod's menus, from Learn a Song's
        pause menu): while one shows, what's done there done here first (a slider moved, a row's mute turned over:
        Return on it, a tone stepped: Left/Right; the Launchkey's mixer and the page follow); then their rows sent as
        they change here (the song's arrangements, by their place, its drums, and the Launchkey keys')."""
        with self._game_mixer_lock:
            if said is not None and said.screen in GAME_MIXER_SCREENS and self._game_mixer_apply():
                return  # (the parts' state here as it was: the next look sends the rows as they are now)
            parts = (followed or {}).get("arrangements") or []
            arrangements = [a for a in parts if a.get("kind") != "drums"][:GAME_MIXER_SLIDERS]
            drum_part = next((a for a in parts if a.get("kind") == "drums"), None)
            drummed = drum_part if drum_part is not None and drum_part.get("made", True) else None
            self._game_mixer_drums = drum_part
            with self._lock:
                volumes, keys = list(self.settings["volumes"]), int(self.settings["keys_volume"])
                keys_sound = bool(self.settings["keys_sound"])
            # (each row's column: a page's 8, as the Launchkey's knobs; the tone mixer's 0-7, the stem mixer's 8-15)
            rows = tuple((f"a{i}", _percent(volumes[i] if i < len(volumes) else NORMAL_VOLUME), not a.get("play", True),
                          str(a["name"]).upper(), min(int(a.get("lights") or 0), 1), str(a.get("kind") or "lead"), i)
                         for i, a in enumerate(arrangements))
            drum_column = len(arrangements) if len(arrangements) < GAME_MIXER_SLIDERS else -1  # (after them)
            self._game_mixer_parts = arrangements
            ids = {f"a{i}": a["id"] for i, a in enumerate(arrangements)}
            if drummed is not None:  # (its own volume and mute, the song's)
                rows += (("drums", _percent(int(drummed.get("volume", DRUMS_VOLUME))), not drummed.get("play", True),
                          "DRUMS", int(bool(self.settings["beat_lights"])), "drums", drum_column),)
                ids["drums"] = "drums"
            elif drum_part is not None:  # (not made yet: its column there to make them, as the Launchkey's pad; being
                # made, how far)
                making = drum_part.get("making")
                rows += (("drums", 0.0, False, "DRUMS", -1,
                          f"drums:{round(100 * (drum_part.get('progress') or 0))}%" if making else "drums:make",
                          drum_column),)
            rows += (("keys", _percent(keys), not keys_sound, "KEYS", int(bool(self.settings["keys_lights"])), "keys",
                      6),)
            rows += (("all", max([row[1] for row in rows if not row[5].startswith("drums:")] or [0.0]),  # (the
                      bool(self.play_options["silent"]), "ALL", -1, "all", 7),)                       # loudest: knob 8's)
            stems = self._game_stem_rows(parts)
            rows += stems
            music = self.game_music()
            if music is not None:  # (the game's Music volume: the stem mixer's, as its knob 7, apart; its pad, muted)
                rows += (("music", float(music), self.game_music_muted(), "MUSIC", -1, "music", 14),)
            on = [row for row in stems if not row[5].endswith(":off")]
            rows += (("smaster", max([row[1] for row in on] or [0.0]), bool(on) and all(row[2] for row in on),
                      "MASTER", -1, "all", 15),)  # (its 8th: its parts together, as knob 8; its pad all of them muted,
            # or back: the page's master mute, as the Launchkey's there)
            self._game_mixer_ids = ids
            if rows != self._game_mixer_sent and self._riff_loop.kmix(rows) is not None:
                self._game_mixer_sent = rows
            tones = {f"a{i}": self._part_tone(a) for i, a in enumerate(arrangements)}
            if drummed is not None and drummed.get("drum_set") is not None:  # (on the keyboard: its kit, a program)
                tones["drums"] = int(drummed["drum_set"])
            tones["keys"] = int(self.keys_info()["tone"])
            self._game_tones = tones
            tone_rows = self._game_tone_rows(arrangements, drummed, drum_column)
            if tone_rows != self._game_tones_sent and self._riff_loop.ktones(tone_rows) is not None:
                self._game_tones_sent = tone_rows

    def _game_tone_rows(self, arrangements: list[dict[str, Any]], drummed: dict[str, Any] | None,
                        drum_column: int) -> tuple[tuple[Any, ...], ...]:
        """The game's TONES, laid out as the Launchkey's knobs: each arrangement's tone for the knobs' page (tone,
        palm mutes, harmonics: its number, "SAME" for the same tone as the rest; its group, its name), whether it
        follows the song's tone changes (1, 0; -1: it has none), its kind and column; the drum kit, and the
        Launchkey keys' tone (7th), whatever the page. Each: id, label, "NUMBER|GROUP|NAME", kind, follow, column,
        the page."""
        page = self._tone_field["field"]
        field = TONE_PAGES[page][0]
        rows: tuple[tuple[Any, ...], ...] = ()
        for i, a in enumerate(arrangements):
            number, group, name = tone_shown(a, field, self.follow_settings.get(a["kind"]) or {})
            follow = int(a["tones"].get("follow_tones") is not False) if a.get("tone_changes_known") else -1
            rows += ((f"a{i}", str(a["name"]).upper(), f"{'SAME' if number is None else f'{number:03d}'}|{group}|{name}",
                      str(a.get("kind") or "lead"), follow, i, page),)
        if drummed is not None and drummed.get("drum_set") is not None and drum_column >= 0:  # (on the keyboard)
            kit = next((t for t in TONES if t.bank == DRUM_SET_BANK and t.program == int(drummed["drum_set"])), None)
            said = f"{kit.number:03d}|Drum kit|{kit.name.title()}" if kit else f"---|Drum kit|Set {drummed['drum_set']}"
            rows += (("drums", "DRUMS", said, "drums", -1, drum_column, page),)
        keys = tone(int(self.keys_info()["tone"]))
        rows += (("keys", "KEYS", f"{keys.number:03d}|{keys.group.title().replace('Gm ', 'GM ')}|{keys.name}", "keys",
                  -1, 6, page),)
        return rows

    def set_tone_field(self, field: int) -> None:
        """The knobs' tones' page (0 tone, 1 palm mutes, 2 harmonics), as the Launchkey's up arrow turned it: the
        game's TONES shows it."""
        self._tone_field = {**self._tone_field, "field": int(field) % len(TONE_PAGES)}

    def _game_stem_rows(self, parts: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
        """The game's MIXER's stem mixer page: the song's parts in the game's song (riffloop's, the song split here:
        each part's volume and mute, the stem mixer's, columns 1 to 6, as the Launchkey's knobs); else the game's
        song's bass and guitar (rearmed-mods' Song Stems: the part played taken out, at its level), as the Launchkey's
        stems page has them (knobs 2 and 3). The one not played waits its turn (":wait"); without Song Stems on, both
        off (":off")."""
        game_parts = self._game_parts
        if game_parts is not None:
            options = self.play_options
            return tuple((f"p{stem}", float(options["stem_volumes"].get(stem, 100)),
                          bool(options["stem_mutes"].get(stem)), GAME_PART_LABELS[stem], -1,
                          f"stem:{GAME_PART_KINDS[stem]}", 8 + column)
                         for column, stem in enumerate(game_parts.get("shown") or stems.STEMS))
        stems_info = self.game_stems_info()
        return self._song_stem_rows(parts, stems_info)

    @staticmethod
    def _song_stem_rows(parts: list[dict[str, Any]], stems: dict[str, Any] | None) -> tuple[tuple[Any, ...], ...]:
        """The game's song's bass and guitar rows (Song Stems': stems, game_stems_info's)."""
        playing = next((a for a in parts if a.get("playing")), None)
        taken = "bass" if playing is not None and playing.get("kind") == "bass" else "guitar"
        rows: tuple[tuple[Any, ...], ...] = ()
        for column, part in ((9, "bass"), (10, "guitar")):
            on = bool(stems and stems.get("on"))
            level = float(stems[part]) if on and stems else 0.0
            state = "" if on and part == taken else ":wait" if on else ":off"
            muted = on and bool(stems and stems["muted"][part])  # (its level kept: greyed, as an arrangement muted)
            rows += ((f"g{part}", level, muted, part.upper(), -1, f"stem:{part}{state}", column),)
        return rows

    def _next_lights(self, part: str) -> int:
        """A part's lit keys after its bottom pad, as the Launchkey's: on or off (the lit parts share the 4 keys)."""
        parts = getattr(self, "_game_mixer_parts", [])
        a = next((x for x in parts if x["id"] == part), {"lights": 0})
        return 0 if int(a.get("lights") or 0) else 1

    def _part_tone(self, arrangement: dict[str, Any]) -> int:
        """The tone an arrangement plays with (following): its own, its kind's default, or the built-in one."""
        own = (arrangement.get("tones") or {}).get("tone") or (self.follow_settings.get(arrangement["kind"]) or {}).get("tone")
        return int(own) if own else BUILT_IN.get(arrangement["kind"], BUILT_IN["lead"])

    def game_mixer_step(self) -> bool:
        """While the game's MIXER or TONES shows (the Launchkey's loop asks each time round): what's done there
        done here at once. Whether anything was (the rows to be sent again, as they are now: at once)."""
        with self._game_mixer_lock:
            return self._game_mixer_apply()

    def _game_mixer_apply(self) -> bool:
        """(Holding _game_mixer_lock.) What was done in the game's MIXER or TONES since last asked, done
        here: a slider moved, a mute turned over (from the mute sent last: the mod shows it turned at once), a tone
        stepped (from the one sent last). Whether anything was."""
        moved = self._riff_loop.kmixed() or {}
        sent = {row[0]: row for row in self._game_mixer_sent or ()}
        slots: dict[int, int] = {}
        for key, value in moved.items():
            part = self._game_mixer_ids.get(key)
            drum_part = getattr(self, "_game_mixer_drums", None)
            arranged = next((a for a in getattr(self, "_game_mixer_parts", []) if a["id"] == part), None)
            if key == "page":  # (its page turned there: the Launchkey's mixer to it)
                self._game_mixer_page = {"turns": self._game_mixer_page["turns"] + 1, "page": value}
            elif key == "field" and isinstance(value, float):  # (TONES' up arrow: its next page, the Launchkey's too)
                self._tone_field = {"turns": self._tone_field["turns"] + 1, "field": int(value) % len(TONE_PAGES)}
            elif value == "follow" and arranged is not None:  # (TONES' pad below: following the song's tone changes)
                self.change_part(arranged["id"], {"follow_tones": False if arranged["tones"].get("follow_tones")
                                                  is not False else None})
            elif isinstance(value, str) and value.startswith("tone:") and arranged is not None:  # (its tone on TONES'
                field = TONE_PAGES[self._tone_field["field"]][0]                                # page, as its knob)
                kind = self.follow_settings.get(arranged["kind"]) or {}
                number = stepped_tone(knob_tone(arranged, field, kind), int(value[5:]), False, tone_choices(kind, field))
                self.change_part(arranged["id"], {field: None if number == DEFAULT_TONE else number})
            elif key in ("gbass", "gguitar"):  # (the stem mixer's: the game's song's part, as the Launchkey's
                stem = key[1:]                        # stems page has it)
                try:
                    if value == "mute" and key in sent:
                        self.set_game_stems({"part": stem, "mute": not sent[key][2]})
                    elif isinstance(value, float):
                        self.set_game_stems({"part": stem, "level": round(value)})
                except GuiError:
                    pass  # (Song Stems turned off since: its rows say so next)
            elif key == "drums" and drum_part is not None and not drum_part.get("made", True):  # (not made yet: its pad
                if value == "mute" and not drum_part.get("making"):                         # makes them)
                    self.change_part("drums", {"make": True})
            elif key == "all" and value == "mute":  # (the tone mixer's 8th: the sound on or off, as the Mute pad)
                self.toggle_sound()
            elif key == "smaster" and value == "mute" and self._game_parts is not None:  # (the song's parts, all
                mutes = self.play_options["stem_mutes"]                                     # muted or back)
                shown = (self._game_parts or {}).get("shown") or stems.STEMS
                muting = not all(mutes.get(stem) for stem in shown)
                self.set_play_options({"stem_mutes": {stem: muting for stem in shown}})
            elif key == "smaster" and value == "mute":  # (the stem mixer's: its parts, as the Mute pad there)
                try:
                    self.toggle_game_stems()
                except GuiError:
                    pass
            elif key[:1] == "p" and key[1:] in stems.ALL:  # (a part of the song's in the game's song: as the
                stem = key[1:]                                 # Launchkey's stems page)
                if value == "mute":
                    self.set_play_options({"stem_mutes": {stem: not self.play_options["stem_mutes"].get(stem)}})
                elif isinstance(value, float):
                    self.set_play_options({"stem_volumes": {stem: round(value)}})
            elif key == "music" and value == "mute" and key in sent:  # (the game's Music muted, or back)
                try:
                    self.set_game_music({"mute": not sent[key][2]})
                except GuiError:
                    pass
            elif key == "music" and isinstance(value, float):  # (its Music: the game's, as knob 6)
                self.set_game_music({"level": round(value)})
            elif value == "lights":  # (a bottom pad: as the Launchkey's)
                if key == "keys":
                    self.set_keys({"lights": not self.settings["keys_lights"]})
                elif key == "drums":
                    self.toggle_beat_lights()
                elif part is not None:
                    self.change_part(part, {"lights": self._next_lights(part)})
            elif value == "mute" and key in sent:
                muted = sent[key][2]  # (as the game showed it before: now the other way)
                if key == "keys":
                    self.set_keys({"sound": muted})
                elif part is not None:
                    self.change_part(part, {"play": muted})
                sent[key] = (*sent[key][:2], not muted, *sent[key][3:])
            elif isinstance(value, float) and key == "keys":
                self.set_keys({"volume": _volume_of(value)})
            elif isinstance(value, float) and key == "drums" and part is not None:
                self.change_part("drums", {"volume": _volume_of(value)})
            elif isinstance(value, float) and key[:1] == "a" and part is not None:
                slots[int(key[1:])] = _volume_of(value)
            elif isinstance(value, str) and value.startswith("tone:") and key == "drums" and key in self._game_tones:
                kits = [program for _name, program in DRUM_SETS]  # (as the Launchkey's knob steps them)
                was = self._game_tones[key]
                kit = kits[((kits.index(was) if was in kits else 0) + int(value[5:])) % len(kits)]
                self._game_tones[key] = kit
                self.change_part("drums", {"kit": kit})
            elif isinstance(value, str) and value.startswith("tone:") and key in self._game_tones:
                was = self._game_tones[key]
                at = TONE_ORDER.index(was) if was in TONE_ORDER else 0
                number = TONE_ORDER[(at + int(value[5:])) % len(TONE_ORDER)]  # (as the Launchkey's knobs step)
                self._game_tones[key] = number
                if key == "keys":
                    self.set_keys({"tone": number})
                elif part is not None:
                    self.change_part(part, {"tone": number})
        if slots:
            self.set_volumes(slots)
        if moved and self._game_mixer_sent is not None:
            self._game_mixer_sent = tuple(sent.get(row[0], row) for row in self._game_mixer_sent)
        return bool(moved)

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

    # The song's parts in the game's own song (gameparts: riffloop mixes them), following

    def _game_parts_now(self, followed: dict[str, Any] | None) -> dict[str, Any] | None:
        """Following, the song's parts in the game's song, for a remote and the page: which play there (its stem
        mixer's: the parts', at the page's levels), by which separator; being made ready; or why not. None: none."""
        parts = self._game_parts
        if followed is not None and parts is not None and parts["sid"] == followed["id"]:
            return {"on": True, "names": list(parts["names"]), "by": parts["by"]}
        if followed is not None and self._game_parts_wanted and self._game_parts_lock.locked():
            return {"on": False, "making": True}
        return {"on": False, "error": self._game_parts_error} if followed is not None and self._game_parts_error else None

    def _game_parts_follow(self, followed: dict[str, Any] | None, said: GameLoop | None) -> None:
        """Following, the song played's parts in the game's own song (riffloop mixes them as it plays, at the stem
        mixer's levels): made ready in the background as the song starts (its parts read and written as the mod maps
        them: a few seconds; kept, played again, they're there at once), then handed to the mod, Song Stems' guitar
        and bass put at 100 meanwhile (it changes the song too). A song not split (or none): none. The mod lost them
        (loaded again, the game started again): handed to it again."""
        sid = followed["id"] if followed else None
        wanted = (sid, self.play_options["separator"], self._splits_done, self._beta("sides")) if sid else None
        if wanted != self._game_parts_wanted:
            self._game_parts_wanted = wanted
            if wanted is None:
                threading.Thread(target=self._game_parts_off, name="rs-keylights game parts", daemon=True).start()
            else:
                threading.Thread(target=self._game_parts_ready, args=(wanted,), name="rs-keylights game parts",
                                 daemon=True).start()
            return
        parts = self._game_parts
        if parts is not None and said is not None and said.parts is not None and said.parts[0] != parts["file_id"] \
                and time.monotonic() - self._game_parts_sent > GAME_PARTS_AGAIN:
            self._game_parts_hand(parts)

    def _game_parts_ready(self, wanted: tuple[Any, ...]) -> None:
        """(Its own thread.) A song's parts made ready for the game and handed to it, if it's still the one wanted
        (one at a time: one song's in memory at most)."""
        import tempfile
        with self._game_parts_lock:
            if wanted != self._game_parts_wanted:
                return
            sid = wanted[0]
            try:
                loaded = self._load(sid)
                ref = next((r for r, _a in loaded if r.artist), loaded[0][0])
                which = self._parts_by(ref)
                if which is None:  # (not split)
                    self._game_parts_error = None
                    self._game_parts_off()
                    return
                names = self._part_names(ref, which)
                path = gameparts.path_for(ref, which, sides=names == stems.WITH_SIDES)
                file_id, data = drums.song_audio_file(ref, self.index.game_dir)
                if file_id is None:
                    raise GuiError("The song's audio has no file ID the game streams it by")
                rate, _channels = gameparts.wem_format(data)
                del data
                head = gameparts.header(path)
                if head is not None and head["file_id"] == file_id and head["rate"] == rate:
                    mixed = head["names"]  # (made before: there at once)
                else:
                    with tempfile.TemporaryDirectory(prefix="rs-keylights-game-parts-") as work:
                        wavs = stems.decode(ref, Path(work), separator=which,
                                            parts=[n for n in names if n != "other"])
                        if wavs is None:
                            self._game_parts_off()
                            return
                        mixed = gameparts.write(path, file_id, rate, wavs)
            except Exception as exc:  # (said on the page; the game's song as it is)
                self._game_parts_error = f"The song's parts in the game: {exc}"
                self._game_parts_off()
                return
            if wanted != self._game_parts_wanted:
                return
            parts = {"sid": sid, "path": str(path), "file_id": file_id, "names": mixed, "by": which,
                     "shown": list(names)}  # (names: those in its file, mixed; shown: the stem mixer's)
            self._game_parts, self._game_parts_error = parts, None
            self._song_stems_aside()
            self._game_parts_hand(parts)
            self.play_version += 1

    def _game_parts_hand(self, parts: dict[str, Any]) -> None:
        """The song's parts handed to the game's mod, and their weights."""
        self._game_parts_sent = time.monotonic()
        if self._riff_loop.kparts(parts["path"]) is not None:
            self._game_parts_gains()

    def _game_parts_gains(self) -> None:
        """The song's parts' weights in the game's song, from the stem mixer's levels (the parts' volumes and mutes:
        playing's, the same as the song's audio here)."""
        parts = self._game_parts
        if parts is not None:
            self._riff_loop.kgains(gameparts.weights(_stem_weights(self.play_options, stems.ALL), parts["names"]))

    def _game_parts_off(self) -> None:
        """None of the song's parts in the game's song (and Song Stems' levels back)."""
        was, self._game_parts = self._game_parts, None
        if was is not None:
            self._riff_loop.kparts(None)
            self.play_version += 1
        self._song_stems_back()

    def _song_stems_aside(self) -> None:
        """Song Stems' guitar and bass at 100 (the song as it is: the parts mix it now), their levels (and which were
        muted here) kept to put back."""
        if self._song_stems_kept is not None:
            return
        path = gamestems.ini_path(self.index.game_dir)
        read = gamestems.read(path)
        if read is None or not read["on"] or all(read[part] == gamestems.FULL for part in gamestems.PARTS):
            return
        self._song_stems_kept = ({part: int(read[part]) for part in gamestems.PARTS}, dict(self._game_stems_was))
        try:
            gamestems.write(path, {part: gamestems.FULL for part in gamestems.PARTS})
        except OSError:
            self._song_stems_kept = None

    def _song_stems_back(self) -> None:
        """Song Stems' levels as they were before the song's parts played in the game."""
        kept, self._song_stems_kept = self._song_stems_kept, None
        if kept is None:
            return
        try:
            gamestems.write(gamestems.ini_path(self.index.game_dir), kept[0])
        except OSError:
            return
        self._game_stems_was = dict(kept[1])

    def game_stems_info(self) -> dict[str, Any] | None:
        """The game's song's guitar and bass (rearmed-mods' Song Stems mod, gamestems): the mod on or off ("on"),
        each part's level (0 to 100; muted, the level it goes back to) and whether it's muted ("muted": at 0 in the
        file, its level kept here; set above 0 since, by the game's own key, not muted); None without rearmed-mods
        (no RSMods.ini). (Read again only as it changes.)"""
        path = gamestems.ini_path(self.index.game_dir)
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (stat.st_mtime_ns, stat.st_size)
        if self._game_stems_read[0] != key:
            self._game_stems_read = (key, gamestems.read(path))
        read = self._game_stems_read[1]
        if read is None:
            return None
        for part in [p for p in self._game_stems_was if read.get(p, 0) > 0]:
            del self._game_stems_was[part]
        return {**read, **{part: self._game_stems_was[part] for part in self._game_stems_was},
                "muted": {part: part in self._game_stems_was for part in gamestems.PARTS}}

    def set_game_stems(self, body: dict[str, Any]) -> dict[str, Any]:
        """The game's song's guitar or bass ("part") at a level ("level", 0 to 100), stepped up or down ("step"),
        or muted and back ("mute": true at 0, false back where it was): kept in RSMods.ini, heard within a second
        (the Song Stems mod reading it again). Muted, its level is kept here, and set there as it's unmuted (as an
        arrangement's volume is, muted). The levels as they are then."""
        part = str(body.get("part", ""))
        if part not in gamestems.PARTS:
            raise GuiError(f"No part {part!r} in the game's song (guitar or bass)")
        info = self.game_stems_info()
        if info is None:
            raise GuiError("The game's song parts need rearmed-mods (its Song Stems mod)")
        if not info["on"]:
            raise GuiError("Song Stems is off: turn it on in rearmed-mods")
        level, muted = info[part], info["muted"][part]
        if "level" in body:
            level = int(_number(body["level"], "level", 0, 100))
        elif "step" in body:
            level = min(max(level + int(_number(body["step"], "step", -100, 100)), 0), 100)
        elif "mute" in body and bool(body["mute"]) != muted:
            muted = bool(body["mute"])
            if muted:
                self._game_stems_was[part] = level
            else:
                del self._game_stems_was[part]
        if muted:
            self._game_stems_was[part] = level  # (its level kept, for when it's back)
        written = 0 if muted else level
        if written != (self._game_stems_read[1] or {}).get(part):
            try:
                gamestems.write(gamestems.ini_path(self.index.game_dir), {part: written})
            except OSError as exc:
                raise GuiError(f"Couldn't write RSMods.ini: {exc}") from exc
        return self.game_stems_info() or {}

    def game_music(self) -> int | None:
        """Following, the game's Music volume (its mixer's: the song, 0 to 100) as the game has it, through its
        riffloop mod (asked at most every GAME_TIME_EVERY); muted here, the level it goes back to; None without the
        mod, or one that hasn't it."""
        self.game_time()
        said = self._game_said
        if said is None or said.music is None:
            return None
        level = round(said.music)
        if self._game_music_kept is not None and level > 0:  # (set in the game since: not muted)
            self._game_music_kept = None
        return level if self._game_music_kept is None else self._game_music_kept

    def game_music_muted(self) -> bool:
        """Following, whether the game's Music is muted here (at 0 in the game, its level kept)."""
        self.game_music()
        return self._game_music_kept is not None

    def toggle_game_stems(self) -> dict[str, Any]:
        """The game's song's bass and guitar both muted (if either isn't), or both back: the stems page's master
        mute (as the Mute pad is elsewhere). Their levels then (muted, kept)."""
        info = self.game_stems_info()
        if info is None:
            raise GuiError("The game's song parts need rearmed-mods (its Song Stems mod)")
        if not info["on"]:
            raise GuiError("Song Stems is off: turn it on in rearmed-mods")
        mute = not all(info["muted"].values())
        for part in gamestems.PARTS:
            info = self.set_game_stems({"part": part, "mute": mute})
        return info

    def set_game_mixer_page(self, page: str) -> None:
        """The game's MIXER on a page ("parts" or "stems"), as the Launchkey's mixer went to it (if it's open)."""
        if page not in ("parts", "stems"):
            raise GuiError(f"No mixer page {page!r}")
        self._riff_loop.kpage(page)

    def set_game_music(self, body: dict[str, Any]) -> dict[str, Any]:
        """Following, the game's Music volume set, through its riffloop mod: "level" (0 to 100), "step" (up or
        down from where the game has it), or "mute" (true: at 0 in the game, its level kept; false: back to it; muted,
        a level is kept for then, as an arrangement's volume is). It's the game's own mixer setting, as its mixer menu
        sets it. The level it's at (muted: kept), and whether it's muted."""
        with self._lock:
            session = self._session
        if not (isinstance(session, Following) and session.running):
            raise GuiError("Follow Rocksmith first: it's its music's volume")
        muted = self.game_music_muted()
        if "level" in body:
            level = _number(body["level"], "music volume", 0, 100)
        else:
            now = self.game_music()
            if now is None:  # (the mod's first answer hasn't it: the one after has)
                self._game_time = (-math.inf, None)
                now = self.game_music()
            if now is None:
                raise GuiError("Rocksmith didn't answer" if self._game_said is None else
                               "Music volume: update the riffloop mod")
            level = now
            if "mute" in body:
                muted = bool(body["mute"])
            else:
                level = min(max(now + _number(body.get("step", 0), "step", -100, 100), 0), 100)
        said = self._riff_loop.music(0 if muted else round(level))
        if said is None:
            raise GuiError("Rocksmith didn't answer: needs its riffloop mod")
        if said.music is None:
            raise GuiError("Music volume: update the riffloop mod")
        self._game_music_kept = round(level) if muted else None
        self._game_time, self._game_said = (time.monotonic(), said.time), said
        return {"music": round(level) if muted else round(said.music), "muted": muted}

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
        and "on"; following, "riff" opens the game's Riff Repeater on it too (the riffloop mod: its menu with the loop
        picked, for its Start). The end without the start: the start is its section's. The start moved to or past the end, the
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
            if on and body.get("riff") and self._riff_said is not None:  # (its menu, on the loop: for its Start)
                self._riff_loop.riff()
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
            if said is not None:  # (what it says of its speed next is this one)
                self._game_time, self._game_said = (time.monotonic(), said.time), replace(said, speed=settings["speed"])
        if "accelerator" in body:
            settings["accelerator"] = bool(body["accelerator"])
            said = self._riff_loop.accelerator(settings["accelerator"])
            if said is not None:  # (what it says of it next is this)
                self._game_time, self._game_said = (time.monotonic(), said.time), replace(
                    said, accelerator=settings["accelerator"])
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
        """A remote's Stop: the song playing stops, and goes back to its start (it plays from there next: as a stop
        button does; a start set before, gone); stopped already, just that. (Following goes on: it has its own pad.)"""
        with self._lock:
            stopped = isinstance(self._session, Playback) and self._session.running
            if stopped:
                sid = self._playing["id"] if self._playing else self.play_options.get("song")
                self._stop()
            else:
                sid = self.play_options.get("song")
            if sid and self.song_settings.get(sid, {}).get("start"):
                self.set_song({"song": sid, "start": 0})
        return "stopped" if stopped else "back to the start" if sid else "nothing playing"

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
        changes = dict(changes)
        drummed = changes.pop("drums", None)  # (the drum part: its own mute and volume)
        if drummed is not None:
            self.set_drums({"song": song["id"], **{k: v for k, v in drummed.items()
                                                   if k in ("play", "volume", "make", "kit")}})
        if not changes:
            return
        if info["mode"] == "follow":
            body: dict[str, Any] = {}
            for arrangement, fields in changes.items():
                along = {k: v for k, v in fields.items() if k in ("play", "lights")}
                tones = {k: v for k, v in fields.items() if k in PART_FIELDS}
                if along:
                    body.setdefault("parts", {song["played"]: {}})[song["played"]][arrangement] = along
                if tones:
                    body.setdefault("arrangements", {})[arrangement] = tones
            self.set_follow(body, later)
        else:
            self.set_song({"song": song["id"], "parts": changes}, later, answer=False)  # (a remote: no answer)

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
        channel = int(self.settings["listen_channel"])
        listener = self._listener
        if listener is not None and (not wanted or listener.device != device or listener.channel != channel):
            listener.stop()
            self._listener = listener = None
        if wanted and (listener is None or not listener.running):
            self._listener = Listener(self._hear, self._expected_now, device, self._lowest_now, channel=channel)
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
            heard = [planned for planned in self._planned_now if not planned.part.muted]
            if not heard:
                return None
            best = max(heard, key=lambda planned: planned.part.lights)
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
                "channel": int(self.settings["listen_channel"]),
                "heard": [note_name(p) for p in listener.now] if listener else [],
                "level": round(listener.level, 4) if listener else 0.0,
                "error": listener.error if listener else None}

    # The guitar's pedal

    def pedal_info(self) -> dict[str, Any]:
        """The pedal's preset as far as it's known (None: not known) and its name, the one before, why it couldn't
        be switched (None: it could), the favourites the Launchkey's pad goes through, and whether its tone's held
        (not switched with the song's tones)."""
        pedal = self.pedal
        slot, previous = pedal.slot, pedal.previous
        return {"slot": slot, "name": pedal.name(slot), "previous": previous, "previous_name": pedal.name(previous),
                "error": pedal.error, "favourites": list(self.settings["pedal_favourites"]), "hold": pedal.held,
                "tone": self._tone_playing(), "names": pedal.names_version, "names_error": self._names_error}

    def _read_pedal_names(self) -> None:
        try:
            self.pedal.refresh_names()
            self._names_error = None
        except PedalError as exc:
            self._names_error = str(exc)

    def pedal_names(self, read: bool = False) -> dict[str, Any]:
        """The presets' names (read from the pedal again first: a few seconds), and when they last changed (0: from
        the DNAfx editor's backup, not read from the pedal yet)."""
        if read:
            self._read_pedal_names()
            if self._names_error:
                raise GuiError(f"Couldn't read the pedal's names: {self._names_error}")
        names = self.pedal.names()
        return {"names": [names.get(slot, "") for slot in range(1, PEDAL_SLOTS + 1)],
                "version": self.pedal.names_version, "error": self._names_error}

    # The Guitar card's meters (the guitar's input, the pedal's sound coming back in), and the tone check: your tone
    # against the record's (tonecheck)

    def _tap(self, device: str, channel: int) -> tonecheck.Tap:
        """An input's channel read (opened if it isn't: closed again once it isn't asked for a while)."""
        with self._taps_lock:
            tap = self._taps.get((device, channel))
            if tap is None:
                tap = self._taps[(device, channel)] = tonecheck.Tap(device, channel)
                if len(self._taps) == 1:
                    threading.Thread(target=self._reap_taps, name="rs-keylights inputs", daemon=True).start()
            if not tap.running:
                tap.start()
            return tap

    def _reap_taps(self) -> None:
        """(Its own thread, while inputs are read.) Each closed once its meter's not asked for a while (TAP_IDLE)
        and it isn't recording."""
        while True:
            time.sleep(1.0)
            with self._taps_lock:
                now = time.monotonic()
                idle = [key for key, tap in self._taps.items() if now - tap.asked > TAP_IDLE and not tap.recording]
                gone = [self._taps.pop(key) for key in idle]
                left = bool(self._taps)
            for tap in gone:
                tap.stop()
            if not left:
                return

    def meters(self) -> dict[str, Any]:
        """The Guitar card's meters: the guitar's input (the one it's heard on) and the pedal's sound coming back in
        (if it's set), each's level now (tonecheck.Tap.level), and which input each is."""
        out: dict[str, Any] = {}
        guitar = (str(self.settings["listen_device"]), int(self.settings["listen_channel"]))
        pedal = self.settings["pedal_input"]
        for name, where in (("guitar", guitar), ("pedal", (pedal["device"], pedal["channel"]) if pedal else None)):
            if where is None:
                out[name] = None
                continue
            try:
                out[name] = {**self._tap(*where).level(), "device": where[0], "channel": where[1]}
            except Exception as exc:  # (no audio: said)
                out[name] = {"error": str(exc), "device": where[0], "channel": where[1]}
        return out

    def _chart_at(self, at: float) -> float | None:
        """Where the song playing (or followed) was at a time on time.monotonic's clock: its chart's time (None:
        nothing plays, or it's paused)."""
        session = self._session
        if isinstance(session, Playback) and session.running:
            return session.where_at(at)[0]
        if isinstance(session, Following) and session.running:
            return session.follower.position(at)
        return None

    def _tone_check_song(self) -> tuple[str, list[tuple[ArrangementRef, Arrangement]], str | None]:
        """The song a tone check's of (the one playing, or followed), its parts, and the part the game plays (None:
        not following)."""
        with self._lock:
            session, playing = self._session, self._playing
        if isinstance(session, Following) and session.running and session.follower.current is not None:
            current = session.follower.current
            return song_id(current), self._load(song_id(current)), current.part
        if isinstance(session, Playback) and session.running and playing:
            return playing["id"], self._load(playing["id"]), None
        raise GuiError("Play a song (or follow the game) to check your tone against it")

    def _preset_on(self) -> dict[str, Any]:
        """The pedal's preset as far as it's known: the one it was switched to, else (following) the song's tone's."""
        slot = self.pedal.slot
        if slot is None:
            tone = self._tone_playing() or {}
            slot = tone.get("pedal") if isinstance(tone.get("pedal"), int) else None
        return {"slot": slot, "name": self.pedal.name(slot) if slot is not None else ""}

    def tone_check_info(self) -> dict[str, Any]:
        """The tone check: recording (the song, your part, how long so far), analysing, or not; the last check's
        result, and the ones before (the latest last)."""
        check = dict(self._tone_check)
        tap = check.pop("tap", None)
        if tap is not None:
            check["seconds"] = round(tap.seconds, 1)
            check["most"] = tonecheck.RECORD_MOST
        kept = tonecheck.history()
        return {**check, "checks": kept[-10:], "pedal_input": self.settings["pedal_input"]}

    def tone_check(self, body: dict[str, Any]) -> dict[str, Any]:
        """A tone check started ("start": your part, "part", the game's if following; else the song's first lead or
        rhythm), stopped (then analysed: a few seconds, in the background), or cancelled ("cancel")."""
        action = body.get("action")
        if action == "start":
            if self._tone_check.get("state") in ("recording", "analysing"):
                return self.tone_check_info()
            pedal = self.settings["pedal_input"]
            if pedal is None:
                raise GuiError("Choose the input the pedal's sound comes back in on first")
            sid, loaded, played = self._tone_check_song()
            guitars = [r.part for r, _a in loaded if kind(r) in ("lead", "rhythm", "bass")]
            part = str(body.get("part") or played or next((p for p in guitars if p in ("lead", "rhythm")), "") or
                       (guitars[0] if guitars else ""))
            if part not in guitars:
                raise GuiError(f"No {part or 'guitar'} part in this song")
            ref = loaded[0][0]
            which = self._parts_by(ref)
            if which is None:
                raise GuiError("Split the song first (Song Audio): your tone's checked against its guitar")
            tap = self._tap(pedal["device"], pedal["channel"])
            guitar = (str(self.settings["listen_device"]), int(self.settings["listen_channel"]))
            # (your guitar dry beside it, if it comes in on the same interface: kept, to play through the pedal again)
            tap.record(self._chart_at, dry=guitar[1] if guitar[0] == pedal["device"] else None)
            named = next((r for r, _a in loaded if r.artist), ref)
            self._tone_check = {"state": "recording", "sid": sid, "title": named.title, "part": part,
                                "separator": which, "tap": tap, "started": time.time(), "preset": self._preset_on()}
        elif action in ("stop", "cancel"):
            check = self._tone_check
            if check.get("state") != "recording":
                return self.tone_check_info()
            take, anchors, dry = check["tap"].take()
            if action == "cancel":
                self._tone_check = {"state": "idle"}
            else:
                self._tone_check = {**{k: v for k, v in check.items() if k != "tap"}, "state": "analysing"}
                threading.Thread(target=self._tone_check_analyse, args=(dict(self._tone_check), take, anchors, dry),
                                 name="rs-keylights tone check", daemon=True).start()
        else:
            raise GuiError("Expected start, stop or cancel")
        return self.tone_check_info()

    def _tone_check_analyse(self, check: dict[str, Any], take: Any, anchors: list[tuple[int, float | None]],
                            dry: Any = None) -> None:
        """(Its own thread.) A tone check's recording against the song's guitar (the side of it you play, if its lead
        and rhythm are apart: else its guitar; a bass part, its bass), kept with its result (and your guitar dry, if
        it was recorded beside it)."""
        import numpy as np
        try:
            sid = check["sid"]
            loaded = self._load(sid)
            ref = loaded[0][0]
            mine = next(a for r, a in loaded if r.part == check["part"])
            part_kind = kind(next(r for r, _a in loaded if r.part == check["part"]))
            names = self._part_names(ref, check["separator"])
            stem = "bass" if part_kind == "bass" else part_kind if part_kind in names else "guitar"
            offset = self._chart_offsets.get(sid, 0.0)
            charts = [c for _e, c in anchors if c is not None]
            if len(take) < tonecheck.RATE or not charts:
                raise GuiError("Nothing recorded while the song played")
            first, last = min(charts) + offset - tonecheck.LAG_MOST - 1.0, max(charts) + offset + tonecheck.LAG_MOST + 1.0
            with tempfile.TemporaryDirectory(prefix="rs-keylights-tone-check-") as work:
                wavs = stems.decode(ref, Path(work), separator=check["separator"], parts=(stem,))
                if wavs is None:
                    raise GuiError("The song's parts are gone: split it again")
                with wave.open(str(wavs[stem])) as w:
                    rate, channels = w.getframerate(), w.getnchannels()
                    start = max(int(first * rate), 0)
                    w.setpos(min(start, w.getnframes()))
                    raw = w.readframes(max(int((last - first) * rate), 0))
                reference = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels).mean(axis=1) / 32768.0
            result = tonecheck.analyse(take, anchors, reference.astype(np.float32), rate, start / rate, mine.notes,
                                       offset)
            preset = check.get("preset") or {}
            if preset.get("slot") is None:  # (not known as it started: the one the song's tone has, if it's known now)
                preset = self._preset_on()
            kept = tonecheck.keep({"at": time.strftime("%Y-%m-%d %H:%M"), "sid": sid, "song": check["title"],
                                   "part": check["part"], "stem": stem, "preset": preset, "offset": offset,
                                   **tonecheck.as_dict(result)}, take, anchors=anchors, dry=dry)
            self._tone_check = {"state": "idle", "last": kept}
        except Exception as exc:  # (said on the page)
            self._tone_check = {"state": "idle", "error": str(exc) or type(exc).__name__}
        self.play_version += 1

    def _followed_now(self) -> tuple[ArrangementRef | None, float | None]:
        """Following, the arrangement the game plays and where it is (None, None: not following)."""
        with self._lock:
            session = self._session
        if not (isinstance(session, Following) and session.running):
            return None, None
        current = session.follower.current
        if current is None:
            return None, None
        at = self.game_time()
        return current, at if at is not None else session.follower.position(time.monotonic())

    def _tone_playing(self) -> dict[str, Any] | None:
        """Following, the song tone the game plays now (its key, short name, the pedal's preset for it, and whether
        that's the song's own); None not following, or not known."""
        current, at = self._followed_now()
        if current is None or at is None:
            return None
        return self.pedal_tones.playing(current, at)

    def song_pedal_tones(self, sid: str, part: str | None = None) -> dict[str, Any]:
        """A song's tones and the pedal's preset for each, for the page's Song tones card (PedalTones.table): an
        arrangement's (the one asked for, by its part or persistent ID; else the one the game plays, following
        it; else the first), and the song's parts."""
        refs = [r for r, _a in self._load(sid)]
        followed, _at = self._followed_now()
        ref = next((r for r in refs if part in (r.part, r.persistent_id)), None) or \
            next((r for r in refs if followed is not None and r.persistent_id == followed.persistent_id), None) or refs[0]
        try:
            table = self.pedal_tones.table(ref)
        except (PedalTonesError, ArchiveError, OSError) as exc:
            raise GuiError(str(exc)) from exc
        return {**table, "parts": [{"part": r.part, "name": r.name or r.part} for r in refs],
                "played": followed is not None and ref.persistent_id == followed.persistent_id}

    def set_song_pedal_tones(self, body: dict[str, Any]) -> dict[str, Any]:
        """A song's tone given a category of its own ("tone", "category": a category, or None for auto), or a preset
        of its own ("tone", "pedal": a slot, or None); or the song's style ("style": a style, or None)."""
        sid, part = str(body.get("id") or ""), body.get("part") or None
        refs = [r for r, _a in self._load(sid)]
        ref = next((r for r in refs if part in (r.part, r.persistent_id)), None)
        if ref is None:
            raise GuiError("Which part?")
        try:
            if "style" in body:
                self.pedal_tones.set_style(ref, body["style"] or None)
            elif "category" in body:
                self.pedal_tones.set_tone(ref, str(body.get("tone") or ""), category=body["category"] or None,
                                          change="category")
            elif "pedal" in body:
                pedal = body["pedal"]
                self.pedal_tones.set_tone(ref, str(body.get("tone") or ""), change="pedal",
                                          pedal=None if pedal is None else int(_number(pedal, "preset", 1, PEDAL_SLOTS)))
            else:
                raise GuiError("Change what?")
        except PedalTonesError as exc:
            raise GuiError(str(exc)) from exc
        return self.song_pedal_tones(sid, ref.part)

    def pedal_rules(self) -> dict[str, Any]:
        """The pedal's rules for every song (PedalTones.rules), for the page's Pedal tone rules card."""
        return self.pedal_tones.rules()

    def set_pedal_rules(self, body: dict[str, Any]) -> dict[str, Any]:
        """A rule changed: the tone map's ("rule": its path, "slot": a preset or None), an artist's category in RS
        Companion's mapping ("artist", "from", "to": a category or None), a style for a song key or an artist
        ("style_for", "style": or None), or the styles by heaviness ("styles": [[name, from]...])."""
        tones = self.pedal_tones
        try:
            if "rule" in body:
                slot = body.get("slot")
                tones.set_rule(list(body["rule"] or []), None if slot is None else int(_number(slot, "preset", 1, PEDAL_SLOTS)))
            elif "artist" in body:
                tones.set_artist_category(str(body["artist"]), str(body.get("from") or ""), body.get("to") or None)
            elif "style_for" in body:
                tones.set_named_style(str(body["style_for"]), body.get("style") or None)
            elif "styles" in body:
                tones.set_styles(list(body["styles"] or []))
            else:
                raise GuiError("Change what?")
        except PedalTonesError as exc:
            raise GuiError(str(exc)) from exc
        return tones.rules()

    def keep_pedal_tone(self, slot: int | None = None) -> str:
        """Following, the pedal's preset (or slot) kept for the tone the game plays now, for its song; on the song's
        own preset for it already, back to automatic. What it did, said short (for the Launchkey's screen)."""
        current, _at = self._followed_now()
        tone = self._tone_playing()
        if current is None or tone is None:
            raise GuiError("No song tone playing")
        slot = slot if slot is not None else self.pedal.slot
        if slot is None:
            raise GuiError("The pedal's preset isn't known")
        back = tone["own"] and tone["pedal"] == slot
        try:
            self.pedal_tones.set_tone(current, tone["key"], change="pedal", pedal=None if back else slot)
        except PedalTonesError as exc:
            raise GuiError(str(exc)) from exc
        return f"{tone['short']}: automatic" if back else f"{tone['short']}: {slot:03d} kept"

    def pedal_name(self, slot: int | None) -> str:
        return self.pedal.name(slot)

    def set_pedal(self, body: dict[str, Any], later: float = 0.0) -> dict[str, Any]:
        """Switches the pedal: to a slot ("slot"), some on from the one it's on ("step", round from the last to the
        first), or back to the one before ("back"); after later seconds, if it's not switched again by then. Or
        holds its tone ("hold": true; dnafx-preset's listener doesn't switch it with the song's tones), or lets it
        follow them again (false)."""
        if "hold" in body:
            try:
                self.pedal.hold(bool(body["hold"]))
            except PedalError as exc:
                raise GuiError(str(exc)) from exc
            return self.pedal_info()
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

        def parts_for(played: ArrangementRef) -> list[tuple[ArrangementRef, int, bool]]:  # (one muted that lights
            # its keys: planned muted, lighting them; one muted as it plays stays in what plays, muted:
            # Follower.mute_parts)
            return [(r, lights, play) for r, play, lights in parts_chosen(settings, played, self._song_refs(played))
                    if play or lights]

        return parts_for

    def _song_refs(self, ref: ArrangementRef) -> list[ArrangementRef]:
        """The arrangements of a song with notes (its vocal melody, once it's made), lead first."""
        return sorted((r for r in self.index.of_song(ref) if self._playable(r)), key=part_order)

    def _playable(self, ref: ArrangementRef) -> bool:
        """An arrangement with notes to play: a vocals part too, once its melody's made (melody), if the vocal melody
        (a beta feature) is on."""
        return ref.part not in NO_NOTES or (ref.part == "vocals" and self._beta("melody") and
                                            melody.kept(ref) is not None)

    def _beta(self, name: str) -> bool:
        """Whether a beta feature's on (BETAS)."""
        return bool((self.settings.get("beta") or {}).get(name))

    def _betas_changed(self, were: dict[str, bool], now: dict[str, bool]) -> None:
        """Beta features turned on or off: the songs' parts read again (the vocal melody among them, or not), the
        song playing's audio with its guitar as one or its lead and rhythm, and what plays (or follows) goes on with
        them."""
        with self._lock:
            self._loaded.clear()
            self.play_version += 1
            session = self._session
        playing = self._playing["id"] if self._playing else None
        try:
            if isinstance(session, Following):
                self._follow_on(session)
            elif playing:
                self._play_on(playing)
                if were.get("sides") != now.get("sides") and self._song_audio is not None:
                    self._audio_on(playing)
        except Exception:  # (the song stopped meanwhile)
            pass

    @staticmethod
    def arrangement_of(ref: ArrangementRef) -> Arrangement:
        """An arrangement's notes: its chart's; a vocals part's, its melody (melody.kept)."""
        loaded = ref.load()
        if ref.part == "vocals":
            notes = melody.kept(ref)
            if notes is not None:
                return melody.arrangement(loaded, notes)
        return loaded

    def _follow_voices(self) -> VoicesFor | None:
        """The voices for each arrangement played: its own tones, else the defaults for its kind of part, else the
        built-in ones (None: silent lights)."""
        settings = self.follow_settings
        if self.play_options["silent"]:
            return None

        def voices_for(ref: ArrangementRef, channels: tuple[int, int, int]) -> Voices:
            own, defaults = settings["arrangements"].get(ref.persistent_id, {}), \
                settings.get(kind(ref), KIND_DEFAULTS.get(kind(ref), {}))

            def pick(field: str) -> Any:
                return own.get(field, defaults.get(field))

            voices = SoundChoice({None: _tone(pick("tone"))}, {None: _pinch(pick("pinch_tone"))},
                                 {None: _tone(pick("mute_tone"))}, settings["real_pitch"],
                                 ring=bool(self.settings["ring"]),
                                 tone_map=self._tones_for_kinds(), techniques=bool(self.settings["techniques"]),
                                 overlap=self.settings["overlap_ms"] / 1000, squeal=self._squeal()).voices(ref, channels)
            if pick("follow_tones") is False:
                voices = replace(voices, tone_map=())
            return self._routed(replace(voices, volume=self._volume(self._slot(ref))), ref)

        return voices_for

    def _check_sound_channel(self) -> None:
        channel = int(self.settings["channel"])
        if channel in KEYBOARD_CHANNELS:
            raise GuiError(f"The lights' channel ({channel}) is one the sound plays on: choose another in Keyboard")

    # A remote's keys (the Launchkey's), played on the keyboard

    def keys_info(self, there: bool = False) -> dict[str, Any]:
        """The keys' settings (asked often: the state's); there, whether the device they play on is there too
        (opened to know: not every time the state's asked)."""
        number = self._keys_tone()
        found = tone(number)
        out = self.settings["keys_output"]
        info = {"sound": bool(self.settings["keys_sound"]), "lights": bool(self.settings["keys_lights"]),
                "tone": number, "name": f"{found.number:03d} {found.name}", "volume": int(self.settings["keys_volume"]),
                "output": out}
        if there:
            info["output_there"] = out is None or self._synth(out["port"]) is not None
        return info

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
        if "output" in body:  # (another MIDI device: {port, channel}; none: the keyboard)
            changed["keys_output"] = _keys_output(body["output"])
        with self._keys_lock:
            if "keys_output" in changed and changed["keys_output"] != self.settings["keys_output"]:
                self._stop_keys_out()  # (what sounds where they played: stopped)
            self.settings.update(changed)
            keyboard = self._keys_keyboard(open_one=False)
            if "keys_volume" in changed and keyboard is not None and self._keys_voiced is not None:
                keyboard.set_volume(KEYS_CHANNEL, changed["keys_volume"])
        self._kept.update(changed)
        self._save()
        return self.keys_info(there=True)

    def play_keys(self, message: list[int]) -> None:
        """A remote's key going down or up, its sustain pedal or its modulation, from its MIDI thread: handed on to
        the keys' own thread."""
        with self._keys_lock:
            if self._keys_thread is None:
                self._keys_thread = threading.Thread(target=self._keys_run, name="rs-keylights keys", daemon=True)
                self._keys_thread.start()
        self._keys_queue.put(list(message))
        self._held_now(message)

    def release_keys(self) -> None:
        """A remote gone: its keys let go, its notes stopped (after the ones it sent)."""
        if self._keys_thread is not None:
            self._keys_queue.put([])
        self._held_now([])

    def _held_now(self, message: list[int]) -> None:
        """The keys held down after a key's message (none: all let go), for the page (held_keys)."""
        status = message[0] & 0xF0 if message else None
        if status not in (None, 0x80, 0x90):
            return  # (the sustain pedal, the modulation strip)
        with self._held_changed:
            if status is None:
                self._held.clear()
            elif status == 0x90 and message[2] > 0:
                self._held[message[1]] = message[2]
            else:
                self._held.pop(message[1], None)
            self._held_version += 1
            self._held_changed.notify_all()

    def press_key(self, body: dict[str, Any]) -> dict[str, Any]:
        """A key pressed on the page ("key", a MIDI note; "down": true, or false let go; "velocity", 1 to 127): played
        as the Launchkey's keys are (where they play, with their tone, lighting it). With a "tone" (a fretboard's
        note: its arrangement's), it sounds on the keyboard in that tone, whether the keys' own sound is on or not."""
        key = int(_number(body.get("key"), "key", 0, 127))
        velocity = int(_number(body.get("velocity", PRESSED_VELOCITY), "velocity", 1, 127))
        message = [0x90, key, velocity] if body.get("down", True) else [0x80, key, 0]
        if body.get("tone") is not None:
            number = int(_number(body["tone"], "tone", 0, 1000))
            if number not in TONE_NUMBERS:
                raise GuiError(f"No tone {number}")
            message.append(number)
        self.play_keys(message)
        return {"held": sorted(self._held)}

    def held_keys(self, since: int = -1, wait: float = HELD_WAIT) -> dict[str, Any]:
        """The keys held down now, and their version: once it isn't since any more (a key going down or up), or
        after wait seconds (the page asks again)."""
        with self._held_changed:
            self._held_changed.wait_for(lambda: self._held_version != since, timeout=wait)
            return {"version": self._held_version, "keys": sorted(self._held)}

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
        toned = message[3] if len(message) > 3 else None  # (a fretboard's note: in its arrangement's tone, here)
        down = status == 0x90 and value > 0
        sound = (bool(self.settings["keys_sound"]) or toned is not None) and not self.play_options["silent"]
        lights = bool(self.settings["keys_lights"])
        out = self.settings["keys_output"]
        synth = self._synth(out["port"]) if out is not None and sound and toned is None else None  # (else here)
        with self._keys_lock:
            if synth is not None and out is not None:
                channel = int(out["channel"])
                if status == 0xB0:
                    synth.control(channel, number, value)
                elif down:
                    synth.play(channel, (number,), value)
                    self._keys_out[number] = (synth, channel)
                elif number in self._keys_out:
                    was, was_channel = self._keys_out.pop(number)
                    was.stop(was_channel, (number,))
                keyboard = self._keys_keyboard(open_one=down and lights)
                if keyboard is not None and status != 0xB0:
                    if down and lights and KEY_RANGE.lowest <= number <= KEY_RANGE.highest:
                        keyboard.hold((number,))
                    elif not down:
                        keyboard.let_go((number,))
                        keyboard.sound(KEYS_CHANNEL, number, 0)  # (one played here before it was moved)
                return
            if not down and number in self._keys_out:  # (played there, moved here since)
                was, was_channel = self._keys_out.pop(number)
                was.stop(was_channel, (number,))
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
                    self._voice_keys(keyboard, toned)
                    keyboard.sound(KEYS_CHANNEL, number, value)
            else:
                keyboard.let_go((number,))
                keyboard.sound(KEYS_CHANNEL, number, 0)

    def _release_keys(self) -> None:
        with self._keys_lock:
            self._stop_keys_out()
            keyboard = self._keys_keyboard(open_one=False)
            if keyboard is not None:
                keyboard.let_go_all()

    def _stop_keys_out(self) -> None:
        """The keys' notes sounding on another device stopped (its sustain pedal up too)."""
        for number, (synth, channel) in sorted(self._keys_out.items()):
            synth.stop(channel, (number,))
        for synth, channel in set(self._keys_out.values()):
            synth.control(channel, 64, 0)
        self._keys_out.clear()

    def _keys_keyboard(self, open_one: bool) -> Keyboard | None:
        """The keyboard a remote's keys play: the song's while one plays or is followed, else their own (or a
        show's while nothing plays), opened if asked."""
        session = self._session
        if session is not None and session.running:
            return session.keyboard
        if self._idle_keyboard is None and open_one:
            self._idle_keyboard = self._open_keyboard()
        return self._idle_keyboard

    def _voice_keys(self, keyboard: Keyboard, number: int | None = None) -> None:
        """The keys' channel in their tone (or the one given: a fretboard's note's), set only as it changes."""
        number = self._keys_tone() if number is None else number
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

    def _trace_audio(self) -> None:
        """What the song's audio did to keep with the song since last asked (its jumps and catch-ups), to the trace
        (--trace): written here, not on the sound device's thread."""
        audio = self._song_audio
        if audio is None or self._trace is None:
            return
        while audio.events:
            at, what, off = audio.events.popleft()
            self._trace({"t": round(at, 3), "audio": what, "off": off, "position": self.song_position()})

    def state(self) -> dict[str, Any]:
        self._trace_audio()
        game_list = self.game_songs() is not None  # (asked first: not while it's locked)
        with self._lock:
            session = self._session
            out: dict[str, Any] = {"mode": "idle", "sound": not self.play_options["silent"],
                                   "volumes": list(self.settings["volumes"]),
                                   "other_follow": self._other_follow_cached(),
                                   "follow_version": self.follow_version, "play_version": self.play_version,
                                   "showing": self.showing(), "active": self.play_options.get("song"),
                                   "browse": self._browse_state(), "remote": self._remote_state(),
                                   "game_list": game_list,
                                   "keys": self.keys_info(), "loop": self.loop_info(None),
                                   "listening": self.listening(), "pedal": self.pedal_info(),
                                   "plays": self._recorder.changed,
                                   "warmup": {**self._warmup.info(), "run": self._warmup_run,
                                              "hearing": self._warmup_heard()}
                                   if self._warmup is not None else None,
                                   "launchkey": self._watch.status if self._watch is not None else None,
                                   "tone_field": self._tone_field["field"],  # (the knobs' tones' page: the song
                                   # card's rows show that tone)
                                   "making": {"stems": {sid: making.get("progress") or 0.0
                                                        for sid, making in list(self._splitting.items())
                                                        if making.get("making")},
                                              "drums": {sid: making.get("progress") or 0.0
                                                        for sid, making in list(self._making.items())
                                                        if making.get("step")},
                                              "melody": {sid: making.get("progress") or 0.0
                                                         for sid, making in list(self._melody_making.items())
                                                         if making.get("step")}}}
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

    @property
    def _keyboard_name(self) -> str:
        """The keyboard's name, for saying (the CASIO LK-170, by its port's)."""
        return keyboard_name(str(self.settings["midi_port"]))

    def get_settings(self) -> dict[str, Any]:
        try:
            ports = self._list_ports()
        except Exception:
            ports = []
        return {**self.settings, "ports": ports, "game_dir": str(self.index.game_dir), "overlay_port": self.overlay_port,
                "outputs_there": self.output_status(), "inputs": _audio_inputs(),
                "input_channels": _audio_input_channels(),
                "keyboard_name": keyboard_name(str(self.settings["midi_port"])),
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
        listen_channel = int(_number(body.get("listen_channel", self.settings["listen_channel"]), "input", 1, 64))
        if listen_device != self.settings["listen_device"] and "listen_channel" not in body:
            listen_channel = 1  # (another device: its first, unless one's chosen with it)
        most = _audio_input_channels().get(listen_device)
        if most is not None and listen_channel > most:
            raise GuiError(f"{listen_device} has {most} inputs")
        backup = str(body.get("pedal_backup", self.settings["pedal_backup"]) or "").strip()
        beta = _betas(self.settings["beta"], body.get("beta") or {})
        pedal_input = _pedal_input(body["pedal_input"]) if "pedal_input" in body else self.settings["pedal_input"]
        if pedal_input is not None:
            most = _audio_input_channels().get(pedal_input["device"])
            if most is not None and pedal_input["channel"] > most:
                raise GuiError(f"{pedal_input['device']} has {most} inputs")
        favourites = _favourites(body.get("pedal_favourites", self.settings["pedal_favourites"]))
        asked, known = _recalled(self.settings["outputs"], body.get("outputs") or {},
                                 self.settings.get("outputs_known"))
        outputs = _outputs(self.settings["outputs"], asked)
        for part, chosen in outputs.items():  # (and as they are now: remembered for their devices)
            known.setdefault(part, {})[chosen["port"]] = chosen
        tone_map = _tone_map(self.settings["tone_map"], body.get("tone_map") or {})
        try:
            pinch = asdict(squeal_from(self._squeal(), body.get("pinch") or {}))
        except (ValueError, AttributeError) as exc:
            raise GuiError(str(exc) if isinstance(exc, ValueError) else "Expected the pinch harmonics' settings") from None
        if not port:
            raise GuiError("Choose the keyboard's MIDI output")
        sounded = (self.settings["ring"], self.settings["tone_changes"], self.settings["tone_map"],
                   self.settings["techniques"], self.settings["outputs"], self.settings["overlap_ms"],
                   self.settings["pinch"])
        changed = dict(midi_port=port, channel=channel, lights_early_ms=early, fastest_flash_ms=flash, launchkey=launchkey,
                       ring=ring, tone_changes=tone_changes, tone_map=tone_map, techniques=played, outputs=outputs,
                       outputs_known=known,
                       overlap_ms=overlap, listen=listen, listen_device=listen_device, listen_channel=listen_channel, pedal_backup=backup,
                       pedal_favourites=favourites, pinch=pinch, beta=beta, pedal_input=pedal_input)
        betas_were = self.settings["beta"]
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
        if beta != betas_were:
            self._betas_changed(betas_were, beta)
        if (ring, tone_changes, tone_map, played, outputs, overlap, pinch) != sounded:  # (the notes ringing, tone
            # changes, techniques, other MIDI devices and pinch harmonics: at once)
            if isinstance(session, Following):
                self._follow_on(session)
            elif self._playing:
                self._play_on(self._playing["id"])
        return self.get_settings()

    def _squeal(self) -> Squeal:
        return Squeal(**self.settings["pinch"])

    def try_pinch(self, body: dict[str, Any]) -> dict[str, Any]:
        """The pinch harmonics as set (or as given, not kept), heard on the keyboard: a riff's chugs, a quick pinch
        harmonic, then a held one with vibrato (as charted), on the lead parts' tones (channels 9 and 2: those a
        fourth part would take)."""
        try:
            shape = squeal_from(self._squeal(), body.get("pinch") or {})
        except (ValueError, AttributeError) as exc:
            raise GuiError(str(exc) if isinstance(exc, ValueError) else "Expected the pinch harmonics' settings") from None
        lead = self.follow_settings["lead"]
        main = _tone(lead.get("tone")) or tone(GUITAR_TONE)
        pinch = _pinch(lead.get("pinch_tone"))
        with self._keys_lock:
            keyboard = self._keys_keyboard(open_one=True)
        if keyboard is None:
            raise GuiError(f"No {self._keyboard_name} to play it on")
        threading.Thread(target=_pinch_demo, args=(keyboard, shape, main, pinch), daemon=True).start()
        return {"playing": True}

    def _routed(self, voices: Voices, ref: ArrangementRef) -> Voices:
        """A part's voices on the MIDI device chosen for its kind of part (outputs), if it's there: all its notes on
        its channel, with the device's own sound, a note at a time if so chosen; else on the keyboard."""
        chosen = self.settings["outputs"].get(kind(ref))
        synth = self._synth(chosen["port"]) if chosen else None
        if chosen is None or synth is None:
            return voices
        return replace(voices, main=Voice(chosen["channel"], voices.main.tone), squeal=None, mute=None, output=synth,
                       mono=chosen["mono"], program=chosen.get("program"), transpose=12 * chosen.get("octave", 0))

    def set_program(self, part: str, program: int) -> None:
        """A remote's knob on an arrangement sounding on another MIDI device (its kind's: part): one of the
        device's own programs (0: its first), kept and sent at once."""
        with self._lock:
            chosen = self.settings["outputs"].get(part)
            if chosen is None:
                raise GuiError(f"The {part} parts sound on the {self._keyboard_name}")
            number = _program(program, chosen.get("programs", PROGRAMS))
            if number is None:
                raise GuiError("Expected one of its programs")
            chosen = {**chosen, "program": number}
            self.settings["outputs"] = self._kept["outputs"] = {**self.settings["outputs"], part: chosen}
            self._save()
        synth = self._synth(chosen["port"])
        if synth is not None:
            synth.program(chosen["channel"], chosen["program"])

    def _output_of(self, part: str) -> dict[str, Any] | None:
        """For a remote: the MIDI device a kind of part sounds on (its name, program and how many it has), if it isn't
        the keyboard."""
        chosen = self.settings["outputs"].get(part)
        return ({"port": chosen["port"], "program": chosen.get("program"),
                 "programs": chosen.get("programs", PROGRAMS)} if chosen else None)

    def _synth(self, port: str) -> Synth | None:
        """Another MIDI device, opened (once: it stays open); None if it isn't there (or it's the keyboard). One
        that wasn't there isn't looked for again for SYNTH_LOOK_AGAIN (asked often: a remote's state)."""
        if port == self.settings["midi_port"]:
            return None
        with self._lock:
            synth = self._synths.get(port)
            missed = self._synths_missing.get(port)
        if synth is not None and synth.is_open:
            return synth
        if missed is not None and time.monotonic() - missed < SYNTH_LOOK_AGAIN:
            return None
        synth = self._synth_factory(port)
        try:
            synth.open()
        except (KeyboardError, OSError, RuntimeError):
            with self._lock:
                self._synths_missing[port] = time.monotonic()
            return None
        with self._lock:
            self._synths[port] = synth
            self._synths_missing.pop(port, None)
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
                song = self.play_options["song"]
                data["play"] = {**self.play_options, "song": kept_id(song, self.index.game_dir) if song else song}
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
            kept = {kept_id(sid, self.index.game_dir): value for sid, value in self.song_settings.items()}
            temp.write_text(json.dumps(kept, indent=1) + "\n")
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
        self._recorder.stop()
        with self._taps_lock:
            taps, self._taps = list(self._taps.values()), {}
        for tap in taps:
            tap.stop()
        for synth in list(self._synths.values()):  # (other MIDI devices: their notes stopped)
            synth.close()
        if self._listener is not None:
            self._listener.stop()

    def _stop(self) -> None:
        """Stops what plays, and closes the keyboard a remote's keys had (before another opens: closing one
        turns the lights' channel up again)."""
        self._end_idle_show()
        self._audio_off()
        with self._keys_lock:
            warming, self._warmup = self._warmup, None
            if warming is not None:
                warming.stop()
            self._stop_hearing()
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


TECHNIQUES_SAID = ((BEND, "bend"), (SLIDE, "slide"), (HAMMER_ON, "hammer-on"), (PULL_OFF, "pull-off"),
                   (PALM_MUTE, "palm mute"), (PINCH_HARMONIC, "pinch harmonic"), (HARMONIC, "harmonic"), (TAP, "tap"),
                   (TREMOLO, "tremolo"))


def _note_said(a: Arrangement, note: Note, bass: bool) -> str:
    """A note as the page says it: a chord's name and frets (low to high, x not played), or a note's name, string
    (numbered as tab has them: 1 the thinnest) and fret; and how it's played (bend, slide...)."""
    strings, pitches = 4 if bass else 6, list(note.pitches)
    if len(pitches) > 1:
        chord = next((c for c in a.chords if c.start - 0.01 <= note.time <= c.end + 0.01), None)
        frets = dict(zip(note.strings, note.frets))
        shape = " ".join(str(frets[s]) if s in frets else "x" for s in range(strings))
        said = f"{chord.name if chord else 'Chord'} ({shape})"
    else:
        on = list(zip(note.strings, note.frets))
        said = f"{note_name(pitches[0])} · string {strings - on[0][0]}, fret {on[0][1]}" if on else \
            note_name(pitches[0])
    how = [name for mask, name in TECHNIQUES_SAID if note.mask & mask]
    if note.bends and "bend" not in how:
        how.insert(0, "bend")
    if (note.slide or note.slide_off) and "slide" not in how:
        how.append("slide")
    if note.vibrato:
        how.append("vibrato")
    return said + (" · " + ", ".join(how) if how else "")


def song_export(loaded: list[tuple[ArrangementRef, Arrangement]], vocals: Arrangement | None = None) -> tuple[str, bytes]:
    """A song's MIDI file (midiexport.py) and a name for it: its parts with notes (as loaded: the page's order), its
    vocals' lyrics and melody, its drum part if one was made, its key, and its sections as markers."""
    if not loaded:
        raise GuiError("No parts with notes in this song")
    named = next((r for r, _a in loaded if r.artist), loaded[0][0])
    parts: list[midiexport.ExportPart] = []
    for ref, a in loaded:
        name = ref.name or ref.part.capitalize()
        if any(p.name == name for p in parts):
            name = f"{name} ({ref.part})"
        parts.append(midiexport.ExportPart(name, kind(ref), a))
    key = find_key([a for _r, a in loaded], [r.bass for r, _a in loaded])
    raw = next((a.sections for _r, a in loaded if a.sections), ())
    data = midiexport.song_midi(named.title, parts, markers=[(s["start"], s["name"]) for s in _sections(raw)],
                                lyrics=vocals.lyrics if vocals else (),
                                drums=drums.kept(loaded[0][0], beats=next((a.beats for _r, a in loaded if a.beats), ()))
                                or (),
                                key=(key.tonic, key.mode) if key else None)
    return midiexport.file_name(named.title, ".mid"), data


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


def _warmup_choices(settings: dict[str, Any], strict: bool = False) -> dict[str, Any]:
    """What to practise (the warm-up, or a drill), in which tuning, with which scale, as kept (or asked for: strict,
    a choice there isn't says so; else it's put back to the default)."""
    what, tuning, scale = settings.get("warmup_what"), settings.get("warmup_tuning"), settings.get("warmup_scale")
    if what not in ("routine", *warmup.DRILL_KEYS):
        if strict:
            raise GuiError(f"No such drill: {what}")
        what = "routine"
    if tuning not in dict(warmup.TUNINGS):
        if strict:
            raise GuiError(f"No such tuning: {tuning}")
        tuning = warmup.TUNINGS[0][0]
    scale = scale if isinstance(scale, dict) else {}
    kind, tonic, start = scale.get("kind", "pentatonic"), scale.get("tonic"), scale.get("start")
    if kind not in ("pentatonic", *warmup.SCALES) or not (tonic is None or isinstance(tonic, int) and
                                                           not isinstance(tonic, bool) and 0 <= tonic < 12) or \
            not (start is None or isinstance(start, int) and not isinstance(start, bool) and 0 <= start <= 15):
        if strict:
            raise GuiError(f"No such scale: {scale}")
        kind, tonic, start = "pentatonic", None, None
    return {"warmup_what": what, "warmup_tuning": tuning, "warmup_scale": {"kind": kind, "tonic": tonic, "start": start}}


def _warmup_speeds(value: Any) -> dict[str, int]:
    """Each warm-up drill's speed, as kept (the rest left out)."""
    if not isinstance(value, dict):
        return {}
    drills = {d.key: d for d in warmup.routine()}
    return {k: v for k, v in value.items() if k in drills and warmup.kept_speed(drills[k], v) == v}


def _pedal_input(given: Any) -> dict[str, Any] | None:
    """Where the pedal's sound comes back in: an audio input and its channel (None: not set)."""
    if given is None or (isinstance(given, dict) and not given.get("device")):
        return None
    if not isinstance(given, dict) or not isinstance(given.get("device"), str):
        raise GuiError("Expected the pedal's input: its device and channel")
    return {"device": given["device"], "channel": int(_number(given.get("channel", 1), "the pedal's input", 1, 64))}


def _betas(were: dict[str, Any], given: Any) -> dict[str, bool]:
    """The beta features on or off (BETAS): as they were, those given changed."""
    if not isinstance(given, dict) or any(k not in BETAS for k in given):
        raise GuiError(f"Expected beta features ({', '.join(BETAS)}) on or off")
    return {name: bool(given.get(name, (were or {}).get(name, False))) for name in BETAS}


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


DEMO_NOTE, DEMO_SQUEAL = 9, 2  # the channels a pinch harmonic's tried on (try_pinch)


def _pinch_demo(keyboard: Keyboard, shape: Squeal, main: Tone, pinch: Tone | None) -> None:
    """Chugs, a quick pinch harmonic (no vibrato charted), chugs, then a held one (vibrato charted), as a song plays
    them (the squeal on the note's channel if it has no tone of its own: without its own pitch)."""
    squeal_channel = DEMO_SQUEAL if pinch is not None else DEMO_NOTE
    keyboard.set_voice(DEMO_NOTE, main.program, main.bank)
    if pinch is not None:
        keyboard.set_voice(DEMO_SQUEAL, pinch.program, pinch.bank)
    low, fretted = 40, 52  # (E2 chugs; E3 pinched)

    def chugs(count: int) -> None:
        for _ in range(count):
            keyboard.play(DEMO_NOTE, (low,), 88)
            time.sleep(0.12)
            keyboard.stop(DEMO_NOTE, (low,))
            time.sleep(0.06)

    def pinched(length: float, vibrato: bool) -> None:
        light = Light(0.0, 0.0, (), (fretted,), PINCH_HARMONIC, rings=(length,), vibrato=vibrato, extra=True)
        bends = squeal_bends(light, length, shape) if pinch is not None else []
        squeal = min(fretted + shape.up, 127)
        if bends:
            keyboard.bend(squeal_channel, bends[0][1])
        keyboard.play(DEMO_NOTE, (fretted,), shape.note_velocity)
        keyboard.play(squeal_channel, (squeal,), shape.velocity)
        began = time.monotonic()
        for at, up in bends[1:]:
            time.sleep(max(0.0, at - (time.monotonic() - began)))
            keyboard.bend(squeal_channel, up)
        time.sleep(max(0.0, length - (time.monotonic() - began)))
        keyboard.stop(DEMO_NOTE, (fretted,))
        keyboard.stop(squeal_channel, (squeal,))
        keyboard.bend(squeal_channel, 0.0)

    chugs(3)
    pinched(0.15, vibrato=False)
    chugs(4)
    pinched(1.2, vibrato=True)


def _keys_output(value: Any) -> dict[str, Any] | None:
    """Where a remote's keys play, checked: another MIDI device ({port, channel 1-16}), or None (the keyboard)."""
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict) or not str(value.get("port") or "").strip():
        raise GuiError("Choose the MIDI output the Launchkey's keys play")
    return {"port": str(value["port"]).strip(), "channel": int(_number(value.get("channel", 1), "channel", 1, 16))}


def _pinch(value: Any) -> Tone | None:
    """Pinch harmonics' tone: as chosen, GM GT HARMONICS if none is (SAME_TONE: none of their own)."""
    if value in (None, ""):
        return tone(PINCH_TONE)
    return _tone(value)


def _split_server_ok(server: Any) -> bool:
    """Whether a kept split server is one: None (none), or its address and key."""
    return server is None or (isinstance(server, dict) and isinstance(server.get("url"), str) and
                              isinstance(server.get("key"), str) and bool(server["url"] and server["key"]))


def _stem_weights(options: dict[str, Any], parts: Any) -> dict[str, float]:
    """The song's parts' weights (0 to 1) from playing's options: their volumes (0 to 100; muted: 0), for those it
    has."""
    volumes, mutes = options["stem_volumes"], options["stem_mutes"]
    return {stem: 0.0 if mutes.get(stem) else volumes.get(stem, 100) / 100 for stem in parts}


def _audio_outputs() -> list[str]:
    """The audio outputs there are (to play a song's own audio on), if sounddevice is installed."""
    try:
        import sounddevice
    except ImportError:
        return []
    try:
        return [str(d["name"]) for d in sounddevice.query_devices() if d["max_output_channels"] > 0]
    except Exception:  # (no audio)
        return []


def _audio_input_channels() -> dict[str, int]:
    """Each audio input's channels (an interface's inputs), if sounddevice is installed."""
    try:
        import sounddevice
        return {str(d["name"]): int(d["max_input_channels"]) for d in sounddevice.query_devices()
                if d["max_input_channels"] > 0}
    except Exception:  # (none, or no audio)
        return {}


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
              cache: str = "no-store", download: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if download:  # (saved as a file of this name)
            plain = download.encode("ascii", "replace").decode().replace("?", "_").replace('"', "'")
            self.send_header("Content-Disposition", f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(download)}")
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
            elif url.path == "/api/drums/part":
                self._json(200, gui.drum_part(parse_qs(url.query).get("id", [""])[0]))
            elif url.path == "/api/warmup":
                self._json(200, gui.warmup_info(parse_qs(url.query).get("id", [""])[0] or None))
            elif url.path == "/api/warmup/progress":
                self._json(200, gui.warmup_progress())
            elif url.path == "/api/theory":
                query = parse_qs(url.query)
                self._json(200, gui.theory(query.get("id", [""])[0], query.get("part", [None])[0] or None))
            elif url.path == "/api/lyrics":
                self._json(200, gui.lyrics(parse_qs(url.query).get("id", [""])[0]))
            elif url.path == "/api/songtones":
                query = parse_qs(url.query)
                self._json(200, gui.song_pedal_tones(query.get("id", [""])[0], query.get("part", [None])[0] or None))
            elif url.path == "/api/pedal/names":
                self._json(200, gui.pedal_names())
            elif url.path == "/api/pedal/rules":
                self._json(200, gui.pedal_rules())
            elif url.path == "/api/plays":
                query = parse_qs(url.query)
                self._json(200, gui.plays_info(query.get("id", [""])[0], query.get("run", [None])[0] or None))
            elif url.path == "/api/export/midi":
                name, data = gui.export_midi(parse_qs(url.query).get("id", [""])[0])
                self._send(200, data, "audio/midi", download=name)
            elif url.path == "/api/export/audio":
                name, data = gui.export_audio(parse_qs(url.query).get("id", [""])[0])
                self._send(200, data, "audio/wav", download=name)
            elif url.path == "/api/roll":
                query = parse_qs(url.query)
                self._json(200, gui.roll(query.get("id", [""])[0], query.get("part", [None])[0] or None))
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
            elif url.path == "/api/stems/server/log":
                since = parse_qs(url.query).get("since", ["0"])[0]
                self._json(200, gui.split_server_log(int(since) if since.isdigit() else 0))
            elif url.path == "/api/keys/held":  # (answered once they change: the page asks again then)
                since = parse_qs(url.query).get("since", ["-1"])[0]
                self._json(200, gui.held_keys(int(since) if since.lstrip("-").isdigit() else -1))
            elif url.path == "/api/state":
                self._json(200, gui.state())
            elif url.path == "/api/meters":
                self._json(200, gui.meters())
            elif url.path == "/api/tonecheck":
                self._json(200, gui.tone_check_info())
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
            "/api/game/focus": lambda: gui.game_focus(body),
            "/api/stop": gui.stop,
            "/api/follow": lambda: gui.follow(body),
            "/api/follow/settings": lambda: gui.set_follow(body),
            "/api/scale": lambda: gui.show_scale(body),
            "/api/scale/stop": gui.stop_scale,
            "/api/settings": lambda: gui.set_settings(body),
            "/api/pinch/try": lambda: gui.try_pinch(body),
            "/api/keys": lambda: gui.set_keys(body),
            "/api/keys/press": lambda: gui.press_key(body),
            "/api/volumes": lambda: gui.set_volumes(body.get("volumes") or {}),
            "/api/loop": lambda: gui.set_loop(body),
            "/api/pedal": lambda: gui.set_pedal(body),
            "/api/pedal/names": lambda: gui.pedal_names(read=True),
            "/api/songtones": lambda: gui.set_song_pedal_tones(body),
            "/api/pedal/rules": lambda: gui.set_pedal_rules(body),
            "/api/riff": lambda: gui.set_game_riff(body),
            "/api/game/music": lambda: gui.set_game_music(body),
            "/api/drums": lambda: gui.set_drums(body),
            "/api/melody": lambda: gui.set_melody(body),
            "/api/tonecheck": lambda: gui.tone_check(body),
            "/api/stems": lambda: gui.set_stems(body),
            "/api/stems/server": lambda: gui.set_split_server(body),
            "/api/game-stems": lambda: gui.set_game_stems(body),
            "/api/drums/hit": lambda: gui.hit_drum(body),
            "/api/warmup": lambda: gui.set_warmup(body),
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
