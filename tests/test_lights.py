from rs_keylights.lights import GAP, MIN_LIGHT, KeyRange, Light, schedule
from rs_keylights.sng import CHILD, Note


def test_octave_shift_moves_low_parts_up_while_the_top_fits():
    keys = KeyRange()
    assert keys.octave_shift([Note(0, 0, (40,)), Note(1, 0, (88,))]) == 0  # standard guitar
    assert keys.octave_shift([Note(0, 0, (35,)), Note(1, 0, (67,))]) == 12  # B standard
    assert keys.octave_shift([Note(0, 0, (23,)), Note(1, 0, (50,))]) == 24  # five-string bass
    assert keys.octave_shift([Note(0, 0, (35,)), Note(1, 0, (90,))]) == 0  # the top wouldn't fit
    assert keys.octave_shift([]) == 0


def test_fit_folds_notes_into_range():
    keys = KeyRange()
    assert [keys.fit(p) for p in (35, 36, 96, 97)] == [47, 36, 96, 85]


def test_short_notes_stay_lit_and_each_note_ends_the_one_before():
    lights = schedule([Note(1.0, 0, (60,)), Note(1.5, 2.0, (64,)), Note(2.0, 0, (67,))])
    assert lights == [
        Light(1.0, 1.0 + MIN_LIGHT, (60,)),
        Light(1.5, 2.0, (64,)),  # sustained, but the next note starts
        Light(2.0, 2.0 + MIN_LIGHT, (67,)),
    ]


def test_repeats_of_a_key_blink():
    lights = schedule([Note(1.0, 1.0, (60,)), Note(1.5, 0, (60, 67))])
    assert lights[0].off == 1.5 - GAP


def test_close_repeats_still_end_before_the_next_note():
    lights = schedule([Note(1.0, 0, (60,)), Note(1.01, 0, (60,))])
    assert lights[0].off <= lights[1].on


def test_linked_notes_keep_the_keys_lit():
    lights = schedule([Note(1.0, 0.5, (60,)), Note(1.5, 0.5, (60,), CHILD)])
    assert lights == [Light(1.0, 2.0, (60,))]


def test_at_most_four_keys_and_the_shift():
    lights = schedule([Note(0.0, 0, (35, 42, 47, 50, 54, 59))])
    assert lights[0].keys == (47, 54, 59, 62)
