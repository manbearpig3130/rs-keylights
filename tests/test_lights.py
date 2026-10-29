from rs_keylights.lights import GAP, MIN_LIGHT, KeyRange, Light, schedule
from rs_keylights.sng import CHILD, PALM_MUTE, Note


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
        Light(1.0, 1.0 + MIN_LIGHT, (60,), (60,)),
        Light(1.5, 2.0, (64,), (64,)),  # sustained, but the next note starts
        Light(2.0, 2.0 + MIN_LIGHT, (67,), (67,)),
    ]


def test_repeats_of_a_key_blink():
    lights = schedule([Note(1.0, 1.0, (60,)), Note(1.5, 0, (60, 67))])
    assert lights[0].off == 1.5 - GAP


def test_close_repeats_still_end_before_the_next_note():
    lights = schedule([Note(1.0, 0, (60,)), Note(1.01, 0, (60,))])
    assert lights[0].off <= lights[1].on


def test_linked_notes_keep_the_keys_lit():
    lights = schedule([Note(1.0, 0.5, (60,), PALM_MUTE), Note(1.5, 0.5, (60,), CHILD)])
    assert lights == [Light(1.0, 2.0, (60,), (60,), PALM_MUTE)]  # the first note's pitches and techniques


def test_at_most_four_keys_and_the_shift_but_every_pitch_kept():
    lights = schedule([Note(0.0, 0, (35, 42, 47, 50, 54, 59))])
    assert lights[0].keys == (47, 54, 59, 62)
    assert lights[0].pitches == (35, 42, 47, 50, 54, 59)


def test_a_chord_that_goes_on_as_linked_notes_per_string_stays_one_light():
    # The Pursuit of Vikings' lead at 83 s: an F# octave, then each string's linked note at the same time
    lights = schedule([
        Note(83.086, 0.23, (42, 54)),
        Note(83.318, 0.17, (54,), CHILD),
        Note(83.318, 0.17, (42,), CHILD),
        Note(83.549, 0.0, (35, 47)),
    ], shift=12)
    assert [(round(l.on, 3), l.keys) for l in lights] == [(83.086, (54, 66)), (83.549, (47, 59))]
    assert all(l.off > l.on for l in lights)
    assert round(lights[0].off, 3) == 83.488  # held to the linked notes' end (83.318 + 0.17)


def test_notes_at_the_same_time_make_one_chord():
    lights = schedule([Note(1.0, 0, (60,)), Note(1.0, 0.5, (64,), PALM_MUTE), Note(2.0, 0, (67,))], shift=0)
    assert lights[0].keys == (60, 64) and lights[0].mask & PALM_MUTE and lights[0].off == 1.5


def test_merge_keys_two_parts_on_one_key():
    from rs_keylights.lights import KeyLight, Light, merge_keys

    lead = [Light(1.0, 1.2, (60, 64)), Light(2.0, 3.0, (67,))]
    rhythm = [Light(1.02, 1.4, (60,)), Light(2.5, 2.6, (67,))]
    keys = merge_keys([lead, rhythm])
    assert KeyLight(1.0, 1.4, 60) in keys  # one strike, as long as the longer note
    assert KeyLight(1.0, 1.2, 64) in keys
    # rhythm strikes G while the lead holds it: dark before the strike (the most, 60 ms: it's 0.5 s on),
    # then lit through the lead's note
    assert KeyLight(2.0, 2.44, 67) in keys and KeyLight(2.5, 3.0, 67) in keys


def repeats(period, count=6, held=1.0):
    from rs_keylights.lights import Light

    return [Light(i * period, i * period + held, (60,)) for i in range(count)]


def test_repeats_flash_with_a_dark_gap_for_their_speed():
    from rs_keylights.lights import merge_keys

    gallop = merge_keys([repeats(0.115)])
    assert len(gallop) == 6
    assert round(gallop[1].on - gallop[0].off, 4) == round(0.45 * 0.115, 4)  # 52 ms dark, 63 lit
    sixteenths = merge_keys([repeats(0.08)])
    assert round(sixteenths[1].on - sixteenths[0].off, 3) == 0.036
    slow = merge_keys([repeats(0.5)])
    assert round(slow[1].on - slow[0].off, 3) == 0.06  # at most 60 ms dark


def test_repeats_too_fast_to_see_flash_on_every_other_one():
    from rs_keylights.lights import merge_keys

    tremolo = merge_keys([repeats(0.05, count=8, held=0.04)])
    assert [round(k.on, 2) for k in tremolo] == [0.0, 0.1, 0.2, 0.3]
    assert all(b.on - a.off > 0.03 for a, b in zip(tremolo, tremolo[1:]))  # visibly dark between
    # at half speed the same notes are 100 ms apart in real time: every one flashes
    assert len(merge_keys([repeats(0.05, count=8, held=0.04)], rate=0.5)) == 8


def test_lead_lights_the_keys_early():
    from rs_keylights.lights import merge_keys

    assert merge_keys([repeats(0.5, count=1)], lead=0.03)[0].on == -0.03
    assert merge_keys([repeats(0.5, count=1)], rate=0.5, lead=0.03)[0].on == -0.015  # in song time
