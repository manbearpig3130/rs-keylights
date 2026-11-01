from rs_keylights.sng import Arrangement, SongTone
from rs_keylights.songtones import kind_changes, tone_kind


def test_a_tones_kind_from_its_name_else_its_gear():
    assert [tone_kind(SongTone(name)) for name in ("amonpurs_dist", "eartsing_lxpiano", "eartsing_synthbass",
                                                   "drowbodi_cleanlead", "ragebulls_acc", "foo_crunch", "x_od",
                                                   "x_tone2")] == [
        "distortion", "piano", "synth bass", "clean lead", "acoustic", "drive", "drive", None]
    assert tone_kind(SongTone("x_tone2", ("Amp_TW22", "Pedal_AcousticEmulator"))) == "acoustic"
    assert tone_kind(SongTone("x_tone3", ("Pedal_GermaniumDrive",))) == "drive"
    assert tone_kind(SongTone("song_bass"), bass=True) == "bass"  # (a bass part's: its kinds)
    assert tone_kind(SongTone("song_dist"), bass=True) == "driven bass"
    assert tone_kind(SongTone("song_bass", ("Bass_Pedal_BassDistortion",)), bass=True) == "driven bass"


def test_an_arrangements_tone_changes_to_kinds():
    base, dist, odd = SongTone("song_clean"), SongTone("song_dist"), SongTone("song_tone3")
    a = Arrangement((), (), 0, 60.0, tone_changes=((10.0, 1), (20.0, 0), (30.0, 2), (40.0, 3)), base_tone=base,
                    tones=(base, dist, odd, None))
    assert kind_changes(a) == [(10.0, "distortion"), (20.0, None)]  # (back to its own; no kind; none: left out)
