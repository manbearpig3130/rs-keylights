import importlib.util
import socket
import subprocess
import sys
import threading
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from rs_keylights import drums, roformer, splitserver, stems
from rs_keylights.library import ArrangementRef

RATE = 48000
SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "rs-keylights-split-server.py"
spec = importlib.util.spec_from_file_location("split_server_script", SERVER_SCRIPT)
assert spec is not None and spec.loader is not None
server_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_script)
SOURCES = ["bass", "drums", "other", "vocals", "guitar", "piano"]


def song():
    return (np.arange(RATE * 2 * 2).reshape(-1, 2) % 2000 - 1000).astype(np.int16)  # (2 s, stereo)


def write_wav(path, samples, rate=RATE):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def fake_split(audio, parts, out, progress):  # (the model: each part the song at a gain of its own, as FLAC)
    import io
    with wave.open(io.BytesIO(audio)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, w.getnchannels())
    for k, part in enumerate(parts):
        write_wav(out / f"{part}.wav", (pcm // (k + 2)).astype(np.int16))
        subprocess.run(["afconvert", "-f", "flac", "-d", "flac", str(out / f"{part}.wav"), str(out / f"{part}.flac")],
                       check=True)
        progress((k + 1) / len(parts))


@pytest.fixture
def server(tmp_path):
    def failing(audio, parts, out, progress):
        if len(parts) == 1 and parts[0] == "piano":
            raise RuntimeError("CUDA out of memory")
        fake_split(audio, parts, out, progress)

    jobs = server_script.Jobs(failing, tmp_path / "jobs")
    describe = lambda: {"server": "rs-keylights split", "version": 1, "model": "BS-RoFormer SW",  # noqa: E731
                        "device": "Fake GPU (CUDA)", "parts": SOURCES}
    http = ThreadingHTTPServer(("127.0.0.1", 0), server_script.handler("the-key", jobs, describe))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http.server_address[1]}", jobs
    http.shutdown()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_song_sent_comes_back_as_its_parts_each_as_long_as_it(server, tmp_path, monkeypatch):
    url, jobs = server
    monkeypatch.setattr(splitserver, "POLL", 0.05)
    write_wav(tmp_path / "song.wav", song())
    assert splitserver.status(url, "the-key")["device"] == "Fake GPU (CUDA)"
    with pytest.raises(splitserver.SplitServerError, match="Not the key") as wrong:
        splitserver.status(url, "another")
    assert not isinstance(wrong.value, splitserver.Unreachable)
    with pytest.raises(splitserver.Unreachable):
        splitserver.status(f"http://127.0.0.1:{free_port()}", "the-key")
    (tmp_path / "out").mkdir()
    far = []
    made = splitserver.split(url, "the-key", tmp_path / "song.wav", ["drums", "vocals"], tmp_path / "out", far.append)
    assert set(made) == {"drums", "vocals"} and far[-1] == 1.0 and far == sorted(far)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", str(made["vocals"]), str(tmp_path / "v.wav")], check=True)
    with wave.open(str(tmp_path / "v.wav")) as w:
        vocals = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)
    assert np.array_equal(vocals, song() // 3)  # (lossless: as it was made)
    assert jobs.waiting() == 0 and not any(jobs.folder.iterdir())  # (fetched: gone there)
    with pytest.raises(splitserver.SplitServerError, match="CUDA out of memory"):  # (its failure, said)
        splitserver.split(url, "the-key", tmp_path / "song.wav", ["piano"], tmp_path / "out")
    with pytest.raises(splitserver.SplitServerError, match="The parts"):
        splitserver.split(url, "the-key", tmp_path / "song.wav", ["kazoo"], tmp_path / "out")


def ref(tmp_path):
    return ArrangementRef("ID", str(tmp_path / "song_m.psarc"), "songs/bin/macos/song_lead.sng", "SongKey", "Lead")


def test_bs_roformer_splits_on_the_server_given_else_here(server, tmp_path, monkeypatch):
    url, _jobs = server
    monkeypatch.setattr(splitserver, "POLL", 0.05)
    monkeypatch.setattr(drums, "decode", lambda ref, game_dir, out, tools=None: write_wav(out, song()))
    tools, cache = tmp_path / "roformer-tools", tmp_path / "stems"
    song_ref = ref(tmp_path)
    stems.make(song_ref, tmp_path, tools=tools, cache=cache, separator=stems.ROFORMER,
               server={"url": url, "key": "the-key"})  # (not set up here: there)
    parts = stems.load(song_ref, song(), cache, separator=stems.ROFORMER)
    assert parts is not None
    for k, stem in enumerate(stems.SEPARATED):  # (each as it was made there: AAC's error aside)
        expected = (song() // (k + 2)).astype(float)
        assert np.sqrt(np.mean((parts[stem] - expected) ** 2)) / np.sqrt(np.mean(expected ** 2)) < 0.05, stem
    gone = {"url": f"http://127.0.0.1:{free_port()}", "key": "the-key"}
    with pytest.raises(stems.StemsError, match="didn't answer"):  # (not answering, and not set up here)
        stems.make(song_ref, tmp_path, tools=tools, cache=cache, separator=stems.ROFORMER, server=gone)
    with pytest.raises(stems.StemsError, match="Not the key"):
        stems.make(song_ref, tmp_path, tools=tools, cache=cache, separator=stems.ROFORMER,
                   server={"url": url, "key": "another"})
    calls = []  # (set up here: not answering, here instead)

    def run(args, env, what, along=None):  # (its own Python here: each part, the song as it is)
        calls.append(what)
        if what == "Separating it with BS-RoFormer SW":
            out, parts = Path(args[5]), args[6:]
            for part in parts:
                song().tofile(out / f"{part}.raw")
        else:
            subprocess.run(args, check=True)

    monkeypatch.setattr(roformer, "ready", lambda tools=None: True)
    monkeypatch.setattr(stems, "_run", run)
    stems.make(song_ref, tmp_path, tools=tools, cache=cache, separator=stems.ROFORMER, server=gone)
    assert "Separating it with BS-RoFormer SW" in calls


def test_the_split_server_is_kept_its_address_made_whole_and_asked(server, tmp_path):
    from rs_keylights.gui import Gui, GuiError
    from rs_keylights.library import SongIndex
    url, _jobs = server
    (tmp_path / "game").mkdir()
    gui = Gui(SongIndex(tmp_path / "game", tmp_path / "index.json"), settings_file=tmp_path / "gui.json",
              list_ports=list, other_follow=lambda: False)
    assert gui.split_server_info() is None
    with pytest.raises(GuiError, match="key"):
        gui.set_split_server({"url": "my-pc.local"})
    said = gui.set_split_server({"url": url, "key": "the-key"})["server"]
    assert said["ok"] and said["device"] == "Fake GPU (CUDA)"
    gui.set_split_server({"url": "my-pc.local", "key": ""})  # (just its name: the rest made up; the key kept)
    assert gui.settings["split_server"] == {"url": "http://my-pc.local:8042", "key": "the-key"}
    again = Gui(gui.index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    assert again.settings["split_server"]["url"] == "http://my-pc.local:8042"  # (kept)
    gui.set_split_server({"url": ""})
    assert gui.settings["split_server"] is None and gui.split_server_info() is None
    assert sys.platform == "darwin"  # (afconvert: these run on the Mac)
