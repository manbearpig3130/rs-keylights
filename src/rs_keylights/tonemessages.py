from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .library import MANIFEST_PREFIX, SNG_PREFIX, ArrangementRef
from .psarc import Archive, ArchiveError
from .rig import (CATEGORIES, COMPANION_DATA, SLOT_NAMES, Effect, Presets, RigArrangement, amp_gain, drive, effects,
                  exact_tone, heaviness, read_tone, song_key, tone_def, tone_order)
from .riffloop import RiffLoop
from .sng import SngError, decrypt, tone_changes

# RS Companion's song tone messages (its docs/song-tone-messages.md), made from the game itself: when an arrangement
# loads, one SysEx (F0 7D, then JSON, F7) lists its tones, each with its category and effects, in the order it first
# plays them; then as it plays, Program Change first + i plays tone i (a little early, as RS Companion has it). Sent on
# a MIDI source of our own, with the arrangement and the time from the game's riffloop mod: what a listener (e.g.
# dnafx-preset, switching a DNAfx GiT's presets) needs, with no RS Companion. Its settings (rscompanion.json: the
# channel, the first program, the lead time) and mapping (presets.json) are used, as it would.

SOURCE = "Rocksmith Tones"  # the MIDI source's name
NON_COMMERCIAL = 0x7D  # the SysEx's manufacturer: one for non-commercial use
MAX_TONES = 5  # Base and A to D
POLL = 0.02  # seconds between asks while a song's loaded (its tone changes, this close)
IDLE = 0.1  # and without one
NO_GAME = 1.0  # and with no game answering
START = 1.5  # seconds a new source waits before sending: a listener finds it (dnafx-preset looks every second)
RECHECK = 1.0  # seconds between looks at presets.json and the settings


class MidiOut(Protocol):
    def send_message(self, message: list[int]) -> None: ...


class GameClock(Protocol):
    def time(self, wait: float = ...) -> tuple[float | None, str | None, str | None] | None: ...


class SongIndexProtocol(Protocol):
    def by_sng(self, name: str) -> ArrangementRef | None: ...

    def refresh(self, progress: Callable[[int, int], None] | None = None) -> int: ...


@dataclass(frozen=True)
class Settings:
    """RS Companion's settings for these (rscompanion.json), as it reads them."""
    channel: int = 1  # 1 to 16
    first_program: int = 8
    lead: float = 0.1  # seconds a tone's Program Change comes before it
    tone_programs: bool = True  # False: each tone's category's program instead
    programs: tuple[int, ...] = (0, 1, 2, 3)  # Clean, Crunch, Heavy, Lead

    @classmethod
    def read(cls, path: Path) -> Settings:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()

        def number(name: str, low: int, high: int, default: int) -> int:
            v = data.get(name)
            return min(max(v, low), high) if isinstance(v, int) and not isinstance(v, bool) else default
        listed = data.get("programs")
        named: dict[str, Any] = listed if isinstance(listed, dict) else {}
        programs = tuple(min(max(named[c], 0), 127) if isinstance(named.get(c), int) else p
                         for c, p in zip(CATEGORIES, cls.programs))
        flag = data.get("song_tone_programs")
        return cls(number("midi_channel", 1, 16, 1), number("song_tone_first_program", 0, 128 - MAX_TONES, 8),
                   number("lead_ms", 0, 2000, 100) / 1000, flag if isinstance(flag, bool) else True, programs)


@dataclass(frozen=True)
class SongRig:
    """A song's arrangements' tones and tone changes (but its vocals'), and its names."""
    arrangements: tuple[RigArrangement, ...]
    song: str = ""
    artist: str = ""

    def arrangement(self, name: str) -> RigArrangement | None:
        return next((a for a in self.arrangements if a.name.lower() == name.lower()), None)


def read_rig(archive: Path, arrangement: str) -> SongRig:
    """The song of an arrangement (amonpurs_lead), from its archive: each of its arrangements' manifest (its tones and
    their gear) and .sng's tone changes; its names from the arrangement's."""
    prefix = arrangement.rsplit("_", 1)[0].lower() + "_"
    found: list[RigArrangement] = []
    names = {"song": "", "artist": ""}
    with Archive(archive) as files:
        listed = set(files.names)
        for path in sorted(files.names):
            name = path.rsplit("/", 1)[-1][:-5] if path.endswith(".json") else ""
            if not (path.startswith(MANIFEST_PREFIX) and name.lower().startswith(prefix)):
                continue
            try:
                manifest = json.loads(files.read(path))
            except (ValueError, ArchiveError):
                continue
            entries = manifest.get("Entries") if isinstance(manifest, dict) else None
            entry = next(iter(entries.values()), None) if isinstance(entries, dict) else None
            a = entry.get("Attributes") if isinstance(entry, dict) else None
            kind = a.get("ArrangementName") if isinstance(a, dict) else None
            if not isinstance(a, dict) or not isinstance(kind, str) or kind.lower().endswith("vocals"):
                continue
            if name.lower() == arrangement.lower():
                names = {"song": str(a.get("SongName") or ""), "artist": str(a.get("ArtistName") or "")}
            changes: tuple[tuple[float, int], ...] = ()
            sng = f"{SNG_PREFIX}{name}.sng"
            if sng in listed:
                try:
                    changes = tone_changes(decrypt(files.read(sng)))
                except (SngError, ArchiveError):
                    pass
            strings = a.get("Tuning")
            tuning: dict[str, Any] = strings if isinstance(strings, dict) else {}
            found.append(RigArrangement(
                name, kind, tuple(str(a.get(f"Tone_{s}") or "") for s in SLOT_NAMES),
                tuple(read_tone(t) for t in a.get("Tones") or [] if isinstance(t, dict)), changes,
                _number(a.get("SongLength")), (_number(tuning.get("string0")) or 0.0) + (_number(a.get("CentOffset")) or 0.0) / 100,
                _number(a.get("SongAverageTempo"))))
    return SongRig(tuple(found), **names)


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class Tone:
    key: str
    category: str
    reason: str  # why the category (presets.json's: "tags CLEAN", "song override"...)
    effects: Mapping[str, Effect] = field(default_factory=dict)
    pedal: int | None = None  # the DNAfx slot with a preset made for it (pedalwriter), if any


@dataclass(frozen=True)
class SongTones:
    """The message: an arrangement's tones (up to five, in the order it first plays them), and its song's names,
    heaviness, style..."""
    song: str
    artist: str
    arrangement: str
    style: str
    styles: tuple[str, ...]
    heaviness: float | None
    tempo: float | None
    low_string: float | None
    first_program: int
    tones: tuple[Tone, ...]

    def index(self, key: str) -> int | None:
        return next((i for i, t in enumerate(self.tones) if t.key.lower() == key.lower()), None)

    def json(self) -> str:
        """As RS Companion writes it: keys sorted, no spaces, 7-bit (other characters \\u escaped)."""
        body: dict[str, Any] = {
            "v": 2, "kind": "song-tones", "song": self.song, "artist": self.artist, "arrangement": self.arrangement,
            "style": self.style, "styles": list(self.styles),
            "tones": [{"program": self.first_program + i, "key": t.key, "category": t.category,
                       "effects": {name: _effect(name, e) for name, e in t.effects.items()},
                       **({"pedal": t.pedal} if t.pedal is not None else {})}  # (rs-keylights' own: dnafx-preset's
                      for i, t in enumerate(self.tones)],                       # listener switches to it)
        }
        for name, value, places in (("heaviness", self.heaviness, 2), ("tempo", self.tempo, 1),
                                    ("low_string", self.low_string, 2)):
            if value is not None:
                body[name] = _decimal(value, places)
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def sysex(self) -> list[int]:
        return [0xF0, NON_COMMERCIAL, *self.json().encode("ascii"), 0xF7]

    def summary(self) -> str:
        """For the log: "Classic rock (0.72): 8 skid18an_clean Clean +modulation chorus, 9 ..."."""
        h = "?" if self.heaviness is None else f"{self.heaviness:.2f}"
        return f"{self.style} ({h}): " + ", ".join(
            f"{self.first_program + i} {t.key} {t.category}"
            + "".join(f" +{name} {e.kind}" if name != "reverb" else " +reverb" for name, e in sorted(t.effects.items()))
            for i, t in enumerate(self.tones))


def _effect(name: str, e: Effect) -> dict[str, Any]:
    out: dict[str, Any] = {"gear": e.gear, **{k: _decimal(v, 1) for k, v in e.knobs}}
    if name != "reverb":
        out["kind"] = e.kind
    return out


def _decimal(value: float, places: int) -> int | float:
    """To so many decimals, as RS Companion writes it (a whole number without its .0)."""
    rounded = round(value, places)
    return int(rounded) if rounded == int(rounded) else rounded


def song_tones(rig: SongRig, arrangement: str, presets: Presets, first_program: int = 8) -> SongTones | None:
    """An arrangement's message: its tones' categories as presets.json maps them (each with the tags, amp and gain the
    game lists for it, and its drive), and the song's style by its heaviness. None: no such arrangement."""
    a = rig.arrangement(arrangement)
    if a is None:
        return None
    tones = []
    for key, _seconds in tone_order(a)[:MAX_TONES]:
        slot = next((SLOT_NAMES[i] for i, s in enumerate(a.slots) if s.lower() == key.lower()), "Base")
        listed = exact_tone(a, key)
        amp = listed.gear.get("Amp") if listed else None
        defined = tone_def(a, key)
        m = presets.match(a.name, slot, key, listed.descriptors if listed else (), rig.artist,
                          amp.key if amp else None, amp_gain(amp) if amp else None, drive(defined) if defined else None)
        tones.append(Tone(key, m.category, m.reason, effects(defined) if defined else {}))
    heavy = heaviness(list(rig.arrangements))
    tempo = a.tempo if a.tempo is not None else next((x.tempo for x in rig.arrangements if x.tempo is not None), None)
    return SongTones(rig.song, rig.artist, a.name, presets.style(heavy, rig.artist, song_key(a.name)),
                     tuple(presets.style_names), heavy, tempo, a.low_string, first_program, tuple(tones))


class ToneSender:
    """Follows the game (its riffloop mod: the arrangement playing and its time) and sends its song tone messages:
    each arrangement's when it loads, then the Program Change of each tone as it comes."""

    def __init__(self, game: GameClock, index: SongIndexProtocol, out: MidiOut, folder: Path = COMPANION_DATA,
                 status: Callable[[str], None] = print, clock: Callable[[], float] = time.monotonic,
                 read: Callable[[Path, str], SongRig] = read_rig, pedal: Any = None):
        self.game, self.index, self.out, self.folder = game, index, out, folder
        self.pedal = pedal  # (a PedalWriter: the tones' presets written on the DNAfx as each song loads; None: not)
        self.status, self.clock, self._read = status, clock, read
        self.presets = Presets(folder)
        self.settings = Settings.read(folder / "rscompanion.json")
        self._settings_seen = self._stamp(folder / "rscompanion.json")
        self._recheck = clock() + RECHECK
        self.arrangement: str | None = None  # the game's, as it said last
        self.rig: SongRig | None = None
        self.song: SongTones | None = None  # its message
        self.sent: int | None = None  # the program sent last (sent only when it changes, as RS Companion does)
        self._answering: bool | None = None
        self._unknown: set[str] = set()  # arrangements not found: not looked for again
        self._said_old_mod = False
        if not self.presets.found:
            status(f"No RS Companion mapping ({self.presets.path}): every tone is Clean, its default")

    def step(self) -> float:
        """Asks the game once, and does what that needs; the seconds to wait before the next."""
        said = self.game.time()
        if said is None:
            if self._answering is not False:
                self.status("Rocksmith isn't answering (it isn't running, or hasn't the riffloop mod)")
            self._answering = False
            return NO_GAME
        if not self._answering:
            self.status("Following Rocksmith")
            self._answering = True
        timer, _screen, arrangement = said
        if arrangement == "":
            if not self._said_old_mod:
                self.status("The riffloop mod doesn't say which arrangement plays: make install rearmed-sidekicks again")
                self._said_old_mod = True
            return NO_GAME
        now = self.clock()
        if now >= self._recheck:
            self._recheck = now + RECHECK
            self._check_files()
        if arrangement != self.arrangement:
            self._load(arrangement)
        if self.rig is None or self.song is None or arrangement is None:
            return IDLE
        a = self.rig.arrangement(arrangement)
        if a is not None:
            self._play(a.key_at(float("-inf") if timer is None else timer + self.settings.lead))
        return POLL

    def run(self, sleep: Callable[[float], None] = time.sleep) -> None:
        sleep(START)
        while True:
            sleep(self.step())

    def _load(self, arrangement: str | None) -> None:
        self.arrangement, self.rig, self.song = arrangement, None, None
        if arrangement is None:
            self.status("No song")
            return
        if arrangement.lower() in self._unknown:
            return
        ref = self.index.by_sng(arrangement)
        if ref is None:
            self.index.refresh()  # (a song added since)
            ref = self.index.by_sng(arrangement)
        if ref is None:
            self._unknown.add(arrangement.lower())
            self.status(f"{arrangement}: not among the game's songs here")
            return
        started = self.clock()
        try:
            self.rig = self._read(Path(ref.archive), arrangement)
        except (OSError, ArchiveError, SngError) as exc:
            self.status(f"{arrangement}: can't read its song ({exc})")
            return
        self._describe(started)

    def _describe(self, started: float | None = None) -> None:
        """The message made (again) and sent; then the tone playing, its program, if that changed."""
        if self.rig is None or self.arrangement is None:
            return
        self.song = song_tones(self.rig, self.arrangement, self.presets, self.settings.first_program)
        if self.song is None:
            self.status(f"{self.arrangement}: not in its song's files")
            return
        if self.pedal is not None:  # (written before the message: the listener switches to them as it comes)
            slots = self.pedal.write(self.rig, self.arrangement, [t.key for t in self.song.tones])
            if slots:
                self.song = replace(self.song, tones=tuple(replace(t, pedal=slots.get(t.key)) for t in self.song.tones))
        message = self.song.sysex()
        self.out.send_message(message)
        took = f", {(self.clock() - started) * 1000:.0f} ms" if started is not None else ""
        self.status(f"Song: {self.song.song} by {self.song.artist}, {self.song.arrangement} (SysEx {len(message)} bytes"
                    f"{took}) {self.song.summary()}")
        for t in self.song.tones:
            self.status(f"  {t.key}: {t.category} ({t.reason})" + (f", its preset in slot {t.pedal}" if t.pedal else ""))

    def _play(self, key: str) -> None:
        if self.song is None:
            return
        i = self.song.index(key)
        if i is None:
            return
        tone = self.song.tones[i]
        if self.settings.tone_programs:
            program, label = self.song.first_program + i, f"song tone, {tone.category}"
        else:
            program, label = self.settings.programs[CATEGORIES.index(tone.category)], tone.category
        if program == self.sent:
            return
        self.out.send_message([0xC0 | (self.settings.channel - 1), program])
        self.sent = program
        self.status(f"PC {program} ch {self.settings.channel} ({label}) for {tone.key}")

    def _check_files(self) -> None:
        """presets.json (or the AI's files) or the settings changed: the message made again, and sent."""
        stamp = self._stamp(self.folder / "rscompanion.json")
        settings_changed = stamp != self._settings_seen
        if settings_changed:
            self._settings_seen = stamp
            settings = Settings.read(self.folder / "rscompanion.json")
            settings_changed, self.settings = settings != self.settings, settings
        if (self.presets.reload() or settings_changed) and self.song is not None:
            self.status("RS Companion's mapping or settings changed: describing the song again")
            self._describe()

    @staticmethod
    def _stamp(path: Path) -> float | None:
        try:
            return path.stat().st_mtime
        except OSError:
            return None


def virtual_source(name: str = SOURCE) -> Any:
    """A MIDI source of our own, for other apps to listen to (python-rtmidi's virtual port)."""
    import rtmidi
    out = getattr(rtmidi, "MidiOut")(name="rs-keylights")
    out.open_virtual_port(name)
    return out


def riffloop_clock() -> GameClock:
    return RiffLoop()
