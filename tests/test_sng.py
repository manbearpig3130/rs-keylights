import pytest
from builders import N, Song, sng, sng_plain

from rs_keylights.chords import chord_name
from rs_keylights.lyrics import Line, lyric_lines
from rs_keylights.sng import (CHORD, FRET_HAND_MUTE, HARMONIC, IGNORE, SLIDE, Chord, Section, SngError, Syllable,
                               decrypt, note_name, parse)


def load(song: Song, bass: bool = False):
    return parse(decrypt(sng(song)), bass=bass)


def test_single_notes_get_their_pitch_from_string_fret_and_tuning():
    song = Song(levels={0: [N(1.0, string=0, fret=0), N(2.0, string=5, fret=3, sustain=0.5)]},
                tuning=(-2, 0, 0, 0, 0, 0))
    arrangement = load(song)
    assert [(n.time, n.pitches, n.sustain) for n in arrangement.notes] == [(1.0, (38,), 0.0), (2.0, (67,), 0.5)]
    assert arrangement.tuning == (-2, 0, 0, 0, 0, 0)
    assert arrangement.length == 60.0


def test_chords_use_their_template_frets():
    song = Song(levels={0: [N(1.0, mask=CHORD, chord=0, string=-1, fret=-1)]}, chords=[(0, 2, 2, -1, -1, -1)])
    assert load(song).notes[0].pitches == (40, 47, 52)  # E power chord


def test_each_phrase_uses_its_hardest_level():
    song = Song(
        levels={
            0: [N(1.0, fret=1, iteration=0), N(5.0, fret=1, iteration=1)],
            1: [N(1.0, fret=2, iteration=0), N(1.5, fret=3, iteration=0), N(5.0, fret=2, iteration=1)],
        },
        phrase_levels=[1, 0],
        iterations=[0, 1],
    )
    assert [(n.time, n.pitches[0] - 40) for n in load(song).notes] == [(1.0, 2), (1.5, 3), (5.0, 1)]


def test_skips_fret_hand_mutes_and_ignored_notes_and_bends_are_read_past():
    song = Song(levels={0: [N(1.0, mask=FRET_HAND_MUTE), N(2.0, mask=IGNORE), N(3.0, fret=5, bends=2), N(4.0)]})
    assert [n.time for n in load(song).notes] == [3.0, 4.0]


def test_capo_applies_to_open_strings():
    song = Song(levels={0: [N(1.0, string=0, fret=0), N(2.0, string=0, fret=4)]}, capo=2)
    assert [n.pitches for n in load(song).notes] == [(42,), (44,)]


def test_bass_strings():
    song = Song(levels={0: [N(1.0, string=0), N(2.0, string=3, fret=2)]}, tuning=(0, 0, 0, 0))
    assert [n.pitches for n in load(song, bass=True).notes] == [(28,), (45,)]


def test_songs_with_vocals_have_symbol_tables():
    song = Song(levels={0: [N(1.0)]}, vocals=2)
    assert len(load(song).notes) == 1


def test_no_levels():
    assert load(Song(levels={})).notes == ()


def test_rejects_other_files_and_layout_mistakes():
    with pytest.raises(SngError, match="not a Mac .sng"):
        decrypt(b"\x00" * 64)
    with pytest.raises(SngError, match="read"):
        parse(sng_plain(Song(levels={0: [N(1.0)]})) + b"extra")
    with pytest.raises(SngError, match="truncated|bad count"):
        parse(sng_plain(Song(levels={0: [N(1.0)]}))[:-30])


def test_note_name():
    assert [note_name(p) for p in (36, 47, 60, 61)] == ["C2", "B2", "C4", "C#4"]


def test_beats_and_sections():
    song = Song(levels={0: [N(1.0)]}, beats=[0.5, 1.0, 1.5], sections=[("intro", 0.5, 10.0), ("chorus2", 10.0, 20.0)])
    a = parse(sng_plain(song))
    assert a.beats == (0.5, 1.0, 1.5)
    assert a.bars == (0.5,)  # (each bar's first beat: 4 to a bar)
    assert a.sections == (Section("intro", 0.5, 10.0), Section("chorus2", 10.0, 20.0))


def test_chords_are_named_from_the_chart_or_their_notes_and_held_for_their_hand_shapes():
    song = Song(levels={0: [N(1.0, chord=0, mask=CHORD), N(3.0, chord=1, mask=CHORD)]},
                chords=[(0, 2, 2, -1, -1, -1), (3, 5, 5, 4, 3, 3), (0, 2, -1, -1, -1, -1)],
                chord_names=["Em", "", "°7"],
                shapes={0: [(0, 1.0, 2.5), (1, 3.0, 4.0), (2, 5.0, 6.0)]})
    a = parse(sng_plain(song))
    assert a.chords == (Chord("Em", 1.0, 2.5, (0, 2, 2, -1, -1, -1)), Chord("G", 3.0, 4.0, (3, 5, 5, 4, 3, 3)),
                        Chord("dim7", 5.0, 6.0, (0, 2, -1, -1, -1, -1)))  # (the second unnamed: its notes G B D G B G;
    # the third's sign spelt out; each with its shape)
    assert [n.frets for n in a.notes] == [(0, 2, 2), (3, 5, 5, 4, 3, 3)]  # (each note's frets, as its pitches are)


def test_with_a_capo_its_open_strings_are_at_the_capo():
    song = Song(levels={0: [N(1.0, string=1, fret=0), N(2.0, string=1, fret=5), N(3.0, chord=0, mask=CHORD)]},
                chords=[(0, 2, 2, -1, -1, -1)], chord_names=["Em"], shapes={0: [(0, 3.0, 4.0)]}, capo=2)
    a = parse(sng_plain(song))
    assert [n.frets for n in a.notes] == [(2,), (5,), (2, 2, 2)]
    assert a.chords[0].frets == (2, 2, 2, -1, -1, -1)


def test_chord_names_from_their_notes():
    assert [chord_name(p) for p in [(40, 47, 52), (48, 52, 55), (47, 50, 55, 59), (48, 52, 55, 57), (45, 52, 55, 60),
                                     (60, 64), (60,)]] == ["E5", "C", "G/B", "C6", "Am7", "", ""]


def test_a_vocals_parts_syllables_make_lines():
    a = parse(sng_plain(Song(vocals=3)))
    assert a.lyrics == (Syllable("la", 0.0, 0.5),) * 3  # (as the builder has them)
    lines = lyric_lines([Syllable("Hey", 1.0, 1.5), Syllable("lit-", 1.5, 2.0), Syllable("tle", 2.0, 2.5),
                         Syllable("kid+", 2.5, 3.0), Syllable("Did", 4.0, 4.5), Syllable("you", 4.5, 5.0),
                         Syllable("Up", 20.0, 21.0)])
    assert lines == (Line("Hey little kid", 1.0, 3.0, ((1.0, 0, 3), (1.5, 4, 7), (2.0, 7, 10), (2.5, 11, 14))),
                     Line("Did you", 4.0, 5.0, ((4.0, 0, 3), (4.5, 4, 7))),  # (a long gap: a new line)
                     Line("Up", 20.0, 21.0, ((20.0, 0, 2),)))


def test_techniques_slides_bends_vibrato_harmonics_and_dead_notes():
    song = Song(levels={0: [
        N(1.0, string=0, fret=5, sustain=0.5, slide_to=7),  # slid up 2 frets
        N(2.0, string=0, fret=7, slide_off_to=3),  # slid off, 4 frets down
        N(3.0, string=1, fret=5, bend_points=[(3.2, 2.0), (3.5, 0.0)], vibrato=80),  # a full step bend, released
        N(4.0, string=2, fret=7, mask=HARMONIC),  # a natural harmonic: an octave and a fifth over the open string
        N(5.0, string=0, fret=3, mask=FRET_HAND_MUTE),  # dead
        N(6.0, chord=0, mask=CHORD | SLIDE, chord_notes=0),  # a power chord slid up 2
    ]}, chords=[(5, 7, -1, -1, -1, -1)], chord_notes=[{"slides": [7, 9, -1, -1, -1, -1]}])
    a = parse(sng_plain(song))
    slid, off, bent, harmonic, chord = a.notes
    assert (slid.pitches, slid.slide, off.slide_off) == ((45,), 2, -4)
    assert [(round(at, 3), step) for at, step in bent.bends] == [(3.2, 2.0), (3.5, 0.0)] and bent.vibrato  # (semitones)
    assert harmonic.pitches == (50 + 19,)
    assert chord.pitches == (45, 52) and chord.slide == 2
    assert [(n.time, n.pitches) for n in a.muted] == [(5.0, (43,))]  # (not among the notes: no keys)
