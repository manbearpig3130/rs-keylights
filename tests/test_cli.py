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
    assert (args.parts, args.start, args.speed, args.silent, args.tone, args.lights) == ([], 0.0, 1.0, False, None, None)
    args = build_parser().parse_args(["play", "song.psarc", "lead", "rhythm", "--tone", "lead=178", "--tone", "79"])
    assert (args.parts, args.tone) == (["lead", "rhythm"], ["lead=178", "79"])
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
    assert "Artist - Song\n  Lead: 1 notes, tuning 0 0 0 0 0 0, 4 keys lit" in out.out
    assert "No MIDI output named 'no such keyboard'" in out.err


def test_missing_game_folder(tmp_path, capsys):
    assert main(["--game-dir", str(tmp_path), "index"]) == 1
    assert "No Rocksmith songs in" in capsys.readouterr().err


def test_sound_options_are_checked_first(capsys, tmp_path):
    cases = [
        (["follow", "--mute-tone", "77"], "--mute-tone needs --sound"),
        (["play", "x.psarc", "--silent", "--low-tone", "80"], "--low-tone plays sound: leave out --silent"),
        (["play", "x.psarc", "--split", "B1"], "--split needs --low-tone"),
        (["play", "x.psarc", "--low-tone", "80", "--split", "H2"], "Not a note: 'H2'"),
        (["play", "x.psarc", "--mute-tone", "kazoo"], "no tone matches 'kazoo'"),
    ]
    for argv, message in cases:
        assert main(argv) == 1, argv
        assert message in capsys.readouterr().err, argv


def two_parts(tmp_path):
    path = tmp_path / "a_m.psarc"
    path.write_bytes(song_archive("a", {"lead": ("L", Song(levels={0: [N(1.0)]})),
                                        "rhythm": ("R", Song(levels={0: [N(1.0)]}))}))
    return str(path)


def test_several_parts_checks(tmp_path, capsys):
    song = two_parts(tmp_path)
    cases = [
        (["play", song, "lead", "--tone", "bass=85"], "bass= is not one of the parts playing (lead)"),
        (["play", song, "lead", "rhythm", "--lights", "bass"], "--lights bass: not one of the parts playing"),
        (["play", song, "lead", "rhythm", "--lights", "lead=3,rhythm=2"], "4 keys at most"),
        (["--channel", "14", "play", song, "lead", "rhythm"], "channel 14 is for the sound of these parts"),
        (["--midi-port", "nothing", "play", song, "lead", "rhythm", "--lights", "lead=1,rhythm"], "No MIDI output"),
    ]
    for argv, message in cases:
        assert main(argv) == 1, argv
        assert message in capsys.readouterr().err, argv
    main(["--midi-port", "nothing", "play", song, "lead", "rhythm", "--lights", "lead=1,rhythm"])
    out = capsys.readouterr().out
    assert "  Lead: 1 notes, tuning 0 0 0 0 0 0, 1 key lit" in out and "  Rhythm: 1 notes, tuning 0 0 0 0 0 0, 3 keys lit" in out


def test_lights_for():
    from rs_keylights.cli import lights_for

    parts = ["lead", "rhythm", "bass"]
    assert lights_for(None, parts) == {"lead": 4}
    assert lights_for("none", parts) == {}
    assert lights_for("rhythm", parts) == {"rhythm": 4}
    assert lights_for("lead,rhythm", parts) == {"lead": 2, "rhythm": 2}
    assert lights_for("lead,rhythm,bass", parts) == {"lead": 2, "rhythm": 1, "bass": 1}
    assert lights_for("lead=3,rhythm", parts) == {"lead": 3, "rhythm": 1}


def test_tones_for_each_part():
    from argparse import Namespace

    from rs_keylights.cli import sound_choice

    args = Namespace(tone=["lead=178", "79"], mute_tone=["rhythm=77"], low_tone=None, split=None, real_pitch=False,
                     channel=4)
    choice = sound_choice(args, True, "")
    lead, rhythm = ref("lead"), ref("rhythm")
    assert choice.voices(lead).main.tone.number == 178 and choice.voices(lead).mute is None
    assert choice.voices(rhythm, (14, 15, 16)).main == choice.voices(rhythm).main.__class__(14, choice.main[None])
    assert choice.voices(rhythm, (14, 15, 16)).mute.channel == 16
