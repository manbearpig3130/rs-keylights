# A tone search: presets tried on the pedal without anyone playing. A tone check's dry guitar (tools/reamp.py) plays
# through each, and what comes back is checked against the record's guitar, as a tone check would (tonecheck.analyse,
# on the same moments each time: the stretches of the take that matched the record best).
#   .venv/bin/python tools/tonesearch.py [--check NAME] [--matched S] [--dry-run] [--round N] [--only N,N] [--quick]
# --quick: each try's exact effect (its sound against the base's, try 0, the same performance moment by moment: no
# matching's noise) on a few of the stretches, then what the record would say of it: the base's difference from the
# record (the latest full run's) less that. Linear changes (cab, mic, EQ) add up in dB, so they can be combined too.
# Each try is written to slot 192 (SLOT), switched to, and played; 188-197 are saved first (~/Music/DNAfx backups).
# The pedal's held (dnafx-preset's hold file) so the song-tone listener can't switch it meanwhile, and it's left on the
# base preset at the end. The expression pedal mustn't be moved while it runs: a preset loads with its stored gain.
import argparse
import copy
import json
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import reamp  # noqa: E402

from rs_keylights import stems, tonecheck  # noqa: E402
from rs_keylights.library import SongIndex  # noqa: E402
from rs_keylights.pedal import remember_names  # noqa: E402
from rs_keylights.pedalwriter import ACK, _client  # noqa: E402

R = tonecheck.RATE
SLOT = 192  # (the search's own: no rule points at it)
NEVER = (187, 200)  # (the user's own: Dimebag, This Love)
BASE = 190  # (Gods: GODS B BRIGHT when rounds 1 and 2 ran; GODS C, their winner, since)
WINDOW, GAP = 5.0, 0.5  # seconds: the take's stretches compared, and the silence between them as they're played
OUT = Path.home() / "Library/Caches/rs-keylights/tonesearch"
HOLD = Path.home() / ".config/dnafx-preset/hold"
AMP = ("GAIN", "BASS", "MID", "TREBLE", "PRES", "MST")
CAB = ("TUBE", "MIC", "CENTER", "DISTANCE")
MICS = ("SM57", "U47", "U87", "NT1", "MD421", "MD441", "MXL2001", "MXL2003", "C3000", "C4000B")
EQS = {0: ("100Hz", "250Hz", "630Hz", "1.6KHz", "4KHz"), 1: ("80Hz", "240Hz", "750Hz", "2.2KHz", "6.6KHz")}


def variant(base, name: str, amp=None, cab=None, eq_model=None, eq=None):
    """The base preset with some values changed (amp's and cab's by name; eq: dB by band name, for eq_model's bands,
    the rest 0), in the search's slot."""
    p = copy.deepcopy(base)
    p.slot, p.name = SLOT, name[:14]
    for key, value in (amp or {}).items():
        p.blocks["AMP"].values[AMP.index(key)] = value
    for key, value in (cab or {}).items():
        p.blocks["CAB"].values[CAB.index(key)] = MICS.index(value) if key == "MIC" else value
    if eq is not None:
        model = p.blocks["EQ"].model if eq_model is None else eq_model
        p.blocks["EQ"].on, p.blocks["EQ"].model = True, model
        for k, band in enumerate(EQS[model]):
            p.blocks["EQ"].values[k] = 16 + eq.get(band, 0)
    return p


def describe(p) -> str:
    amp = dict(zip(AMP, p.blocks["AMP"].values))
    cab = dict(zip(CAB, p.blocks["CAB"].values))
    eq = p.blocks["EQ"]
    bands = EQS.get(eq.model, ())
    said = " ".join(f"{b} {v - 16:+d}" for b, v in zip(bands, eq.values) if v != 16) if eq.on else "off"
    return (f"amp {p.blocks['AMP'].model} {amp}; cab {p.blocks['CAB'].model} {MICS[cab['MIC']]} centre "
            f"{cab['CENTER']} distance {cab['DISTANCE']}; EQ {['GUITAR', 'METAL', '6BANDS', 'CUSTOM'][eq.model]}: {said}")


def song(check: dict):
    """The check's song: the record's guitar (mono), its rate, and the part's chart notes."""
    index_file = Path.home() / "Library/Caches/rs-keylights/index.json"
    index = SongIndex(Path(json.loads(index_file.read_text())["game_dir"]), index_file)
    archive = check["sid"].split("#")[0]
    mine = [r for r in index.arrangements() if str(r.archive) == archive]
    ref = next(r for r in mine if stems.split_with(r))
    notes = next(r for r in mine if r.part == check["part"]).load().notes
    with tempfile.TemporaryDirectory() as work:
        wavs = stems.decode(ref, Path(work), separator="roformer", parts=(check["stem"],))
        if wavs is None:
            sys.exit("The song's parts are gone: split it again")
        with wave.open(str(wavs[check["stem"]])) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            guitar = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, channels).mean(axis=1)
    return (guitar / 32768).astype(np.float32), rate, notes


def windows(live, anchors, guitar, rate, notes, offset, matched_wanted: float) -> list[tuple[int, int, float]]:
    """The take's WINDOW-second stretches that matched the record most (start, end, seconds matched), in order, till
    they've matched_wanted seconds between them."""
    found = []
    for start in range(0, len(live) - int(WINDOW * R) + 1, int(WINDOW * R)):
        end = start + int(WINDOW * R)
        inside = [(e - start, c) for e, c in anchors if start < e <= end]
        if len(inside) < 2:
            continue
        got = tonecheck.analyse(live[start:end], inside, guitar, rate, 0.0, notes, offset)
        if got.matched > 0:
            found.append((start, end, got.matched))
    found.sort(key=lambda w: -w[2])
    chosen, total = [], 0.0
    for w in found:
        if total >= matched_wanted:
            break
        chosen.append(w)
        total += w[2]
    return sorted(chosen)


def playlist(x: np.ndarray, chosen) -> np.ndarray:
    gap = np.zeros(int(GAP * R), np.float32)
    return np.concatenate([np.concatenate([x[s:e], gap]) for s, e, _m in chosen])


def placed(played: np.ndarray, chosen, length: int) -> np.ndarray:
    """A playlist's windows put back where they were in the take (silence between: not compared)."""
    out = np.zeros(length, np.float32)
    at = 0
    for s, e, _m in chosen:
        out[s:e] = played[at:at + (e - s)]
        at += (e - s) + int(GAP * R)
    return out


SCORED = slice(0, -1)  # (63 Hz to 8 kHz: a record's guitar has next to nothing at 10 kHz, 50 dB under, so a little
# there of yours, as an EQ's top boost gives, shows as -14 dB though it's barely there either way)


def score(eq) -> float:
    """How far a tone is from the record's: its EQ difference's RMS over the bands scored (dB)."""
    return float(np.sqrt(np.mean(np.square(np.asarray(eq)[SCORED])))) if eq is not None and len(eq) else float("inf")


def round_two(base) -> list:
    """Round 1's effects combined (predicted 1.57 against GODS B's 1.86: METAL 6.6k +6 with the NT1), and the lows the
    record has over GODS B (+2-4 dB at 80-125 Hz) added."""
    metal = {"240Hz": -4, "2.2KHz": 3}
    return [
        variant(base, "S GODS B"),
        variant(base, "S M6 NT1", cab={"MIC": "NT1"}, eq_model=1, eq={**metal, "6.6KHz": 6}),
        variant(base, "S M6 SM57", eq_model=1, eq={**metal, "6.6KHz": 6}),
        variant(base, "S M6 80+6 NT1", cab={"MIC": "NT1"}, eq_model=1, eq={**metal, "80Hz": 6, "6.6KHz": 6}),
        variant(base, "S M6 80+10 NT1", cab={"MIC": "NT1"}, eq_model=1, eq={**metal, "80Hz": 10, "6.6KHz": 6}),
        variant(base, "S G 100+6 NT1", cab={"MIC": "NT1"}, eq={"100Hz": 6, "250Hz": -4, "1.6KHz": 3, "4KHz": 2}),
        variant(base, "S M4 C0 NT1", cab={"MIC": "NT1", "CENTER": 0}, eq_model=1, eq={**metal, "6.6KHz": 4}),
    ]


def effects(back: list, chosen, centres_in) -> list:
    """Each try's effect against try 0's (the base), per band (dB, the middle's level taken off as the check does),
    over the moments the base plays in."""
    bands = np.array(tonecheck.BANDS)
    middle = (bands >= tonecheck.SHAPED[0]) & (bands <= tonecheck.SHAPED[1])

    def shape(x):
        m = tonecheck.moments(x, R, centres_in)
        each = m.bands / np.maximum(m.bands[:, middle].sum(axis=1, keepdims=True), 1e-12)
        return 10 * np.log10(np.maximum(each, 1e-7)), m.level
    base, level = shape(back[0])
    playing = level > np.percentile(level, 95) - 20
    out = []
    for x in back:
        d = shape(x)[0][playing] - base[playing]
        d -= np.median(d[:, middle], axis=1, keepdims=True)
        e = np.median(d, axis=0)
        out.append(e - np.median(e[middle]))
    return out


def round_three(base) -> list:
    """Pursuit of Vikings from GODS C (its record against GODS C, live: lows +4.4, upper mids -1.3, presence -1.5,
    treble -5.7: darker and bassier), one change at a time."""
    metal = {"80Hz": 6, "240Hz": -4, "2.2KHz": 3, "6.6KHz": 6}  # (GODS C's)
    return [
        variant(base, "S GODS C"),
        variant(base, "S 6K6 0", eq={**metal, "6.6KHz": 0}),
        variant(base, "S 6K6 -6", eq={**metal, "6.6KHz": -6}),
        variant(base, "S 80 +12", eq={**metal, "80Hz": 12}),
        variant(base, "S 2K2 0", eq={**metal, "2.2KHz": 0}),
        variant(base, "S CENTER 50", cab={"CENTER": 50}),
        variant(base, "S MD441", cab={"MIC": "MD441"}),
        variant(base, "S SM57", cab={"MIC": "SM57"}),
        variant(base, "S PRES 40", amp={"PRES": 40}),
        variant(base, "S TREBLE 70", amp={"TREBLE": 70}),
        variant(base, "S BASS 64", amp={"BASS": 64}),
    ]


def round_four(base) -> list:
    """Pursuit from GODS C: round 3's combination (predicted 2.67 against 4.44), and the METAL EQ's 240, 750 and 2.2k
    bands for what's left (the record -4 to -7 dB at 160-200 Hz, -5 at 1 kHz, +4 at 3.2 kHz). Try 0 is GODS C, the
    reference each try's measured against."""
    best = {"80Hz": 12, "240Hz": -4, "2.2KHz": 3, "6.6KHz": -6}
    amp = {"BASS": 64}
    return [
        variant(base, "S GODS C"),
        variant(base, "S P1", amp=amp, eq=best),
        variant(base, "S P1 240-10", amp=amp, eq={**best, "240Hz": -10}),
        variant(base, "S P1 750-4", amp=amp, eq={**best, "750Hz": -4}),
        variant(base, "S P1 2K2+6", amp=amp, eq={**best, "2.2KHz": 6}),
        variant(base, "S P1 ALL3", amp=amp, eq={**best, "240Hz": -10, "750Hz": -4, "2.2KHz": 6}),
    ]


def round_one(base) -> list:
    """Gods' treble (the record +4-6 dB at 6-10 kHz, which GODS B's GUITAR EQ can't reach): one change at a time."""
    gods_metal = {"240Hz": -4, "2.2KHz": 3}  # (GODS B's EQ, as near as the METAL EQ's bands go)
    return [
        variant(base, "S GODS B"),
        variant(base, "S METAL 6K6+4", eq_model=1, eq={**gods_metal, "6.6KHz": 4}),
        variant(base, "S METAL 6K6+8", eq_model=1, eq={**gods_metal, "6.6KHz": 8}),
        variant(base, "S CENTER 40", cab={"CENTER": 40}),
        variant(base, "S CENTER 80", cab={"CENTER": 80}),
        variant(base, "S DIST 10", cab={"DISTANCE": 10}),
        variant(base, "S DIST 60", cab={"DISTANCE": 60}),
        variant(base, "S MD421", cab={"MIC": "MD421"}),
        variant(base, "S MD441", cab={"MIC": "MD441"}),
        variant(base, "S C4000B", cab={"MIC": "C4000B"}),
        variant(base, "S NT1", cab={"MIC": "NT1"}),
        variant(base, "S PRES 95", amp={"PRES": 95}),
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", help="the check's recording name (default: the latest with a dry take)")
    ap.add_argument("--matched", type=float, default=30.0, help="seconds matched to compare on")
    ap.add_argument("--dry-run", action="store_true", help="the windows and the live take's check only: no pedal")
    ap.add_argument("--only", help="which tries (their numbers, from 0)")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--base", type=int, default=BASE, help="the slot the tries start from")
    ap.add_argument("--quick", type=float, help="seconds of playing per try: each try's exact effect, from try 0")
    args = ap.parse_args()
    check, folder = reamp.find_check(args.check)
    dry, live = reamp.read_wav(folder / check["dry"]), reamp.read_wav(folder / check["recording"])
    anchors = [(int(e), None if c is None else float(c)) for e, c in
               json.loads((folder / (check["recording"][:-4] + ".json")).read_text())]
    guitar, rate, notes = song(check)
    offset = float(check.get("offset", 0.0))
    chosen = windows(live, anchors, guitar, rate, notes, offset, args.matched)
    seconds = sum(e - s for s, e, _m in chosen) / R
    print(f"{check['song']}: {len(chosen)} stretches, {seconds:.0f} s of playing, "
          f"{sum(m for _s, _e, m in chosen):.1f} s matched", flush=True)
    baseline = tonecheck.analyse(placed(playlist(live, chosen), chosen, len(live)), anchors, guitar, rate, 0.0, notes,
                                 offset)
    print(f"  live ({check['preset']['name']}): score {score(baseline.eq):.2f}, matched {baseline.matched} s,", baseline.regions,
          flush=True)
    if args.dry_run:
        return
    if not reamp.headphones() or "headphone" in reamp.sd.query_devices(kind="output")["name"].lower():
        sys.exit("The Mac's sound output must stay the Blender")
    client = _client()
    presets = {p.slot: p for p in client.read_presets()}
    assert len(presets) == 200, "the DNAfx didn't send its presets"
    backups = Path.home() / "Music/DNAfx backups" / time.strftime("%Y-%m-%d %H%M%S custom slots")
    backups.mkdir(parents=True, exist_ok=True)
    for slot in range(188, 198):
        (backups / f"{slot:03d}-{presets[slot].name or 'unnamed'}.bin").write_bytes(presets[slot].to_bytes())
    print("saved 188-197 in", backups, flush=True)
    base = presets[args.base]
    print(f"base: {args.base} {base.name}: {describe(base)}", flush=True)
    tries = {1: round_one, 2: round_two, 3: round_three, 4: round_four}[args.round](base)
    if args.only:
        tries = [tries[int(k)] for k in args.only.split(",")]
    held = HOLD.exists()
    HOLD.touch()
    base_eq = None
    if args.quick:
        full = [json.loads(f.read_text()) for f in sorted(OUT.glob("*.json")) if not f.stem.endswith("-quick")]
        full = [f for f in full if f["check"] == check["recording"] and f["tries"]]  # (this take's, its base first)
        base_eq = np.array(full[-1]["tries"][0]["eq"]) if full else None
        if base_eq is None:
            sys.exit("--quick needs a full run first (the base's difference from the record)")
        most = sorted(chosen, key=lambda w: -w[2])[: max(1, int(args.quick / WINDOW))]
        chosen = sorted(most)
        print(f"quick: {len(chosen)} stretches ({len(chosen) * WINDOW:.0f} s), each try against try 0", flush=True)
    piece = playlist(dry, chosen)
    played = []
    results = []
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        for n, p in enumerate(tries):
            assert p.slot == SLOT and p.slot not in NEVER
            said = client.upload(p)
            if not any(c == ACK for c, _d in said):
                sys.exit(f"The pedal didn't take {p.name}")
            client.switch(args.base)  # (away and back: the slot's new values loaded)
            time.sleep(0.3)
            client.switch(SLOT)
            time.sleep(0.7)
            got = reamp.reamp(piece)
            at = reamp.lag_of(got, playlist(live, chosen))
            back = got[at:at + len(piece)]
            reamp.write_wav(OUT / f"{stamp}-{n:02d}-{p.name.replace(' ', '_')}.wav", back)
            if args.quick:
                played.append(back)
                centres = np.arange(tonecheck.FRAME // 2, len(back) - tonecheck.FRAME // 2, int(0.025 * R))
                effect = effects(played, chosen, centres)[-1]
                predicted = base_eq - effect
                results.append({"try": n, "name": p.name, "preset": describe(p),
                                "effect": [round(float(v), 2) for v in effect],
                                "predicted": [round(float(v), 2) for v in predicted],
                                "score": round(score(predicted), 2)})
                print(f"{n:2d} {p.name:14s} predicted score {results[-1]['score']:5.2f}  effect " +
                      " ".join(f"{v:+.1f}" for v in effect), flush=True)
                continue
            result = tonecheck.analyse(placed(back, chosen, len(live)), anchors, guitar, rate, 0.0, notes, offset)
            results.append({"try": n, "name": p.name, "preset": describe(p), "score": round(score(result.eq), 2),
                            "matched": result.matched, "regions": result.regions, "eq": result.eq,
                            "crest": [result.crest_yours, result.crest_record]})
            print(f"{n:2d} {p.name:14s} score {score(result.eq):5.2f} " +
                  " ".join(f"{k} {v:+.1f}" for k, v in result.regions.items()), flush=True)
    finally:
        client.switch(args.base)
        if not held:
            HOLD.unlink(missing_ok=True)
        remember_names({SLOT: tries[-1].name if results else presets[SLOT].name})
        (OUT / f"{stamp}{'-quick' if args.quick else ''}.json").write_text(json.dumps({"check": check["recording"], "windows": chosen,
                                                       "baseline": {"score": score(baseline.eq),
                                                                    "regions": baseline.regions, "eq": baseline.eq},
                                                       "tries": results}, indent=1))
    best = min(results, key=lambda r: r["score"])
    print(f"best: {best['name']} (score {best['score']}): {best['preset']}")
    print("saved", OUT / f"{stamp}{'-quick' if args.quick else ''}.json")


if __name__ == "__main__":
    main()
