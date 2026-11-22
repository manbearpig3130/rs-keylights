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
    monkeypatch.setattr(pedal, "HOLD_FILE", tmp_path / "dnafx-preset-hold")  # (not this Mac's listener's)
    monkeypatch.setattr(pedal, "NAMES_CACHE", tmp_path / "dnafx-names.json")  # (nor its names, nor the pedal's)

    def no_names():
        raise pedal.PedalError("no pedal in tests")
    monkeypatch.setattr(pedal, "read_pedal_names", no_names)
    return switched


from rs_keylights import pedaltones, rig  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_pedal_tones(monkeypatch, tmp_path):
    """No test reads or changes this Mac's RS Companion mapping, sidekicks.conf or dnafx-preset tone map."""
    monkeypatch.setattr(rig, "COMPANION_DATA", tmp_path / "rs-mac-companion")
    monkeypatch.setattr(pedaltones, "SIDEKICKS", tmp_path / "sidekicks.conf")
    monkeypatch.setattr(pedaltones, "DEFAULT_TONE_MAP", tmp_path / "tones.toml")
    monkeypatch.delenv("DNAFX_CONFIG", raising=False)


from rs_keylights import drums, library, pedalwriter, plays, stems  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_pedal_presets(monkeypatch, tmp_path):
    """No test reads or writes what this Mac remembers of the DNAfx's song slots, or its backups."""
    monkeypatch.setattr(pedalwriter, "STATE", tmp_path / "pedal-song-presets.json")
    monkeypatch.setattr(pedalwriter, "BACKUPS", tmp_path / "DNAfx backups")


@pytest.fixture(autouse=True)
def no_real_index(monkeypatch, tmp_path):
    """No test writes this Mac's index of the game's songs (a command's, opened with its default cache)."""
    monkeypatch.setattr(library, "default_cache", lambda: tmp_path / "default-index.json")


@pytest.fixture(autouse=True)
def no_real_plays(monkeypatch, tmp_path):
    """No test reads or keeps this Mac's runs of songs: a folder of its own."""
    monkeypatch.setattr(plays, "plays_dir", lambda: tmp_path / "plays")


@pytest.fixture(autouse=True)
def no_real_drums(monkeypatch, tmp_path):
    """No test reads or writes this Mac's drum parts or finds its drum tools: a cache and a tools folder of its own."""
    monkeypatch.setattr(drums, "cache_dir", lambda: tmp_path / "drums-cache")
    monkeypatch.setattr(stems, "cache_dir", lambda: tmp_path / "stems-cache")  # (and its songs' parts)
    monkeypatch.setenv("RS_KEYLIGHTS_DRUMS_TOOLS", str(tmp_path / "drums-tools"))
    monkeypatch.setenv("RS_KEYLIGHTS_ROFORMER_TOOLS", str(tmp_path / "roformer-tools"))  # (BS-RoFormer SW's too)


from rs_keylights import gui as gui_module  # noqa: E402
from rs_keylights.riffloop import GameLoop, GameSongs  # noqa: E402


class FakeRiffLoop:
    """The game's riffloop mod, as tests have it: what was sent to it, answered as it would (the game on a screen:
    paused and resumed as the game would be)."""

    def __init__(self, *args, **kwargs):
        self.sent, self.range, self.screen = [], None, "LearnASong_Game"
        self.song_list, self.highlighted = None, 0  # (Learn a Song's song list: its keys, while it shows)
        self.riff_accelerator = None  # (its Accelerator, as the game has it)
        self.riff_speed = None  # (Riff Repeater's, as the game has it; None: a mod that doesn't say)
        self.music_level = 80.0  # (the game's Music volume; None: a mod without it)
        self.game_time = None  # (the game's own song time and screen, as its time command says; None: no answer)
        self.note_counts = None  # (the player's note counts, as its notes command says; None: no answer)
        self.kmix_rows, self.kmix_moved = None, {}  # (its MIXER's rows, as sent; what was done there in the game)
        self.ktone_rows = None  # (its TONES' rows, as sent)

    def loop(self, a, b):
        self.sent.append(("loop", a, b))
        self.range = (a, b)
        return GameLoop(True, False, self.range)

    def off(self):
        self.sent.append(("off",))
        self.range = None
        return GameLoop(True, False, None)

    def time(self, wait=None):
        return self.game_time  # (not kept in sent: asked many times a second)

    def notes(self, since):
        return self.note_counts

    def status(self):
        return GameLoop(True, False, self.range, None, self.screen, 0, self.riff_speed, self.riff_accelerator, self.music_level)

    def music(self, level):
        self.sent.append(("music", level))
        if self.music_level is not None:
            self.music_level = float(level)
        return self.status()

    def pause(self):
        self.sent.append(("pause",))
        if self.screen and self.screen.endswith("_Game"):
            self.screen = self.screen.replace("_Game", "_Pause")
        return self.status()

    def kmix(self, rows):
        self.sent.append(("kmix", tuple(rows)))
        self.kmix_rows = tuple(rows)
        return self.status()

    def kpage(self, page):
        self.sent.append(("kpage", page))
        return self.status()

    def ktones(self, rows):
        self.sent.append(("ktones", tuple(rows)))
        self.ktone_rows = tuple(rows)
        return self.status()

    def kmixed(self):
        moved, self.kmix_moved = self.kmix_moved, {}
        return moved

    def riff(self):
        self.sent.append(("riff",))
        if self.screen and self.screen.endswith("_Game"):
            self.screen = self.screen.replace("_Game", "_RiffRepeater")
        return self.status()

    def songs(self):
        if not self.song_list:
            return None
        return GameSongs(self.highlighted, len(self.song_list), self.song_list[self.highlighted])

    def song_keys(self, start, count):
        return (len(self.song_list), self.song_list[start:start + count]) if self.song_list else None

    def focus(self, key):
        self.sent.append(("focus", key))
        self.highlighted = self.song_list.index(key)
        return True

    def key(self, name):
        self.sent.append(("key", name))
        return self.status()

    def resume(self):
        self.sent.append(("resume",))
        if self.screen and self.screen.endswith("_Pause"):
            self.screen = self.screen.replace("_Pause", "_Game")
        return self.status()

    def speed(self, percent):
        self.sent.append(("speed", percent))
        return self.status()

    def accelerator(self, on):
        self.sent.append(("accel", on))
        return self.status()

    def passes(self, count):
        self.sent.append(("passes", count))
        return self.status()


@pytest.fixture(autouse=True)
def no_real_game_loop(monkeypatch):
    """No test sends to the game (its riffloop mod, if it's running): each gui's goes to a FakeRiffLoop, kept."""
    made = []

    def fake(*args, **kwargs):
        made.append(FakeRiffLoop())
        return made[-1]

    monkeypatch.setattr(gui_module, "RiffLoop", fake)
    return made
