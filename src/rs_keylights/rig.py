from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

# What a song's tones are, as RS Companion (rs-mac-companion, MIT licence) has them: how distorted a tone's gear is,
# its delay, modulation and reverb, how heavy the song is, and the category (Clean, Crunch, Heavy, Lead) and style its
# presets.json gives them. Its rig.py, presets.py and Rig.swift, Presets.swift, the same rules and tables, so a tone
# gets here the category it gets there.

CATEGORIES = ("Clean", "Crunch", "Heavy", "Lead")
SLOT_NAMES = ("Base", "A", "B", "C", "D")
COMPANION_DATA = Path(os.environ.get("RS_COMPANION_DATA") or
                      Path.home() / "Library" / "Application Support" / "rs-mac-companion")

# Amp key without "Amp_": the gain where it starts to break up, and where it gets heavy (None: it never does)
AMPS: dict[str, tuple[float, float | None]] = {
    "HG500": (30, 70), "MarshallJVM410H": (25, 50), "MarshallJCM800": (35, 75),
    "OrangeRockerverb": (30, 55), "OrangeJimmyBean": (25, 50),
    "CA100": (30, 50), "GB50": (40, 70), "OrangeTinyTerror": (40, 70),
    "MarshallDSL100H": (40, 80), "MarshallDSL15H": (40, 80),
    "GB100": (40, 95), "HG100": (40, 95), "HG180": (40, 95), "CA85": (40, 95), "EN50": (40, 95),
    "MarshallPlexi": (40, None), "MarshallJTM45": (40, None), "Marshall1962Bluesbreaker": (45, None),
    "OrangeAD50": (45, None), "OrangeOR100": (40, None), "OrangeOR50H": (40, None),
    "TW22": (50, None), "TW26": (50, None), "TW40": (50, None), "EN30": (50, None),
    "AT20": (50, None), "AT120": (50, None), "BT15": (50, None), "BT30": (50, None), "BT45": (50, None),
    "GibsonGA8": (50, None), "GibsonGA79": (50, None), "GibsonGA88": (50, None),
    "EpiphoneZephyr": (50, None), "EpiphoneElectarM": (50, None), "EpiphoneElectarCentury": (50, None),
    "CA38": (55, None), "GB38": (60, None), "CS90": (70, None), "CS100": (70, None), "CS120": (70, None),
}
UNKNOWN_AMP: tuple[float, float | None] = (50, 95)
DISTORTION_PEDALS = {  # pedals that distort by themselves: drive at least floor + span * gain
    "Pedal_MetalDistortion": (0.45, 0.5), "Pedal_ShredZone": (0.45, 0.5), "Pedal_MarshallGuvnorPlus": (0.4, 0.45),
    "Pedal_Distortion": (0.4, 0.45), "Pedal_VintageDistortion": (0.4, 0.4),
    "Pedal_CaptFuzzle": (0.3, 0.35), "Pedal_BuzzOne": (0.3, 0.35), "Pedal_BuzzToo": (0.3, 0.35),
    "Pedal_FuzzWasHe": (0.3, 0.35), "Pedal_Octavius": (0.3, 0.3),
}
BOOST_PEDALS = {"Pedal_LineDrive": 0.15, "Pedal_GermaniumDrive": 0.15, "Pedal_SuperDrive": 0.15,  # drive + push * gain
                "Pedal_CustomDrive": 0.15, "Pedal_RangeBooster": 0.1}
ACOUSTIC = ("Pedal_AcousticEmulator",)
MODULATION = (("Flanger", "flanger"), ("Phase", "phaser"), ("Chorus", "chorus"), ("Enbig", "chorus"),
              ("Clones", "chorus"), ("Trem", "tremolo"), ("Vibe", "vibe"), ("Rota", "vibe"), ("OmniMod", "vibe"))
REVERBS = ("Verb", "Spring", "Chamber", "Plate")
BUILTIN_CATEGORIES = {"distortion": "Heavy", "acoustic": "Clean", "bass": "Clean", "bass drive": "Crunch"}
DEFAULT_STYLES = (("Soft", 0.0), ("Classic rock", 0.3), ("Metal", 0.75))


@dataclass(frozen=True)
class Gear:
    key: str  # e.g. Amp_HG500, Pedal_Chorus
    knobs: Mapping[str, float] = field(default_factory=dict)  # named without the key's prefix: Gain, Mix...

    def knob(self, *names: str) -> float | None:
        return next((self.knobs[n] for n in names if n in self.knobs), None)


@dataclass(frozen=True)
class RigTone:
    key: str
    gear: Mapping[str, Gear] = field(default_factory=dict)  # by slot: Amp, Cabinet, PrePedal1, Rack2...
    descriptors: tuple[str, ...] = ()  # its tags: CLEAN, DISTORTION...


@dataclass(frozen=True)
class RigArrangement:
    name: str  # e.g. amonpurs_rhythm
    path: str  # Lead, Rhythm, Combo, Bass
    slots: tuple[str, ...]  # tone keys of Base and A to D ("": unused)
    tones: tuple[RigTone, ...]
    changes: tuple[tuple[float, int], ...] = ()  # when, and to which (0 to 3: A to D)
    length: float | None = None
    low_string: float = 0.0  # the low string's tuning, semitones, its cents included
    tempo: float | None = None

    def slot_at(self, at: float) -> int:
        """The slot playing at a time (0: Base; 1 to 4: A to D)."""
        slot = 0
        for when, change in self.changes:
            if when > at:
                break
            if 0 <= change < 4:
                slot = change + 1
        return slot

    def key_at(self, at: float) -> str:
        """The tone key playing at a time (its slot's, else Base's)."""
        slot = self.slot_at(at)
        return self.slots[slot] if slot < len(self.slots) and self.slots[slot] else (self.slots[0] if self.slots else "")


def read_tone(raw: Mapping[str, Any]) -> RigTone:
    """A manifest's tone: its key, its GearList (knobs named without their gear's key) and its ToneDescriptors
    ("$[35716]CLEAN": CLEAN)."""
    gear: dict[str, Gear] = {}
    listed = raw.get("GearList")
    for slot, item in (listed.items() if isinstance(listed, dict) else ()):
        if not isinstance(item, dict) or not isinstance(item.get("Key"), str):
            continue
        key = item["Key"]
        values = item.get("KnobValues")
        knobs = {name[len(key) + 1:] if name.startswith(key + "_") else name: float(value)
                 for name, value in (values.items() if isinstance(values, dict) else ())
                 if isinstance(value, (int, float)) and not isinstance(value, bool)}
        gear[str(slot)] = Gear(key, knobs)
    descriptors = tuple(d[d.index("]") + 1:] if d.startswith("$[") and "]" in d else d
                        for d in (raw.get("ToneDescriptors") or []) if isinstance(d, str))
    return RigTone(str(raw.get("Key") or ""), gear, descriptors)


def amp_drive(amp: str, gain: float) -> float:
    """0 to 1 from an amp ("Amp_HG500") and its gain knob (0 to 100)."""
    crunch, heavy = AMPS.get(amp[4:] if amp.startswith("Amp_") else amp, UNKNOWN_AMP)
    g = min(max(gain, 0.0), 100.0)
    if g < crunch:
        return g / crunch / 3
    if heavy is None:  # it breaks up but never gets heavy
        return 1 / 3 + (g - crunch) / (100 - crunch) * 0.25
    if g < heavy:
        return 1 / 3 + (g - crunch) / (heavy - crunch) / 3
    return 2 / 3 + (g - heavy) / max(100 - heavy, 1) / 3


def amp_gain(amp: Gear) -> float | None:
    """An amp's gain knob (the Plexi has two Loudness knobs instead: the higher); a knob named like one counts, as
    some custom songs name knobs after another amp."""
    found = [v for name, v in amp.knobs.items()
             if any(name == n or name.endswith("_" + n) for n in ("Gain", "Loudness1", "Loudness2"))]
    return max(found) if found else None


def drive(tone: RigTone) -> float | None:
    """How distorted a tone is, 0 to 1 (below 1/3 clean, then crunch, above 2/3 heavy); None without an amp's gain."""
    amp = tone.gear.get("Amp")
    gain = amp_gain(amp) if amp is not None else None
    if amp is None or gain is None:
        return None
    d = amp_drive(amp.key, gain)
    if any(g.key in ACOUSTIC for g in tone.gear.values()):
        return min(d, 0.25)
    pedals = [(g.key, min(max(g.knob("Gain", "Boost") or 0.0, 0.0), 100.0) / 100)
              for slot, g in tone.gear.items() if "Pedal" in slot]
    d += sum(BOOST_PEDALS.get(key, 0.0) * g for key, g in pedals)  # (boosts first: the order doesn't matter)
    for key, g in pedals:
        if key in DISTORTION_PEDALS:
            floor, span = DISTORTION_PEDALS[key]
            d = max(d, floor + span * g)
    return min(d, 1.0)


def band(d: float | None) -> str:
    """clean, crunch or heavy for a drive ("" not known)."""
    return "" if d is None else "clean" if d < 1 / 3 else "crunch" if d < 2 / 3 else "heavy"


@dataclass(frozen=True)
class Effect:
    kind: str  # a delay's tape, analog, modulated or digital; a modulation's chorus, flanger...; reverb
    gear: str
    knobs: tuple[tuple[str, float], ...] = ()  # (time_ms, mix, feedback; mix, rate: those the gear has)

    @property
    def mix(self) -> float | None:
        return dict(self.knobs).get("mix")


def delay_kind(gear: str) -> str:
    if any(w in gear for w in ("TapeEcho", "ValveEcho", "OilCanEcho", "NoFiEcho", "CosmicEcho")):
        return "tape"
    if any(w in gear for w in ("AnalogueDelay", "NPNDelay")):
        return "analog"
    return "modulated" if "ModDelay" in gear else "digital"


def effects(tone: RigTone) -> dict[str, Effect]:
    """A tone's delay, modulation and reverb (each the one with the most mix, if it has several; ties: the first in
    slot order), by "delay", "modulation", "reverb"."""
    out: dict[str, Effect] = {}

    def keep(name: str, effect: Effect) -> None:
        if name not in out or (effect.mix or 0) > (out[name].mix or 0):
            out[name] = effect

    def knobs(**values: float | None) -> tuple[tuple[str, float], ...]:
        return tuple((name, v) for name, v in values.items() if v is not None)

    for slot, item in sorted(tone.gear.items()):
        if slot in ("Amp", "Cabinet"):
            continue
        mix = item.knobs.get("Mix")
        time = item.knob("Time", "TimeL")
        if "Feedback" in item.knobs and time is not None:
            keep("delay", Effect(delay_kind(item.key), item.key,
                                 knobs(time_ms=time, mix=mix, feedback=item.knobs["Feedback"])))
        elif any(w in item.key for w in REVERBS):
            keep("reverb", Effect("reverb", item.key, knobs(mix=mix)))
        else:
            kind = next((k for w, k in MODULATION if w in item.key), None)
            if kind:
                keep("modulation", Effect(kind, item.key, knobs(mix=mix, rate=item.knob("Rate", "Speed"))))
    return out


def tone_def(arrangement: RigArrangement, key: str) -> RigTone | None:
    """An arrangement's tone for a slot's key; some custom songs name it a little differently ("Hotel California" for
    "HotelCalifornia"): then the one whose key matches without spaces and punctuation, or the only one there is."""
    def loose(k: str) -> str:
        return "".join(c for c in k.lower() if c.isalnum())
    tones = arrangement.tones
    return (next((t for t in tones if t.key.lower() == key.lower()), None)
            or next((t for t in tones if loose(t.key) == loose(key)), None)
            or (tones[0] if len(tones) == 1 else None))


def exact_tone(arrangement: RigArrangement, key: str) -> RigTone | None:
    """Its tone with just that key (case aside): the one the game's own list of its tones has."""
    return next((t for t in arrangement.tones if t.key.lower() == key.lower()), None)


def tone_order(arrangement: RigArrangement) -> list[tuple[str, float]]:
    """Its slots' tone keys in the order it first plays them (Base first), each once (case aside), with the seconds
    each plays."""
    a = arrangement
    end = a.length or (a.changes[-1][0] if a.changes else 0.0)
    order: list[list[Any]] = []

    def add(key: str, seconds: float) -> None:
        if not key:
            return
        for entry in order:
            if entry[0].lower() == key.lower():
                entry[1] += seconds
                return
        order.append([key, seconds])

    key, start = (a.slots[0] if a.slots else ""), 0.0
    for when, change in a.changes:
        add(key, max(min(when, end) - start, 0.0))
        if 0 <= change < 4 and change + 1 < len(a.slots) and a.slots[change + 1]:
            key = a.slots[change + 1]
        start = when
    add(key, max(end - start, 0.0))
    return [(k, s) for k, s in order]


def heaviness(arrangements: list[RigArrangement]) -> float | None:
    """How heavy a song is, 0 to 1: the drive of the tone its rhythm part (else combo, else lead) plays longest, plus
    0.1 with its low string tuned to C or lower; of parts of a kind (combo, combo2), the first by name."""
    ordered = sorted(arrangements, key=lambda a: a.name.lower())
    for path in ("Rhythm", "Combo", "Lead"):
        for a in ordered:
            if a.path != path:
                continue
            longest: tuple[str, float] | None = None
            for entry in tone_order(a):  # (the longest; of as long ones, the first played)
                if longest is None or entry[1] > longest[1]:
                    longest = entry
            tone = tone_def(a, longest[0]) if longest else None
            d = drive(tone) if tone else None
            if d is None:
                continue
            return min(d + (0.1 if a.low_string <= -4 else 0.0), 1.0)
    return None


def amp_name(amp: str | None) -> str:
    """Amp_HG500 and HG500 both: hg500."""
    a = (amp or "").lower()
    return a[4:] if a.startswith("amp_") else a


def song_key(arrangement: str) -> str:
    """The song's key, as the AI files have it: selftrap for selftrap_rhythm."""
    return arrangement.rsplit("_", 1)[0].lower()


@dataclass(frozen=True)
class Match:
    preset: str
    category: str  # Clean, Crunch, Heavy or Lead
    reason: str


class Presets:
    """RS Companion's tone mapping, its presets.json (with ai_suggestions.json and ai_classified.json beside it), read
    again when they change. First match wins: 1. songs: per arrangement and slot; 2. tones: per tone key; 3. rules,
    by tags ("all", "any"), words in the key ("name"), the amp ("amp"), its gain ("gain": at least) and the tone's drive
    ("drive": clean, crunch or heavy); 4. default. The artist's section may change 3 and 4's, or where it hasn't
    anything for it, the AI: the song's answer, else the suggestions for its artist."""

    def __init__(self, folder: Path = COMPANION_DATA):
        self.path = folder / "presets.json"
        self._files = {name: folder / name for name in ("presets.json", "ai_suggestions.json", "ai_classified.json")}
        self._seen: dict[str, float | None] = {name: None for name in self._files}
        self.data: dict[str, Any] = {}
        self.ai: dict[str, Any] = {}
        self.answers: dict[str, Any] = {}
        self.reload()

    @property
    def found(self) -> bool:
        return self._seen["presets.json"] is not None

    def reload(self) -> bool:
        """Its files read again if they changed (presets.json not JSON: the last read kept); whether any did."""
        changed = False
        for name, path in self._files.items():
            try:
                stamp: float | None = path.stat().st_mtime
            except OSError:
                stamp = None
            if stamp == self._seen[name]:
                continue
            self._seen[name] = stamp
            try:
                data = json.loads(path.read_text()) if stamp is not None else {}
            except (OSError, ValueError):
                if name == "presets.json":
                    continue
                data = {}
            data = data if isinstance(data, dict) else {}
            if name == "presets.json":
                self.data = data
            elif name == "ai_suggestions.json":
                self.ai = data
            else:
                self.answers = data
            changed = True
        return changed

    def category(self, preset: str) -> str:
        """The category a preset's name means: one's own name, else its "categories" entry."""
        for c in CATEGORIES:
            if c.lower() == preset.lower():
                return c
        named = {k.lower(): v for k, v in _dict(self.data.get("categories")).items()}.get(preset.lower())
        for c in CATEGORIES:
            if isinstance(named, str) and c.lower() == named.lower():
                return c
        return BUILTIN_CATEGORIES.get(preset.lower(), "Clean")

    def answer(self, key: str) -> dict[str, Any] | None:
        """The AI's answer for a song key, unless a guess (low confidence)."""
        a = self.answers.get(key.lower())
        if not isinstance(a, dict) or not all(isinstance(a.get(f), str) for f in ("style", "distortion", "confidence")):
            return None
        return a if a["confidence"] != "low" else None

    def rejected(self, key: str) -> bool:
        return key.lower() in {str(k).lower() for k in self.data.get("ai_rejected") or []}

    def match(self, arrangement: str, slot: str, key: str, descriptors: tuple[str, ...] = (), artist: str = "",
              amp: str | None = None, gain: float | None = None, drive: float | None = None,
              overrides: bool = True) -> Match:
        """overrides False: what the tone would be without changes made to its song (its "songs" and "tones")."""
        def result(preset: str, reason: str) -> Match:
            return Match(preset, self.category(preset), reason)
        song = _dict(_dict(self.data.get("songs")).get(arrangement))
        if overrides and isinstance(song.get(slot), str):
            return result(song[slot], "song override")
        tones = {k.lower(): v for k, v in _dict(self.data.get("tones")).items()}
        if overrides and isinstance(tones.get(key.lower()), str):
            return result(tones[key.lower()], "tone override")
        preset, why = self._rule(key, descriptors, amp, gain, drive)
        name = artist.strip()
        remapped = self._remap(self.data, name, preset)
        if remapped:
            return result(remapped, f"{why}, artist {name}")
        songkey = song_key(arrangement)
        if self.rejected(songkey):
            return result(preset, why)
        answer = self.answer(songkey)
        if answer is not None:
            c = self.category(preset)
            if answer["distortion"] == "crunch" and c == "Heavy":
                return result("Crunch", f"{why}, AI")
            if answer["distortion"] == "heavy" and c == "Crunch":
                return result("Heavy", f"{why}, AI")
            return result(preset, why)
        remapped = self._remap(self.ai, name, preset)
        return result(remapped, f"{why}, AI") if remapped else result(preset, why)

    def _remap(self, source: dict[str, Any], artist: str, preset: str) -> str | None:
        """What an "artists" section maps the preset (or its category) to for the artist."""
        artists = {str(k).strip().lower(): v for k, v in _dict(source.get("artists")).items()}
        remap = artists.get(artist.lower())
        if not isinstance(remap, dict):
            return None
        lowered = {str(k).lower(): v for k, v in remap.items()}
        for w in (preset.lower(), self.category(preset).lower()):
            if isinstance(lowered.get(w), str):
                return lowered[w]
        return None

    def _rule(self, key: str, descriptors: tuple[str, ...], amp: str | None, gain: float | None,
              drive: float | None) -> tuple[str, str]:
        tags = {d.upper() for d in descriptors}
        lower = key.lower()
        words = set(re.findall(r"[a-z]+", lower))
        for rule in self.data.get("rules") or []:
            if not isinstance(rule, dict) or not isinstance(rule.get("preset"), str):
                continue
            if "all" in rule and not set(rule["all"]) <= tags:
                continue
            if "amp" in rule and amp_name(amp) not in {amp_name(a) for a in rule["amp"]}:
                continue
            if "gain" in rule and (gain is None or gain < rule["gain"]):
                continue
            if "drive" in rule and band(drive) not in {str(b).lower() for b in rule["drive"]}:
                continue
            gear = ""
            if "amp" in rule or "gain" in rule:
                gear = f", amp {amp_name(amp) or '?'} gain " + ("?" if gain is None else f"{gain:g}")
            if "drive" in rule and drive is not None:
                gear += f", drive {drive:.2f} {band(drive)}"
            listed = "tags " + "+".join(sorted(tags)) + gear
            if "any" in rule and tags & set(rule["any"]):
                return rule["preset"], listed
            if "name" in rule and any(w in words or (len(w) >= 4 and w in lower) for w in rule["name"]):
                return rule["preset"], "name" + gear
            if "any" not in rule and "name" not in rule:
                return rule["preset"], listed
        default = self.data.get("default")
        return (default if isinstance(default, str) else "Clean"), "default"

    def style(self, heaviness: float | None, artist: str, key: str, own: bool = True) -> str:
        """A song's style: "song_styles" names one for its key or its artist (the key first), then the AI's answer, then
        the AI suggestions' "song_styles"; else the last of "styles" its heaviness reaches. own False: as it'd be
        without the song's own ("song_styles" for its key)."""
        use_ai = not self.rejected(key)
        sources = [self._song_styles if own else {k: v for k, v in self._song_styles.items()
                                                  if str(k).strip().lower() != key.lower()}]
        answer = self.answer(key) if use_ai else None
        if answer is not None:
            sources.append({key: answer["style"]})
        if use_ai:
            sources.append(_dict(self.ai.get("song_styles")))
        for source in sources:
            named = {str(k).strip().lower(): v for k, v in source.items() if isinstance(v, str)}
            found = named.get(key.lower()) or named.get(artist.strip().lower())
            if found:
                return found
        h = heaviness or 0.0
        styles = self._styles
        return next((name for name, start in reversed(styles) if h >= start), styles[0][0])

    @property
    def style_names(self) -> list[str]:
        """Every style a song may get: "styles", then the ones only "song_styles" (or the AI's) name."""
        named = [v for _, v in sorted(self._song_styles.items())] + [v for _, v in sorted(_dict(self.ai.get("song_styles")).items())]
        names: list[str] = []
        for name in [n for n, _ in self._styles] + [v for v in named if isinstance(v, str)]:
            if name.lower() not in {n.lower() for n in names}:
                names.append(name)
        return names

    @property
    def _song_styles(self) -> dict[str, Any]:
        return _dict(_first(self.data, "song_styles", "song_banks", "song_rigs"))  # (what they were called before)

    @property
    def _styles(self) -> list[tuple[str, float]]:
        listed = _first(self.data, "styles", "banks", "rigs")
        styles = [(r["name"], float(r.get("from") or 0)) for r in (listed if isinstance(listed, list) else [])
                  if isinstance(r, dict) and isinstance(r.get("name"), str)]
        return styles or list(DEFAULT_STYLES)


def _first(data: dict[str, Any], *names: str) -> object:
    """The first of these a file has (empty or not)."""
    return next((data[name] for name in names if data.get(name) is not None), None)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
