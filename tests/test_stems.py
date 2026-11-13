import json
import struct
import sys
import wave

import numpy as np
import pytest

from rs_keylights import drums, stems
from rs_keylights.library import ArrangementRef

RATE = 44100


def ref(tmp_path):
    return ArrangementRef("ID", str(tmp_path / "song_m.psarc"), "songs/bin/macos/song_lead.sng", "SongKey", "Lead")


def parts():
    """Each part a tone of its own (the song: their sum), half a second, stereo."""
    t = np.arange(RATE // 2) / RATE
    out = {}
    for k, stem in enumerate(stems.STEMS):
        tone = 0.08 * np.sin(2 * np.pi * 200 * (k + 1) * t)
        out[stem] = np.stack([tone, tone], axis=1)
    return out


def as_int16(x):
    return np.round(x * 32767).astype(np.int16)


def test_a_songs_parts_are_made_kept_and_read_back_and_what_is_left_is_other(tmp_path, monkeypatch):
    song = sum(parts().values())
    tools, home = tmp_path / "tools", tmp_path / "stems-home"
    (home / "htdemucs_6s.mlmodelc").mkdir(parents=True)
    tools.mkdir()
    (tools / "codebooks.bin").write_bytes(b"")
    song_wav = tmp_path / "the-song.wav"
    with wave.open(str(song_wav), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(RATE), w.writeframes(as_int16(song).tobytes())
    each = tmp_path / "each.npz"
    np.savez(each, **{stem: as_int16(part) for stem, part in parts().items()})
    helper = tools / "rs-stems-drums"  # (the separator: the song, and the song less a part, as it gives them)
    helper.write_text(f"""#!{sys.executable}
import shutil, struct, sys
import numpy as np
args = sys.argv[1:]
if args[0] == "decode":
    shutil.copy({str(song_wav)!r}, args[2])
else:
    parts = np.load({str(each)!r})
    rest = sum(parts[s].astype(np.int32) for s in parts.files if s != args[3]).astype(np.int16)
    head = bytearray(64)
    struct.pack_into("<III", head, 24, len(rest), 1, len(rest))
    open(args[4], "wb").write(bytes(head) + rest.tobytes())
""")
    helper.chmod(0o755)
    monkeypatch.setattr(stems, "can_make", lambda tools=None: True)
    monkeypatch.setattr(drums, "song_audio", lambda ref, game: b"wem")
    song_ref = ref(tmp_path)
    assert not stems.made(song_ref)
    far = []
    stems.make(song_ref, tmp_path, tools=tools, stems_home=home, progress=far.append)
    assert stems.made(song_ref) and far[-1] == 1.0 and far == sorted(far)
    got = stems.load(song_ref, as_int16(song))
    assert set(got) == set(stems.SEPARATED)  # (other: the song less them, not kept apart)
    got["other"] = as_int16(song) - sum(got[s].astype(np.int32) for s in stems.SEPARATED)
    for stem, part in parts().items():  # (each part as it was: AAC's error, about 1%)
        error = np.sqrt(np.mean((got[stem] / 32767 - part) ** 2)) / np.sqrt(np.mean(part ** 2))
        assert error < 0.05, stem
    folder = stems._folder(song_ref)
    assert json.loads((folder / "done.json").read_text())["version"] == stems.STEMS_VERSION
    (folder / "vocals.m4a").unlink()  # (a part gone: not made)
    assert not stems.made(song_ref) and stems.load(song_ref, as_int16(song)) is None


def test_making_parts_says_what_the_separator_lacks(tmp_path):
    with pytest.raises(stems.StemsError, match="drums-setup.sh"):
        stems.make(ref(tmp_path), tmp_path, tools=tmp_path / "none")


def test_the_least_used_songs_parts_go_first_over_the_most_kept(tmp_path, monkeypatch):
    import os
    for k, name in enumerate(("old", "mid", "new")):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "drums.m4a").write_bytes(b"x" * 100)
        os.utime(folder, (1000 + k, 1000 + k))
    monkeypatch.setattr(stems, "CACHE_MOST", 250)
    stems._prune(tmp_path, keep=tmp_path / "old")
    assert sorted(f.name for f in tmp_path.iterdir()) == ["new", "old"]  # (the one just made: kept)


def test_the_song_and_its_parts_mixed_at_their_weights_give_each_part_at_its_level():
    rng = np.random.default_rng(1)
    each = {stem: rng.normal(size=(100, 2)) for stem in stems.STEMS}
    song = sum(each.values())
    levels = {"drums": 0.0, "bass": 1.0, "guitar": 0.5, "vocals": 1.0, "piano": 0.2, "other": 0.3}
    weights = stems.mix_weights(levels)
    mixed = song * weights["song"] + sum(each[s] * weights[s] for s in stems.SEPARATED)
    assert np.allclose(mixed, sum(each[s] * levels[s] for s in stems.STEMS))
    assert stems.mix_weights({}) == {"song": 1.0, **{s: 0.0 for s in stems.SEPARATED}}  # (all as it is: the song)
