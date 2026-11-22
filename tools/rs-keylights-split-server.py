#!/usr/bin/env python3
"""rs-keylights-split-server.py: splits songs into their parts (stems) for rs-keylights, with BS-RoFormer SW on this
computer's NVIDIA GPU. rs-keylights (on the Mac) sends a song's audio; this sends back its drums, bass, guitar,
vocals and piano, and the Mac keeps them as it does its own. It splits the drums into the kit's pieces too (DrumSep:
kick, snare, toms, hi-hat, ride, crash), for rs-keylights' drum parts: crash or ride, and open or closed hi-hats,
from the cymbals' own sound.

    python3 rs-keylights-split-server.py [--port 8042] [--data DIR] [--device cuda|cpu|mps]

Each time it starts, it checks what it needs: Python 3.10 or later, an NVIDIA driver (nvidia-smi), 6 GB of free disk
for its first run. If something's missing it says what, and stops. It installs nothing system-wide, runs nothing as
root, and changes nothing outside its own folder (DIR, by default ~/.local/share/rs-keylights-split):

  venv/    a Python of its own: PyTorch 2.14.1 for CUDA 12.6 (with its own CUDA libraries: any NVIDIA driver from
           525 on), numpy, scipy, soundfile, pyyaml, einops, rotary-embedding-torch, beartype. About 4 GB. Made with
           uv if it's installed (it fetches Python 3.12 for it, into uv's own folder), else with this Python's venv.
  code/    the models' code: three files of ZFTurbo's Music-Source-Separation-Training (MIT licence) at a fixed
           commit, each checked by its SHA-256, and a few lines of this script's own in place of the rest of theirs.
  model/   the models: jarredou's BS-Rofo-SW-Fixed checkpoint (700 MB) and its settings, from the FractalSurfer
           mirror on Hugging Face at a fixed revision; and aufr33 and jarredou's DrumSep (MDX23C, 6 pieces: 440 MB)
           and its settings, from openmirlab's copy on GitHub (its own hosting's gone). Each checked by SHA-256.
           Neither's licence is stated: for your own use.
  key      a random key, made once: rs-keylights needs it (it's printed when this starts). Without it, nothing's
           answered (403).
  jobs/    songs being split and their parts, each gone once rs-keylights has them (or an hour after).

Then it runs itself in that Python and listens on the port, on your network (not the internet, unless your router
forwards the port: don't). The model's put on the GPU when a song comes, and let go of 10 minutes after the last,
so the GPU's memory is free for games. Ctrl-C stops it. To remove it all, delete DIR.

What rs-keylights asks it (each with the header X-RS-Keylights-Key: the key):
  GET    /status                    what it is, the GPU it runs on, how many songs are waiting
  POST   /split?parts=drums,bass…   a song's audio (WAV or FLAC, any rate): {"id": …}, split in turn. Its parts:
                                    BS-RoFormer SW's (drums, bass, other, vocals, guitar, piano) and DrumSep's
                                    (kick, snare, toms, hh, ride, crash: from BS-RoFormer SW's drums); &from=drums:
                                    the audio's a drum part already, just DrumSep's
  GET    /split/ID                  {"state": waiting|splitting|done|failed, "progress": 0-1, "error": …}
  GET    /split/ID/PART.flac        a part once it's done: 16-bit FLAC, as long as the song, at its rate
  DELETE /split/ID                  its files gone

The split is ZFTurbo's own (their utils/model_utils.py demix): the song at 44.1 kHz, mirrored past either end,
chunks of the model's (BS-RoFormer SW's 588 800 samples, 13.35 s, half a chunk apart; DrumSep's 130 560, 2.96 s, a
quarter apart), each through the model at half-precision, cross-faded (linear, a tenth of a chunk) and added up; a
song at another rate taken to 44.1 kHz and its parts back (scipy's resample_poly: zero-phase, each part in line with
the song).
"""
# pyright: reportMissingImports=false, reportMissingModuleSource=false
# (torch, numpy, scipy, soundfile and yaml: in its own Python, set up as it starts)
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

VERSION = 2  # (2: DrumSep's kit pieces too)
PORT = 8042
DATA = Path.home() / ".local" / "share" / "rs-keylights-split"
KEY_HEADER = "X-RS-Keylights-Key"

TORCH = "torch==2.14.1"
TORCH_INDEX = {"cuda": "https://download.pytorch.org/whl/cu126", "cpu": "https://download.pytorch.org/whl/cpu"}
PACKAGES = ("numpy", "scipy", "soundfile", "pyyaml", "packaging", "einops==0.8.1", "rotary-embedding-torch==0.3.5",
            "beartype==0.14.1")

CODE_URL = ("https://raw.githubusercontent.com/ZFTurbo/Music-Source-Separation-Training/"
            "1523206d12a521bdb20673726562524cf0f9635b/")
CODE = {  # (ZFTurbo's files: SHA-256)
    "models/bs_roformer/bs_roformer.py": "a6f670325cdb8a7a212914f62602119b23eb888df9bcfe4a85f9be74728b3ef4",
    "models/bs_roformer/attend.py": "c7abbc40a3fd20ff7f6001fa9f8ee9ad5df6452272712a5e719b8f8468bf2223",
    "models/mdx23c_tfc_tdf_v3.py": "45b29c37cbba4b06e49dcd6bef668d372501df2517392b71b93804355cb7d535e",
    "LICENSE": "3282dc057695ef5b9a64909a7092ca40b2c292c232580fc6ace6e5d665cc0207",
}
# In place of ZFTurbo's utils/model_utils.py (which brings in their training's packages too): the one function the
# MDX23C model's code takes from it, as theirs does it
MODEL_UTILS = '''"""rs-keylights-split-server's stand-in for ZFTurbo's utils/model_utils.py: prefer_target_instrument alone."""


def prefer_target_instrument(config):
    target = config.training.get("target_instrument")
    return [target] if target else list(config.training.instruments)
'''
MODEL_URL = ("https://huggingface.co/FractalSurfer/BS-RoFormer-SW-6stem/resolve/"
             "2b98254b19bdab07ce225d884ec4aa9dd8aafce1/")
CHECKPOINT, SETTINGS = "BS-Rofo-SW-Fixed.ckpt", "BS-Rofo-SW-Fixed.yaml"
MODEL = {
    CHECKPOINT: "24e7d35ee9c64415673d3fd33e06a67cac2c103c5df6267ba1576459c775916e",
    SETTINGS: "f9fada9f94e5ba2d2e4600196299459294bc5f532b314c209cc156ac63e4329b",
}
KIT_URL = "https://github.com/openmirlab/mdxnet-infer/releases/download/weights-drumsep-v1/"
KIT_CHECKPOINT = "aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.ckpt"
KIT_SETTINGS = "aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.yaml"
KIT_MODEL = {  # (as openmirlab's release has them, the same as the UVR and MSST-WebUI mirrors')
    KIT_CHECKPOINT: "d2a4aa53eb584d21eead358a4e66d1882ad182911be018f052b5da73be9096d0",
    KIT_SETTINGS: "17d1649a227f841165bdb4c11a42082898192a1ea3ceab7e7e0b9293d6589dd6",
}

RATE = 44100  # (the model's)
IDLE = 600  # seconds after the last song the model's let go of (the GPU's memory free again)
KEPT = 3600  # seconds a song's parts are kept for rs-keylights to fetch
MOST_UPLOAD = 1 << 30  # bytes of a song's audio, at most


# ------------------------------------------------------------------------------------------------------------------
# Setting up: with this Python, before anything else is installed
# ------------------------------------------------------------------------------------------------------------------

def say(text: str) -> None:
    print(text, flush=True)


def check(data: Path, device: str) -> None:
    """What it needs, there; else what's missing, and stop."""
    missing = []
    if sys.version_info < (3, 10):
        missing.append(f"Python 3.10 or later (this is {sys.version.split()[0]})")
    if device == "cuda":
        smi = shutil.which("nvidia-smi")
        if smi is None:
            missing.append("an NVIDIA driver (nvidia-smi isn't found: on Arch, the nvidia-open or nvidia package; "
                           "or run with --device cpu, many times slower)")
        else:
            gpus = subprocess.run([smi, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                                  capture_output=True, text=True)
            if gpus.returncode != 0 or not gpus.stdout.strip():
                missing.append(f"a working NVIDIA driver (nvidia-smi says: {(gpus.stderr or gpus.stdout).strip()})")
            else:
                say(f"GPU: {gpus.stdout.strip().splitlines()[0]}")
    first = not (data / "venv" / "ready.json").exists()
    if first or not (data / "model" / KIT_CHECKPOINT).exists():  # (its first run; or DrumSep's, set up before it)
        free = shutil.disk_usage(data if data.exists() else data.parent if data.parent.exists() else Path.home()).free
        if free < (6e9 if first else 1e9):
            missing.append(f"{6 if first else 1} GB of free disk for what it downloads ({free / 1e9:.1f} GB free "
                           f"where {data} goes)")
        if shutil.which("uv") is None and sys.version_info >= (3, 14):
            missing.append("uv (sudo pacman -S uv), or a Python 3.10 to 3.13: PyTorch has nothing for Python "
                           f"{sys.version_info.major}.{sys.version_info.minor} yet")
    if missing:
        say("rs-keylights-split-server needs:\n" + "\n".join(f"  - {m}" for m in missing))
        sys.exit(1)


def set_up(data: Path, device: str) -> Path:
    """Its own Python, the model's code and the model, in data (each once; checked each time); its Python."""
    data.mkdir(parents=True, exist_ok=True)
    venv, python = data / "venv", data / "venv" / "bin" / "python"
    wanted = {"torch": TORCH, "index": TORCH_INDEX["cpu" if device == "cpu" else "cuda"], "packages": list(PACKAGES)}
    if sys.platform == "darwin":  # (a Mac: PyTorch's own build, for trying it out there)
        wanted["index"] = None
    ready = venv / "ready.json"
    if not ready.exists() or json.loads(ready.read_text()) != wanted:
        say(f"Setting up its Python in {venv} (the first time: about 4 GB to download)")
        shutil.rmtree(venv, ignore_errors=True)
        uv = shutil.which("uv")
        if uv:
            run([uv, "venv", "--python", "3.12", str(venv)])
            install = [uv, "pip", "install", "--python", str(python)]
        else:
            run([sys.executable, "-m", "venv", str(venv)])
            install = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
        run(install + ([TORCH, "--index-url", wanted["index"]] if wanted["index"] else [TORCH]))
        run(install + list(PACKAGES))
        ready.write_text(json.dumps(wanted))
    for name, sha in CODE.items():
        fetch(CODE_URL + name, data / "code" / name, sha)
    for package in ("models", "models/bs_roformer", "utils"):  # (just the files needed: not ZFTurbo's package's own
        (data / "code" / package).mkdir(parents=True, exist_ok=True)  # __init__, which brings in every other model)
        (data / "code" / package / "__init__.py").touch()
    (data / "code" / "utils" / "model_utils.py").write_text(MODEL_UTILS)
    for name, sha in MODEL.items():
        fetch(MODEL_URL + name, data / "model" / name, sha)
    for name, sha in KIT_MODEL.items():
        fetch(KIT_URL + name, data / "model" / name, sha)
    key = data / "key"
    if not key.exists():
        key.write_text(secrets.token_urlsafe(18) + "\n")
        key.chmod(0o600)
    return python


def run(args: list[str]) -> None:
    say("  $ " + " ".join(args))
    if subprocess.run(args).returncode != 0:
        say(f"That failed: {args[0]} (above, what it said)")
        sys.exit(1)


def fetch(url: str, path: Path, sha: str) -> None:
    """A file, downloaded once and checked by its SHA-256 (checked again each time)."""
    if path.exists() and _sha256(path) == sha:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    say(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "rs-keylights-split-server"})
    with urllib.request.urlopen(request, timeout=60) as answer, open(part, "wb") as out:
        total, got, shown = int(answer.headers.get("Content-Length") or 0), 0, -1
        while block := answer.read(1 << 20):
            out.write(block)
            got += len(block)
            if total > 50e6 and (tenth := got * 10 // total) != shown:
                shown = tenth
                say(f"  {got / 1e6:.0f} of {total / 1e6:.0f} MB")
    if _sha256(part) != sha:
        part.unlink()
        say(f"{url} isn't the file expected (its SHA-256 differs): not used")
        sys.exit(1)
    part.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------------------------------------------------------
# Splitting: in its own Python
# ------------------------------------------------------------------------------------------------------------------

SONGS, KIT = "BS-RoFormer SW", "DrumSep"  # (its two models: a song's parts, and the drums' pieces)


class Settings(dict):
    """A model's settings, as its yaml has them, each also an attribute (MDX23C's code reads them so, as ZFTurbo's
    ConfigDict has them)."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _settings(value: Any) -> Any:
    if isinstance(value, dict):
        return Settings({k: _settings(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_settings(v) for v in value]
    return value


class Separator:
    """A model on the device (BS-RoFormer SW, or DrumSep): loaded for a song, let go of IDLE seconds after the last."""

    def __init__(self, data: Path, device: str, name: str = SONGS):
        self.data, self.device, self.name = data, device, name
        self.checkpoint, self.settings = (KIT_CHECKPOINT, KIT_SETTINGS) if name == KIT else (CHECKPOINT, SETTINGS)
        self._model: Any = None
        self._config: dict[str, Any] = {}
        self._used = 0.0
        self._lock = threading.Lock()

    @property
    def sources(self) -> list[str]:
        return list(self.config()["training"]["instruments"])

    def config(self) -> dict[str, Any]:
        if not self._config:
            import yaml

            class Loader(yaml.SafeLoader):  # (their settings have Python tuples in them, which the models' code
                pass  # checks are tuples: those, and nothing else of Python's)

            def as_tuple(loader: Any, node: Any) -> tuple[Any, ...]:
                return tuple(loader.construct_sequence(node))

            Loader.add_constructor("tag:yaml.org,2002:python/tuple", as_tuple)
            self._config = _settings(yaml.load((self.data / "model" / self.settings).read_text(), Loader=Loader))
        return self._config

    def describe(self) -> str:
        import torch
        if self.device == "cuda" and torch.cuda.is_available():
            return f"{torch.cuda.get_device_name(0)} (CUDA)"
        return self.device

    def model(self) -> Any:
        import torch
        with self._lock:
            if self._model is None:
                code = str(self.data / "code")
                if code not in sys.path:
                    sys.path.insert(0, code)
                if self.name == KIT:
                    from models.mdx23c_tfc_tdf_v3 import TFC_TDF_net  # pyright: ignore[reportMissingImports]
                    model = TFC_TDF_net(self.config())
                else:
                    from models.bs_roformer.bs_roformer import BSRoformer  # pyright: ignore[reportMissingImports]
                    model = BSRoformer(**self.config()["model"])
                state = torch.load(self.data / "model" / self.checkpoint, map_location="cpu", weights_only=True)
                model.load_state_dict(state.get("state_dict", state) if isinstance(state, dict) else state)
                self._model = model.eval().to(self.device)
                say(f"{self.name} on {self.describe()}")
            self._used = time.monotonic()
            return self._model

    def check(self) -> None:
        """The model loaded and run on one chunk of silence, timed: what it can do here, before any song's sent (or
        what's wrong, at once)."""
        import contextlib

        import torch
        config = self.config()
        chunk = int(config.get("inference", {}).get("chunk_size") or config["audio"]["chunk_size"])
        started = time.monotonic()
        model = self.model()
        loaded = time.monotonic()
        half = torch.autocast("cuda", dtype=torch.float16) if self.device == "cuda" else contextlib.nullcontext()
        with torch.inference_mode(), half:
            out = model(torch.zeros((1, 2, chunk), device=self.device))
        if self.device == "cuda":
            torch.cuda.synchronize()
        took = time.monotonic() - loaded
        step = chunk // int(config["inference"]["num_overlap"])
        what = "drum part" if self.name == KIT else "song"
        say(f"Checked {self.name}: loaded in {loaded - started:.0f} s; a {chunk / RATE:.1f} s chunk in {took:.2f} s, "
            f"so a 10-minute {what} in about {600 / (step / RATE) * took / 60:.1f} minutes (out: {tuple(out.shape)})")

    def let_go_if_idle(self) -> None:
        import torch
        with self._lock:
            if self._model is not None and time.monotonic() - self._used > IDLE:
                self._model = None
                if self.device == "cuda":
                    torch.cuda.empty_cache()
                say(f"{self.name} let go of (idle)")

    def separate(self, audio: Any, wanted: list[int], progress: Callable[[float], None]) -> Any:
        """The parts wanted (by the model's sources' index) of a song ([2, frames], at RATE): [parts, 2, frames]. As
        ZFTurbo's demix does it (generic mode)."""
        import contextlib

        import numpy as np
        import torch
        import torch.nn.functional as F
        config = self.config()
        chunk = int(config.get("inference", {}).get("chunk_size") or config["audio"]["chunk_size"])
        step = chunk // int(config["inference"]["num_overlap"])
        border, fade = chunk - step, chunk // 10
        window = torch.ones(chunk)
        window[:fade] = torch.linspace(0, 1, fade)
        window[-fade:] = torch.linspace(1, 0, fade)
        mix = torch.from_numpy(audio).float()
        frames = mix.shape[-1]
        padded = frames > 2 * border and border > 0
        if padded:  # (mirrored past either end: no edge to the first and last chunks)
            mix = F.pad(mix[None], (border, border), mode="reflect")[0]
        length = mix.shape[-1]
        result = torch.zeros((len(wanted), 2, length))
        counter = torch.zeros(length)
        starts = list(range(0, length, step))
        model = self.model()
        half = torch.autocast("cuda", dtype=torch.float16) if self.device == "cuda" else contextlib.nullcontext()
        with torch.inference_mode(), half:
            for k, start in enumerate(starts):
                part = mix[:, start:start + chunk]
                n = part.shape[-1]
                part = F.pad(part[None], (0, chunk - n), mode="reflect" if n > chunk // 2 else "constant")
                out = model(part.to(self.device))[0, wanted].float().cpu()
                w = window.clone()
                if start == 0:
                    w[:fade] = 1
                if start + step >= length:
                    w[-fade:] = 1
                result[..., start:start + n] += out[..., :n] * w[:n]
                counter[start:start + n] += w[:n]
                progress((k + 1) / len(starts))
                self._used = time.monotonic()
        out = (result / counter).numpy()
        out = out[..., border:-border] if padded else out
        return np.nan_to_num(out, copy=False)


class Jobs:
    """Songs to split, in turn on a thread of their own, and their parts kept (in folder) until fetched; idle, now and
    then, while there are none."""

    def __init__(self, split: Callable[[bytes, list[str], Path, Callable[[float], None]], None], folder: Path,
                 idle: Callable[[], None] = lambda: None):
        self._split, self.folder, self._idle = split, folder, idle
        shutil.rmtree(folder, ignore_errors=True)  # (any from before: gone)
        folder.mkdir(parents=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._waiting: list[tuple[str, bytes, dict[str, Any]]] = []
        self._lock = threading.Condition()
        threading.Thread(target=self._work, name="splitting", daemon=True).start()

    def add(self, audio: bytes, parts: list[str], **options: Any) -> str:
        """A song to split into parts (options: the split's own, from_drums), in turn: its job's ID."""
        job = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job] = {"state": "waiting", "progress": 0.0, "error": None, "parts": parts,
                               "made": time.monotonic()}
            self._waiting.append((job, audio, options))
            self._lock.notify()
        return job

    def info(self, job: str) -> dict[str, Any] | None:
        with self._lock:
            got = self._jobs.get(job)
            return None if got is None else {k: v for k, v in got.items() if k != "made"}

    def waiting(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j["state"] in ("waiting", "splitting"))

    def file(self, job: str, part: str) -> Path | None:
        info = self.info(job)
        if info is None or info["state"] != "done" or part not in info["parts"]:
            return None
        return self.folder / job / f"{part}.flac"

    def remove(self, job: str) -> bool:
        with self._lock:
            gone = self._jobs.pop(job, None)
        shutil.rmtree(self.folder / job, ignore_errors=True)
        return gone is not None

    def _work(self) -> None:
        while True:
            with self._lock:
                while not self._waiting:
                    self._lock.wait(timeout=60)
                    self._forget_old()
                    if not self._waiting:
                        self._idle()
                job, audio, options = self._waiting.pop(0)
                self._jobs[job]["state"] = "splitting"
                parts = self._jobs[job]["parts"]

            def progress(done: float, job: str = job) -> None:
                with self._lock:
                    if job in self._jobs:
                        self._jobs[job]["progress"] = round(done, 3)

            started = time.monotonic()
            try:
                (self.folder / job).mkdir()
                self._split(audio, parts, self.folder / job, progress, **options)
                state, error = "done", None
                say(f"{job}: split in {time.monotonic() - started:.0f} s")
            except Exception as exc:  # (said to rs-keylights, and here)
                state, error = "failed", f"{type(exc).__name__}: {exc}"
                say(f"{job}: failed: {error}")
            del audio
            with self._lock:
                if job in self._jobs:
                    self._jobs[job].update(state=state, error=error, progress=1.0 if state == "done" else
                                           self._jobs[job]["progress"])

    def _forget_old(self) -> None:
        now = time.monotonic()
        for job in [j for j, info in self._jobs.items() if now - info["made"] > KEPT]:
            self._jobs.pop(job, None)
            shutil.rmtree(self.folder / job, ignore_errors=True)


def splitter(songs: Separator, kit: Separator | None = None) -> Callable[..., None]:
    """A song's audio (WAV or FLAC, any rate) to its parts' FLACs (16-bit, at its rate), in a folder: BS-RoFormer SW's,
    and DrumSep's from its drums (from_drums: the audio's a drum part already, just DrumSep's)."""

    def split(audio: bytes, parts: list[str], out: Path, progress: Callable[[float], None],
              from_drums: bool = False) -> None:
        import numpy as np
        import soundfile
        from scipy.signal import resample_poly
        pcm, rate = soundfile.read(io.BytesIO(audio), dtype="float32", always_2d=True)
        frames, channels = pcm.shape
        say(f"Splitting {frames / rate / 60:.1f} minutes at {rate} Hz into {', '.join(parts)}"
            + (" (a drum part)" if from_drums else ""))
        song = (pcm.T[:2] if channels > 1 else np.repeat(pcm.T, 2, axis=0)).astype(np.float32)
        del pcm
        divisor = int(np.gcd(rate, RATE))
        if rate != RATE:
            song = resample_poly(song, RATE // divisor, rate // divisor, axis=-1).astype(np.float32)
        pieces = [p for p in parts if kit is not None and p in kit.sources]
        made: dict[str, Any] = {}
        if from_drums:
            drums = song
        else:  # (the song's parts: its drums too, for the pieces)
            wanted = [p for p in parts if p in songs.sources]
            wanted += ["drums"] if pieces and "drums" not in wanted else []
            share = 0.75 if pieces else 1.0
            got = songs.separate(song, [songs.sources.index(p) for p in wanted], lambda done: progress(share * done))
            made.update(zip(wanted, got))
            drums = made.get("drums")
        del song
        if pieces and kit is not None:
            first = 0.0 if from_drums else 0.75
            got = kit.separate(np.ascontiguousarray(drums, dtype=np.float32), [kit.sources.index(p) for p in pieces],
                               lambda done: progress(first + (1 - first) * done))
            made.update(zip(pieces, got))
        del drums
        for name in parts:
            part = made.pop(name)
            if rate != RATE:
                part = resample_poly(part, rate // divisor, RATE // divisor, axis=-1)
            part = part[:, :frames]
            if part.shape[1] < frames:
                part = np.pad(part, ((0, 0), (0, frames - part.shape[1])))
            if channels == 1:
                part = part.mean(axis=0, keepdims=True)
            pcm16 = np.clip(np.rint(part.T * 32768.0), -32768, 32767).astype(np.int16)
            soundfile.write(str(out / f"{name}.flac"), pcm16, rate, format="FLAC", subtype="PCM_16")

    return split


def handler(key: str, jobs: Jobs, describe: Callable[[], dict[str, Any]]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "rs-keylights-split/1"

        def log_message(self, format: str, *args: Any) -> None:  # (quiet: its own lines say what it does)
            pass

        def _allowed(self) -> bool:
            if secrets.compare_digest(self.headers.get(KEY_HEADER, ""), key):
                return True
            self._json(403, {"error": "Not the key (rs-keylights needs the one this prints as it starts)"})
            return False

        def _json(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if not self._allowed():
                return
            path = urlparse(self.path).path.strip("/").split("/")
            if path == ["status"]:
                return self._json(200, {**describe(), "waiting": jobs.waiting()})
            if len(path) == 2 and path[0] == "split":
                info = jobs.info(path[1])
                return self._json(200, info) if info else self._json(404, {"error": "No such song (gone?)"})
            if len(path) == 3 and path[0] == "split" and path[2].endswith(".flac"):
                found = jobs.file(path[1], path[2][:-len(".flac")])
                if found is None or not found.exists():
                    return self._json(404, {"error": "No such part (not done yet, or gone)"})
                self.send_response(200)
                self.send_header("Content-Type", "audio/flac")
                self.send_header("Content-Length", str(found.stat().st_size))
                self.end_headers()
                with open(found, "rb") as f:
                    shutil.copyfileobj(f, self.wfile, 1 << 20)
                return
            self._json(404, {"error": "No such thing"})

        def do_POST(self) -> None:
            if not self._allowed():
                return
            url = urlparse(self.path)
            if url.path.strip("/") != "split":
                return self._json(404, {"error": "No such thing"})
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= MOST_UPLOAD:
                return self._json(400, {"error": "Expected a song's audio (WAV or FLAC), up to 1 GB"})
            audio = self.rfile.read(size)  # (all of it, before any answer: else the sender's cut off mid-song)
            said = describe()
            query = parse_qs(url.query)
            from_drums = query.get("from", [""])[0] == "drums"
            known = said.get("kit", []) if from_drums else said["parts"]
            parts = [p for p in ",".join(query.get("parts", [])).split(",") if p] or \
                [p for p in known if p != "other" and p not in said.get("kit", [])]
            if not parts or any(p not in known for p in parts):
                return self._json(400, {"error": f"The parts{' of a drum part' if from_drums else ''}: "
                                                 f"{', '.join(known) or 'none'}"})
            job = jobs.add(audio, parts, **({"from_drums": True} if from_drums else {}))
            say(f"{job}: a song from {self.client_address[0]} ({size / 1e6:.0f} MB)")
            self._json(200, {"id": job})

        def do_DELETE(self) -> None:
            if not self._allowed():
                return
            path = urlparse(self.path).path.strip("/").split("/")
            if len(path) == 2 and path[0] == "split":
                return self._json(200, {"gone": jobs.remove(path[1])})
            self._json(404, {"error": "No such thing"})

    return Handler


def addresses(port: int) -> list[str]:
    """Where rs-keylights finds it: its name on the network, and its address."""
    out = [f"http://{socket.gethostname().split('.')[0]}.local:{port}"]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:  # (no packet's sent: just the route looked up)
            s.connect(("192.0.2.1", 9))
            out.append(f"http://{s.getsockname()[0]}:{port}")
    except OSError:
        pass
    return out


def serve(data: Path, device: str, host: str, port: int) -> None:
    songs, kit = Separator(data, device), Separator(data, device, KIT)
    split = splitter(songs, kit)

    def idle() -> None:
        songs.let_go_if_idle()
        kit.let_go_if_idle()

    jobs = Jobs(split, data / "jobs", idle)
    gpu = songs.describe()
    if device == "cuda" and "CUDA" not in gpu:
        say("PyTorch can't use the NVIDIA GPU (torch.cuda isn't available): is the driver loaded? (nvidia-smi)")
        sys.exit(1)
    for separator in (songs, kit):
        try:
            separator.check()
        except Exception as exc:  # (said whole: what to show rs-keylights' maker)
            import traceback
            traceback.print_exc()
            say(f"\n{separator.name} didn't run here: {type(exc).__name__}: {exc}")
            sys.exit(1)

    def describe() -> dict[str, Any]:
        return {"server": "rs-keylights split", "version": VERSION, "model": SONGS, "models": [SONGS, KIT],
                "device": gpu, "parts": songs.sources + kit.sources, "kit": kit.sources}

    key = (data / "key").read_text().strip()
    server = ThreadingHTTPServer((host, port), handler(key, jobs, describe))
    say(f"\nrs-keylights split server: {SONGS} and {KIT} on {gpu}")
    say("In rs-keylights (the page's Song's audio card, Split on another computer), its address and key:")
    for address in addresses(port):
        say(f"  address  {address}")
    say(f"  key      {key}\nListening (Ctrl-C stops it)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        say("Stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Splits songs into their parts for rs-keylights (BS-RoFormer SW), "
                                                 "and their drums into the kit's pieces (DrumSep).")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default="0.0.0.0", help="the address to listen on (default: every one this has)")
    parser.add_argument("--data", type=Path, default=DATA, help=f"its own folder (default {DATA})")
    parser.add_argument("--device", choices=("cuda", "cpu", "mps"), default="mps" if sys.platform == "darwin" else
                        "cuda")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)  # (in its own Python, set up)
    args = parser.parse_args()
    data = args.data.expanduser().resolve()
    if not args.serve:
        check(data, args.device)
        python = set_up(data, args.device)
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), "--serve", "--data", str(data),
                               "--device", args.device, "--host", args.host, "--port", str(args.port)])
    serve(data, args.device, args.host, args.port)


if __name__ == "__main__":
    main()
