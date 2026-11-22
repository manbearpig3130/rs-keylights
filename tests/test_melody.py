import json

import numpy as np

from rs_keylights import melody
from rs_keylights.library import ArrangementRef
from rs_keylights.sng import Arrangement, Syllable

HOP = 0.01


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def track(length, sung):
    """A pitch track (Hz every HOP, how sure): sung as (start, end, midi) stretches, silent elsewhere."""
    f0 = np.zeros(int(length / HOP))
    for start, end, midi in sung:
        f0[int(start / HOP):int(end / HOP)] = hz(midi)
    return f0, np.where(f0 > 0, 0.9, 0.01)


def test_each_syllable_is_a_note_at_the_pitch_its_sung_at_and_a_melisma_a_few():
    f0, sure = track(6.0, [(1.0, 1.5, 64), (1.6, 2.0, 67), (2.2, 2.5, 69), (2.5, 3.0, 71), (3.5, 4.0, 60)])
    f0[int(1.2 / HOP)] = hz(76)  # (a frame off: no note of its own)
    syllables = [Syllable("Oh", 1.0, 1.5), Syllable("say", 1.6, 2.0), Syllable("caaan", 2.2, 3.0),
                 Syllable("(scream)", 4.5, 5.0)]  # (unsung: no pitch, no note)
    notes = melody.notes_from(f0, sure, HOP, syllables)
    assert [n[2] for n in notes] == [64, 67, 69, 71]  # (the melisma: two)
    assert notes[0][:2] == (1.0, 1.5) and notes[2][0] == 2.2 and abs(notes[2][1] - 2.5) < 0.02 and notes[3][1] == 3.0


def test_without_lyrics_each_sung_stretch_is_split_where_its_pitch_moves_on_and_the_chart_offset_moves_it_back():
    f0, sure = track(4.0, [(1.0, 1.4, 62), (1.4, 1.8, 65), (2.5, 2.55, 70)])  # (the last too short: none)
    notes = melody.notes_from(f0, sure, HOP, offset=0.05)  # (the audio 50 ms after its chart)
    assert [n[2] for n in notes] == [62, 65]
    assert abs(notes[0][0] - 0.95) < 0.011 and abs(notes[1][1] - 1.75) < 0.011


def test_a_melody_is_kept_and_played_as_the_vocals_parts_notes(tmp_path):
    ref = ArrangementRef("V", str(tmp_path / "song_m.psarc"), "songs/bin/macos/song_vocals.sng", "SongKey", "Vocals")
    assert melody.kept(ref, tmp_path) is None
    melody.keep(ref, [(1.0, 1.5, 64), (2.0, 2.4, 67)], tmp_path)
    assert melody.kept(ref, tmp_path) == [(1.0, 1.5, 64), (2.0, 2.4, 67)]
    base = Arrangement(notes=(), tuning=(), capo=0, length=10.0, lyrics=(Syllable("Oh", 1.0, 1.5),))
    vocals = melody.arrangement(base, melody.kept(ref, tmp_path))
    assert [(n.time, n.pitches) for n in vocals.notes] == [(1.0, (64,)), (2.0, (67,))] and vocals.lyrics == base.lyrics
    (tmp_path / "melody.json").write_text(json.dumps({"hop": HOP, "f0": list(track(2.0, [(1.0, 1.5, 64)])[0]),
                                                      "sure": [0.9] * 200}))
    assert [n[2] for n in melody.notes_from_file(tmp_path / "melody.json", None, 0.0)] == [64]
