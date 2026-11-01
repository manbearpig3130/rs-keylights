import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from rs_keylights import pedal  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_pedal(monkeypatch, tmp_path):
    """No test reads this Mac's pedal backups or dnafx-preset's log, or switches a real pedal: switches are kept."""
    switched = []
    monkeypatch.setattr(pedal, "SIDEKICK_LOG", tmp_path / "sidekick-dnafx-preset.log")
    monkeypatch.setattr(pedal, "find_backup", lambda home=None: None)
    monkeypatch.setattr(pedal, "dnafx_switcher", lambda: switched.append)
    return switched


from rs_keylights import gui as gui_module  # noqa: E402
from rs_keylights.riffloop import GameLoop  # noqa: E402


class FakeRiffLoop:
    """The game's riffloop mod, as tests have it: what was sent to it, answered as it would."""

    def __init__(self, *args, **kwargs):
        self.sent, self.range = [], None

    def loop(self, a, b):
        self.sent.append(("loop", a, b))
        self.range = (a, b)
        return GameLoop(True, False, self.range)

    def off(self):
        self.sent.append(("off",))
        self.range = None
        return GameLoop(True, False, None)

    def status(self):
        return GameLoop(True, False, self.range)


@pytest.fixture(autouse=True)
def no_real_game_loop(monkeypatch):
    """No test sends to the game (its riffloop mod, if it's running): each gui's goes to a FakeRiffLoop, kept."""
    made = []

    def fake(*args, **kwargs):
        made.append(FakeRiffLoop())
        return made[-1]

    monkeypatch.setattr(gui_module, "RiffLoop", fake)
    return made
