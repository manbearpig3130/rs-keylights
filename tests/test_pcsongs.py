import json
import os
import random
import zlib

import pytest
from builders import N, Song, manifest, psarc, sng
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from rs_keylights import pcsongs
from rs_keylights.cli import main
from rs_keylights.library import SongIndex, read_arrangements
from rs_keylights.pcsongs import FREE_APP, PcSongsError, mac_chart, mac_graph, mac_name, sync
from rs_keylights.psarc import Archive, Stored, _unpacked, write_archive
from rs_keylights.sng import SNG_KEY_MAC

GRAPH = ('<urn:uuid:1> <http://emergent.net/aweb/1.0/canonical> "/songs/bin/generic".\n'
         '<urn:uuid:1> <http://emergent.net/aweb/1.0/relpath> "/songs/bin/generic/{key}_lead.sng".\n'
         '<urn:uuid:2> <http://emergent.net/aweb/1.0/tag> "dx9".\n'
         '<urn:uuid:2> <http://emergent.net/aweb/1.0/canonical> "/audio/windows".\n')


def pc_chart(song):
    """A chart as the PC has it: the Mac's (the builder's) encrypted with the PC's key instead."""
    raw = sng(song)
    iv, body, trailer = raw[8:24], raw[24:-56], raw[-56:]
    mac = Cipher(algorithms.AES(SNG_KEY_MAC), modes.CTR(iv)).decryptor()
    pc = Cipher(algorithms.AES(pcsongs.SNG_KEY_PC), modes.CTR(iv)).encryptor()
    return raw[:24] + pc.update(mac.update(body) + mac.finalize()) + pc.finalize() + trailer


def pc_archive(path, key, persistent_id, song="Song", artist="Band", app="12345", windows=False):
    sep = "\\" if windows else "/"
    path.write_bytes(psarc({
        "appid.appid": app.encode(),
        f"audio{sep}windows{sep}1.wem": b"RIFF" + bytes(range(256)) * 300,
        f"songs/bin/generic/{key}_lead.sng": pc_chart(Song(levels={0: [N(1.0, fret=3), N(2.0, fret=5)]})),
        f"manifests/songs_dlc_{key}/{key}_lead.json": manifest(persistent_id, f"{key}_lead", "Lead", song=song,
                                                               artist=artist, song_key=key),
        f"{key}_aggregategraph.nt": GRAPH.format(key=key).encode(),
    }))
    return path


def mac_archive(path, key, persistent_id, song, artist):
    path.write_bytes(psarc({
        f"songs/bin/macos/{key}_lead.sng": sng(Song(levels={0: [N(1.0)]})),
        f"manifests/songs_dlc_{key}/{key}_lead.json": manifest(persistent_id, f"{key}_lead", "Lead", song=song,
                                                               artist=artist, song_key=key),
    }))


def test_a_pc_chart_encrypted_for_the_mac_and_its_names_and_graph():
    song = Song(levels={0: [N(1.0, fret=7)]})
    assert mac_chart(pc_chart(song)) == sng(song)  # (as the Mac's own: its trailer as it was)
    with pytest.raises(PcSongsError, match="not a PC chart"):
        mac_chart(b"\x00" * 100)
    assert mac_name("songs/bin/generic/a.sng") == "songs/bin/macos/a.sng"
    assert mac_name("audio\\windows\\1.wem") == "audio/mac/1.wem" and mac_name("manifests/x.json") == "manifests/x.json"
    graph = mac_graph(GRAPH.format(key="k"))
    assert '"/songs/bin/macos".' in graph and '"/songs/bin/macos/k_lead.sng"' in graph and '"macos".' in graph
    assert '"/audio/mac".' in graph and "generic" not in graph and "windows" not in graph


def test_the_pc_songs_without_a_mac_archive_made_mac_ones_once_and_removed_once_theyre_gone(tmp_path, capsys):
    game, steam = tmp_path / "game", tmp_path / "steam"
    (game / "dlc").mkdir(parents=True)
    (steam / "dlc" / "more").mkdir(parents=True)
    (game / ".rearmed").mkdir()
    (game / ".rearmed" / "source.json").write_text(json.dumps({"source": str(steam)}))
    (game / ".rearmed" / "steam.env").write_text(f"RS_LANGUAGE=en\nRS_STEAM_DLC=221680,{FREE_APP}\n")
    mac_archive(game / "dlc" / "have_m.psarc", "have", "HAVE", "Had", "Band")
    pc_archive(steam / "dlc" / "have_p.psarc", "have", "HAVE")  # (its Mac archive's IDs)
    pc_archive(steam / "dlc" / "retitled_p.psarc", "other", "OTHER", song="HAD!", artist="band")  # (its artist and title)
    new = pc_archive(steam / "dlc" / "more" / "new_p.psarc", "new", "NEW", windows=True)
    old = pc_archive(steam / "dlc" / "new-old_p.psarc", "newold", "NEW")  # (an older copy of it)
    os.utime(old, (1_600_000_000, 1_600_000_000))
    index = SongIndex(game, tmp_path / "index.json")

    report = sync(game, index)
    assert (report.made, report.kept, sorted(report.have_mac), report.older, report.failed, report.apps_changed) == (
        ["new_m.psarc"], 0, ["have_p.psarc", "retitled_p.psarc"], ["new-old_p.psarc"], [], 1)
    made = game / "dlc" / "from-pc" / "new_m.psarc"
    with Archive(made) as archive, Archive(new) as source:
        assert sorted(archive.names) == ["appid.appid", "audio/mac/1.wem", "manifests/songs_dlc_new/new_lead.json",
                                         "new_aggregategraph.nt", "songs/bin/macos/new_lead.sng"]
        assert archive.read("appid.appid") == FREE_APP.encode()  # (12345 isn't one the game lists)
        assert archive.read("audio/mac/1.wem") == source.read("audio\\windows\\1.wem")
        assert '"macos"' in archive.read("new_aggregategraph.nt").decode()
    [ref] = read_arrangements(made)
    assert ref.song == "Song" and [n.time for n in ref.load().notes] == [1.0, 2.0]
    index.refresh()
    assert sorted(r.sng for r in index.arrangements()) == ["songs/bin/macos/have_lead.sng", "songs/bin/macos/new_lead.sng"]

    assert main(["--game-dir", str(game), "pc-songs"]) == 0
    assert "0 made, 1 made before, 2 with a Mac archive already, 1 older PC copies of a song" in capsys.readouterr().out
    new.unlink()  # (gone: the older copy's made instead)
    report = sync(game, index)
    assert report.made == ["new-old_m.psarc"] and report.removed == ["new_m.psarc"] and not made.exists()
    (steam / "dlc" / "bad_p.psarc").write_bytes(b"PSAR")
    assert main(["--game-dir", str(game), "pc-songs", "--dry-run"]) == 1
    assert "bad_p.psarc" in capsys.readouterr().err


def test_archives_written_with_entries_copied_block_for_block(tmp_path):
    source = tmp_path / "a.psarc"
    big = bytes(random.Random(1).randbytes(70_000)) + b"x" * 90_000
    source.write_bytes(psarc({"one": b"hello", "big": big}))
    with Archive(source) as archive:
        kept = archive.stored("big")
    assert isinstance(kept, Stored) and kept.length == len(big) and len(kept.blocks) == 3
    write_archive(tmp_path / "b.psarc", [("big/copied", kept), ("new", b"made here" * 1000)])
    with Archive(tmp_path / "b.psarc") as archive:
        assert archive.names == ["big/copied", "new"]
        assert archive.read("big/copied") == big and archive.read("new") == b"made here" * 1000


def test_blocks_that_are_zlib_whatever_their_size_and_without_their_checksum():
    data = b"riff and chorus " * 400
    packed = zlib.compress(data)
    assert _unpacked(packed, len(data)) == data
    assert _unpacked(packed[:-4], len(data)) == data  # (its checksum left out)
    assert _unpacked(data[:300], 300) == data[:300]  # (stored as it is)
    noise = random.Random(2).randbytes(2000)  # (zlib its own size: stored so, it's still zlib)
    for n in range(1, 200):
        plain = b"a" * n + noise
        packed = zlib.compress(plain, 9)
        if len(packed) == len(plain):
            assert _unpacked(packed, len(plain)) == plain
            break
    else:
        raise AssertionError("no zlib of its own size found")
