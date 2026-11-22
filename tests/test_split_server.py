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


class FakeModel:  # (a separator, as the server has it: its sources, each part the audio at a gain of its own)
    def __init__(self, sources, gain):
        self.sources, self.gain, self.seen = sources, gain, []

    def separate(self, audio, wanted, progress):
        self.seen.append((audio.copy(), list(wanted)))
        progress(1.0)
        return np.stack([audio * self.gain * (k + 1) for k in wanted])


def stand_ins(monkeypatch):  # (soundfile and scipy, which the server's own Python has: 16-bit WAVs, through wave)
    import types
    soundfile = types.ModuleType("soundfile")

    def read(source, dtype="float32", always_2d=True):
        with wave.open(source) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, w.getnchannels())
            return pcm.astype(np.float32) / 32768.0, w.getframerate()

    def write(path, pcm16, rate, format=None, subtype=None):
        with wave.open(path, "wb") as w:
            w.setnchannels(pcm16.shape[1])
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm16.tobytes())
    soundfile.read, soundfile.write = read, write
    scipy, signal = types.ModuleType("scipy"), types.ModuleType("scipy.signal")
    signal.resample_poly = lambda x, up, down, axis=-1: x
    scipy.signal = signal
    monkeypatch.setitem(sys.modules, "soundfile", soundfile)
    monkeypatch.setitem(sys.modules, "scipy", scipy)
    monkeypatch.setitem(sys.modules, "scipy.signal", signal)


def test_the_server_splits_the_drums_into_the_kits_pieces_from_its_own_drums_or_a_drum_part_sent(tmp_path, monkeypatch):
    stand_ins(monkeypatch)
    songs = FakeModel(["bass", "drums", "other", "vocals", "guitar", "piano"], 0.1)
    kit = FakeModel(["kick", "snare", "toms", "hh", "ride", "crash"], 0.2)
    split = server_script.splitter(songs, kit)
    write_wav(tmp_path / "song.wav", (np.ones((1000, 2)) * 10000).astype(np.int16), rate=44100)
    audio = (tmp_path / "song.wav").read_bytes()
    (tmp_path / "song.wav").unlink()
    far = []
    split(audio, ["guitar", "kick", "crash"], tmp_path, far.append)
    assert songs.seen[0][1] == [4, 1]  # (the guitar, and its drums for the pieces: not written)
    assert np.allclose(kit.seen[0][0], songs.seen[0][0] * 0.1 * 2)  # (the pieces from those drums)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["crash.flac", "guitar.flac", "kick.flac"]
    with wave.open(str(tmp_path / "crash.flac")) as w:  # (16-bit, as long as the song)
        crash = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert len(crash) == 2000 and abs(int(crash[0]) - round(10000 * 0.1 * 2 * 0.2 * 6)) <= 2
    assert far == sorted(far) and far[-1] == 1.0
    (out := tmp_path / "drums").mkdir()
    split(audio, ["hh"], out, far.append, from_drums=True)  # (a drum part sent: the pieces alone)
    assert len(songs.seen) == 1 and np.allclose(kit.seen[1][0], songs.seen[0][0])


def test_a_drum_part_sent_alone_is_asked_for_its_pieces_and_an_older_server_hasnt_them(tmp_path, monkeypatch):
    monkeypatch.setattr(splitserver, "POLL", 0.05)
    jobs = server_script.Jobs(lambda audio, parts, out, progress, from_drums=False: fake_split(audio, parts, out, progress)
                              if from_drums else None, tmp_path / "jobs")
    kit = ["kick", "snare", "toms", "hh", "ride", "crash"]
    said = {"server": "rs-keylights split", "version": 2, "parts": SOURCES + kit, "kit": kit}
    http = ThreadingHTTPServer(("127.0.0.1", 0), server_script.handler("k", jobs, lambda: dict(said)))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{http.server_address[1]}"
    write_wav(tmp_path / "drums.wav", song())
    (tmp_path / "out").mkdir()
    assert splitserver.has_kit(url, "k")
    made = splitserver.split(url, "k", tmp_path / "drums.wav", ["hh", "ride"], tmp_path / "out", from_drums=True)
    assert set(made) == {"hh", "ride"}
    with pytest.raises(splitserver.SplitServerError, match="of a drum part"):  # (a song's part, of a drum part)
        splitserver.split(url, "k", tmp_path / "drums.wav", ["guitar"], tmp_path / "out", from_drums=True)
    said.pop("kit")  # (a server from before DrumSep)
    assert not splitserver.has_kit(url, "k")
    http.shutdown()


def test_bs_roformer_on_a_server_with_drumsep_brings_the_kits_pieces_too_kept_apart(server, tmp_path, monkeypatch):
    url, _jobs = server
    monkeypatch.setattr(splitserver, "POLL", 0.05)
    monkeypatch.setattr(drums, "decode", lambda ref, game_dir, out, tools=None: write_wav(out, song()))
    monkeypatch.setattr(splitserver, "has_kit", lambda url, key: True)
    asked = []
    real_split = splitserver.split

    def split(url, key, song_path, parts, out, progress=lambda done: None, from_drums=False):
        asked.append(list(parts))
        made = real_split(url, key, song_path, [p for p in parts if p in SOURCES], out, progress)
        for piece in stems.KIT:  # (as a server with DrumSep sends them)
            if piece in parts:
                (out / f"{piece}.flac").write_bytes(made["drums"].read_bytes())
                made[piece] = out / f"{piece}.flac"
        return made
    monkeypatch.setattr(splitserver, "split", split)
    (kit := tmp_path / "kit").mkdir()
    pieces = stems.make(ref(tmp_path), tmp_path, tools=tmp_path / "t", cache=tmp_path / "stems",
                        separator=stems.ROFORMER, server={"url": url, "key": "the-key"}, kit_out=kit)
    assert asked == [[*stems.SEPARATED, *stems.KIT]] and pieces == list(stems.KIT)
    assert sorted(p.name for p in kit.iterdir()) == sorted(f"{p}.wav" for p in stems.KIT)
    assert stems.load(ref(tmp_path), song(), tmp_path / "stems", separator=stems.ROFORMER) is not None
    assert not any(p.name.startswith("kick") for p in (tmp_path / "stems").rglob("*"))  # (not kept as parts)


def test_every_file_the_server_downloads_is_pinned_by_a_whole_sha256():
    import re
    pinned = {**server_script.CODE, **server_script.MODEL, **server_script.KIT_MODEL}
    assert pinned and all(re.fullmatch(r"[0-9a-f]{64}", sha) for sha in pinned.values()), pinned
