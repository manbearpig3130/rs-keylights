import pytest

np = pytest.importorskip("numpy")

from rs_keylights.listen import BASS_LOWEST, HOP, OFF, RATE, Detector, frequency  # noqa: E402


def tone(pitches, seconds=0.6, harmonics=6):
    """A plucked string's sound (or a chord's), as the cable hears it: harmonics, dying away, and a little noise."""
    t = np.arange(int(seconds * RATE)) / RATE
    x = sum(sum(np.sin(2 * np.pi * frequency(p) * h * t) / h ** 1.2 for h in range(1, harmonics + 1)) for p in pitches)
    x = x / np.max(np.abs(x)) * 0.2 * np.exp(-t * 1.5)
    return x + np.random.default_rng(1).normal(0, 0.0005, len(x))


def heard(detector, sound, expected=()):
    out = ()
    for i in range(0, len(sound) - HOP, HOP):
        out = detector.feed(sound[i:i + HOP], expected)
    return out


def test_a_note_played_is_heard():
    for pitch in (23, 35, 40, 45, 52, 64, 76):  # B0 (B standard an octave down), B1, E2 up to E5
        assert heard(Detector(), tone([pitch])) == (pitch,)


def test_a_songs_chord_is_heard_note_by_note_and_a_wrong_one_as_its_played():
    assert heard(Detector(), tone([40, 47, 52]), expected=[40, 47, 52]) == (40, 47, 52)  # E5
    assert heard(Detector(), tone([48, 52, 55, 60]), expected=[48, 52, 55, 60]) == (48, 52, 55, 60)  # C
    assert heard(Detector(), tone([40, 47, 52]), expected=[41, 48, 53]) == heard(Detector(), tone([40, 47, 52]))


def test_a_chord_is_heard_with_none_expected():
    # E5, C, G, B in B standard, B5 an octave down (a note doubled an octave up can go unheard: its harmonics the
    # lower one's)
    for chord in ([40, 47, 52], [48, 52, 55, 60, 64], [43, 47, 50, 55, 59, 67], [35, 42, 47, 51, 54, 59], [23, 30, 35]):
        notes = heard(Detector(), tone(chord))
        assert set(notes) <= set(chord) and {p % 12 for p in notes} == {p % 12 for p in chord}, notes


def test_silence_isnt_heard_and_a_note_goes_out_after_it_stops():
    detector = Detector()
    assert heard(detector, np.zeros(RATE // 4)) == ()
    assert heard(detector, tone([45])) == (45,)
    quiet = np.zeros(HOP)
    looks = next(k for k in range(1, 30) if detector.feed(quiet) == ())
    assert OFF <= looks <= 4 + OFF  # (its sound's last moments, then a look or two missing: then out)


def test_a_bass_needs_its_lower_range():
    assert 20 not in heard(Detector(), tone([20]))  # G#0, under a guitar's
    assert heard(Detector(lowest=BASS_LOWEST), tone([20])) == (20,)
