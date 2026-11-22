from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .library import ArrangementRef

# BS-RoFormer SW, the better of the two separators of a song's parts (stems): jarredou's 6-stem checkpoint
# (BS-Rofo-SW-Fixed), as jonkubis' Core ML port has it, on the Mac's GPU (the Neural Engine can't run it). Set up by
# tools/roformer-setup.sh in ~/Library/Application Support/rs-keylights/roformer: the model, and a Python with
# coremltools to run it (roformer_worker.py).

MODEL = "bs_roformer_6stem.mlpackage"


class RoformerError(Exception):
    pass


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
    """A song's parts made by BS-RoFormer SW and kept (stems.store)."""
    from .stems import StemsError
    if not ready(tools):
        raise StemsError("BS-RoFormer SW isn't set up: run tools/roformer-setup.sh")
    raise StemsError("BS-RoFormer SW can't split songs here yet")
