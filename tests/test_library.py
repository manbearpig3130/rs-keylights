import json

from builders import N, Song, psarc, song_archive

from rs_keylights.library import SongIndex, read_arrangements, song_archives

LEAD = Song(levels={0: [N(1.0, fret=5)]})
BASS = Song(levels={0: [N(2.0, string=0)]}, tuning=(0, 0, 0, 0))


def game(tmp_path):
    dlc = tmp_path / "dlc"
    (dlc / "sub").mkdir(parents=True)
    (dlc / "amonpurs_m.psarc").write_bytes(song_archive(
        "amonpurs", {"lead": ("ABC123", LEAD), "bass": ("DEF456", BASS)}, song="The Pursuit of Vikings", artist="Amon Amarth"))
    (dlc / "sub" / "other_m.psarc").write_bytes(song_archive("other", {"rhythm": ("0A0B", LEAD)}, song="Rooster", artist="Alice in Chains"))
    (dlc / "amonpurs_p.psarc").write_bytes(b"PC archive, never read")
    (dlc / "._amonpurs_m.psarc").write_bytes(b"AppleDouble")
    (dlc / "broken_m.psarc").write_bytes(b"not an archive")
    return tmp_path


def test_finds_mac_archives(tmp_path):
    names = sorted(p.name for p in song_archives(game(tmp_path)))
    assert names == ["amonpurs_m.psarc", "broken_m.psarc", "other_m.psarc"]


def test_reads_arrangements_from_manifests(tmp_path):
    refs = read_arrangements(game(tmp_path) / "dlc" / "amonpurs_m.psarc")
    assert {(r.persistent_id, r.sng, r.part, r.bass, r.title) for r in refs} == {
        ("ABC123", "songs/bin/macos/amonpurs_lead.sng", "lead", False, "Amon Amarth - The Pursuit of Vikings"),
        ("DEF456", "songs/bin/macos/amonpurs_bass.sng", "bass", True, "Amon Amarth - The Pursuit of Vikings"),
    }
    bass = next(r for r in refs if r.bass)
    assert bass.load().notes[0].pitches == (28,)


def test_manifest_without_song_asset_falls_back_to_its_file_name(tmp_path):
    manifest = json.dumps({"Entries": {"X1": {"Attributes": {"PersistentID": "x1", "ArrangementName": "Lead"}}}})
    path = tmp_path / "a.psarc"
    path.write_bytes(psarc({"songs/bin/macos/key_lead.sng": b"", "manifests/songs_dlc_key/key_lead.json": manifest.encode(),
                            "manifests/songs_dlc_key/bad.json": b"{not json"}))
    [ref] = read_arrangements(path)
    assert (ref.persistent_id, ref.sng) == ("X1", "songs/bin/macos/key_lead.sng")


def test_index_finds_searches_and_caches(tmp_path):
    root = game(tmp_path)
    cache = tmp_path / "cache" / "index.json"
    index = SongIndex(root, cache)
    assert index.refresh() == 3
    assert len(index) == 3
    assert index.find("abc123").sng.endswith("amonpurs_lead.sng")
    assert {r.part for r in index.search("amon amarth")} == {"lead", "bass"}
    assert [r.title for r in index.search("other_m")] == ["Alice in Chains - Rooster"]

    again = SongIndex(root, cache)
    assert len(again) == 3
    assert again.refresh() == 0  # nothing changed

    (root / "dlc" / "sub" / "other_m.psarc").unlink()
    assert again.refresh() == 0
    assert again.find("0A0B") is None


def test_cache_for_another_game_folder_is_ignored(tmp_path):
    root = game(tmp_path)
    cache = tmp_path / "index.json"
    SongIndex(root, cache).refresh()
    assert len(SongIndex(tmp_path / "elsewhere", cache)) == 0
