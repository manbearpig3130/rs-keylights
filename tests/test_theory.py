from rs_keylights import theory
from rs_keylights.sng import Arrangement, Chord, Note, Section
from rs_keylights.songkey import Key

E_MINOR, G_MAJOR, D_PHRYGIAN = Key(4, "minor"), Key(7, "major"), Key(2, "Phrygian")
POWER = {"E5": (0, 2, 2, -1, -1, -1), "C5": (8, 10, 10, -1, -1, -1), "D5": (10, 12, 12, -1, -1, -1),
         "A5": (5, 7, 7, -1, -1, -1), "G5": (3, 5, 5, -1, -1, -1), "B5": (7, 9, 9, -1, -1, -1)}


def chart(names, every=1.0, notes=(), sections=()):
    """A guitar part in E standard: its chords (power chord shapes, else no shape), one a second."""
    chords = tuple(Chord(name, i * every, i * every + every * 0.9, POWER.get(name, ())) for i, name in enumerate(names))
    return Arrangement(tuple(notes), (0,) * 6, 0, len(names) * every, sections=tuple(sections), chords=chords)


def test_chord_names_have_a_root_and_a_kind():
    assert theory.parse_chord("C#m7") == (1, "minor") and theory.parse_chord("Bb5") == (10, "power")
    assert theory.parse_chord("Fmaj7/A") == (5, "major") and theory.parse_chord("Bdim") == (11, "dim")
    assert theory.parse_chord("Riff") is None


def test_chords_are_numbers_in_the_key_against_the_major_scale_with_what_they_do_and_what_is_borrowed():
    assert theory.numeral(E_MINOR, 4, "power") == ("i", "home", "")  # (a power chord: the key's chord's case)
    assert theory.numeral(E_MINOR, 0, "major") == ("♭VI", "near home", "")
    assert theory.numeral(E_MINOR, 2, "power") == ("♭VII", "pulls home", "")
    assert theory.numeral(E_MINOR, 11, "major")[0::2] == ("V", "major V: from harmonic minor, pulling hard to i")
    assert theory.numeral(G_MAJOR, 5, "major") == ("♭VII", "colour", "borrowed from G minor")
    assert theory.numeral(G_MAJOR, 0, "minor")[0::2] == ("iv", "minor here: borrowed from G minor")
    assert theory.numeral(E_MINOR, 5, "power") == ("♭II", "colour", "outside the key: ♭II, the dark Phrygian sound")
    assert theory.numeral(D_PHRYGIAN, 3, "power") == ("♭II", "away", "")  # (Phrygian's own)


def test_a_sections_chords_are_the_phrases_that_repeat_in_it_named_when_well_known():
    names = ["E5", "C5", "D5"] * 4 + ["E5", "A5", "B5", "A5", "B5", "A5", "B5", "E5"]
    a = chart(names)
    chords = theory.chords_of(E_MINOR, a)
    found = theory.phrases(chords, 0.0, len(names))
    assert [(f["loop"], f["times"]) for f in found] == [(["i", "♭VI", "♭VII"], 4), (["iv", "v"], 3), (["i"], 1)]
    assert found[0]["start"] == 0.0 and found[0]["end"] == 12.9  # (landing home after it: the loop's)
    assert theory.pattern_of(E_MINOR, found[0]["roots"]) == "i–♭VI–♭VII: the Aeolian climb"
    loop = theory.phrases(theory.chords_of(E_MINOR, chart(["E5", "C5", "D5", "E5", "C5", "D5", "E5"])), 0, 9)
    assert [(f["loop"], f["times"]) for f in loop] == [(["i", "♭VI", "♭VII"], 2)]  # (home again: the loop's)


def test_a_chords_root_is_from_its_notes_in_the_tuning_when_its_name_says_otherwise():
    a = Arrangement((), (-2,) * 6, 0, 2.0, chords=(Chord("E5", 0.0, 1.0, (0, 2, 2, -1, -1, -1)),))
    assert [c.name for c in theory.chords_of(Key(2, "minor"), a)] == ["D5"]  # (D standard: named as an E shape)


def test_the_riffs_scale_is_the_simplest_that_covers_them_with_whats_outside_it():
    def notes(pcs):
        return [Note(i * 0.25, 0.2, (40 + pc,)) for i, pc in enumerate(pcs)]
    pentatonic = chart([], notes=notes([0, 3, 5, 7, 10, 0, 3, 5, 0, 7, 6, 0] * 3))  # (E minor pentatonic, and B♭)
    assert theory.riff_scale(E_MINOR, [pentatonic], 0, 99)["said"] == "E blues scale"
    chugging = chart([], notes=notes([0, 0, 0, 7, 0, 0, 7, 0]))
    assert theory.riff_scale(E_MINOR, [chugging], 0, 99)["said"] == "E5: just root and 5th"


def test_each_note_is_a_note_of_the_chord_under_it_of_the_key_or_outside_it():
    rhythm = chart(["E5", "C5"], every=2.0)
    lead = chart([], notes=[Note(0.0, 0.5, (64,)), Note(0.5, 0.5, (66,)), Note(1.0, 0.5, (65,)),
                            Note(2.0, 0.5, (67,)), Note(2.5, 0.2, (64, 67))])  # (E, F#, F; then G over C; a chord)
    why = theory.notes_why(E_MINOR, lead, theory.chords_of(E_MINOR, rhythm))
    assert [n[2:] for n in why] == [["E", "R", "chord"], ["F#", "2", "scale"], ["F", "♭2", "outside"],
                                    ["G", "5", "chord"]]


def test_the_key_the_chords_say_of_two_that_fit():
    a = chart(["E5", "C5", "D5", "E5", "A5", "E5"])
    assert theory.chord_key([Key(7, "major"), E_MINOR], a) == E_MINOR
    assert theory.key_named("F# Dorian") == Key(6, "Dorian") and theory.key_named("") is None


def test_sections_have_their_phrases_and_scale():
    a = chart(["E5", "C5", "D5"] * 2, notes=[Note(i * 0.5, 0.4, (40 + pc,)) for i, pc in enumerate([0, 3, 5, 7, 10, 0])],
              sections=[Section("verse", 0.0, 6.0)])
    found = theory.sections_of(E_MINOR, a.sections, [{"name": "Verse 1"}], theory.chords_of(E_MINOR, a), [a])
    assert found[0]["name"] == "Verse 1" and found[0]["phrases"][0]["loop"] == ["i", "♭VI", "♭VII"]
    assert found[0]["scale"]["said"] == "E minor pentatonic"
