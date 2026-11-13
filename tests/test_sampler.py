import wave

import numpy as np
import pytest

from rs_keylights.cli import main
from rs_keylights.sampler import (DRUM_CHANNEL, FULL, GM_DRUMS, LEAD, SamplerError, find_channels, sample_kit, save_kit,
                                  trimmed)

RATE = 48000
LOUD = {36: 0.5, 38: 0.4, 42: 0.1}  # the kit's drums, and how loud each is (the rest: not in it)
DECAY = 0.05  # seconds each falls to 1/e in
LATENCY = 0.01  # seconds from a strike to its sound


class Room:
    """A keyboard and the audio input it's plugged into (inputs 3 and 4 of 4, the left louder): time goes on as it's
    slept through, and what's heard is its drums' bursts over a little noise."""

    rate, channels = RATE, 4

    def __init__(self):
        self.now = self.taken = 0
        self.strikes = []  # (when, note)
        self.sent = []
        self.noise = np.random.default_rng(1)

    def sleep(self, seconds):
        self.now += round(seconds * RATE)

    def sound(self, channel, note, velocity):
        self.sent.append((channel, note, velocity))
        if velocity > 0:
            self.strikes.append((self.now + round(LATENCY * RATE), note))

    def take(self):
        t = np.arange(self.taken, self.now)
        out = self.noise.normal(0, 0.0003, (len(t), 4)).astype(np.float32)
        for at, note in self.strikes:
            since = (t - at) / RATE
            burst = np.where(since >= 0, LOUD.get(note, 0.0) * np.exp(-np.maximum(since, 0) / DECAY)
                             * np.sin(2 * np.pi * 200 * since), 0.0)
            out[:, 2] += burst
            out[:, 3] += burst * 0.6
        self.taken = self.now
        return out


def test_the_keyboards_inputs_are_found_by_striking_the_snare():
    room = Room()
    channels, quiet = find_channels(room, room, sleep=room.sleep)
    assert channels == (2, 3) and quiet < 0.005
    assert room.sent[:2] == [(DRUM_CHANNEL, 38, 110), (DRUM_CHANNEL, 38, 0)]
    silent = Room()
    silent.sound = lambda channel, note, velocity: None  # (unplugged: struck, nothing heard)
    with pytest.raises(SamplerError, match="weren't heard"):
        find_channels(silent, silent, sleep=silent.sleep)


def test_each_drum_is_recorded_until_it_dies_away_and_trimmed(tmp_path):
    room, said = Room(), []
    samples = sample_kit(room, room, [36, 38, 42, 58], velocity=120, sleep=room.sleep, say=said.append)
    assert sorted(samples) == [36, 38, 42]  # (58: not in this kit, left out)
    assert said[0] == "Hearing the keyboard on inputs 3 + 4" and "nothing heard" in said[-1]
    assert (DRUM_CHANNEL, 36, 120) in room.sent and (DRUM_CHANNEL, 36, 0) in room.sent
    for note, sample in samples.items():
        assert sample.shape[1] == 2 and 0.15 < len(sample) / RATE < 0.5  # (until it's in the noise: not its 6 s most)
        loudest = np.abs(sample).max()
        assert abs(loudest - LOUD[note]) < 0.05 * LOUD[note]
        first = np.flatnonzero(np.abs(sample).max(axis=1) > loudest * 0.05)[0]
        assert first <= round((LEAD + 0.003) * RATE)  # (it starts as it's struck: no gap before it)
        assert np.abs(sample[-1]).max() < 0.001  # (faded out)
    written = save_kit(samples, RATE, tmp_path / "kit")
    assert [p.name for p in written] == ["36 Kick 1.wav", "38 Snare 1.wav", "42 Closed Hi-Hat.wav"]

    def read(path):
        with wave.open(str(path)) as w:
            assert w.getframerate() == RATE and w.getsampwidth() == 2
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, w.getnchannels()) / 32767

    kick, hat = read(written[0]), read(written[2])
    assert kick.shape[1] == 1 and abs(np.abs(kick).max() - FULL) < 0.01  # (the kit's loudest: full)
    assert abs(np.abs(hat).max() / np.abs(kick).max() - 0.2) < 0.02  # (the kit's balance kept: a fifth as loud)
    stereo = read(tmp_path / "kit" / "stereo" / "36 Kick 1.wav")
    assert stereo.shape[1] == 2 and abs(np.abs(stereo[:, 1]).max() / np.abs(stereo[:, 0]).max() - 0.6) < 0.02
    with pytest.raises(SamplerError):
        save_kit({}, RATE, tmp_path / "none")


def test_inputs_can_be_said_and_nothing_heard_is_trimmed_to_nothing():
    room = Room()
    samples = sample_kit(room, room, [36], channels=(2,), sleep=room.sleep)  # (one input: mono)
    assert samples[36].shape[1] == 1
    assert len(trimmed(np.zeros((1000, 2), dtype=np.float32), RATE, 0.001)) == 0
    assert GM_DRUMS[49] == "Crash 1" and len(GM_DRUMS) == 25


def test_the_command_checks_what_its_given(capsys):
    assert main(["sample-drums", "--velocity", "200"]) == 1
    assert main(["sample-drums", "--channels", "1,2,3"]) == 1
    assert "a velocity of 1 to 127" in capsys.readouterr().err


def test_a_kit_is_chosen_by_name_or_program_or_all_of_them():
    from argparse import Namespace

    from rs_keylights.cli import _drum_kits
    assert _drum_kits(Namespace(all_kits=False, kit=None)) == [(None, None)]  # (the one it's on)
    assert _drum_kits(Namespace(all_kits=False, kit="room")) == [("Room Set", 8)]
    assert _drum_kits(Namespace(all_kits=False, kit="16")) == [("Power Set", 16)]
    assert _drum_kits(Namespace(all_kits=False, kit="3")) == [("kit 3", 3)]  # (one it has no name for)
    every = _drum_kits(Namespace(all_kits=True, kit=None))
    assert [name for name, _p in every] == ["Standard Set", "Room Set", "Power Set", "Synth Set", "Brush Set",
                                            "Orchestra Set"] and every[0][1] == 0
    for bad in ("set", "kazoo", "200"):  # (more than one, none, no such program)
        with pytest.raises(ValueError):
            _drum_kits(Namespace(all_kits=False, kit=bad))


def test_a_kit_not_switched_to_sounds_the_same():
    from rs_keylights.sampler import same_sound
    room = Room()
    samples = sample_kit(room, room, [36, 38], channels=(2, 3), sleep=room.sleep)
    again = sample_kit(room, room, [38], channels=(2, 3), sleep=room.sleep)
    assert same_sound(samples[38], again[38])  # (the same drum, struck again)
    t = np.arange(len(samples[38])) / RATE
    other = (0.4 * np.exp(-t / 0.02) * np.sin(2 * np.pi * 330 * t))[:, None].repeat(2, axis=1)
    assert not same_sound(samples[38], other) and not same_sound(samples[38], samples[38][:4])
