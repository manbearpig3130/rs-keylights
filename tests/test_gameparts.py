import struct
import wave

import numpy as np
import pytest

from rs_keylights import gameparts, stems


def write_wav(path, samples, rate=48000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())


def test_a_wems_rate_and_channels_come_from_its_fmt_chunk():
    fmt = struct.pack("<HHIIHH", 0xFFFF, 2, 48000, 0, 0, 0)
    junk = b"JUNK" + struct.pack("<I", 3) + b"abc\0"  # (an odd chunk first: padded)
    data = b"RIFF" + struct.pack("<I", 0) + b"WAVE" + junk + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    assert gameparts.wem_format(data) == (48000, 2)
    with pytest.raises(ValueError):
        gameparts.wem_format(b"OggS" + bytes(40))


def test_the_parts_are_written_a_frame_at_a_time_as_the_mod_maps_them_the_silent_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(gameparts, "CHUNK", 1000)  # (a few pieces)
    frames = 2500
    ramp = np.arange(frames * 2).reshape(-1, 2) % 3000
    wavs = {}
    for k, stem in enumerate(stems.SEPARATED):
        samples = np.zeros((frames + k, 2)) if stem == "piano" else ramp + 100 * k  # (the piano: silent; each a
        wavs[stem] = tmp_path / f"{stem}.wav"                                         # little longer than the last)
        write_wav(wavs[stem], samples)
    old = tmp_path / "out" / "another song.pcm"
    old.parent.mkdir()
    old.write_bytes(b"x")
    path = tmp_path / "out" / "song.pcm"
    names = gameparts.write(path, 123456789, 48000, wavs)
    assert names == ["drums", "bass", "guitar", "vocals"] and not old.exists()  # (one song's kept)
    assert gameparts.header(path) == {"file_id": 123456789, "rate": 48000, "channels": 2, "frames": frames,
                                      "names": names}
    data = np.frombuffer(path.read_bytes()[gameparts.HEADER:], dtype=np.int16).reshape(frames, len(names), 2)
    for i, _name in enumerate(names):
        assert np.array_equal(data[:, i, :], ramp + 100 * i)  # (frame N of each: frame N of the song)
    assert gameparts.header(tmp_path / "nothing.pcm") is None
    path.write_bytes(path.read_bytes()[:100])  # (cut short: not one)
    assert gameparts.header(path) is None


def test_the_mods_weights_are_the_stem_mixers_the_song_at_others_level():
    levels = {"drums": 0.0, "bass": 1.0, "guitar": 0.5, "vocals": 1.0, "piano": 1.0, "other": 1.0}
    assert gameparts.weights(levels, ["drums", "bass", "guitar", "vocals"]) == [1.0, -1.0, 0.0, -0.5, 0.0]
    quiet = {**levels, "other": 0.0}  # (other muted: the song gone, each part at its own level)
    assert gameparts.weights(quiet, ["drums", "guitar"]) == [0.0, 0.0, 0.5]
