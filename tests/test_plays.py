import json
from threading import Thread
from urllib.parse import quote

from builders import N, Song, manifest, psarc, sng
from test_gui import call

from rs_keylights import plays
from rs_keylights.gui import Gui, GuiServer, song_id
from rs_keylights.library import SongIndex
from rs_keylights.plays import Judged, Recorder, Run, judge, read_run, runs_of
from rs_keylights.riffloop import NoteCounts, parse_notes
from rs_keylights.sng import BEND, PALM_MUTE, Note


def notes(*times):
    return [Note(t, 0.0, (40,)) for t in times]


def test_the_mods_counts_and_their_changes_read():
    said = parse_notes("notes run=2 next=5 hits=3 misses=1 streak=2 best=3 arr=amontwil_lead 3:2:12.500:3:0 "
                       "4:2:-1.000:3:1")
    assert said == NoteCounts(2, 5, 3, 1, 2, 3, "amontwil_lead", ((3, 2, 12.5, 3, 0), (4, 2, None, 3, 1)))
    gone = parse_notes("notes run=2 next=5 hits=-1 misses=-1 streak=-1 best=-1 arr=-")
    assert gone is not None and (gone.hits, gone.arrangement, gone.changes) == (None, None, ())
    assert parse_notes("notes error") is None and parse_notes("riffloop error: not a command: notes") is None


def test_each_hit_is_the_note_nearest_it_and_each_miss_the_earliest_not_counted_yet():
    song = notes(1.0, 1.5, 2.0, 3.0, 3.1)
    changes = [(0.5, 0, 0), (1.02, 1, 0), (1.9, 1, 1), (2.05, 2, 1), (2.9, 3, 1), (3.6, 3, 2)]
    j = judge(song, changes)
    assert j.judged == (Judged(0, True, 1.02), Judged(1, False, 1.9), Judged(2, True, 2.05), Judged(3, True, 2.9),
                        Judged(4, False, 3.6))  # (3.0 hit early; 3.1 missed, counted once gone by)
    assert (j.hits, j.misses, j.unmatched, j.passes) == (3, 2, 0, 1)
    # Riff Repeater: the song goes back, its notes counted again; a run found late: its first counts as they were
    again = judge(song, [(1.1, 4, 2), (1.6, 5, 2), (2.1, 5, 3), (0.9, 5, 3), (1.01, 6, 3), (9.0, 7, 3)])
    assert again.judged == (Judged(1, True, 1.6), Judged(2, False, 2.1), Judged(0, True, 1.01))
    assert (again.passes, again.unmatched, again.hits) == (2, 1, 7)  # (a hit at 9 s: no note there)


class FakeMod:
    def __init__(self):
        self.answers = []

    def notes(self, since):
        self.asked = since
        return self.answers.pop(0) if self.answers else None


def counts(run, next_, changes, arr="s_lead"):
    return NoteCounts(run, next_, 0, 0, 0, 0, arr, tuple(changes))


def test_each_run_kept_in_a_file_of_its_own_as_it_goes(tmp_path):
    mod = FakeMod()
    clock = iter([1_700_000_000.0, 1_700_000_100.0, 1_700_000_200.0])
    recorder = Recorder(mod, tmp_path, clock=lambda: next(clock))
    mod.answers = [counts(2, 3, [(0, 1, 5.0, 3, 0), (1, 2, 1.0, 0, 0), (2, 2, 1.1, 1, 0)])]  # (run 1 before it started)
    assert recorder.poll() and mod.asked == 0
    [kept] = runs_of(["S_LEAD"], tmp_path)
    assert kept.changes == [(1.0, 0, 0), (1.1, 1, 0)] and kept.arrangement == "s_lead" and kept.started == 1_700_000_000.0
    mod.answers = [counts(2, 4, [(3, 2, 1.5, 1, 1)]), counts(3, 6, [(4, 3, 0.5, 0, 0), (5, 3, None, 0, 0)])]
    recorder.poll()
    assert mod.asked == 3
    recorder.poll()
    assert [r.counts for r in runs_of(["s_lead"], tmp_path)] == [(1, 1)]  # (run 3: nothing counted yet, not kept)
    mod.answers = [counts(1, 0, [])]  # (the mod started again: from 0)
    assert not recorder.poll()
    mod.answers = [counts(1, 1, [(0, 1, 2.0, 0, 1)])]
    recorder.poll()
    assert mod.asked == 0 and len(runs_of(["s_lead"], tmp_path)) == 2
    assert read_run(tmp_path / "nothing.json") is None and recorder.changed == 4


def test_the_page_shows_a_runs_hits_and_misses_by_section_and_the_notes_missed(tmp_path):
    lead = Song(levels={0: [N(1.0, string=0, fret=3), N(2.0, string=3, fret=7, mask=BEND, sustain=0.5),
                            N(3.0, string=1, fret=5, mask=PALM_MUTE), N(5.0, string=5, fret=12)]},
                sections=[("verse", 0.0, 4.0), ("chorus", 4.0, 8.0)], tone_changes=[])
    game = tmp_path / "game" / "dlc"
    game.mkdir(parents=True)
    (game / "s_m.psarc").write_bytes(psarc({
        "songs/bin/macos/s_lead.sng": sng(lead),
        "manifests/songs_dlc_s/s_lead.json": manifest("LEAD", "s_lead", "Lead", song="Song", artist="Band", song_key="s"),
    }))
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    day = plays.plays_dir() / "2026-11-10"
    day.mkdir(parents=True)
    (day / "190000-s_lead.json").write_text(json.dumps(
        Run("s_lead", 1_700_000_000.0, [(0.5, 0, 0), (1.05, 1, 0), (2.8, 1, 1), (3.0, 2, 1), (5.9, 2, 2)]).to_json()))
    (day / "180000-s_lead.json").write_text(json.dumps(Run("s_lead", 1_699_990_000.0, [(1.0, 1, 0)]).to_json()))
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    srv = GuiServer(gui, 0)
    Thread(target=srv.serve_forever, daemon=True).start()
    try:
        sid = song_id(index.by_sng("s_lead"))
        status, info = call(srv, "/api/plays?id=" + quote(sid))
        assert status == 200, info
        assert [(r["id"], r["part"], r["hits"], r["misses"]) for r in info["runs"]] == [
            ("2026-11-10/190000-s_lead", "Lead", 2, 2), ("2026-11-10/180000-s_lead", "Lead", 1, 0)]
        run = info["run"]
        assert (run["hits"], run["misses"], run["accuracy"], run["passes"], run["unmatched"]) == (2, 2, 50.0, 1, 0)
        assert run["sections"] == [["Verse 1", 0.0, 2, 1], ["Chorus 1", 4.0, 0, 1]]
        assert run["missed"] == [[2.0, "Verse 1", "D4 · string 3, fret 7 · bend", 1, 0],
                                 [5.0, "Chorus 1", "E5 · string 1, fret 12", 1, 0]]
        status, older = call(srv, "/api/plays?id=" + quote(sid) + "&run=" + quote("2026-11-10/180000-s_lead"))
        assert older["run"]["hits"] == 1 and older["run"]["missed"] == []
        assert gui.state()["plays"] == 0
    finally:
        gui.close()
        srv.shutdown()
        srv.server_close()
