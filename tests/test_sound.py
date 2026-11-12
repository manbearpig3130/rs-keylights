import pytest

from rs_keylights.lights import Light
from rs_keylights.sng import ACCENT, FRET_HAND_MUTE, PALM_MUTE, PINCH_HARMONIC
from rs_keylights.sound import (DEAD_VELOCITY, PALM_VELOCITY, PINCH_UP, PLAIN_DEAD_VELOCITY, PINCHED_VELOCITY, SQUEAL_VELOCITY, UP_SOFTER,
                                Voice, Voices, parse_note)
from rs_keylights.tones import tone

MAIN, PINCH, MUTE = Voice(11, tone(79)), Voice(12, tone(298)), Voice(13, tone(77))


def light(pitches, mask=0):
    return Light(0.0, 0.1, tuple(p + 12 for p in pitches)[:4], tuple(pitches), mask)


def test_palm_mutes_on_their_voice_and_the_rest_on_the_main_one():
    voices = Voices(MAIN, mute=MUTE, squeal=PINCH).for_arrangement(12)
    assert voices.pick(light((35,), PALM_MUTE))[0] == 13
    assert voices.pick(light((35, 42)))[0] == 11
    assert voices.pick(light((47,), PALM_MUTE | PINCH_HARMONIC))[0] == 13  # (a pinch harmonic's note: as ever)


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
    voices = Voices(MAIN, mute=MUTE, squeal=PINCH, real_pitch=True)
    assert voices.describe() == ("tone 079 DISTORTION GT, palm mutes 077 MUTE GUITAR, pinch harmonics 298 GM GT "
                                 "HARMONICS, at the real pitch")


def test_parse_note():
    assert [parse_note(t) for t in ("B1", "E2", "F#2", "Bb2", "c4", "C-1", "35")] == [35, 40, 42, 46, 60, 0, 35]
    for bad in ("H2", "E", "200", "B10"):
        with pytest.raises(ValueError):
            parse_note(bad)


def test_the_main_tone_switches_as_the_chart_does():
    from rs_keylights.sng import Arrangement, SongTone
    from rs_keylights.sound import Voice, Voices
    base, dist, acoustic = SongTone("s_clean"), SongTone("s_dist"), SongTone("s_acc")
    a = Arrangement((), (), 0, 60.0, tone_changes=((5.0, 1), (9.0, 0), (12.0, 2), (14.0, 2)), base_tone=base,
                    tones=(base, dist, acoustic))
    voices = Voices(Voice(11, tone(30)), tone_map=(("distortion", tone(79)),))
    assert voices.changes_for(a) == ((5.0, tone(79)), (9.0, tone(30)))  # (back: its own; acoustic: none for it)
    assert Voices(Voice(11, tone(30))).changes_for(a) == ()  # (not followed)


def test_how_hard_a_technique_is_played():
    from rs_keylights.sng import FRET_HAND_MUTE, HAMMER_ON, SLAP
    from rs_keylights.sound import ACCENT_VELOCITY, DEAD_VELOCITY, LEGATO_VELOCITY, VELOCITY
    voices = Voices(Voice(11, tone(30)))
    assert [voices.velocity(mask) for mask in (0, ACCENT, SLAP, HAMMER_ON, FRET_HAND_MUTE)] == [
        VELOCITY, ACCENT_VELOCITY, ACCENT_VELOCITY, LEGATO_VELOCITY, DEAD_VELOCITY]
    plain = Voices(Voice(11, tone(30)), techniques=False)
    assert [plain.velocity(mask) for mask in (ACCENT, SLAP, HAMMER_ON)] == [ACCENT_VELOCITY, VELOCITY, VELOCITY]


def test_a_chord_strummed_up_is_softer():
    assert Voices(MAIN).pick(Light(1.0, 1.2, (40, 47), (40, 47), up=True))[2] == 100 - UP_SOFTER
    assert Voices(MAIN).pick(Light(1.0, 1.2, (40,), (40,), up=True))[2] == 100  # (a note: as it is)
    assert Voices(MAIN, techniques=False).pick(Light(1.0, 1.2, (40, 47), (40, 47), up=True))[2] == 100


def test_palm_mutes_a_little_softer_and_dead_notes_a_tick_without_a_palm_mute_tone():
    assert Voices(MAIN).pick(light((40,), PALM_MUTE))[2] == PALM_VELOCITY
    assert Voices(MAIN).pick(light((40,), PALM_MUTE | ACCENT))[2] == 127
    assert Voices(MAIN, techniques=False).pick(light((40,), PALM_MUTE))[2] == 100
    dead = light((40,), FRET_HAND_MUTE | PALM_MUTE)  # (as techniques makes them)
    assert Voices(MAIN).pick(dead)[::2] == (11, PLAIN_DEAD_VELOCITY)  # (on its own tone)
    assert Voices(MAIN, mute=MUTE).pick(dead)[::2] == (13, DEAD_VELOCITY)  # (on the palm-mute tone)


def test_a_pinch_harmonics_squeal_sounds_softer_over_its_note():
    note = light((52,), PINCH_HARMONIC)
    squeal = Light(0.0, 0.0, (), (52,), PINCH_HARMONIC, rings=(0.5,), extra=True)  # (lights.techniques')
    assert Voices(MAIN).pick(note)[1:] == ((52,), PINCHED_VELOCITY)  # (the note, softer)
    assert Voices(MAIN).pick(light((52,), PINCH_HARMONIC | ACCENT))[2] == 127
    assert Voices(MAIN).pick(squeal)[1:] == ((52 + PINCH_UP,), SQUEAL_VELOCITY)
    assert Voices(MAIN).for_arrangement(12).pick(squeal)[1] == (64 + PINCH_UP,)  # (over the note as it's moved)
    assert Voices(MAIN, techniques=False).pick(squeal)[1:] == ((52,), 100)


def test_a_squeal_on_its_own_voice_when_there_is_one():
    squeal = Light(0.0, 0.0, (), (52,), PINCH_HARMONIC | PALM_MUTE, rings=(0.5,), extra=True)
    voices = Voices(MAIN, mute=MUTE, squeal=Voice(2, tone(298)))
    assert voices.pick(squeal)[0] == 2
    assert voices.pick(light((52,), PINCH_HARMONIC | PALM_MUTE))[0] == 13  # (the note: as ever)
    assert Voices(MAIN, mute=MUTE).pick(squeal)[0] == 13  # (none: the note's own)
