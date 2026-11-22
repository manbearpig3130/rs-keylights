import json
import sys
import wave

import numpy as np
import pytest

from rs_keylights import drums, roformer, roformer_worker, stems
from rs_keylights.library import ArrangementRef
from rs_keylights.roformer_worker import CHUNK, STEP, separate, starts, window


def test_each_part_is_cross_faded_chunk_to_chunk_and_written_once_in_order_as_its_done():
    rng = np.random.default_rng(3)
    audio = rng.uniform(-0.5, 0.5, (2, int(CHUNK * 3.3))).astype(np.float32)  # (a song of a few chunks, ragged)
    gains = (0.5, -1.0, 2.0)

    def run(chunk):  # (a model each of whose parts is the chunk at a gain of its own)
        return [chunk * g for g in gains]

    out = {i: [] for i in range(len(gains))}
    progress = []
    separate(audio, [0, 1, 2], run, lambda i, sound: out[i].append(sound.copy()),
             lambda k, n: progress.append((k, n)))
    n = len(starts(audio.shape[1]))
    assert progress == [(k + 1, n) for k in range(n)]
    for i, g in enumerate(gains):
        whole = np.concatenate(out[i], axis=1)
        assert whole.shape == audio.shape  # (all of it, once)
        assert np.allclose(whole[:, 5:], audio[:, 5:] * g, atol=1e-5)  # (the first few: faded in to nothing yet,
        # as the driver has them)
        assert np.abs(whole[:, :5]).max() < 1e-6
        assert all(block.shape[1] <= STEP for block in out[i][:-1])  # (half a chunk at a time: never all of it)
    w = window()
    assert np.allclose(w[:STEP] + w[STEP:], 1.0, atol=1e-6)  # (two chunks overlapping: their cross-fade, 1)
    assert starts(CHUNK // 3) == [0]


def test_a_short_song_is_one_chunk():
    audio = np.ones((2, 1000), dtype=np.float32)
    got = []
    separate(audio, [0], lambda chunk: [chunk], lambda i, sound: got.append(sound))
    assert np.concatenate(got, axis=1).shape == (2, 1000)


def test_its_parts_are_made_by_its_own_python_and_kept_as_its_separators(tmp_path, monkeypatch):
    rate, frames = 48000, 4800
    song = (np.arange(frames * 2).reshape(-1, 2) % 1000).astype(np.int16)

    def decode(ref, game_dir, out, tools=None):
        with wave.open(str(out), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(song.tobytes())

    monkeypatch.setattr(drums, "decode", decode)
    tools = tmp_path / "roformer-tools"
    assert not roformer.ready(tools)
    ref = ArrangementRef("ID", str(tmp_path / "song_m.psarc"), "songs/bin/macos/song_lead.sng", "SongKey", "Lead")
    with pytest.raises(stems.StemsError, match="roformer-setup.sh"):
        stems.make(ref, tmp_path, tools=tools, separator=stems.ROFORMER)
    (tools / roformer.MODEL).mkdir(parents=True)
    (tools / roformer.COMPILED).mkdir()
    (tools / "venv" / "bin").mkdir(parents=True)
    python = tools / "venv" / "bin" / "python"  # (its own Python: each part, the song at a gain of its own)
    python.write_text(f"""#!{sys.executable}
import json, sys
import numpy as np
args = sys.argv[1:]
assert args[0] == "-I" and args[1].endswith("roformer_worker.py")
model, song, out, parts = args[2], args[3], args[4], args[5:]
json.dump({{"model": model, "parts": parts}}, open({str(tmp_path / "asked.json")!r}, "w"))
import wave
with wave.open(song) as w:
    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
for k, part in enumerate(parts):
    (pcm // (k + 2)).astype(np.int16).tofile(f"{{out}}/{{part}}.raw")
open(f"{{out}}/progress", "w").write("3 3")
""")
    python.chmod(0o755)
    cache = tmp_path / "stems"
    far = []
    assert roformer.ready(tools)
    stems.make(ref, tmp_path, tools=tools, cache=cache, progress=far.append, separator=stems.ROFORMER)
    asked = json.loads((tmp_path / "asked.json").read_text())
    assert asked["model"].endswith(roformer.COMPILED) and asked["parts"] == list(stems.SEPARATED)
    assert stems.split_with(ref, cache) == [stems.ROFORMER] and far[-1] == 1.0 and far == sorted(far)
    assert json.loads((stems._folder(ref, cache, stems.ROFORMER) / "done.json").read_text())["separator"] == "roformer"
    parts = stems.load(ref, song, cache, separator=stems.ROFORMER)
    assert parts is not None
    for k, stem in enumerate(stems.SEPARATED):  # (each kept as it was made: AAC's error aside)
        expected = song // (k + 2)
        error = np.sqrt(np.mean((parts[stem].astype(float) - expected) ** 2)) / np.sqrt(np.mean(expected ** 2.0))
        assert error < 0.05, stem
    assert roformer_worker.SOURCES.index("guitar") == 4  # (the model's order)
