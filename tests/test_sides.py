import numpy as np

from rs_keylights import sides
from rs_keylights.sng import Arrangement, Note

RATE = 22050


def _tone(pitch: int, seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    f = 440 * 2 ** ((pitch - 69) / 12)
    return sum(np.sin(2 * np.pi * f * h * t) / h for h in range(1, 5)) * np.exp(-t * 2)


def _guitars(lead_left: float, rhythm_left: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, Arrangement, Arrangement]:
    """A guitar stem of two guitars, each a note every half second (the lead high, the rhythm low, never the same
    notes), each its share on the left (the rest right); and each alone, and their charts."""
    length = 40.0
    lead, rhythm = np.zeros((int(length * RATE), 2)), np.zeros((int(length * RATE), 2))
    lead_notes, rhythm_notes = [], []
    for k in range(70):
        at = 1.0 + k * 0.5
        for out, notes, pitch, left in ((lead, lead_notes, 76 + k % 5, lead_left),
                                        (rhythm, rhythm_notes, 45 + k % 3 * 2, rhythm_left)):
            tone = _tone(pitch, 0.45)
            a = int(at * RATE)
            out[a:a + len(tone), 0] += tone * left
            out[a:a + len(tone), 1] += tone * (1 - left)
            notes.append(Note(at, 0.4, (pitch,)))
    scale = 8000

    def chart(notes: list[Note]) -> Arrangement:
        return Arrangement(tuple(notes), (0,) * 6, 0, length)
    return ((lead + rhythm) * scale).astype(np.int16), lead * scale, rhythm * scale, chart(lead_notes), \
        chart(rhythm_notes)


def test_guitars_on_either_side_are_apart_and_parted_into_the_lead_and_the_rhythm():
    guitar, lead, rhythm, lead_chart, rhythm_chart = _guitars(0.85, 0.2)
    found = sides.measure(guitar, RATE, lead_chart, rhythm_chart)
    assert found.apart and found.lead > sides.SIDE and found.rhythm < -sides.SIDE
    assert found.lead_notes == found.rhythm_notes == 70
    parted = sides.part(guitar, found.lead, found.rhythm)
    rest = guitar.astype(np.int32) - parted  # (the rhythm: the guitar less the lead, the two the guitar as it was)
    leak = lambda got, own, other: float(np.sum((got - own) ** 2) / np.sum(own ** 2))
    assert leak(parted.astype(np.float64), lead, rhythm) < 0.05  # (each mostly its own)
    assert leak(rest.astype(np.float64), rhythm, lead) < 0.05


def test_guitars_together_in_the_middle_or_on_the_same_side_arent_apart():
    for lead_left, rhythm_left in ((0.5, 0.5), (0.8, 0.7)):
        guitar, _lead, _rhythm, lead_chart, rhythm_chart = _guitars(lead_left, rhythm_left)
        assert not sides.measure(guitar, RATE, lead_chart, rhythm_chart).apart


def test_too_few_notes_alone_or_a_mono_stem_arent_measured():
    guitar, _lead, _rhythm, lead_chart, rhythm_chart = _guitars(0.85, 0.2)
    found = sides.measure(guitar, RATE, Arrangement(lead_chart.notes[:5], (0,) * 6, 0, 40.0), rhythm_chart)
    assert not found.apart and found.lead_notes == 5
    assert not sides.measure(guitar[:, :1], RATE, lead_chart, rhythm_chart).apart
    same = Arrangement(tuple(Note(n.time, n.sustain, (n.pitches[0] - 24,)) for n in lead_chart.notes), (0,) * 6, 0,
                       40.0)  # (the rhythm playing the lead's notes, octaves down: none alone)
    assert sides.measure(guitar, RATE, lead_chart, same).lead_notes == 0
