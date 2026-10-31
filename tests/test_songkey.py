from rs_keylights.lights import KeyRange
from rs_keylights.sng import Arrangement, Note
from rs_keylights.songkey import Key, find_key, scale_show, shapes


def riff(pitches, beat=0.5, repeats=8):
    """An arrangement playing these pitches (or chords), one a beat, over and over."""
    notes, time = [], 0.0
    for _ in range(repeats):
        for pitch in pitches:
            notes.append(Note(time, 0.0, pitch if isinstance(pitch, tuple) else (pitch,)))
            time += beat
    return Arrangement(tuple(notes), (0,) * 6, -1, time)


def test_the_tonic_comes_from_where_the_riffs_sit():
    # E minor: the bass on E, the guitar around the E minor scale
    guitar, bass = riff([(52, 59), 55, 57, (52, 59), 59, 62, 60, 54]), riff([40, 40, 40, 43, 40, 40, 45, 47])
    key = find_key([guitar, bass], [False, True])
    assert (key.name, key.notes) == ("E minor", ("E", "F#", "G", "A", "B", "C", "D"))
    # The same notes with the bass on G: G major
    key = find_key([riff([(55, 62), 59, 57, (55, 62), 52, 60, 54, 55])], [False])
    assert key.name == "G major"


def test_a_mode_when_its_own_note_is_strong():
    phrygian = riff([(52, 59), 53, 52, 53, (52, 59), 55, 53, 52])  # E with F all over it
    assert find_key([phrygian]).name == "E Phrygian"
    mixolydian = riff([(50, 57), 48, 55, (50, 57), 50, 54, 48, 57])  # D with C natural
    assert find_key([mixolydian]).name == "D Mixolydian"


def test_flat_keys_are_spelled_with_flats_and_notes_outside_the_scale_are_named():
    key = Key(5, "minor", other=(11,))
    assert key.notes == ("F", "G", "Ab", "Bb", "C", "Db", "Eb") and key.spell(11) == "B"
    assert Key(4, "minor").notes == ("E", "F#", "G", "A", "B", "C", "D")
    assert find_key([Arrangement((), (0,) * 6, -1, 0.0)]) is None


def test_the_key_a_guitarist_plays_in_a_tuning_or_with_a_capo():
    key = Key(1, "minor")  # C# minor, in B standard: E minor shapes... a fourth up: F# minor
    b_standard = Arrangement((), (-5,) * 6, -1, 0.0)
    played, tuning = shapes(key, b_standard)
    assert (played.name, tuning) == ("F# minor", "B standard")
    capo = Arrangement((), (0,) * 6, 2, 0.0)
    played, tuning = shapes(Key(6, "minor"), capo)
    assert (played.name, tuning) == ("E minor", "E standard, capo 2")
    assert shapes(key, Arrangement((), (-2, 0, 0, 0, 0, 0), -1, 0.0)) is None  # drop D: shapes are mixed
    assert shapes(key, Arrangement((), (0,) * 6, -1, 0.0)) is None  # standard: nothing to say


def test_the_scale_climbs_the_whole_keyboard_and_back_holding_its_tonic_chord_at_each_end():
    frames = scale_show(Key(4, "minor"), KeyRange(), step=0.1, hold=1.0)  # E minor on C2 (36) to C7 (96)
    assert all(len(keys) <= 4 for _at, keys in frames)
    up = [max(keys) for _at, keys in frames[:36]]
    assert up[:8] == [36, 38, 40, 42, 43, 45, 47, 48] and up[-1] == 96  # every E minor note on the keyboard
    assert frames[1][1] == (36, 38) and frames[3][1] == (38, 40, 42)  # a trail of three
    assert frames[5][1] == (40, 42, 43, 45)  # E (40), the tonic, still lit as the trail moves on
    assert frames[36][1] == (76, 79, 83, 88)  # the top's tonic chord: the highest E with its octave on the keys
    assert [min(keys) for _at, keys in frames[37:73]][-1] == 36  # back down
    assert frames[73][1] == (40, 43, 47, 52)  # the bottom's tonic chord
    assert frames[-1] == (frames[73][0] + 1.0, ())  # where it repeats
