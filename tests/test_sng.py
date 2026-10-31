import pytest
from builders import N, Song, sng, sng_plain

from rs_keylights.sng import CHORD, FRET_HAND_MUTE, IGNORE, Section, SngError, decrypt, note_name, parse


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
    assert a.sections == (Section("intro", 0.5, 10.0), Section("chorus2", 10.0, 20.0))
