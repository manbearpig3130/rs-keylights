import wave

import numpy as np

from rs_keylights import chartsync, drums, stems
from rs_keylights.library import ArrangementRef

RATE = 48000


def click_track(beats, late, length):
    """Clicks on each beat and half-beat, late seconds after it (the audio after its chart), as a WAV's samples."""
    x = np.zeros(int(RATE * length), dtype=np.float32)
    rng = np.random.default_rng(3)
    grid = sorted(list(beats) + [(a + b) / 2 for a, b in zip(beats, beats[1:])])
    for t in grid:
        at = int((t + late) * RATE)
        n = min(2400, len(x) - at)
        if n > 0:
            x[at:at + n] += rng.standard_normal(n).astype(np.float32) * 0.5 * np.exp(-np.arange(n) / 300)
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def write(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())


def test_a_charts_offset_from_its_audio_is_measured_and_applied_only_when_its_clear(tmp_path):
    beats = np.arange(0.5, 120.0, 0.5)  # (120 bpm, 2 minutes)
    write(tmp_path / "late.wav", click_track(beats, 0.054, 122))
    flux, first = chartsync.onsets(tmp_path / "late.wav")
    got = chartsync.measure(flux, first, beats, "drums")
    assert abs(got.offset - 0.054) <= 0.006 and got.stretches >= 5 and got.spread <= chartsync.SPREAD
    write(tmp_path / "close.wav", click_track(beats, 0.006, 122))  # (within the measuring's error: none)
    flux, first = chartsync.onsets(tmp_path / "close.wav")
    close = chartsync.measure(flux, first, beats, "mix")
    assert close.offset == 0.0 and abs(close.measured) < chartsync.LEAST
    quiet = chartsync.measure(np.zeros(1000, dtype=np.float32), 0.0, beats, "mix")  # (no drums on the beats)
    assert quiet.offset == 0.0 and quiet.stretches == 0


def test_a_songs_offset_is_kept_and_measured_again_from_its_drums_once_its_split(tmp_path, monkeypatch):
    beats = list(np.arange(0.5, 90.0, 0.5))
    ref = ArrangementRef("ID", str(tmp_path / "song_m.psarc"), "songs/bin/macos/song_lead.sng", "SongKey", "Lead")
    measured = []
    monkeypatch.setattr(drums, "decode", lambda ref, game_dir, out, tools=None: measured.append("mix") or
                        write(out, click_track(beats, -0.03, 92)))
    split = []
    monkeypatch.setattr(stems, "split_with", lambda ref, cache=None: list(split))

    def decode(ref, work, cache=None, separator="demucs", parts=stems.SEPARATED):
        measured.append("drums")
        write(work / "drums.wav", click_track(beats, 0.04, 92))
        return {"drums": work / "drums.wav"}
    monkeypatch.setattr(stems, "decode", decode)
    cache = tmp_path / "offsets.json"
    first = chartsync.offset_for(ref, beats, tmp_path, cache)
    assert first.source == "mix" and abs(first.offset + 0.03) <= 0.006
    assert chartsync.offset_for(ref, beats, tmp_path, cache) == first and measured == ["mix"]  # (kept)
    split.append("roformer")  # (split since: its drums, more clearly)
    again = chartsync.offset_for(ref, beats, tmp_path, cache)
    assert again.source == "drums" and abs(again.offset - 0.04) <= 0.006 and measured == ["mix", "drums"]
