from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Sequence

from .library import ArrangementRef
from .psarc import Archive, ArchiveError

# A song's drum part, transcribed from its own audio, kept to play along with its arrangements: the song's audio
# taken from its archive and decoded, its drums separated from the rest (Demucs, the model rearmed-mods' Song Stems
# runs on the Neural Engine), transcribed (ADTOF: kick, snare, tom, hi-hat, cymbal, a strength for each 100 times a
# second), then refined here (each hit's velocity from how loud it is; the toms as low, mid and high by their pitch;
# the cymbals as crash or ride by their pattern; the hi-hats closed: open ones can't be told from the cymbals' wash
# in the drum track) and snapped to
# the chart's 16th notes, so it keeps time with the game's song (Riff Repeater and speed changes too).

DRUMS_VERSION = 2  # (of the kept parts: another, made again; 2: hits no longer pulled onto the chart's beats)
KICK, SNARE, CLOSED_HAT, OPEN_HAT, CRASH, RIDE = 36, 38, 42, 46, 49, 51  # General MIDI drums
TOMS = (43, 47, 50)  # low (high floor tom), mid (low-mid tom), high
NAMES = {KICK: "kick", SNARE: "snare", CLOSED_HAT: "hi-hat", OPEN_HAT: "open hi-hat", CRASH: "crash", RIDE: "ride",
         43: "low tom", 47: "mid tom", 50: "high tom"}
FPS = 100  # the model's strengths a second
CLASSES = ("kick", "snare", "tom", "hi-hat", "cymbal")  # (its columns)
ADTOF_THRESHOLDS = (0.22, 0.24, 0.32, 0.22, 0.30)  # a hit rises this much at least (ADTOF's own)
THRESHOLDS = (0.22, 0.16, 0.32, 0.22, 0.30)  # (the snare's lower: a few quiet ones missed)
AVERAGE = 0.1  # seconds: a hit rises over its drum's strength on average over this before it (as ADTOF's)
COMBINE = 0.02  # seconds: one drum's peaks this close, one hit
SNAP_MOST = 0.012  # seconds a hit's moved to the chart's grid at most (about the model's own precision: further,
# left where the record has it)
DRUM_CHANNEL = 10  # General MIDI's drums


@dataclass(frozen=True)
class Hit:
    time: float  # seconds into the song
    note: int  # General MIDI drum note
    velocity: int


def tools_dir() -> Path:
    """Where the tools that make drum parts are (drums-setup.sh puts them there)."""
    return Path(os.environ.get("RS_KEYLIGHTS_DRUMS_TOOLS",
                               Path.home() / "Library" / "Application Support" / "rs-keylights" / "drums"))


def cache_dir() -> Path:
    return Path.home() / "Library" / "Caches" / "rs-keylights" / "drums"


def _cache_file(ref: ArrangementRef, cache: Path | None = None) -> Path:
    return (cache or cache_dir()) / f"{Path(ref.archive).name}#{ref.song_key}.json"


_read: dict[Path, tuple[float, int, int, list[Hit] | None]] = {}  # (parts read: by file, as it was then: its time
# and size, and the version read for)


def kept(ref: ArrangementRef, cache: Path | None = None) -> list[Hit] | None:
    """A song's drum part, made already; None if it hasn't one. (Read once, again only when its file changes: a
    remote asks often.)"""
    path = _cache_file(ref, cache)
    try:
        stat = path.stat()
    except OSError:
        return None
    seen = _read.get(path)
    if seen is not None and seen[:3] == (stat.st_mtime, stat.st_size, DRUMS_VERSION):
        return seen[3]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    hits = None
    if isinstance(data, dict) and data.get("version") == DRUMS_VERSION:
        hits = [Hit(float(t), int(n), int(v)) for t, n, v in data.get("hits", [])]
    _read[path] = (stat.st_mtime, stat.st_size, DRUMS_VERSION, hits)
    return hits


def made_before(ref: ArrangementRef, cache: Path | None = None) -> bool:
    """Whether the song's drum part was made, by this version or an earlier one."""
    return _cache_file(ref, cache).exists()


def keep(ref: ArrangementRef, hits: Sequence[Hit], cache: Path | None = None) -> None:
    path = _cache_file(ref, cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": DRUMS_VERSION, "song": ref.title,
                                "hits": [[round(h.time, 4), h.note, h.velocity] for h in hits]}))
    temp.replace(path)


# Hits from the model's strengths

def pick(strengths: Any, thresholds: Sequence[float] = THRESHOLDS) -> list[tuple[float, int, float]]:
    """Each drum's hits in the model's strengths (frames x CLASSES), as ADTOF picks them (madmom's note peak picking):
    where its strength rises over its average (from AVERAGE before to a frame after) by its threshold at least, the
    highest of the frames around it (2 before, 1 after), peaks within COMBINE of each other taken as one (the
    highest). (seconds, class, how far it rises), in time."""
    import numpy as np
    act = np.asarray(strengths, dtype=np.float32)
    before, after = round(AVERAGE * FPS), 1
    out: list[tuple[float, int, float]] = []
    for c in range(act.shape[1]):
        x = act[:, c]
        padded = np.pad(x, (before, after), mode="edge")
        average = np.convolve(padded, np.ones(before + 1 + after) / (before + 1 + after), mode="valid")
        rise = np.maximum(0.0, x - average)
        around = np.pad(rise, (2, 1), mode="edge")
        highest = np.max(np.stack([around[k:k + len(rise)] for k in range(4)]), axis=0)
        peaks = np.flatnonzero((rise >= highest) & (rise >= thresholds[c]))
        group: list[int] = []
        for i in [*peaks.tolist(), None]:
            if i is not None and group and i - group[-1] <= COMBINE * FPS:
                group.append(i)
                continue
            if group:
                best = max(group, key=lambda k: rise[k])
                out.append((best / FPS, c, float(rise[best])))
            group = [i] if i is not None else []
    return sorted(out)


# Refined from the drum track: velocities, which tom, crash or ride, open or closed hi-hat

def refine(picked: Sequence[tuple[float, int, float]], audio: Any, rate: int) -> list[Hit]:
    """The picked hits as General MIDI drums, from the drum track (mono samples, -1 to 1): each one's velocity from
    how loud its drum's band is as it's struck (against that drum's loudest), the toms in up to 3 pitches (by their
    low resonance), the cymbals crash or ride (a ride's hits come in steady runs), and the hi-hats open where they
    ring on: there's no telling them from the cymbals' wash in the drum track, so they're all closed)."""
    import numpy as np
    x = np.asarray(audio, dtype=np.float32)
    window = 2048
    spectrum = np.fft.rfftfreq(window, 1 / rate)

    def frame(t: float, after: float = 0.0) -> Any:
        start = max(int((t + after) * rate) - window // 4, 0)
        seg = x[start:start + window]
        if len(seg) < window:
            seg = np.pad(seg, (0, window - len(seg)))
        return np.abs(np.fft.rfft(seg * np.hanning(window)))

    def band(mag: Any, lo: float, hi: float) -> float:
        sel = (spectrum >= lo) & (spectrum < hi)
        return float(np.sqrt(np.mean(mag[sel] ** 2))) if sel.any() else 0.0

    BANDS = {0: (40, 120), 1: (150, 5000), 2: (70, 400), 3: (6000, 14000), 4: (4000, 12000)}
    loudness = [band(frame(t), *BANDS[c]) for t, c, _s in picked]
    out: list[Hit] = []
    by_class: dict[int, list[int]] = {}
    for i, (_t, c, _s) in enumerate(picked):
        by_class.setdefault(c, []).append(i)
    velocity = [100] * len(picked)
    for c, members in by_class.items():
        top = float(np.percentile([loudness[i] for i in members], 95)) or 1.0
        for i in members:
            velocity[i] = int(np.clip(round(127 * (loudness[i] / top) ** 0.6), 30, 127))
    # Toms: their pitch, the strongest peak of 60-400 Hz just after the stroke; up to 3 groups
    tom_note: dict[int, int] = {}
    toms = by_class.get(2, [])
    if toms:
        sel = (spectrum >= 60) & (spectrum < 400)
        pitch = np.array([spectrum[sel][int(np.argmax(frame(picked[i][0], 0.02)[sel]))] for i in toms])
        logs = np.log(pitch)
        spread = float(np.percentile(logs, 90) - np.percentile(logs, 10))
        groups = 3 if spread > np.log(1.5) else 2 if spread > np.log(1.2) else 1
        edges = np.percentile(logs, np.linspace(0, 100, groups + 1)[1:-1]) if groups > 1 else np.array([])
        notes = TOMS if groups == 3 else (TOMS[0], TOMS[2]) if groups == 2 else (TOMS[1],)
        for i, lg in zip(toms, logs):
            tom_note[i] = notes[int(np.searchsorted(edges, lg))]
    # Cymbals: a ride's hits are a steady run (at least 4, each within 0.6 s of the next); the rest crash
    ride: set[int] = set()
    cymbals = by_class.get(4, [])
    run: list[int] = []
    for i in cymbals + [-1]:
        if i >= 0 and run and picked[i][0] - picked[run[-1]][0] <= 0.6:
            run.append(i)
            continue
        if len(run) >= 4:
            ride.update(run)
        run = [i] if i >= 0 else []
    for i, (t, c, _s) in enumerate(picked):
        note = (KICK if c == 0 else SNARE if c == 1 else tom_note.get(i, TOMS[1]) if c == 2 else
                CLOSED_HAT if c == 3 else RIDE if i in ride else CRASH)
        out.append(Hit(t, note, velocity[i]))
    return out


def snap(hits: Sequence[Hit], beats: Sequence[float]) -> list[Hit]:
    """The hits as the record has them, tidied: one within SNAP_MOST of the chart's 16th notes (between its beats)
    put on it, the rest left where they are, and one drum twice on a time once (the louder). Not moved to the chart:
    a song's chart can be tens of milliseconds off its audio (custom ones especially), and the part plays with the
    audio, not the chart."""
    import numpy as np
    b = np.array(sorted(beats), dtype=np.float64)
    if len(b) < 2 or not hits:
        return list(hits)
    grid = np.concatenate([np.linspace(b0, b1, 5)[:-1] for b0, b1 in zip(b, b[1:])] + [b[-1:]])
    t = np.array([h.time for h in hits])
    i = np.clip(np.searchsorted(grid, t), 1, len(grid) - 1)
    at = np.where(np.abs(grid[i] - t) < np.abs(grid[i - 1] - t), grid[i], grid[i - 1])
    kept_hits: dict[tuple[float, int], Hit] = {}
    for h, m, g in zip(hits, t, at):
        when = float(g) if abs(g - m) <= SNAP_MOST else float(m)
        key = (round(when, 4), h.note)
        if key not in kept_hits or kept_hits[key].velocity < h.velocity:
            kept_hits[key] = Hit(round(when, 4), h.note, h.velocity)
    return sorted(kept_hits.values(), key=lambda h: (h.time, h.note))


# Making one: the tools

# The game's Vorbis codebooks (for decoding its audio), from its x86-64 image: a table of pointers into one block
# (rearmed-mods' Song Stems writes the same file from inside the game). For the one image these addresses are of.
IMAGE_SHA256 = "3716171942ad3b920853a6e7c330d839d2cacd876058989ece5dfa9f466a3c9d"
CODEBOOK_TABLE, CODEBOOK_COUNT, CODEBOOK_SLACK, IMAGE_BASE = 0x1018ad3c0, 599, 0x400, 0x100000000


class DrumsError(Exception):
    pass


def codebooks(game_dir: Path, out: Path) -> None:
    """The codebook library (codebooks.bin: "RSCB", the count, each one's offset, the data) from the game's image."""
    image = game_dir / "Rocksmith2014.app" / "Contents" / "Resources" / "Rocksmith2014.x86_64"
    try:
        data = image.read_bytes()
    except OSError as exc:
        raise DrumsError(f"Can't read the game's image ({image}): {exc}") from exc
    if hashlib.sha256(data).hexdigest() != IMAGE_SHA256:
        raise DrumsError("The game's image isn't the one known (another Rocksmith build): no codebooks")
    pointers = struct.unpack_from(f"<{CODEBOOK_COUNT}Q", data, CODEBOOK_TABLE - IMAGE_BASE)
    offsets = [p - pointers[0] for p in pointers]
    if any(b < a for a, b in zip(offsets, offsets[1:])) or offsets[-1] >= 1 << 20:
        raise DrumsError("The game's codebook table doesn't look right")
    block = data[pointers[0] - IMAGE_BASE:pointers[0] - IMAGE_BASE + offsets[-1] + CODEBOOK_SLACK]
    out.write_bytes(b"RSCB" + struct.pack(f"<I{CODEBOOK_COUNT}I", CODEBOOK_COUNT, *offsets) + block)


def _song_bank(ref: ArrangementRef) -> str:
    """The name of a song's sound bank, as its manifest has it (SongBank: song_painkiller.bnk), else as they're named."""
    try:
        with Archive(Path(ref.archive)) as archive:
            for name in archive.names:
                if not (name.startswith("manifests/") and name.endswith(".json")):
                    continue
                try:
                    manifest = json.loads(archive.read(name))
                except ValueError:
                    continue
                for entry in ((manifest.get("Entries") if isinstance(manifest, dict) else None) or {}).values():
                    attributes = entry.get("Attributes") if isinstance(entry, dict) else None
                    if isinstance(attributes, dict) and attributes.get("SongKey") == ref.song_key and \
                            attributes.get("SongBank"):
                        return str(attributes["SongBank"])
    except OSError:
        pass
    return f"song_{ref.song_key.lower()}.bnk"


def song_audio(ref: ArrangementRef, game_dir: Path) -> bytes:
    """A song's audio (its Wwise .wem): the one its sound bank names, from the archive with that bank (its own, else
    songs.psarc: on-disc songs and Rocksmith 1's, whose charts are in a pack of their own, else any other); an archive
    with just one song's audio and no bank, its longer (the other is its preview)."""
    import struct
    bank = f"audio/mac/{_song_bank(ref)}".lower()
    own = Path(ref.archive)
    others = sorted(p for p in [*game_dir.glob("*.psarc"), *(game_dir / "dlc").glob("*.psarc")]
                    if p not in (own, game_dir / "songs.psarc"))
    for path in [own, game_dir / "songs.psarc", *others]:
        try:
            with Archive(path) as archive:
                names = {name.lower(): name for name in archive.names}
                if bank not in names:
                    continue
                data = archive.read(names[bank])
                wems = {int(Path(name).stem): name for name in archive.names
                        if name.lower().endswith(".wem") and Path(name).stem.isdigit()}
                named = {struct.unpack_from("<I", data, i)[0] for i in range(len(data) - 3)} & wems.keys()
                if named:
                    return max((archive.read(wems[i]) for i in named), key=len)
        except (OSError, ArchiveError):
            continue
    with Archive(own) as archive:  # (no bank found: its own audio, if it's just one song's: one bank at most)
        wems = [name for name in archive.names if name.lower().endswith(".wem")]
        banks = [name for name in archive.names if name.lower().endswith(".bnk") and "preview" not in name.lower()]
        if 0 < len(wems) <= 2 and len(banks) <= 1:
            return max((archive.read(name) for name in wems), key=len)
    raise DrumsError(f"No audio found for this song (its sound bank, {_song_bank(ref)}, isn't in the game's files)")


# How far along making a part is (0 to 1): each step's share. Separating says how far it is (the frames it's done);
# transcribing doesn't, so it's reckoned from the song's length (TRANSCRIBING a second of it: ~10 s for 4:48)
DECODED, SEPARATED, TRANSCRIBED = 0.03, 0.68, 0.98
TRANSCRIBING = 0.04  # seconds a second of song, about
PROGRESS_EVERY = 0.25  # seconds between looks at how far a step is


def _ready(pcm: Path) -> float | None:
    """How much of the song the separator's done (0 to 1), from its .pcm's header (frames ready of the total)."""
    try:
        with open(pcm, "rb") as f:
            head = f.read(36)
        total, ready = struct.unpack_from("<I", head, 24)[0], struct.unpack_from("<I", head, 32)[0]
    except (OSError, struct.error):
        return None
    return min(ready / total, 1.0) if total else None


def make(ref: ArrangementRef, beats: Sequence[float], game_dir: Path, tools: Path | None = None,
         stems_home: Path | None = None, say: Callable[[str], None] = lambda _text: None,
         cache: Path | None = None, progress: Callable[[float], None] = lambda _done: None) -> list[Hit]:
    """A song's drum part made (a minute or so) and kept: its audio from its archive, decoded and its drums separated
    (drums-setup.sh's rs-stems-drums, with Song Stems' model), transcribed (ADTOF, in drums-setup.sh's Python),
    refined and snapped to beats (the chart's). How far along it is goes to progress (0 to 1) as it goes."""
    import numpy as np
    tools = tools or tools_dir()
    stems_home = stems_home or Path.home() / "Library" / "Application Support" / "RS Re-ARMed" / "stems"
    helper, python = tools / "rs-stems-drums", tools / "venv" / "bin" / "python"
    for needed, what in ((helper, "the drum separator"), (python, "the drum transcriber's Python"),
                         (stems_home / "htdemucs_6s.mlmodelc", "Song Stems' model (rearmed-mods)")):
        if not needed.exists():
            raise DrumsError(f"No {what} at {needed}: run tools/drums-setup.sh")
    if not (tools / "codebooks.bin").exists():
        codebooks(game_dir, tools / "codebooks.bin")
    with tempfile.TemporaryDirectory(prefix="rs-keylights-drums-") as work_dir:
        work = Path(work_dir)
        (work / "song.wem").write_bytes(song_audio(ref, game_dir))
        env = {**os.environ, "RS_STEMS_CACHE": str(tools), "RS_STEMS_HOME": str(stems_home)}

        def run(args: list[str], what: str, along: Callable[[float], float | None] | None = None) -> None:
            """A tool run (along: how far it is, from the time it's taken, as it goes: for progress)."""
            with tempfile.TemporaryFile("w+") as out:
                started = time.monotonic()
                tool = subprocess.Popen(args, env=env, stdout=out, stderr=subprocess.STDOUT, text=True)
                while True:
                    try:
                        code = tool.wait(PROGRESS_EVERY)
                        break
                    except subprocess.TimeoutExpired:
                        far = along(time.monotonic() - started) if along is not None else None
                        if far is not None:
                            progress(far)
                if code != 0:
                    out.seek(0)
                    raise DrumsError(f"{what} failed: {out.read().strip()[-300:]}")

        def between(start: float, end: float, done: float | None) -> float | None:
            return None if done is None else start + (end - start) * min(max(done, 0.0), 1.0)

        progress(0.0)
        say("decoding its audio")
        run([str(helper), "decode", str(work / "song.wem"), str(work / "song.wav")], "Decoding the song")
        progress(DECODED)
        say("separating its drums")
        run([str(helper), "separate", str(work / "song.wem"), "1", "drums", str(work / "rest.pcm")],
            "Separating the drums", lambda _took: between(DECODED, SEPARATED, _ready(work / "rest.pcm")))
        progress(SEPARATED)
        with wave.open(str(work / "song.wav")) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            mix = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, channels)
        raw = (work / "rest.pcm").read_bytes()
        total, state = struct.unpack_from("<I", raw, 24)[0], struct.unpack_from("<I", raw, 28)[0]
        if state != 1:
            raise DrumsError("Separating the drums didn't finish")
        rest = np.frombuffer(raw, dtype=np.int16, offset=64, count=total * channels).reshape(-1, channels)
        drums = np.clip(mix[:len(rest)].astype(np.int32) - rest, -32768, 32767).astype(np.int16)
        with wave.open(str(work / "drums.wav"), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(drums.tobytes())
        say("transcribing them")
        worker = files("rs_keylights").joinpath("drums_activations.py")
        expected = max(len(drums) / rate * TRANSCRIBING, 1.0)  # (it doesn't say: reckoned, never quite there)
        run([str(python), "-I", str(worker), str(work / "drums.wav"), str(work / "act.npy")], "Transcribing",
            lambda took: between(SEPARATED, TRANSCRIBED, 0.95 * min(took / expected, 1.0)))
        progress(TRANSCRIBED)
        strengths = np.load(work / "act.npy").astype(np.float32)
        mono = drums.astype(np.float32).mean(axis=1) / 32768.0
        hits = snap(refine(pick(strengths), mono, rate), beats)
    keep(ref, hits, cache)
    progress(1.0)
    return hits


def setup_done(tools: Path | None = None) -> bool:
    tools = tools or tools_dir()
    return (tools / "rs-stems-drums").exists() and (tools / "venv" / "bin" / "python").exists()
