import math
import threading
import time

import numpy as np

from rs_keylights import tonecheck
from rs_keylights.sng import Note

RATE = tonecheck.RATE


def test_where_the_song_was_follows_its_blocks_but_not_through_a_pause_or_a_jump():
    b = tonecheck.BLOCK
    step = b / RATE
    anchors = [(b * (k + 1), 10.0 + step * (k + 1)) for k in range(10)]  # (playing)
    anchors += [(b * (k + 11), anchors[-1][1]) for k in range(5)]  # (paused)
    anchors += [(b * 16, 50.0), (b * 17, 50.0 + step)]  # (moved on: jumped, then playing)
    at = np.array([b * 2 + b // 2, b * 12, b * 15 + b // 2, b * 16 + b // 2])
    got = tonecheck.chart_times(anchors, at)
    assert math.isclose(got[0], 10.0 + 2.5 * step) and np.isnan(got[1]) and np.isnan(got[2])
    assert math.isclose(got[3], 50.0 + 0.5 * step)
    assert np.isnan(tonecheck.chart_times(anchors, np.array([0]))[0])  # (before the first block's end)


def _riff(seconds: float, rate: int, seed: int = 1, tilt: float = 0.0) -> tuple[np.ndarray, list[Note]]:
    """A riff of power chords, a new one every 0.4 s (each a root and its fifth, with harmonics), and its chart;
    tilt dB per octave over 1 kHz (a brighter or darker tone)."""
    rng = np.random.default_rng(seed)
    out, notes = np.zeros(int(seconds * rate)), []
    t = np.arange(int(0.38 * rate)) / rate
    for k in range(int(seconds / 0.4)):
        root = int(rng.choice([40, 43, 45, 47, 50]))
        at = k * 0.4
        tone = np.zeros(len(t))
        for pitch in (root, root + 7, root + 12):
            f = 440 * 2 ** ((pitch - 69) / 12)
            for h in range(1, 40):
                if f * h < rate / 2:
                    gain = 10 ** (tilt * math.log2(max(f * h / 1000, 1e-3)) / 20) if f * h > 1000 else 1.0
                    tone += gain * np.sin(2 * np.pi * f * h * t + h) / h
        a = int(at * rate)
        out[a:a + len(t)] += tone * np.exp(-t * 3)
        notes.append(Note(at, 0.35, (root, root + 7)))
    return (out / np.abs(out).max() * 0.5).astype(np.float32), notes


def _anchors(length: int, start: float) -> list[tuple[int, float]]:
    return [(e, start + e / RATE) for e in range(tonecheck.BLOCK, length + 1, tonecheck.BLOCK)]


def test_a_take_like_the_record_but_darker_and_late_says_the_record_has_more_highs():
    record, notes = _riff(30.0, 44100)
    yours, _notes = _riff(30.0, RATE, tilt=-6.0)  # (the same notes, at another rate: darker)
    late = int(0.06 * RATE)
    take = np.concatenate([np.zeros(late, np.float32), yours])[:len(yours)]
    got = tonecheck.analyse(take, _anchors(len(take), 0.0), record, 44100, 0.0, notes)
    assert got.enough and got.matched > 20 and 50 <= got.late_ms <= 70
    assert got.regions["presence"] > 3 and got.regions["treble"] > got.regions["presence"]  # (the record brighter)
    assert abs(got.regions["low mids"]) < 1.5
    assert any(line.startswith("More presence") for line in got.said)


def test_wrong_notes_dont_count_and_too_few_right_ones_say_nothing():
    record, notes = _riff(30.0, RATE, seed=1)
    other, _notes = _riff(30.0, RATE, seed=2)  # (another riff altogether)
    got = tonecheck.analyse(other, _anchors(len(other), 0.0), record, RATE, 0.0, notes)
    assert not got.enough and got.matched < got.played / 2 and not got.eq
    half = record.copy()
    half[len(half) // 2:] = other[len(half) // 2:]  # (right for the first half, wrong after)
    got = tonecheck.analyse(half, _anchors(len(half), 0.0), record, RATE, 0.0, notes)
    assert got.enough and 0.4 * got.played <= got.matched <= 0.75 * got.played
    assert all(abs(v) < 1.0 for v in got.regions.values())  # (as the record: what matched is)


def _eq(x: np.ndarray, rate: int, lo: float, hi: float, db: float) -> np.ndarray:
    """An EQ (as one after the amp: linear) of db between lo and hi Hz."""
    spectrum, freqs = np.fft.rfft(x), np.fft.rfftfreq(len(x), 1 / rate)
    return np.fft.irfft(spectrum * np.where((freqs >= lo) & (freqs < hi), 10 ** (db / 20), 1.0), len(x)).astype(
        np.float32)


def _chugs_and_notes(seconds: float, rate: int, seed: int = 1) -> tuple[np.ndarray, list[Note]]:
    """Two low power chords (chugs: most of their level the lows'), then a high single note, and again; and its
    chart."""
    rng = np.random.default_rng(seed)
    out, notes = np.zeros(int(seconds * rate)), []
    t = np.arange(int(0.38 * rate)) / rate
    for k in range(int(seconds / 0.4)):
        tone = np.zeros(len(t))
        if k % 3 != 2:
            root = int(rng.choice([35, 38, 40, 43]))
            pitches, rolloff, level = (root, root + 7), 1.5, 1.0
            sounding = (root, root + 7, root + 12)
        else:
            pitches = sounding = (int(rng.choice([64, 67, 69, 71, 74])),)
            rolloff, level = 1.0, 0.5
        for pitch in sounding:
            f = 440 * 2 ** ((pitch - 69) / 12)
            for h in range(1, 40):
                if f * h < rate / 2:
                    tone += level * np.sin(2 * np.pi * f * h * t + h) / h ** rolloff
        a = int(k * 0.4 * rate)
        out[a:a + len(t)] += tone * np.exp(-t * 3)
        notes.append(Note(k * 0.4, 0.35, pitches))
    return (out / np.abs(out).max() * 0.5).astype(np.float32), notes


def test_an_eq_on_the_lows_shows_in_full_though_they_rule_the_chugs_level():
    # (Each moment's spectrum was taken by its level before the moments were averaged: a chug's level is its lows',
    # so a boost there hardly showed, and smeared into the low mids: +6 dB showed as +1.5)
    record, notes = _chugs_and_notes(30.0, RATE)
    for db in (6.0, -6.0):
        yours = _eq(record, RATE, 70, 180, db)
        got = tonecheck.analyse(yours, _anchors(len(yours), 0.0), record, RATE, 0.0, notes)
        assert got.enough and abs(got.matched - tonecheck.analyse(record, _anchors(len(record), 0.0), record, RATE,
                                                                  0.0, notes).matched) < 1.0  # (the same moments)
        assert abs(got.regions["lows"] + db) < 1.5, got.regions  # (the record: db less, all of it)
        assert all(abs(got.regions[r]) < 1.0 for r in ("low mids", "mids", "upper mids", "presence")), got.regions


def test_more_distortion_says_less_gain():
    record, notes = _riff(30.0, RATE)
    driven = (np.tanh(12 * record / np.abs(record).max()) * 0.5).astype(np.float32)
    got = tonecheck.analyse(driven, _anchors(len(driven), 0.0), record, RATE, 0.0, notes)
    assert got.enough and got.crest_record - got.crest_yours > tonecheck.CREST_SAYS
    assert any("try less gain" in line for line in got.said)


class FakeStream:
    """An input's stream: a tone at half scale, then full scale (clipped), BLOCK samples a read."""

    def __init__(self, channels):
        self.channels, self.latency, self.reads = channels, 0.005, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, count):
        time.sleep(0.002)
        self.reads += 1
        level = 0.5 if self.reads < 20 else 1.0
        block = np.zeros((count, self.channels), np.float32)
        block[:, -1] = level * np.sin(np.arange(count) / 5)
        return block, False


def test_an_inputs_tap_meters_its_channel_and_records_with_where_the_song_was(monkeypatch):
    monkeypatch.setattr(tonecheck, "_input_stream", lambda device, channels: FakeStream(channels))
    tap = tonecheck.Tap("Blender", 3)
    tap.start()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not tap.level()["clipped"]:
            time.sleep(0.01)
        level = tap.level()
        assert level["clipped"] and level["loudest"] >= -0.1 and level["error"] is None
        where_lock = threading.Lock()
        asked = []

        def where(at):
            with where_lock:
                asked.append(at)
            return 12.0 + len(asked) * tonecheck.BLOCK / RATE
        tap.record(where)
        assert tap.recording
        while tap.seconds < 0.05:
            time.sleep(0.01)
        take, anchors = tap.take()
        assert not tap.recording and len(take) == anchors[-1][0] and len(anchors) == len(take) // tonecheck.BLOCK
        assert anchors[0][1] > 12.0 and len(take) % tonecheck.BLOCK == 0
    finally:
        tap.stop()
    assert not tap.running


def test_checks_are_kept_with_their_recordings_the_oldest_going(tmp_path, monkeypatch):
    monkeypatch.setattr(tonecheck, "CHECKS_MOST", 2)
    names = iter(["a", "b", "c"])
    monkeypatch.setattr(tonecheck.time, "strftime", lambda _f: next(names))
    for song in ("one", "two", "three"):
        tonecheck.keep({"song": song}, np.zeros(100, np.float32), cache=tmp_path)
    assert [c["song"] for c in tonecheck.history(tmp_path)] == ["two", "three"]
    assert sorted(p.name for p in tmp_path.glob("*.wav")) == ["b.wav", "c.wav"]
    assert sorted(p.name for p in tmp_path.glob("[abc].json")) == ["b.json", "c.json"]
