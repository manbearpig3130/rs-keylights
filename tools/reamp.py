# Reamping: a tone check's dry guitar played into the DNAfx again (the MacBook's headphone jack, left channel only,
# into the pedal's own guitar input: after the Drop, as the dry was recorded), the pedal's sound recorded where it comes
# back (Blender 11), and set against the live take of the same moments (the same performance: what differs is the
# level going in, or the preset).
#   .venv/bin/python tools/reamp.py [--check NAME] [--start S] [--seconds N] [--gain DB]
# Calibrated 2026-11-24: the jack at full volume with the dry's samples as recorded (--gain 0) matches the live take
# within 0.1 dB in every region (treble 0.3), the same crest. The jack's set to full only while it plays (hpvol, built
# from tools/hpvol.swift the first time), then put back: headphones plugged in later aren't blasted.
# Takes kept for it: ~/Library/Application Support/rs-keylights/reamp-takes (NAME.wav, NAME-dry.wav, NAME.json: the
# anchors, NAME.check.json: the check), else the tone checks' cache (pruned after CHECKS_MOST checks).
import argparse
import json
import re
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from rs_keylights import tonecheck

CHECKS = Path.home() / "Library/Caches/rs-keylights/tone-checks"
KEPT = Path.home() / "Library/Application Support/rs-keylights/reamp-takes"
OUT = Path.home() / "Library/Caches/rs-keylights/reamp"
HPVOL = Path.home() / "Library/Application Support/rs-keylights/bin/hpvol"
R = tonecheck.RATE
PEDAL = ("Blender", 11)
DRY = 1  # (the guitar's channel on the same interface: what reaches the pedal, if it's played in at the chain's start)


def read_wav(p: Path) -> np.ndarray:
    with wave.open(str(p)) as w:
        width, raw = w.getsampwidth(), w.readframes(w.getnframes())
    if width == 2:
        return (np.frombuffer(raw, np.int16) / 32768).astype(np.float32)
    b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
    whole = b[:, 0].astype(np.int32) | b[:, 1].astype(np.int32) << 8 | b[:, 2].astype(np.int8).astype(np.int32) << 16
    return (whole / 8388608).astype(np.float32)


def write_wav(p: Path, x: np.ndarray) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(R)
        w.writeframes(np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16).tobytes())


def find_check(name: str | None) -> tuple[dict, Path]:
    """A check with a dry take (the latest, or the one named) and the folder its recordings are in."""
    found = []
    for p in sorted(KEPT.glob("*.check.json")):
        found.append((json.loads(p.read_text()), KEPT))
    try:
        found += [(c, CHECKS) for c in json.loads((CHECKS / "checks.json").read_text()) if c.get("dry")
                  and (CHECKS / c["dry"]).exists()]
    except (OSError, ValueError):
        pass
    found = [(c, f) for c, f in found if name in (None, c["recording"][:-4], c["recording"])]
    if not found:
        sys.exit(f"No tone check with a dry take{f' named {name}' if name else ''}")
    return max(found, key=lambda cf: cf[0]["recording"])


def jack_volume(scalar: float | None = None) -> float:
    """The headphone jack's volume (0..1), set first if asked."""
    if not HPVOL.exists():
        HPVOL.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", "-o", str(HPVOL), str(Path(__file__).with_name("hpvol.swift"))], check=True,
                       capture_output=True)
    out = subprocess.run([str(HPVOL), *([] if scalar is None else [str(scalar)])], check=True, capture_output=True,
                         text=True).stdout
    got = re.search(r"volume ([0-9.]+)", out)
    if not got:
        sys.exit("No headphone jack volume: plug the cable into the MacBook's headphone jack")
    return float(got.group(1))


def headphones() -> int:
    """The MacBook's headphone jack (never its speakers, never the Blender: the Blender's mixes carry the pedal back)."""
    for i, d in enumerate(sd.query_devices()):
        if "headphone" in d["name"].lower() and d["max_output_channels"] >= 2:
            return i
    sys.exit("No headphone output: plug the cable into the MacBook's headphone jack")


def loudest(x: np.ndarray, seconds: float) -> int:
    """Where the window of that many seconds with the most playing starts (its 1 s blocks' median level)."""
    blocks = np.sqrt((x[: len(x) // R * R].reshape(-1, R) ** 2).mean(axis=1))
    n = int(seconds)
    best = max(range(0, len(blocks) - n + 1), key=lambda k: np.median(blocks[k:k + n]))
    return best * R


def envelope(x: np.ndarray) -> np.ndarray:
    e = np.sqrt(np.convolve(x.astype(np.float64) ** 2, np.ones(48) / 48, mode="same"))
    return (e - e.mean()) / (e.std() + 1e-12)


def lag_of(recorded: np.ndarray, live: np.ndarray) -> int:
    """Where the live take's moments start in what was recorded (their envelopes' best match; it fails far too quiet,
    under about -24 dB going in)."""
    a, b = envelope(recorded), envelope(live)
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)[: len(a) - len(b) + 1]
    return int(np.argmax(c))


def compare(live: np.ndarray, reamped: np.ndarray) -> dict:
    """The reamped against the live, moment by moment (both the same performance): dB more (or less) in each region,
    level, and crest; over the moments the live take's playing in."""
    centres = np.arange(tonecheck.FRAME // 2, len(live) - tonecheck.FRAME // 2, int(0.025 * R))
    a, b = tonecheck.moments(live, R, centres), tonecheck.moments(reamped, R, centres)
    playing = a.level > np.percentile(a.level, 95) - 20
    out = {"moments": int(playing.sum()), "level": round(float(np.median((b.level - a.level)[playing])), 1),
           "crest live": round(float(np.median(a.crest[playing])), 1),
           "crest reamped": round(float(np.median(b.crest[playing])), 1)}
    bands = np.array(tonecheck.BANDS)
    for name, lo, hi in tonecheck.REGIONS:
        k = (bands >= lo) & (bands < hi)
        pa, pb = a.bands[playing][:, k].sum(axis=1), b.bands[playing][:, k].sum(axis=1)
        out[name] = round(float(np.median(10 * np.log10((pb + 1e-20) / (pa + 1e-20)))) - out["level"], 1)
    return out


def reamp(piece: np.ndarray, with_dry: bool = False):
    """A piece of dry guitar played into the pedal (the jack at full volume while it plays), and what came back:
    from a second before it was sent to a moment after it ended (with_dry: and what reached the guitar's channel, as
    it was played in at the start of the chain, beside it)."""
    jack = headphones()
    if "headphone" in sd.query_devices(kind="output")["name"].lower():
        sys.exit("The Mac's sound output switched to the headphones: set it back to the Blender (the game's sound would"
                 " go into the pedal)")
    if np.abs(piece).max() >= 0.99:
        sys.exit("It would clip the output")
    stereo = np.zeros((len(piece) + R // 2, 2), np.float32)
    stereo[: len(piece), 0] = piece  # (left only: a mono 1/4" plug in a stereo adapter grounds the right)
    recorded, dry_in, stop = [], [], threading.Event()

    def record() -> None:
        with sd.InputStream(samplerate=R, channels=PEDAL[1], device=PEDAL[0], blocksize=512, dtype="float32",
                            latency="low") as s:
            while not stop.is_set():
                x, _ = s.read(512)
                recorded.append(x[:, PEDAL[1] - 1].copy())
                dry_in.append(x[:, DRY - 1].copy())
    before = jack_volume()
    try:
        jack_volume(1.0)
        t = threading.Thread(target=record, daemon=True)
        t.start()
        time.sleep(1.0)
        sd.play(stereo, R, device=jack, blocking=True)
        time.sleep(0.3)
        stop.set()
        t.join()
    finally:
        jack_volume(before)
    return (np.concatenate(recorded), np.concatenate(dry_in)) if with_dry else np.concatenate(recorded)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", help="the check's recording name (default: the latest with a dry take)")
    ap.add_argument("--start", type=float, help="seconds into the take (default: its loudest stretch)")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--gain", type=float, default=0.0, help="dB over the dry as recorded (0: calibrated)")
    args = ap.parse_args()
    check, folder = find_check(args.check)
    dry, live = read_wav(folder / check["dry"]), read_wav(folder / check["recording"])
    start = int(args.start * R) if args.start is not None else loudest(dry, args.seconds)
    end = start + int(args.seconds * R)
    piece, live_piece = dry[start:end] * 10 ** (args.gain / 20), live[start:end]
    print(f"{check['song']} ({check['preset']['name']}), {start / R:.1f}–{end / R:.1f} s, gain {args.gain:+.1f} dB: "
          f"the piece peaks at {20 * np.log10(np.abs(piece).max()):.1f} dBFS", flush=True)
    got, came = reamp(piece, with_dry=True)
    at_dry = lag_of(came, piece)
    if np.abs(came).max() > 1e-3:  # (played in at the chain's start: what reached the pedal's input, against the take)
        print("reached the pedal's input, against the dry take (dB):", compare(piece, came[at_dry:at_dry + len(piece)]))
    at = lag_of(got, live_piece)
    reamped = got[at:at + len(live_piece)]
    name = f"{check['recording'][:-4]}-{start / R:.0f}s-{args.gain:+.0f}dB"
    write_wav(OUT / f"{name}.wav", reamped)
    write_wav(OUT / f"{name}-live.wav", live_piece)
    peak = 20 * np.log10(np.abs(got).max() + 1e-12)
    print(f"came back {at / R - 1.0:+.3f} s after it was sent; its peak {peak:.1f} dBFS"
          + (" (CLIPPED)" if np.abs(got).max() >= tonecheck.CLIP else ""))
    print("reamped against live (dB):", compare(live_piece, reamped))
    print("saved", OUT / f"{name}.wav")


if __name__ == "__main__":
    main()
