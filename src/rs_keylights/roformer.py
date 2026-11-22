from __future__ import annotations

import os
import tempfile
import wave
from importlib.resources import files
from pathlib import Path
from typing import Callable

from .library import ArrangementRef

# BS-RoFormer SW, the better of the two separators of a song's parts (stems): jarredou's 6-stem checkpoint
# (BS-Rofo-SW-Fixed), as jonkubis' Core ML port has it, on the Mac's GPU (the Neural Engine can't run it). Set up by
# tools/roformer-setup.sh in ~/Library/Application Support/rs-keylights/roformer: the model (compiled for this Mac
# too), and a Python with coremltools and scipy to run it (roformer_worker.py). A little longer than the song itself
# to split one, at the background priority it runs at (out of the game's and the keyboard's way): on an M1 MacBook
# Air, 1.1 times the song's length (8 minutes for Ghost of Perdition's 10:40 at the usual priority).

MODEL = "bs_roformer_6stem.mlpackage"
COMPILED = "bs_roformer_6stem.mlmodelc"  # (compiled once by the setup: else at each start, a while)


def tools_dir() -> Path:
    """Where it's set up (roformer-setup.sh puts it there)."""
    return Path(os.environ.get("RS_KEYLIGHTS_ROFORMER_TOOLS",
                               Path.home() / "Library" / "Application Support" / "rs-keylights" / "roformer"))


def ready(tools: Path | None = None) -> bool:
    """Whether it's set up (roformer-setup.sh run): its model and the Python that runs it."""
    tools = tools or tools_dir()
    return (tools / MODEL).is_dir() and (tools / "venv" / "bin" / "python").exists()


def make(ref: ArrangementRef, game_dir: Path, tools: Path | None = None, cache: Path | None = None,
         progress: Callable[[float], None] = lambda _done: None) -> None:
    """A song's parts made by BS-RoFormer SW and kept (stems.store), how far along going to progress (0 to 1): its
    audio decoded (drums-setup.sh's decoder), separated by roformer_worker.py in the setup's own Python."""
    import numpy as np

    from . import drums, stems
    tools = tools or tools_dir()
    if not ready(tools):
        raise stems.StemsError("BS-RoFormer SW isn't set up: run tools/roformer-setup.sh")
    model = tools / COMPILED if (tools / COMPILED).is_dir() else tools / MODEL
    worker = files("rs_keylights").joinpath("roformer_worker.py")
    with tempfile.TemporaryDirectory(prefix="rs-keylights-roformer-") as work_dir:
        work = Path(work_dir)
        try:
            drums.decode(ref, game_dir, work / "song.wav")
        except drums.DrumsError as exc:
            raise stems.StemsError(str(exc)) from exc
        with wave.open(str(work / "song.wav")) as w:
            rate, channels, frames = w.getframerate(), w.getnchannels(), w.getnframes()
        out = work / "parts"
        out.mkdir()

        def along(_took: float) -> None:
            try:
                k, n = (int(v) for v in (out / "progress").read_text().split())
            except (OSError, ValueError):
                return
            progress(stems.SEPARATING * k / max(n, 1))

        stems._run([str(tools / "venv" / "bin" / "python"), "-I", str(worker), str(model), str(work / "song.wav"),
                    str(out), *stems.SEPARATED], dict(os.environ), "Separating it with BS-RoFormer SW", along)
        (work / "song.wav").unlink()
        wavs = {}
        for stem in stems.SEPARATED:
            raw = out / f"{stem}.raw"
            part = np.fromfile(raw, dtype=np.int16)
            if part.size != frames * channels:
                raise stems.StemsError(f"BS-RoFormer SW's {stem} didn't finish")
            wavs[stem] = work / f"{stem}.wav"
            stems._write_wav(wavs[stem], part.reshape(-1, channels), rate)
            raw.unlink()
            del part
        stems.store(ref, wavs, rate, stems.ROFORMER, cache, progress, stems.SEPARATING)
