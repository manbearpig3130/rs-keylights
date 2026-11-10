import json
import tomllib
from urllib.parse import quote

import pytest
from test_gui import call
from test_tonemessages import RULES, presets, song_file

from rs_keylights import pedaltones
from rs_keylights.gui import Gui, GuiError, GuiServer, song_id
from rs_keylights.library import SongIndex
from rs_keylights.pedaltones import PedalTones, PedalTonesError, edit_tone_map, listener_setup, short_name

TONES_TOML = """# My pedal's slots
[artists."The Band"]
Heavy = 51   # 5050 GAIN

# Everything else
[categories]
Clean = 49     # 5050 CL
Heavy = 52
"""


def setup_files(tmp_path, tone_map=TONES_TOML, line=None):
    """RS Companion's mapping, a tone map, and a sidekicks.conf running dnafx-preset's listener with them."""
    folder = presets(tmp_path)
    (tmp_path / "my-tones.toml").write_text(tone_map)
    conf = tmp_path / "sidekicks.conf"
    conf.write_text("# the pedal\n#dnafx-preset listen --config /nowhere.toml\n" + (line or (
        f'~/git/dnafx-preset/.venv/bin/dnafx-preset listen --midi-source "Rocksmith Tones" '
        f'--midi-program-map 0=49,3=187 --config {tmp_path / "my-tones.toml"}\n')))
    return folder, conf


def tones_for(tmp_path, **files):
    folder, conf = setup_files(tmp_path, **files)
    game = tmp_path / "game" / "dlc"
    game.mkdir(parents=True)
    song_file(game)
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    names = {49: "5050 CL", 51: "5050 GAIN", 52: "OTHER", 187: "BOSS"}
    return PedalTones(lambda slot: names.get(slot, "") if slot else "", folder, conf), index


def test_the_listeners_files_are_read_from_its_line_in_sidekicks_conf(tmp_path, monkeypatch):
    _folder, conf = setup_files(tmp_path)
    setup = listener_setup(conf)
    assert (setup.tone_map, setup.given, setup.program_map, setup.source, setup.found) == (
        tmp_path / "my-tones.toml", True, {0: 49, 3: 187}, "Rocksmith Tones", True)
    conf.write_text("dnafx-preset listen --midi-source=X\n")
    assert listener_setup(conf).tone_map == pedaltones.DEFAULT_TONE_MAP and not listener_setup(conf).given
    monkeypatch.setenv("DNAFX_CONFIG", str(tmp_path / "env.toml"))
    assert listener_setup(tmp_path / "none.conf") == pedaltones.ListenerSetup(tmp_path / "env.toml")


def test_a_rule_changed_in_the_tone_map_keeps_its_comments_and_order():
    changed = edit_tone_map(TONES_TOML, ("categories", "heavy"), 60, "NEW")
    assert changed == TONES_TOML.replace("Heavy = 52", "Heavy = 60  # NEW")
    added = edit_tone_map(changed, ("artists", "the band", "Lead"), 70, "")
    assert 'Heavy = 51   # 5050 GAIN\nLead = 70\n\n# Everything else' in added
    song = edit_tone_map(added, ("songs", 'A "Quoted" Song', "s_clean"), 3, "X")
    assert song.endswith('Heavy = 60  # NEW\n\n[songs."A \\"Quoted\\" Song"]\ns_clean = 3  # X\n')
    assert tomllib.loads(song)["songs"]['A "Quoted" Song'] == {"s_clean": 3}
    gone = edit_tone_map(edit_tone_map(song, ("songs", 'A "Quoted" Song', "S_CLEAN"), None), ("artists", "The Band", "Lead"), None)
    assert gone == changed
    assert edit_tone_map(TONES_TOML, ("styles", "Metal", "Heavy"), None) == TONES_TOML  # (nothing to take out)
    assert edit_tone_map("", ("categories", "Lead"), 68) == "[categories]\nLead = 68\n"
    emptied = edit_tone_map(TONES_TOML, ("artists", "The Band", "Heavy"), None)
    assert emptied == "# My pedal's slots\n\n# Everything else\n[categories]\nClean = 49     # 5050 CL\nHeavy = 52\n"


def test_a_songs_tones_each_with_its_category_and_the_pedals_preset_and_why(tmp_path):
    tones, index = tones_for(tmp_path)
    ref = index.by_sng("s_lead")
    table = tones.table(ref)
    assert (table["song"], table["artist"], table["arrangement"], table["style"], table["auto_style"]) == (
        "Song", "Band", "s_lead", "Metal", "Metal")
    assert [(t["short"], t["slot"], t["seconds"], t["category"], t["pedal"], t["rule"]) for t in table["tones"]] == [
        ("clean", "Base", 20.0, "Clean", 49, "category"),
        ("lead", "A", 35.0, "Lead", 187, "Lead's program, 3"),  # (no rule: its category's program, mapped)
        ("dist", "B", 5.0, "Heavy", 52, "category"),
    ]
    assert table["tones"][0]["effects"] == ["chorus"] and table["tones"][1]["effects"] == ["analog delay"]
    assert table["tones"][0]["pedal_name"] == "5050 CL" and not table["tones"][0]["own_pedal"]
    assert table["first"] == 0 and table["changes"] == [[20.0, 1], [40.0, 2], [45.0, 1]]
    assert table["files"]["tone_map_given"] and table["files"]["problem"] is None
    assert tones.playing(ref, 41.0) == {"key": "s_dist", "short": "dist", "pedal": 52, "own": False, "category": "Heavy"}
    assert tones.table(ref) is table  # (kept while its files are as they were)


def test_a_tones_category_and_preset_of_its_own_for_its_song_and_back_to_auto(tmp_path):
    tones, index = tones_for(tmp_path)
    ref = index.by_sng("s_lead")
    tones.set_tone(ref, "s_clean", category="heavy", change="category")
    mapping = json.loads((tmp_path / "companion" / "presets.json").read_text())
    assert mapping["songs"] == {"s_lead": {"Base": "Heavy"}, "s_rhythm": {"A": "Heavy"}}  # (each part that has it)
    clean = tones.table(ref)["tones"][0]
    assert (clean["category"], clean["auto_category"], clean["own_category"], clean["pedal"]) == ("Heavy", "Clean", True, 52)
    tones.set_tone(ref, "S_CLEAN", pedal=99, change="pedal")
    clean = tones.table(ref)["tones"][0]
    assert (clean["pedal"], clean["own_pedal"], clean["rule"]) == (99, True, "song 'Song', tone s_clean")
    assert tomllib.loads((tmp_path / "my-tones.toml").read_text())["songs"] == {"Song": {"S_CLEAN": 99}}
    tones.set_tone(ref, "s_clean", category=None, change="category")
    tones.set_tone(ref, "s_clean", pedal=None, change="pedal")
    assert json.loads((tmp_path / "companion" / "presets.json").read_text()).get("songs") == {}
    assert (tmp_path / "my-tones.toml").read_text() == TONES_TOML
    assert (tmp_path / "my-tones.toml.bak").exists()
    with pytest.raises(PedalTonesError, match="not a category"):
        tones.set_tone(ref, "s_clean", category="Loud", change="category")


def test_the_songs_style_and_the_rules_for_every_song(tmp_path):
    tones, index = tones_for(tmp_path)
    ref = index.by_sng("s_lead")
    tones.set_style(ref, "Soft")
    table = tones.table(ref)
    assert (table["style"], table["auto_style"], table["own_style"]) == ("Soft", "Metal", True)
    tones.set_named_style("Band", "Classic rock")
    tones.set_artist_category("Band", "heavy", "Crunch")
    tones.set_styles([["Quiet", 0], ["Loud", 0.5]])
    tones.set_rule(["effects", "clean", "Chorus"], 12)
    tones.set_rule(["styles", "Soft", "clean"], 13)
    rules = tones.rules()
    assert rules["categories"] == [["Clean", 49], ["Heavy", 52]] and rules["artists"] == [["The Band", "Heavy", 51]]
    assert rules["effects"] == [["Clean", "chorus", 12]] and rules["styles"] == [["Soft", "Clean", 13]]
    assert rules["companion"] == {"artists": [["Band", "Heavy", "Crunch"]],
                                  "song_styles": [["Band", "Classic rock"], ["s", "Soft"]],
                                  "styles": [["Quiet", 0.0], ["Loud", 0.5]]}
    table = tones.table(ref)
    assert table["style"] == "Soft" and table["tones"][2]["category"] == "Crunch"  # (the artist's Heavy: Crunch)
    assert table["tones"][0]["pedal"] == 12 and table["tones"][0]["rule"] == "effect chorus"
    tones.set_style(ref, None)
    assert tones.table(ref)["style"] == "Classic rock"  # (the artist's, then)
    for bad, said in ((["categories", "Loud"], "not a category"), (["effects", "Clean", "fuzz"], "not an effect"),
                      (["artists", "X"], "two names"), (["sounds", "X", "Y"], "Which rule")):
        with pytest.raises(PedalTonesError, match=said):
            tones.set_rule(bad, 1)
    with pytest.raises(PedalTonesError, match="1 to 200"):
        tones.set_rule(["categories", "Lead"], 201)


def test_a_rule_the_file_has_another_way_is_left_to_be_changed_by_hand(tmp_path):
    tones, _index = tones_for(tmp_path, tone_map='categories = {Clean = 49}\n')
    with pytest.raises(PedalTonesError, match="by hand"):
        tones.set_rule(["categories", "Lead"], 68)
    assert (tmp_path / "my-tones.toml").read_text() == 'categories = {Clean = 49}\n'


def test_short_names():
    assert short_name("amontwil_dist", "amontwil_lead") == "dist" and short_name("Other", "amontwil_lead") == "Other"


def test_the_page_shows_and_changes_a_songs_tones_and_the_launchkey_keeps_the_pedals_preset(tmp_path):
    folder, conf = setup_files(tmp_path)
    game = tmp_path / "game" / "dlc"
    game.mkdir(parents=True)
    song_file(game)
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False,
              pedal_tones_factory=lambda names: PedalTones(names, folder, conf))
    srv = GuiServer(gui, 0)
    from threading import Thread
    Thread(target=srv.serve_forever, daemon=True).start()
    try:
        sid = song_id(index.by_sng("s_lead"))
        status, table = call(srv, "/api/songtones?id=" + quote(sid))
        assert status == 200, table
        assert table["arrangement"] == "s_lead" and [p["part"] for p in table["parts"]] == ["lead", "rhythm"]
        status, table = call(srv, "/api/songtones", {"id": sid, "part": "rhythm", "tone": "s_dist", "pedal": 77})
        assert status == 200 and table["arrangement"] == "s_rhythm" and table["tones"][0]["pedal"] == 77
        status, said = call(srv, "/api/songtones", {"id": sid, "part": "rhythm", "tone": "s_dist", "category": "Loud"})
        assert status == 400 and "not a category" in said["error"]
        status, rules = call(srv, "/api/pedal/rules", {"rule": ["categories", "Lead"], "slot": 68})
        assert status == 200 and ["Lead", 68] in rules["categories"]
        assert call(srv, "/api/pedal/rules")[1]["songs"] == [["Song", "s_dist", 77]]

        ref = index.by_sng("s_lead")
        gui._followed_now = lambda: (ref, 41.0)  # type: ignore[method-assign]  # (following, the dist tone playing)
        assert gui.pedal_info()["tone"] == {"key": "s_dist", "short": "dist", "pedal": 77, "own": True,
                                             "category": "Heavy"}
        assert gui.keep_pedal_tone(120) == "dist: 120 kept"
        assert gui.pedal_info()["tone"]["pedal"] == 120
        assert gui.keep_pedal_tone(120) == "dist: automatic"
        assert gui.pedal_info()["tone"]["pedal"] == 52
        gui._followed_now = lambda: (None, None)  # type: ignore[method-assign]
        with pytest.raises(GuiError, match="No song tone"):
            gui.keep_pedal_tone(1)
    finally:
        gui.close()
        srv.shutdown()
        srv.server_close()
