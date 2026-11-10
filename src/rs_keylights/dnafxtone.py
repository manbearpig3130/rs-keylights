from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from .rig import AMPS, RigTone, effects

# A Rocksmith tone as a preset for a Harley Benton DNAfx GiT Core: a .phb file, as its editor (DNAfx GiT Core.app)
# exports and imports them: each of its blocks' model (TYPE: from 0, as the editor lists them) and knobs. The gear both
# have is the same model, as near as can be: Rocksmith's Marshall JCM800 and Plexi and Orange Rockerverb amps are the
# DNAfx's J800, PLX100 and ROCK VRB, its Marshall 1960 4x12s and Orange cabinets the DNAfx's 1960 412 and CITRUS (EL34
# power tubes, as those amps have; Rocksmith's SM57 the DNAfx's, its condenser a U87, its ribbon a U47, there being no
# ribbon), with the amp's knobs as they are (0 to 100 in both). Reverbs, delays, modulation, a noise gate and a 5-band
# EQ are the DNAfx's nearest; anything else (other amps, drive pedals...) is left out, and said.

# The editor's empty preset (its Empty.phb), the blocks off
TEMPLATE: dict[str, Any] = {
    # The expression pedal on the amp's gain, heel to toe 0 to 100: block 2 (AMP), parameter 13 (its GAIN, as the
    # device numbers them), as the factory presets AMP GAIN, EXP GAIN and HiGain have it (the editor's empty preset has
    # 0 and 1: FX/COMP's Q)
    "Exp": {"FUN_SWITCH": 1, "MODULE_CTRL": 2, "PARA_CTRL": 13, "VOL_MAX": 100, "VOL_MIN": 0, "VOL_SWITCH": 0},
    "effectModule": {
        "AMP": {"Data": {"BASS": 50, "GAIN": 50, "MID": 50, "MST": 50, "PRES": 50, "TREBLE": 50}, "SWITCH": 0, "TYPE": 0},
        "CAB": {"Data": {"CEBTER": 20, "DISTANCE": 35, "MIC": 1, "TUBE": 0}, "SWITCH": 0, "TYPE": 0},
        "DELAY": {"Data": {"F.BACK": 20, "LEVEL": 20, "SUB-D": 1, "TIME": 600}, "SWITCH": 0, "TYPE": 0},
        "DS/OD": {"Data": {"GAIN": 50, "TONE": 50, "VOLUME": 50}, "SWITCH": 0, "TYPE": 0},
        "EQ": {"Data": {"1.6KHz": 16, "100Hz": 16, "250Hz": 16, "4KHz": 16, "630Hz": 16}, "SWITCH": 0, "TYPE": 0},
        "FX/COMP": {"Data": {"LEVEL": 50, "PEAK": 50, "POSITION": 50, "Q": 50}, "SWITCH": 0, "TYPE": 0},
        "MOD": {"Data": {"DEPTH": 50, "LEVEL": 50, "RATE": 50}, "SWITCH": 0, "TYPE": 0},
        "NS GATE": {"Data": {"THRES": 20}, "SWITCH": 0, "TYPE": 0},
        "REVERB": {"Data": {"DECAY": 40, "LEVEL": 25, "P.DELAY": 80, "TONE": 40}, "SWITCH": 0, "TYPE": 0},
    },
    "fileInfo": {"app": "HB100 Edit", "app_version": "V1.1.0", "device": "MOOER HB100", "device_version": "V1.1.0",
                 "schema": "HB80 Preset", "preset_name": ""},
}

J800, PLX100, ROCK_VRB_CL, ROCK_VRB_DS, PV5050_DS = 6, 8, 21, 22, 36  # the DNAfx's amps (from 0)
TWIN_AMPS = ("Amp_MarshallJCM800", "Amp_MarshallPlexi", "Amp_OrangeRockerverb", "Amp_HG100")  # (Rocksmith's: the
# DNAfx has them, or near enough: HG100, "a custom high-gain amp", has no twin; the 5150 is its nearest)
GAIN_AT = {"Amp_MarshallJCM800": (65, 100)}  # Rocksmith's gain, and the DNAfx's that sounds as distorted (by ear: its
# J800 at 65 isn't); between, in proportion (0 is 0), and no more than 100
CAB_1960, CITRUS_412, CITRUS_212 = 5, 10, 11  # and cabinets
EL34, L6 = 1, 3  # its cabinets' power tubes: OFF, EL34, EL84, 6L6, 6V6
SM57, U47, U87 = 0, 1, 2  # and mics
MASTER = 50  # its amps' master (Rocksmith's have none: their level's the game's)
CABS = ((re.compile(r"^Cab_Marshall1960(a|TV|AX|B)_"), CAB_1960, "Marshall 1960 4x12: 1960 412"),
        (re.compile(r"^Cab_OrangePPC412_"), CITRUS_412, "Orange PPC412: CITRUS 412"),
        (re.compile(r"^Cab_OrangePPC212"), CITRUS_212, "Orange PPC212: CITRUS 212"))
MICS = {"57": (SM57, "SM57"), "Condenser": (U87, "condenser: U87"), "Ribbon": (U47, "ribbon: U47 (the DNAfx has none)")}
POSITIONS = {"Cone": (20, 35), "Edge": (70, 35), "OffAxis": (45, 60)}  # (its CENTER and DISTANCE: a start, by ear)
REVERBS = {"Spring": 4, "Plate": 3, "Chamber": 1, "Verb": 0}  # ROOM, HALL, CHURCH, PLATE, SPRING...
DELAYS = {"digital": 0, "analog": 1, "tape": 3, "modulated": 4}
MODS = {"phaser": 0, "flanger": 3, "tremolo": 5, "chorus": 11, "vibe": 10}  # PHASER, FLANGER, TREMOLO, ROTARY, ANA-CHORUS
EQ5_BANDS = (("63", "100Hz"), ("250", "250Hz"), ("750", "630Hz"), ("2200", "1.6KHz"), ("5700", "4KHz"))
NOT_GEAR = ("Rack_StudioEQ",)
SONG_SLOTS = range(194, 200)  # the pedal's slots songs' presets go in (the user's choice: 187 Dimebag, 188 to 193
# their Amon Amarth presets and 200 "My love", This Love, kept)
GATE_FROM, GATE_THRESHOLD = 80, 16  # the DNAfx's gain from which a tone gets its noise gate (NOISE KILLER), and its
# threshold: as the user sets them (high gain hums)
# Each block's values in the order the pedal keeps them (lminiero/dnafx-editor's effects.h), then the rest of its
# room as the editor fills it in
ORDER = {"FX/COMP": (("Q", "POSITION", "PEAK", "LEVEL"), ()), "DS/OD": (("VOLUME", "TONE", "GAIN"), ()),
         "AMP": (("GAIN", "BASS", "MID", "TREBLE", "PRES", "MST"), ()),
         "CAB": (("TUBE", "MIC", "CEBTER", "DISTANCE"), (18,)), "NS GATE": (("THRES",), (50, 50)),
         "EQ": (("100Hz", "250Hz", "630Hz", "1.6KHz", "4KHz"), (16, 100, 600, 1250)),
         "MOD": (("RATE", "LEVEL", "DEPTH"), (50, 200, 120, 50)),
         "DELAY": (("LEVEL", "F.BACK", "TIME", "SUB-D"), (1040, 1, 50)),
         "REVERB": (("P.DELAY", "LEVEL", "DECAY", "TONE"), ())}  # (a studio EQ after the amp: mostly small cuts, left out)


@dataclass
class Converted:
    preset: dict[str, Any]
    exact: list[str] = field(default_factory=list)  # the gear that's the same model
    near: list[str] = field(default_factory=list)  # the DNAfx's nearest
    dropped: list[str] = field(default_factory=list)  # left out

    @property
    def faithful(self) -> bool:
        """Its amp and cabinet the same models, nothing left out but a studio EQ."""
        return len(self.exact) >= 2 and all(d.split(":")[0] in NOT_GEAR for d in self.dropped)


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def _gain(amp: str, gain: float) -> int:
    theirs, ours = GAIN_AT.get(amp, (100, 100))
    return _clamp(gain * ours / theirs)


def convert(tone: RigTone) -> Converted:
    preset = copy.deepcopy(TEMPLATE)
    blocks = preset["effectModule"]
    out = Converted(preset)

    def use(block: str, kind: int, **data: int) -> None:
        blocks[block]["SWITCH"], blocks[block]["TYPE"] = 1, kind
        blocks[block]["Data"].update(data)

    taken: set[str] = set()
    amp = tone.gear.get("Amp")
    if amp is not None:
        k = amp.knobs
        knobs = {"BASS": _clamp(k.get("Bass", 50)), "MID": _clamp(k.get("Mid", 50)),
                 "TREBLE": _clamp(k.get("Treble", 50)), "PRES": _clamp(k.get("Pres", 50)), "MST": MASTER}
        if amp.key == "Amp_MarshallJCM800":
            use("AMP", J800, GAIN=_gain(amp.key, k.get("Gain", 50)), **knobs)
            out.exact.append("Marshall JCM800: J800")
        elif amp.key == "Amp_MarshallPlexi":  # (its gain is its two channels' loudness: the louder)
            use("AMP", PLX100, GAIN=_clamp(max(k.get("Loudness1", 0), k.get("Loudness2", 0))), **knobs)
            out.exact.append("Marshall Plexi: PLX100")
        elif amp.key == "Amp_OrangeRockerverb":
            gain = k.get("Gain", 50)
            dirty = gain >= AMPS["OrangeRockerverb"][0]
            use("AMP", ROCK_VRB_DS if dirty else ROCK_VRB_CL, GAIN=_clamp(gain), **knobs)
            out.exact.append(f"Orange Rockerverb: ROCK VRB {'DS' if dirty else 'CL'}")
        elif amp.key == "Amp_HG100":
            use("AMP", PV5050_DS, GAIN=_clamp(k.get("Gain", 50)), **knobs)
            out.near.append("HG100 (a custom high-gain amp): PV 5050 DS, the nearest")
        else:
            out.dropped.append(f"{amp.key}: no DNAfx twin")
        taken.add("Amp")
    cab = tone.gear.get("Cabinet")
    if cab is not None:
        found = next(((kind, said) for pattern, kind, said in CABS if pattern.match(cab.key)), None)
        parts = cab.key.split("_")
        if found is not None:
            mic, mic_said = MICS.get(parts[2] if len(parts) > 2 else "", (SM57, "SM57"))
            center, distance = POSITIONS.get(parts[3] if len(parts) > 3 else "", POSITIONS["Cone"])
            tube = L6 if amp is not None and amp.key == "Amp_HG100" else EL34  # (a 5150's 6L6s)
            use("CAB", found[0], TUBE=tube, MIC=mic, CEBTER=center, DISTANCE=distance)
            (out.exact if mic != U47 else out.near).append(f"{found[1]}, {mic_said}, {parts[3] if len(parts) > 3 else ''}")
        else:
            out.dropped.append(f"{cab.key}: no DNAfx twin")
        taken.add("Cabinet")

    found_effects = effects(tone)
    reverb = found_effects.get("reverb")
    if reverb is not None:
        kind = next((v for w, v in REVERBS.items() if w in reverb.gear), REVERBS["Verb"])
        item = next(g for g in tone.gear.values() if g.key == reverb.gear)
        use("REVERB", kind, LEVEL=_clamp(reverb.mix if reverb.mix is not None else 25),
            DECAY=_clamp(item.knobs.get("Time", item.knobs.get("Decay", 40))), TONE=40, **{"P.DELAY": 0})
        out.near.append(f"{reverb.gear}: reverb {kind}")
    delay = found_effects.get("delay")
    if delay is not None:
        k = dict(delay.knobs)
        use("DELAY", DELAYS.get(delay.kind, 0), LEVEL=_clamp(k.get("mix", 20)), TIME=_clamp(k["time_ms"], 40, 2500),
            **{"F.BACK": _clamp(k.get("feedback", 20)), "SUB-D": 0})
        out.near.append(f"{delay.gear}: {delay.kind} delay")
    modulation = found_effects.get("modulation")
    if modulation is not None and modulation.kind in MODS:
        k = dict(modulation.knobs)
        use("MOD", MODS[modulation.kind], RATE=_clamp(k.get("rate", 50)), LEVEL=_clamp(k.get("mix", 50)), DEPTH=50)
        out.near.append(f"{modulation.gear}: {modulation.kind}")
    used = {e.gear for e in found_effects.values() if e is not modulation or e.kind in MODS}
    for slot, item in sorted(tone.gear.items()):
        if slot in taken or item.key in used:
            continue
        if item.key == "Pedal_NoiseGate":
            use("NS GATE", 0, THRES=_clamp(item.knobs.get("Threshold", item.knobs.get("Thresh", 20))))
            out.near.append("Noise gate: NOISE KILLER")
        elif item.key == "Pedal_EQ5" and blocks["EQ"]["SWITCH"] == 0:  # (its bands the DNAfx's nearest, dB as they are)
            use("EQ", 0, **{ours: _clamp(16 + item.knobs.get(theirs, 0), 0, 32) for theirs, ours in EQ5_BANDS})
            out.near.append("5-band EQ: GUITAR EQ")
        else:
            out.dropped.append(f"{item.key}: left out")
    if blocks["AMP"]["SWITCH"] and blocks["AMP"]["Data"]["GAIN"] >= GATE_FROM and not blocks["NS GATE"]["SWITCH"]:
        use("NS GATE", 0, THRES=GATE_THRESHOLD)
        out.near.append(f"high gain: a noise gate (NOISE KILLER {GATE_THRESHOLD})")
    return out


def pedal_preset(converted: Converted, slot: int, name: str) -> Any:
    """A converted tone as the pedal keeps it (dnafx-preset's Preset), for a slot, by a name (14 characters at most):
    each block's values in its order; the expression pedal on the amp's gain."""
    from dnafx_preset.presetdata import Block, Preset
    blocks = {}
    for section, module in converted.preset["effectModule"].items():
        names, rest = ORDER[section]
        data = module["Data"]
        blocks[section] = Block(bool(module["SWITCH"]), int(module["TYPE"]), [int(data.get(n, 0)) for n in names] + list(rest))
    expression = {k: int(v) for k, v in converted.preset["Exp"].items()}
    return Preset(slot, name[:14], blocks, expression)


def short_name(tone_key: str, taken: set[str] = frozenset()) -> str:  # type: ignore[assignment]
    """A preset's name the editor takes (its long names it doesn't): 8 letters and digits at most, as a person would
    shorten it: the song's part of the tone's key without its vowels (but the first), 4 of them, then its own part's
    first 4 (RageKilling_dist: RGKLDIST); a digit after it if it's taken."""
    song, _, own = tone_key.rpartition("_") if "_" in tone_key else (tone_key, "", "")
    letters = re.sub(r"[^A-Za-z0-9]", "", song).upper()
    squeezed = (letters[:1] + re.sub(r"[AEIOU]", "", letters[1:]))[:4 if own else 8] if letters else "TONE"
    name = (squeezed + re.sub(r"[^A-Za-z0-9]", "", own).upper()[:4])[:8] or "TONE"
    n = 2
    base = name
    while name in taken:
        name, n = f"{base[:7]}{n}", n + 1
    return name
