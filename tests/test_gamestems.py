import pytest

from rs_keylights import gamestems

INI = """[Toggle Switches]
ShowSongTimer = on
SongStems = on

[Mod Settings]
SongStemsGuitarLevel = 100
SongStemsBassLevel = 70

[Audio Keybindings]
SongStemVolumeKey = 4
"""


def test_the_mods_switch_and_levels_are_read_as_the_mod_reads_them(tmp_path):
    path = tmp_path / "RSMods.ini"
    path.write_text(INI)
    assert gamestems.read(path) == {"on": True, "guitar": 100, "bass": 70}
    path.write_text(INI.replace("SongStems = on", "songstems = off") + "[Mod Settings]\nsongstemsbasslevel = 140\n")
    assert gamestems.read(path) == {"on": False, "guitar": 100, "bass": 100}  # (the last wins; at most 100)
    path.write_text("[Toggle Switches]\nSongStems = on\n")
    assert gamestems.read(path) == {"on": True, "guitar": 100, "bass": 100}  # (none: the song as it is)
    assert gamestems.read(tmp_path / "none.ini") is None


def test_a_level_written_replaces_its_line_and_keeps_the_rest(tmp_path):
    path = tmp_path / "RSMods.ini"
    path.write_text(INI)
    gamestems.write(path, {"guitar": 35})
    assert path.read_text() == INI.replace("SongStemsGuitarLevel = 100", "SongStemsGuitarLevel = 35")
    path.write_text("[Toggle Switches]\nSongStems = on\n\n[Mod Settings]\nOther = 1\n\n[Audio Keybindings]\nX = 4\n")
    gamestems.write(path, {"bass": 0})  # (none yet: at the end of its section)
    assert path.read_text() == ("[Toggle Switches]\nSongStems = on\n\n[Mod Settings]\nOther = 1\nSongStemsBassLevel = 0"
                                "\n\n[Audio Keybindings]\nX = 4\n")
    path.write_text("[Toggle Switches]\r\nSongStems = on\r\n")
    gamestems.write(path, {"guitar": 50})  # (no section: one added, the file's line endings kept)
    assert path.read_bytes().decode() == ("[Toggle Switches]\r\nSongStems = on\r\n\r\n[Mod Settings]\r\n"
                                          "SongStemsGuitarLevel = 50\r\n")
    assert [p.name for p in tmp_path.iterdir()] == ["RSMods.ini"]  # (nothing left over)
