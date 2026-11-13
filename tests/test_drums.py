import numpy as np
import pytest

from rs_keylights import drums
from rs_keylights.drums import ALIGN_LEAST, CRASH, KICK, RIDE, SNARE, TOMS, Hit, align, pick, refine, snap
from rs_keylights.library import ArrangementRef
from rs_keylights.parts import DrumVoices, drum_track, drum_tracks, place
from rs_keylights.player import Player

from test_player import FakeKeys

RATE = 48000


def strengths(seconds, hits):
    """The model's strengths: a spike for each (seconds, class, how high), falling off a frame either side."""
    act = np.zeros((int(seconds * drums.FPS), len(drums.CLASSES)), dtype=np.float32)
    for t, c, high in hits:
        i = round(t * drums.FPS)
        act[i, c] = max(act[i, c], high)
        act[i - 1, c] = max(act[i - 1, c], high / 3)
        act[i + 1, c] = max(act[i + 1, c], high / 3)
    return act


def test_hits_are_picked_where_a_drum_rises_over_its_threshold():
    act = strengths(4, [(0.5, 0, 0.9), (0.5, 3, 0.6), (1.0, 1, 0.5), (1.5, 1, 0.1), (2.0, 2, 0.8), (2.01, 2, 0.7)])
    act[300:330, 4] = 0.8  # (a cymbal's strength staying up: struck once, not again as it rings)
    picked = [(t, c) for t, c, _rise in pick(act)]
    assert picked == [(0.5, 0), (0.5, 3), (1.0, 1), (2.0, 2), (3.0, 4)]  # (the faint snare left out; the two toms 10
    # ms apart one)


def test_hits_close_to_the_charts_16ths_go_on_them_the_rest_stay_where_the_record_has_them():
    beats = [0.0, 0.5, 1.0, 1.5, 2.0]  # (a 16th: 0.125 s)
    hits = [Hit(0.51, KICK, 100), Hit(0.66, SNARE, 90), Hit(1.255, KICK, 100), Hit(1.246, KICK, 120),
            Hit(1.84, CRASH, 80)]
    assert snap(hits, beats) == [Hit(0.5, KICK, 100), Hit(0.66, SNARE, 90), Hit(1.25, KICK, 120),
                                 Hit(1.84, CRASH, 80)]  # (10 ms off: on it; 35 ms off: as they are, not pulled to the
    # chart; the kick twice on a 16th: the louder)
    assert snap(hits, [1.0]) == hits  # (no grid)


def tone(freq, seconds=0.2, loud=0.5):
    t = np.arange(int(seconds * RATE)) / RATE
    return loud * np.sin(2 * np.pi * freq * t) * np.exp(-t * 12)


def test_toms_are_low_mid_or_high_by_their_pitch_and_a_steady_run_of_cymbals_is_a_ride():
    audio = np.zeros(RATE * 12, dtype=np.float32)
    picked = []
    for i, (t, freq) in enumerate([(1.0, 80), (1.5, 82), (2.0, 140), (2.5, 145), (3.0, 260), (3.5, 250)]):
        audio[int(t * RATE):int(t * RATE) + int(0.2 * RATE)] += tone(freq, loud=0.3 + 0.1 * i)
        picked.append((t, 2, 0.8))
    for t in (5.0, 5.4, 5.8, 6.2, 6.6, 9.0):  # (a ride's run, then a crash on its own)
        audio[int(t * RATE):int(t * RATE) + 4800] += np.random.default_rng(1).normal(0, 0.2, 4800) * np.exp(-np.arange(4800) / 2000)
        picked.append((t, 4, 0.8))
    hits = refine(sorted(picked), audio, RATE)
    assert [h.note for h in hits if h.time < 4] == [TOMS[0], TOMS[0], TOMS[1], TOMS[1], TOMS[2], TOMS[2]]
    assert [h.note for h in hits if h.time >= 5] == [RIDE] * 5 + [CRASH]
    toms = [h.velocity for h in hits if h.time < 4]
    assert all(30 <= v <= 127 for v in toms) and toms[0] < toms[-1]  # (louder: harder)


def test_a_drum_part_is_kept_by_its_song_and_made_again_with_another_version(tmp_path, monkeypatch):
    ref = ArrangementRef("ID", str(tmp_path / "song_m.psarc"), "songs/bin/macos/song_lead.sng", "SongKey", "Lead")
    assert drums.kept(ref) is None
    drums.keep(ref, [Hit(1.0, KICK, 100), Hit(1.5, SNARE, 80)])
    assert drums.kept(ref) == [Hit(1.0, KICK, 100), Hit(1.5, SNARE, 80)]
    monkeypatch.setattr(drums, "DRUMS_VERSION", drums.DRUMS_VERSION + 1)
    assert drums.kept(ref) is None


def a_beat(early=0.045, count=ALIGN_LEAST + 10):
    """Kicks on the beat and snares on the 16th after, as audio 45 ms ahead of its chart has them (and a hi-hat)."""
    beats = [1.0 + 0.5 * i for i in range(count + 2)]
    hits = [Hit(round(b - early, 4), KICK, 100) for b in beats[:count]]
    hits += [Hit(round(b + 0.125 - early, 4), SNARE, 90) for b in beats[:count]] + [Hit(2.0 - early, 42, 70)]
    return beats, sorted(hits, key=lambda h: h.time)


def test_a_drum_part_is_moved_onto_its_chart():
    beats, hits = a_beat()
    aligned = align(hits, beats)
    assert {h.time for h in aligned if h.note == KICK} == set(beats[:ALIGN_LEAST + 10])  # (on the beats)
    assert [h.time for h in aligned if h.note == 42] == [2.0]  # (everything moved alike)
    _beats, few = a_beat(count=ALIGN_LEAST // 4)  # (too few to tell: as it is, snapped)
    assert align(few, beats) == snap(few, beats)
    _beats, late = a_beat(early=-0.03)
    assert {h.time for h in align(late, beats) if h.note == KICK} == set(beats[:ALIGN_LEAST + 10])


def test_one_made_before_drum_parts_were_aligned_is_aligned_once_it_has_the_beats(tmp_path):
    import json
    ref = ArrangementRef("ID", str(tmp_path / "song_m.psarc"), "songs/bin/macos/song_lead.sng", "SongKey", "Lead")
    beats, hits = a_beat()
    drums.keep(ref, hits)
    path = drums._cache_file(ref)
    old = json.loads(path.read_text())
    del old["aligned"]  # (as an earlier version kept it)
    path.write_text(json.dumps(old))
    assert drums.kept(ref) == hits  # (without the beats: as it is)
    assert drums.kept(ref, beats=beats) == align(hits, beats)
    assert json.loads(path.read_text())["aligned"] is True and drums.kept(ref) == align(hits, beats)  # (kept so)


def test_making_one_says_what_its_tools_lack(tmp_path):
    ref = ArrangementRef("ID", str(tmp_path / "song_m.psarc"), "songs/bin/macos/song_lead.sng", "SongKey", "Lead")
    with pytest.raises(drums.DrumsError, match="tools/drums-setup.sh"):
        drums.make(ref, [], tmp_path)
    assert not drums.setup_done()
    with pytest.raises(drums.DrumsError, match="game's image"):
        drums.codebooks(tmp_path, tmp_path / "codebooks.bin")


def test_a_drum_part_sounds_on_the_drum_channel_its_hits_at_their_velocity_mapped_to_a_devices_notes():
    keys = FakeKeys()
    track = drum_track([Hit(1.0, KICK, 110), Hit(1.0, CRASH, 70), Hit(1.5, TOMS[1], 90)],
                       DrumVoices(notes={CRASH: 65, TOMS[1]: None}))
    player = Player([track], keys)
    for at in (0.5, 0.9, 1.2, 1.6, 2.0):  # (from before it: played as it goes)
        player.advance(at)
    played = [e for e in keys.events if e[0] == "play"]
    assert played == [("play", 10, (KICK,), 110), ("play", 10, (65,), 70)]  # (the tom: mapped to none)
    assert not any(e[0] == "on" for e in keys.events)  # (no keys lit)


def test_on_a_device_that_chooses_a_drums_sound_its_hits_send_it_first_only_when_it_changes():
    keys = FakeKeys()
    keys.controls = []
    keys.control = lambda channel, number, value: keys.events.append(("control", channel, number, value))
    voices = DrumVoices(notes={TOMS[2]: 65, TOMS[0]: 65, CRASH: 65}, patches={TOMS[2]: (50, 10), TOMS[0]: (50, 12)})
    track = drum_track([Hit(1.0, TOMS[2], 90), Hit(1.25, TOMS[2], 90), Hit(1.5, TOMS[0], 90), Hit(2.0, CRASH, 100),
                        Hit(2.0, TOMS[0], 80)], voices)  # (the crash and a tom on one drum at once: the crash)
    player = Player([track], keys)
    for at in (0.5, 0.9, 1.2, 1.4, 1.6, 1.9, 2.1):
        player.advance(at)
    sent = [e for e in keys.events if e[0] in ("control", "play")]
    assert sent == [("control", 10, 50, 10), ("play", 10, (65,), 90), ("play", 10, (65,), 90),
                    ("control", 10, 50, 12), ("play", 10, (65,), 90), ("play", 10, (65,), 100)]



def test_shared_drums_go_where_their_sound_is_else_where_its_died_away_two_at_once_both_played():
    hat, crash, ride = 42, CRASH, 51
    placed = place([Hit(1.0, crash, 100), Hit(1.0, hat, 80), Hit(1.25, hat, 80), Hit(1.5, TOMS[2], 90),
                    Hit(1.75, TOMS[0], 90), Hit(2.0, ride, 90), Hit(2.0, hat, 70), Hit(2.0, TOMS[1], 60)], (64, 65))
    assert [(h.time, h.note) for h in placed[64]] == [(1.0, crash), (2.0, hat)]
    assert [(h.time, h.note) for h in placed[65]] == [(1.0, hat), (1.25, hat), (1.5, TOMS[2]), (1.75, TOMS[0]),
                                                      (2.0, ride)]
    # (the crash rings on while the hi-hats and toms take turns on the other; three at once: the ride and hi-hat,
    # the tom dropped)


def test_a_part_with_shared_drums_has_a_track_for_each_drum_they_share_choosing_its_sound_as_its_needed():
    keys = FakeKeys()
    keys.control = lambda channel, number, value: keys.events.append(("control", channel, number, value))
    voices = DrumVoices(notes={KICK: 60}, patches={KICK: (8, 1)}, shared={CRASH: 14, TOMS[2]: 15},
                        pool={64: 44, 65: 50})
    tracks = drum_tracks([Hit(1.0, KICK, 110), Hit(1.0, CRASH, 100), Hit(1.0, TOMS[2], 90)], voices)
    assert len(tracks) == 3
    player = Player(tracks, keys)
    for at in (0.5, 1.2):
        player.advance(at)
    sent = sorted(e for e in keys.events if e[0] in ("control", "play"))
    assert sent == [("control", 10, 8, 1), ("control", 10, 44, 14), ("control", 10, 50, 15),
                    ("play", 10, (60,), 110), ("play", 10, (64,), 100), ("play", 10, (65,), 90)]
    assert len(drum_tracks([Hit(1.0, KICK, 110)], DrumVoices())) == 1  # (none shared: one)


def test_a_songs_audio_is_the_one_its_sound_bank_names_wherever_the_bank_is(tmp_path):
    import json
    import struct
    from builders import psarc

    def manifest(key, bank):
        return json.dumps({"Entries": {"ID": {"Attributes": {"SongKey": key, "SongBank": bank}}}}).encode()

    game = tmp_path / "game"
    (game / "dlc").mkdir(parents=True)
    # Rocksmith 1's: the charts in a pack of their own, the bank and audio in songs.psarc, with another song's
    (game / "dlc" / "rs1pack_m.psarc").write_bytes(psarc({"manifests/rs1/pain_lead.json": manifest("Pain", "song_pain.bnk")}))
    (game / "songs.psarc").write_bytes(psarc({
        "audio/mac/song_pain.bnk": b"BKHD" + struct.pack("<I", 111) + b"..",
        "audio/mac/song_other.bnk": b"BKHD" + struct.pack("<I", 222),
        "audio/mac/111.wem": b"pain" * 10, "audio/mac/222.wem": b"other" * 100}))
    # a song of its own, without a bank (its audio and its shorter preview)
    (game / "dlc" / "own_m.psarc").write_bytes(psarc({"audio/mac/1.wem": b"song" * 50, "audio/mac/2.wem": b"pre"}))
    ref = lambda archive, key: ArrangementRef("ID", str(archive), "songs/bin/macos/x_lead.sng", key, "Lead")
    assert drums.song_audio(ref(game / "dlc" / "rs1pack_m.psarc", "Pain"), game) == b"pain" * 10
    assert drums.song_audio(ref(game / "dlc" / "own_m.psarc", "Own"), game) == b"song" * 50
    with pytest.raises(drums.DrumsError, match="No audio found"):  # (songs.psarc's song without its bank: not a guess)
        drums.song_audio(ref(game / "songs.psarc", "Missing"), game)


def test_a_drum_part_on_the_keyboard_has_its_kit_chosen_there_as_it_starts():
    from rs_keylights.parts import player_for
    from rs_keylights.tones import DRUM_SET_BANK
    keys = FakeKeys()
    keys.set_volume = lambda channel, volume: keys.events.append(("volume", channel, volume))
    player_for([], keys, extra=drum_tracks([Hit(1.0, KICK, 110)], DrumVoices(kit=16, volume=90)))
    assert ("voice", 10, 16, DRUM_SET_BANK) in keys.events  # (the Power set, on the drum channel)
    keys.events.clear()
    player_for([], keys, extra=drum_tracks([Hit(1.0, KICK, 110)], DrumVoices(volume=90)))  # (none: as it is)
    assert not any(e[0] == "voice" for e in keys.events)
