import pytest

from rs_keylights.lights import Light
from rs_keylights.sng import ACCENT, PALM_MUTE
from rs_keylights.sound import Voice, Voices, parse_note
from rs_keylights.tones import tone

MAIN, LOW, MUTE = Voice(11, tone(79)), Voice(12, tone(80)), Voice(13, tone(77))


def light(pitches, mask=0):
    return Light(0.0, 0.1, tuple(p + 12 for p in pitches)[:4], tuple(pitches), mask)


def test_palm_mutes_then_low_notes_then_the_rest():
    voices = Voices(MAIN, low=LOW, mute=MUTE, split=40).for_arrangement(12)
    assert voices.pick(light((35,), PALM_MUTE))[0] == 13
    assert voices.pick(light((35, 42)))[0] == 12  # lowest below E2
    assert voices.pick(light((40, 47)))[0] == 11
    assert voices.pick(light((47,), PALM_MUTE))[0] == 13  # a palm mute anywhere


def test_without_those_voices_everything_is_main():
    voices = Voices(MAIN)
    assert voices.pick(light((35,), PALM_MUTE))[0] == 11
    assert [v.channel for v in voices.all()] == [11]


def test_pitches_follow_the_keys_or_their_own():
    moved = Voices(MAIN).for_arrangement(12)
    assert moved.pick(light((35, 42, 47, 50, 54, 59))) == (11, (47, 54, 59, 62, 66, 71), 100)  # all six, not 4
    real = Voices(MAIN, real_pitch=True).for_arrangement(12)
    assert real.pick(light((35, 42))) == (11, (35, 42), 100)


def test_accents_play_louder():
    assert Voices(MAIN).pick(light((40,), ACCENT))[2] == 127


def test_describe():
    voices = Voices(MAIN, low=LOW, mute=MUTE, split=40, real_pitch=True)
    assert voices.describe() == ("tone 079 DISTORTION GT, palm mutes 077 MUTE GUITAR, below E2 080 POWER DIST.GT, "
                                 "at the real pitch")


def test_parse_note():
    assert [parse_note(t) for t in ("B1", "E2", "F#2", "Bb2", "c4", "C-1", "35")] == [35, 40, 42, 46, 60, 0, 35]
    for bad in ("H2", "E", "200", "B10"):
        with pytest.raises(ValueError):
            parse_note(bad)
