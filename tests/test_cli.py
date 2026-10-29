from builders import N, Song, song_archive

from rs_keylights.cli import build_parser, choose_part, choose_tone, main
from rs_keylights.library import ArrangementRef


def ref(part):
    return ArrangementRef(part, "/a.psarc", f"songs/bin/macos/a_{part}.sng", "A", part.capitalize())


def test_choose_part():
    refs = [ref("vocals"), ref("bass"), ref("rhythm"), ref("lead")]
    assert choose_part(refs, None).part == "lead"
    assert choose_part(refs[:3], None).part == "rhythm"
    assert choose_part(refs, "Bass").part == "bass"
    assert choose_part(refs, "vocals") is None
    assert choose_part([ref("vocals")], None) is None


def test_parser_defaults():
    args = build_parser().parse_args(["play", "song.psarc"])
    assert (args.part, args.start, args.speed, args.silent, args.tone) == (None, 0.0, 1.0, False, None)
    args = build_parser().parse_args(["--channel", "3", "follow", "--offset-ms", "40"])
    assert (args.channel, args.offset_ms) == (3, 40.0)


def test_choose_tone(capsys):
    assert choose_tone(None, bass=False).name == "DISTORTION GT"
    assert choose_tone(None, bass=True).name == "FINGERED BASS"
    assert choose_tone("178", bass=False).name == "SAW LEAD 1"
    assert choose_tone("jazz organ", bass=False) is None
    assert "'jazz organ' matches 2 tones" in capsys.readouterr().err
    assert choose_tone("kazoo", bass=False) is None
    assert "no tone matches 'kazoo'" in capsys.readouterr().err


def test_tones_command(capsys):
    assert main(["tones", "saw lead"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("SYNTH-LEAD\n  178  SAW LEAD 1\n")
    assert "348  GM SAW LEAD" in out


def test_play_reports_a_missing_part(tmp_path, capsys):
    path = tmp_path / "a_m.psarc"
    path.write_bytes(song_archive("a", {"lead": ("ID", Song(levels={0: [N(1.0)]}))}))
    assert main(["play", str(path), "bass"]) == 1
    assert "no part 'bass'; there are lead" in capsys.readouterr().err


def test_play_without_the_keyboard(tmp_path, capsys):
    path = tmp_path / "a_m.psarc"
    path.write_bytes(song_archive("a", {"lead": ("ID", Song(levels={0: [N(1.0)]}))}))
    assert main(["--midi-port", "no such keyboard", "play", str(path)]) == 1
    out = capsys.readouterr()
    assert "Artist - Song (Lead): 1 notes, tuning 0 0 0 0 0 0" in out.out
    assert "No MIDI output named 'no such keyboard'" in out.err


def test_missing_game_folder(tmp_path, capsys):
    assert main(["--game-dir", str(tmp_path), "index"]) == 1
    assert "No Rocksmith songs in" in capsys.readouterr().err


def test_follow_tone_needs_sound(capsys):
    assert main(["follow", "--tone", "178"]) == 1
    assert "--tone needs --sound" in capsys.readouterr().err
