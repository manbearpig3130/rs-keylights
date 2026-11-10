from __future__ import annotations

import json
import os
import re
import shlex
import tomllib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import rig
from .library import ArrangementRef
from .pedal import NEEDS, SLOTS
from .rig import CATEGORIES, SLOT_NAMES, Presets, song_key
from .tonemessages import Settings, SongRig, SongTones, read_rig, song_tones

# The guitar pedal's tones for a song, for the page and the Launchkey: each of an arrangement's song tones (as
# song-tones describes them: its category from RS Companion's presets.json) and the preset dnafx-preset's listener
# picks for it (its tones.toml and program map, as RS Re-ARMed's sidekicks.conf runs it), by dnafx-preset's own rules;
# and changes to them written to those files: a tone's category or preset for its song, the song's style, and the rules
# for every song. Both read their files again when they change: the pedal follows within a second or so.

SIDEKICKS = Path.home() / "Library" / "Application Support" / "RS Re-ARMed" / "mods" / "sidekicks.conf"
DEFAULT_TONE_MAP = Path.home() / ".config" / "dnafx-preset" / "tones.toml"
EFFECTS = ("chorus", "flanger", "phaser", "tremolo", "vibe", "modulation", "delay")  # (dnafx-preset's, in its order)
SECTIONS = ("categories", "effects", "styles", "artists", "songs")
CACHED = 8  # songs' gear kept


class PedalTonesError(ValueError):
    pass


@dataclass(frozen=True)
class ListenerSetup:
    """How dnafx-preset's listener runs (its line in sidekicks.conf): its tone map's file, whether it's given it, its
    program map (a category's program: its slot without a rule), and where it listens."""
    tone_map: Path
    given: bool = False
    program_map: dict[int, int] = field(default_factory=dict)
    source: str | None = None
    found: bool = False  # a listener's line there


def listener_setup(conf: Path | None = None) -> ListenerSetup:
    """dnafx-preset's listener as sidekicks.conf starts it (its first line that isn't a comment); the default tone
    map's file (or $DNAFX_CONFIG) without one."""
    try:
        lines = (conf or SIDEKICKS).read_text().splitlines()
    except OSError:
        lines = []
    fallback = Path(os.environ.get("DNAFX_CONFIG") or DEFAULT_TONE_MAP).expanduser()
    for line in lines:
        line = line.strip()
        if line.startswith("#") or "dnafx-preset" not in line or "listen" not in line:
            continue
        try:
            words = shlex.split(line)
        except ValueError:
            continue
        options: dict[str, str] = {}
        for i, word in enumerate(words):
            if word.startswith("--"):
                name, _, value = word.partition("=")
                options[name] = value or (words[i + 1] if i + 1 < len(words) else "")
        programs: dict[int, int] = {}
        for pair in options.get("--midi-program-map", "").split(","):
            program, _, slot = pair.partition("=")
            if program.strip().isdigit() and slot.strip().isdigit():
                programs[int(program)] = int(slot)
        config = options.get("--config")
        return ListenerSetup(Path(config).expanduser() if config else fallback, bool(config), programs,
                             options.get("--midi-source"), True)
    return ListenerSetup(fallback)


def _stamp(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _write(path: Path, text: str) -> None:
    """A file written whole (its last version kept beside it, .bak), so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


# tones.toml, changed a line at a time: its comments and order kept

_KEY = r'"(?:[^"\\]|\\.)*"|\'[^\']*\'|[A-Za-z0-9_-]+'
_HEADER = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(?:#.*)?$")
_ENTRY = re.compile(rf"^(\s*)({_KEY})\s*=")


def _names(dotted: str) -> list[str] | None:
    """A table header's names ('artists."Amon Amarth"': artists, Amon Amarth); None if it isn't one."""
    names, rest = [], dotted.strip()
    while rest:
        m = re.match(rf"\s*({_KEY})\s*(\.|$)", rest)
        if not m:
            return None
        names.append(_unquote(m.group(1)))
        rest = rest[m.end():]
    return names


def _unquote(key: str) -> str:
    if key.startswith('"'):
        return json.loads(key)
    return key[1:-1] if key.startswith("'") else key


def _quote(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name, ensure_ascii=False)


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def edit_tone_map(text: str, path: tuple[str, ...], slot: int | None, comment: str = "") -> str:
    """tones.toml's text with one rule set to a slot (with a comment: the preset's name) or taken out (None): path is
    its table's names and its key (categories, Lead; artists, Amon Amarth, Crunch; songs, a title, a tone key...).
    Names are found as dnafx-preset finds them, case aside; a table that empties goes (its comments stay)."""
    *table, key = path
    lines = text.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if not m:
            continue
        if start is not None:
            end = i
            break
        names = _names(m.group(1))
        if names is not None and len(names) == len(table) and all(_same(a, b) for a, b in zip(names, table)):
            start = i
    if start is not None and end is None:
        end = len(lines)
    entry = f"{_quote(key)} = {slot}" + (f"  # {comment}" if comment else "")
    if start is None:
        if slot is None:
            return text
        while lines and not lines[-1].strip():
            lines.pop()
        header = "[" + ".".join(_quote(n) for n in table) + "]"
        return "\n".join([*lines, *([""] if lines else []), header, entry]) + "\n"
    assert end is not None
    found = last = None
    for i in range(start + 1, end):
        m = _ENTRY.match(lines[i])
        if m:
            last = i
            if _same(_unquote(m.group(2)), key):
                found = i
    if slot is not None:
        if found is not None:  # (its key as the file has it)
            m = _ENTRY.match(lines[found])
            assert m is not None
            lines[found] = m.group(1) + m.group(2) + entry[len(_quote(key)):]
        else:
            lines.insert((last if last is not None else start) + 1, entry)
    elif found is not None:
        del lines[found]
        if last == found and not any(_ENTRY.match(lines[i]) for i in range(start + 1, end - 1)):  # (it was the last)
            del lines[start]
            while start < len(lines) and not lines[start].strip() and (start == 0 or not lines[start - 1].strip()):
                del lines[start]
    return "\n".join(lines).rstrip("\n") + "\n" if lines else ""


def _rows(data: dict[str, Any]) -> dict[str, list[list[Any]]]:
    """A tone map's rules as tomllib reads them, a row each: categories (category, slot); effects (category, effect,
    slot); styles, artists, songs (name, category or tone key, slot)."""
    out: dict[str, list[list[Any]]] = {name: [] for name in SECTIONS}
    for name, value in (data.get("categories") or {}).items():
        out["categories"].append([name, value])
    for section in ("effects", "styles", "artists", "songs"):
        for name, table in (data.get(section) or {}).items():
            for key, value in (table.items() if isinstance(table, dict) else ()):
                out[section].append([name, key, value])
    return out


class PedalTones:
    """The pedal's tones for songs (their gear read once and kept for a few), and changes to their files."""

    def __init__(self, names: Callable[[int | None], str] = lambda _slot: "", folder: Path | None = None,
                 sidekicks: Path | None = None, read: Callable[[Path, str], SongRig] = read_rig):
        self.names = names
        self.folder = folder or rig.COMPANION_DATA
        self.sidekicks = sidekicks or SIDEKICKS
        self.presets = Presets(self.folder)
        self._read = read
        self._rigs: OrderedDict[tuple[str, str], SongRig] = OrderedDict()
        self._tables: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}  # an arrangement's, as its files were

    # What the files say

    def setup(self) -> ListenerSetup:
        return listener_setup(self.sidekicks)

    def tone_map(self, setup: ListenerSetup | None = None) -> tuple[Any, str | None]:
        """dnafx-preset's ToneMap of the listener's file (None: no file), and why it can't be used (None: it can)."""
        path = (setup or self.setup()).tone_map
        try:
            from dnafx_preset.tone_map import ToneMap
        except ImportError:
            return None, NEEDS
        if not path.exists():
            return None, None
        try:
            return ToneMap.load(path), None
        except (OSError, ValueError) as exc:
            return None, str(exc)

    def _rig(self, ref: ArrangementRef, arrangement: str) -> SongRig:
        key = (ref.archive, ref.song_key.lower())
        if key in self._rigs:
            self._rigs.move_to_end(key)
            return self._rigs[key]
        made = self._rigs[key] = self._read(Path(ref.archive), arrangement)
        while len(self._rigs) > CACHED:
            self._rigs.popitem(last=False)
        return made

    def table(self, ref: ArrangementRef) -> dict[str, Any]:
        """An arrangement's tones (up to five, in the order it first plays them), each with: its slot (Base, A to D),
        the seconds it plays, its effects, its category (and as it'd be without the song's own: auto) and why, the
        pedal's preset for it (and its name) and the rule that picks it, and whether that's the song's own; when its
        tones change (the index of each); the song's style (and auto), the styles there are; and where the files
        are, and what's wrong with them."""
        arrangement = Path(ref.sng).stem
        setup = self.setup()
        stamps = (_stamp(self.presets.path), _stamp(self.folder / "rscompanion.json"), _stamp(setup.tone_map),
                  _stamp(self.sidekicks))
        kept = self._tables.get(arrangement.lower())
        if kept is not None and kept[0] == stamps:
            return kept[1]
        self.presets.reload()
        song_rig = self._rig(ref, arrangement)
        settings = Settings.read(self.folder / "rscompanion.json")
        made = song_tones(song_rig, arrangement, self.presets, settings.first_program)
        a = song_rig.arrangement(arrangement)
        if made is None or a is None:
            raise PedalTonesError(f"{arrangement}: not in its song's files")
        tone_map, problem = self.tone_map(setup)
        picks = self._picks(made, tone_map, setup)
        own_slots = {key.casefold() for name, key, _slot in (tone_map.songs if tone_map else ())
                     if _same(name, made.song)}
        overrides = rig._dict(rig._dict(self.presets.data.get("songs")).get(a.name))
        seconds = dict((k.lower(), s) for k, s in rig.tone_order(a))
        tones = []
        for i, t in enumerate(made.tones):
            slot = next((SLOT_NAMES[j] for j, s in enumerate(a.slots) if s.lower() == t.key.lower()), "Base")
            listed = rig.exact_tone(a, t.key)
            amp = listed.gear.get("Amp") if listed else None
            defined = rig.tone_def(a, t.key)
            auto = self.presets.match(a.name, slot, t.key, listed.descriptors if listed else (), song_rig.artist,
                                      amp.key if amp else None, rig.amp_gain(amp) if amp else None,
                                      rig.drive(defined) if defined else None, overrides=False)
            pedal, rule = picks[i]
            tones.append({
                "key": t.key, "short": short_name(t.key, a.name), "slot": slot, "program": made.first_program + i,
                "seconds": round(seconds.get(t.key.lower(), 0.0), 1),
                "effects": [e.kind if name != "delay" else f"{e.kind} delay" for name, e in sorted(t.effects.items())],
                "category": t.category, "auto_category": auto.category, "reason": t.reason,
                "own_category": t.reason in ("song override", "tone override") or isinstance(overrides.get(slot), str),
                "pedal": pedal, "pedal_name": self.names(pedal) if pedal else "", "rule": rule,
                "own_pedal": t.key.casefold() in own_slots,
            })
        index = {t.key.lower(): i for i, t in enumerate(made.tones)}
        changes = [[round(at, 3), index.get(a.key_at(at).lower(), -1)] for at, _change in a.changes]
        own_style = song_key(a.name) in {str(k).strip().lower() for k in self.presets._song_styles}
        out = {
            "song": made.song, "artist": made.artist, "arrangement": a.name, "part": ref.part,
            "style": made.style, "auto_style": self.presets.style(made.heaviness, made.artist, song_key(a.name), own=False),
            "own_style": own_style, "styles": list(made.styles), "heaviness": made.heaviness, "tones": tones,
            "first": index.get(a.key_at(float("-inf")).lower(), 0), "changes": changes,
            "files": {"presets": str(self.presets.path), "presets_found": self.presets.found,
                      "tone_map": str(setup.tone_map), "tone_map_given": setup.given, "listener": setup.found,
                      "source": setup.source, "problem": problem},
        }
        self._tables[arrangement.lower()] = (stamps, out)
        return out

    def _picks(self, made: SongTones, tone_map: Any, setup: ListenerSetup) -> list[tuple[int | None, str]]:
        """The preset dnafx-preset's listener picks for each tone, and why: its tone map's rule, else its category's
        program (the program map's slot, else the program's own: 0 is slot 1)."""
        try:
            from dnafx_preset.song_tones import parse_song_tones
        except ImportError:
            return [(None, NEEDS)] * len(made.tones)
        song = parse_song_tones(made.sysex())  # (as the listener has it)
        out: list[tuple[int | None, str]] = []
        for tone in song.tones if song else ():
            choice = tone_map.choose(song, tone) if tone_map is not None else None
            if choice is not None:
                out.append((choice.slot_number, choice.rule))
                continue
            program = CATEGORIES.index(tone.category)
            slot = setup.program_map.get(program, program + 1)
            out.append((slot if 1 <= slot <= SLOTS else None, f"{tone.category}'s program, {program}"))
        return out + [(None, "")] * (len(made.tones) - len(out))

    def playing(self, ref: ArrangementRef, at: float) -> dict[str, Any] | None:
        """The tone playing at a time: its key, short name, the pedal's preset for it, and whether that's the song's
        own (None: its tones can't be read)."""
        try:
            table = self.table(ref)
        except (PedalTonesError, OSError, ValueError):
            return None
        i = table["first"]
        for when, index in table["changes"]:
            if when > at:
                break
            if index >= 0:
                i = index
        if not 0 <= i < len(table["tones"]):
            return None
        t = table["tones"][i]
        return {"key": t["key"], "short": t["short"], "pedal": t["pedal"], "own": t["own_pedal"],
                "category": t["category"]}

    def rules(self) -> dict[str, Any]:
        """The rules for every song: the tone map's (its rows, as the file has them), and RS Companion's that the page
        changes (its artists' categories, songs' and artists' styles, and the styles by heaviness)."""
        setup = self.setup()
        problem: str | None = None
        data: dict[str, Any] = {}
        try:
            data = tomllib.loads(setup.tone_map.read_text()) if setup.tone_map.exists() else {}
        except (OSError, ValueError) as exc:
            problem = str(exc)
        self.presets.reload()
        p = self.presets
        artists = [[artist, source, target] for artist, remap in rig._dict(p.data.get("artists")).items()
                   for source, target in rig._dict(remap).items()]
        return {
            **_rows(data), "effect_names": list(EFFECTS), "style_names": p.style_names,
            "companion": {"artists": artists, "song_styles": [[k, v] for k, v in p._song_styles.items()],
                          "styles": [list(s) for s in p._styles]},
            "files": {"tone_map": str(setup.tone_map), "tone_map_given": setup.given, "listener": setup.found,
                      "presets": str(p.path), "presets_found": p.found, "problem": problem},
        }

    # Changes

    def set_rule(self, path: list[Any], slot: int | None) -> None:
        """A rule of the tone map set (or taken out: None): categories, Clean; effects, Clean, flanger; styles, Metal,
        Heavy; artists, a name, Crunch; songs, a title, a tone key or category."""
        if not path or path[0] not in SECTIONS or not all(isinstance(p, str) and p.strip() for p in path):
            raise PedalTonesError("Which rule?")
        section = path[0]
        if len(path) != (2 if section == "categories" else 3):
            raise PedalTonesError(f"A {section} rule has {'a category' if section == 'categories' else 'two names'}")
        if section in ("categories", "effects") and _category(path[1]) is None:
            raise PedalTonesError(f"{path[1]}: not a category ({', '.join(CATEGORIES)})")
        if section in ("styles", "artists") and _category(path[2]) is None:
            raise PedalTonesError(f"{path[2]}: not a category ({', '.join(CATEGORIES)})")
        if section == "effects" and path[2].lower() not in EFFECTS:
            raise PedalTonesError(f"{path[2]}: not an effect ({', '.join(EFFECTS)})")
        if slot is not None and not (isinstance(slot, int) and 1 <= slot <= SLOTS):
            raise PedalTonesError(f"Presets are 1 to {SLOTS}")
        fixed = [p.strip() for p in path]  # (categories and effects named as dnafx-preset names them)
        if section in ("categories", "effects"):
            fixed[1] = _category(fixed[1]) or fixed[1]
        if section in ("styles", "artists"):
            fixed[2] = _category(fixed[2]) or fixed[2]
        if section == "effects":
            fixed[2] = fixed[2].lower()
        file = self.setup().tone_map
        try:
            text = file.read_text() if file.exists() else ""
        except OSError as exc:
            raise PedalTonesError(f"Can't read {file}: {exc}") from exc
        changed = edit_tone_map(text, tuple(fixed), slot, self.names(slot) if slot else "")
        data: dict[str, Any] = {}
        try:  # (checked as dnafx-preset reads it, and the change there)
            data = tomllib.loads(changed)
            from dnafx_preset.tone_map import ToneMap
            ToneMap.from_dict(data)
        except ImportError:
            pass
        except ValueError as exc:
            raise PedalTonesError(f"{file} can't be changed so ({exc}): change it by hand") from exc
        now = _lookup(data, fixed)
        if now != slot:
            raise PedalTonesError(f"{file} has that rule another way: change it by hand")
        if changed != text:
            _write(file, changed)

    def set_tone(self, ref: ArrangementRef, key: str, *, category: str | None = None, pedal: int | None = None,
                 change: str) -> None:
        """A song's tone given a category of its own (presets.json, as RS Companion's app does: for each of the
        song's arrangements that has it), or a preset of its own (the tone map's song rule); None: back to auto."""
        song_rig = self._rig(ref, Path(ref.sng).stem)
        if change == "category":
            if category is not None and _category(category) is None:
                raise PedalTonesError(f"{category}: not a category ({', '.join(CATEGORIES)})")
            wanted = _category(category) if category else None

            def apply(data: dict[str, Any]) -> None:
                songs = data.setdefault("songs", {})
                for a in song_rig.arrangements:
                    for slot, tone in zip(SLOT_NAMES, a.slots):
                        if tone and tone.lower() == key.lower():
                            entry = songs.setdefault(a.name, {})
                            if wanted is None:
                                entry.pop(slot, None)
                            else:
                                entry[slot] = wanted
                            if not entry:
                                songs.pop(a.name)
                if wanted is None and isinstance(data.get("tones"), dict):
                    for k in [k for k in data["tones"] if k.lower() == key.lower()]:
                        del data["tones"][k]
            self._presets_change(apply)
        else:
            if not song_rig.song:
                raise PedalTonesError("This song has no name for a rule")
            self.set_rule(["songs", song_rig.song, key], pedal)

    def set_style(self, ref: ArrangementRef, style: str | None) -> None:
        """A song's own style (RS Companion's song_styles, by its key), or back to auto (None)."""
        self.set_named_style(song_key(Path(ref.sng).stem), style)

    def set_named_style(self, name: str, style: str | None) -> None:
        """A style for a song key or an artist (song_styles), or none (None)."""
        if not name.strip():
            raise PedalTonesError("Whose style?")

        def apply(data: dict[str, Any]) -> None:
            section = "song_styles" if "song_styles" in data or not ({"song_banks", "song_rigs"} & set(data)) else \
                next(k for k in ("song_banks", "song_rigs") if k in data)
            styles = data.setdefault(section, {})
            for k in [k for k in styles if str(k).strip().lower() == name.strip().lower()]:
                del styles[k]
            if style:
                styles[name.strip()] = style.strip()
        self._presets_change(apply)

    def set_artist_category(self, artist: str, source: str, target: str | None) -> None:
        """RS Companion's artist section: an artist's tones of a category (or preset) as another category; None takes
        it out."""
        if not artist.strip() or not source.strip():
            raise PedalTonesError("Whose, and which category?")
        if target is not None and _category(target) is None:
            raise PedalTonesError(f"{target}: not a category ({', '.join(CATEGORIES)})")

        def apply(data: dict[str, Any]) -> None:
            artists = data.setdefault("artists", {})
            name = next((k for k in artists if k.strip().lower() == artist.strip().lower()), artist.strip())
            remap = artists.setdefault(name, {})
            for k in [k for k in remap if k.lower() == source.lower()]:
                del remap[k]
            if target is not None:
                remap[_category(source) or source] = _category(target)
            if not remap:
                del artists[name]
        self._presets_change(apply)

    def set_styles(self, styles: list[Any]) -> None:
        """RS Companion's styles by heaviness: each one's name, and the heaviness it starts at (0 to 1)."""
        rows = []
        for row in styles:
            if not (isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) and row[0].strip()
                    and isinstance(row[1], (int, float)) and 0 <= row[1] <= 1):
                raise PedalTonesError("A style is a name, and the heaviness it starts at (0 to 1)")
            rows.append({"name": row[0].strip(), "from": round(float(row[1]), 3)})
        if not rows:
            raise PedalTonesError("At least one style")
        rows.sort(key=lambda r: r["from"])

        def apply(data: dict[str, Any]) -> None:
            for old in ("banks", "rigs"):
                data.pop(old, None)
            data["styles"] = rows
        self._presets_change(apply)

    def _presets_change(self, apply: Callable[[dict[str, Any]], None]) -> None:
        path = self.presets.path
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError) as exc:
            raise PedalTonesError(f"{path} can't be read ({exc}): fix it first") from exc
        if not isinstance(data, dict):
            raise PedalTonesError(f"{path} isn't RS Companion's mapping")
        apply(data)
        _write(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        self.presets.reload()


def _category(name: str | None) -> str | None:
    return next((c for c in CATEGORIES if name is not None and _same(c, name)), None)


def _lookup(data: dict[str, Any], path: list[str]) -> Any:
    """A rule's slot as tomllib read the file (names case aside)."""
    node: Any = data
    for name in path:
        if not isinstance(node, dict):
            return None
        node = next((v for k, v in node.items() if _same(k, name)), None)
    return node


def short_name(key: str, arrangement: str) -> str:
    """A tone's name without its song's ("amontwil_dist": dist)."""
    prefix = arrangement.rsplit("_", 1)[0].lower() + "_"
    return key[len(prefix):] if key.lower().startswith(prefix) and len(key) > len(prefix) else key
