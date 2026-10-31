import json

import pytest

from rs_keylights.follow import Follower, GameState, OverlayError, SongClock, parse_state
from rs_keylights.library import ArrangementRef
from rs_keylights.sng import Arrangement, Note


def test_parse_state_reads_the_readout_before_the_song_details():
    head = {"memoryReadout": {"songTimer": 12.5, "songID": "AmonPurs", "arrangementID": "4DC3", "gameStage": "las_game"},
            "currentState": 4}
    body = json.dumps(head)[:-1].encode() + b',"songDetails":{"albumArt":"' + b"x" * 1000 + b'"}}'
    assert parse_state(body) == GameState(4, 12.5, "AmonPurs", "4DC3", "las_game")
    assert parse_state(b'{"memoryReadout":{"songTimer":0,"songID":"","arrangementID":""},"currentState":0,'
                       b'"songDetails":null}') == GameState(0, 0.0, "", "")
    with pytest.raises(OverlayError):
        parse_state(b"<html>")


def test_clock_uses_the_freshest_sample():
    clock = SongClock()
    clock.update(10.0, now=100.08)  # seen 80 ms late
    clock.update(10.25, now=100.26)  # seen 10 ms late
    assert clock.position(100.3) == pytest.approx(10.29)


def test_clock_stops_when_the_timer_stops_and_restarts_after_a_pause():
    clock = SongClock()
    for i in range(8):
        clock.update(5.0 + i * 0.25, now=50.0 + i * 0.25)
    assert clock.position(52.0) == pytest.approx(7.0)
    assert clock.position(52.5) is None  # no new value for 0.75 s: paused
    clock.update(6.8, now=60.0)  # resumed, a little before where it paused (the game rewinds)
    assert clock.position(60.1) == pytest.approx(6.9)


def test_clock_goes_dark_while_the_timer_goes_back_and_follows_from_where_it_lands():
    clock = SongClock()
    clock.update(30.0, now=10.0)
    clock.update(30.25, now=10.25)
    clock.update(12.0, now=10.5)  # back to an earlier section
    assert clock.position(10.6) is None
    clock.update(12.25, now=10.75)
    assert clock.position(10.85) == pytest.approx(12.35)


def test_clock_jumps_ahead_with_a_seek():
    clock = SongClock()
    clock.update(30.0, now=10.0)
    clock.update(30.25, now=10.25)
    clock.update(50.0, now=10.5)
    assert clock.position(10.6) == pytest.approx(50.1)


def play(clock, start, rate, seconds, now, step=0.25):
    timer = start
    for _ in range(round(seconds / step)):
        now += step
        timer += rate * step
        clock.update(round(timer, 3), now)
    return timer, now


def test_clock_is_dark_during_riff_repeaters_rewind():
    clock = SongClock()
    timer, now = play(clock, 40.0, 1.0, 3.0, 0.0)
    for _ in range(6):  # rewinding 1.25 s per update
        now += 0.25
        timer -= 1.25
        clock.update(timer, now)
        assert clock.position(now + 0.05) is None
    timer, now = play(clock, timer, 1.0, 0.25, now)
    assert clock.position(now + 0.05) == pytest.approx(timer + 0.05)


def test_clock_goes_dark_as_soon_as_a_pause_shows():
    clock = SongClock()
    timer, now = play(clock, 10.0, 1.0, 3.0, 0.0)
    clock.update(timer + 0.02, now + 0.25)  # stopped 20 ms after the last update
    assert clock.position(now + 0.3) is None
    clock.update(timer + 0.27, now + 0.5)  # not a pause after all
    assert clock.position(now + 0.55) == pytest.approx(timer + 0.32, abs=0.01)


def test_clock_takes_a_much_slower_speed_after_three_updates_dark_until_then():
    clock = SongClock()
    timer, now = play(clock, 10.0, 1.0, 3.0, 0.0)
    timer, now = play(clock, timer, 0.3, 0.5, now)
    assert clock.position(now + 0.05) is None  # very short steps: maybe pausing
    timer, now = play(clock, timer, 0.3, 0.25, now)
    assert clock.rate == pytest.approx(0.3, abs=0.01)
    assert clock.position(now + 0.1) == pytest.approx(timer + 0.03, abs=0.01)


def test_clock_takes_a_much_faster_speed_after_three_updates_lit_all_along():
    clock = SongClock()
    timer, now = play(clock, 10.0, 0.34, 4.0, 0.0)
    assert clock.rate == pytest.approx(0.34, abs=0.01)
    timer, now = play(clock, timer, 1.0, 0.5, now)
    assert clock.position(now + 0.05) is not None
    timer, now = play(clock, timer, 1.0, 0.25, now)
    assert clock.rate == pytest.approx(1.0)


def test_clock_takes_a_moderately_slower_speed_within_two_seconds():
    clock = SongClock()
    timer, now = play(clock, 10.0, 0.8, 1.75, 0.0)
    assert clock.rate == pytest.approx(0.8, abs=0.01)


def test_clock_measures_a_slower_song_speed():
    clock = SongClock()
    for i in range(25):
        clock.update(i * 0.2, now=i * 0.25)  # Riff Repeater at 80%
    assert clock.rate == pytest.approx(0.8)
    assert clock.position(6.1) == pytest.approx(4.8 + 0.8 * 0.1)


def test_clock_rate_stays_1_with_late_samples():
    clock = SongClock()
    lateness = [0.0, 0.07, 0.03, 0.09, 0.01, 0.05]
    for i in range(30):
        clock.update(i * 0.25, now=i * 0.25 + lateness[i % 6])
    assert clock.rate == 1.0


class FakeClient:
    def __init__(self):
        self.state: GameState | Exception = OverlayError("down")

    def read(self):
        if isinstance(self.state, Exception):
            raise self.state
        return self.state


class FakeIndex:
    def __init__(self, refs):
        self.refs = {}
        self.pending = {r.persistent_id: r for r in refs}
        self.refreshes = 0

    def find(self, persistent_id):
        return self.refs.get(persistent_id)

    def refresh(self, progress=None):
        self.refreshes += 1
        self.refs.update(self.pending)
        return len(self.pending)


class FakeKeys:
    def __init__(self):
        self.lit = set()
        self.events = []

    def light(self, keys):
        self.lit |= set(keys)
        self.events.append(("on", keys))

    def unlight(self, keys):
        self.lit -= set(keys)

    def all_off(self):
        self.lit.clear()


REF = ArrangementRef("ID1", "/x.psarc", "songs/bin/macos/x_lead.sng", "X", "Lead", "Song", "Artist")
NOTES = Arrangement((Note(10.0, 1.0, (60,)), Note(11.0, 0.0, (64,))), (0,) * 6, -1, 60.0)


def follower(client, index, keys, messages):
    return Follower(client, index, keys, status=messages.append, load=lambda ref: NOTES)  # type: ignore[arg-type]


def test_follows_the_song_and_its_timer():
    client, index, keys, messages = FakeClient(), FakeIndex([REF]), FakeKeys(), []
    f = follower(client, index, keys, messages)
    f.step(0.0)
    assert messages == ["Waiting for RS Companion's overlay API (Settings > Stream overlays)"]

    client.state = GameState(4, 9.8, "X", "ID1")
    f.step(1.0)  # the arrangement isn't indexed yet: refresh, then found
    assert index.refreshes == 1
    assert messages[1:] == ["Connected to RS Companion", "Looking for new songs...", "Artist - Song (Lead): 2 notes"]
    client.state = GameState(4, 10.05, "X", "ID1")
    f.step(1.25)
    assert keys.lit == {60}

    for i in range(1, 5):  # the timer runs on
        client.state = GameState(4, 10.05 + i * 0.25, "X", "ID1")
        f.step(1.25 + i * 0.25)
    assert keys.lit == {64}

    f.step(3.5)  # the timer stopped: paused
    assert keys.lit == set()

    client.state = GameState(1, 11.05, "", "")  # back in the menus
    f.step(3.6)
    assert f._player is None


def test_unknown_arrangement_is_reported_once():
    client, index, keys, messages = FakeClient(), FakeIndex([]), FakeKeys(), []
    f = follower(client, index, keys, messages)
    client.state = GameState(4, 5.0, "Song", "NOPE")
    f.step(0.0)
    f.step(0.2)
    assert messages[-1] == "Song Song: arrangement NOPE isn't in the game's archives"
    assert index.refreshes == 1


def test_offset_lights_earlier():
    client, index, keys, messages = FakeClient(), FakeIndex([REF]), FakeKeys(), []
    f = follower(client, index, keys, messages)
    f.offset = 0.1
    client.state = GameState(4, 9.95, "X", "ID1")
    f.step(0.0)
    assert keys.lit == {60}


class FakeSoundKeys(FakeKeys):
    def __init__(self):
        super().__init__()
        self.voices = []
        self.played = []

    def set_voice(self, channel, program, bank=None, volume=100):
        self.voices.append((channel, program, bank))

    def play(self, channel, notes, velocity):
        self.played.append((channel, notes, velocity))

    def stop(self, channel, notes):
        pass


def test_with_sound_each_song_gets_its_voices_and_the_trace_records_the_polls():
    from rs_keylights.sound import Voice, Voices
    from rs_keylights.tones import tone

    client, index, keys, messages, trace = FakeClient(), FakeIndex([REF]), FakeSoundKeys(), [], []
    voices = Voices(Voice(11, tone(178)), mute=Voice(13, tone(77)))
    f = Follower(client, index, keys, status=messages.append, load=lambda ref: NOTES,  # type: ignore[arg-type]
                 voices_for=lambda ref, channels: voices, trace=trace.append)
    client.state = GameState(4, 9.8, "X", "ID1")
    f.step(0.0)
    assert keys.voices == [(11, 81, 2), (13, 28, 2)]
    assert messages[-1] == "Artist - Song (Lead): 2 notes; tone 178 SAW LEAD 1, palm mutes 077 MUTE GUITAR"
    assert trace[0] == {"t": 0.0, "state": 4, "stage": "", "timer": 9.8, "song": "X", "arrangement": "ID1",
                        "estimate": None}
    assert trace[1] == {"t": 0.0, "playing": 9.8, "rate": 1.0}
    f.step(1.0)  # the timer hasn't moved for a second: paused
    assert {"t": 1.0, "stopped": True} in trace


def test_other_tones_go_on_from_where_the_song_is():
    from rs_keylights.sound import Voice, Voices
    from rs_keylights.tones import tone

    client, index, keys, messages, loads = FakeClient(), FakeIndex([REF]), FakeSoundKeys(), [], []
    f = Follower(client, index, keys, status=messages.append,  # type: ignore[arg-type]
                 load=lambda ref: loads.append(ref) or NOTES, voices_for=lambda ref, channels: Voices(Voice(11, tone(178))))
    client.state = GameState(4, 10.05, "X", "ID1")
    f.step(0.0)
    assert keys.lit == {60} and keys.played == [(11, (60,), 100)]
    f.set_parts(lambda ref, channels: Voices(Voice(11, tone(178))), None)  # the same voices: nothing to do
    assert keys.voices == [(11, 81, 2)] and keys.lit == {60}
    f.set_parts(lambda ref, channels: Voices(Voice(11, tone(77))), None)
    assert keys.voices[-1] == (11, 28, 2)
    assert messages[-1] == "Artist - Song (Lead): tone 077 MUTE GUITAR"
    f.step(0.05)  # no new timer value: the song goes on where it was, the note again in the new tone
    assert keys.lit == {60} and keys.played[-1] == (11, (60,), 100) and len(keys.played) == 2
    f.set_parts(None, None)
    assert messages[-1] == "Artist - Song (Lead): lights only"
    f.step(0.1)
    assert keys.lit == {60} and len(keys.played) == 2 and len(loads) == 1


def test_other_arrangements_play_along_and_can_change_while_the_song_plays():
    from rs_keylights.sound import SoundChoice
    from rs_keylights.tones import tone

    bass = ArrangementRef("ID2", "/x.psarc", "songs/bin/macos/x_bass.sng", "X", "Bass", "Song", "Artist")
    bass_notes = Arrangement((Note(10.0, 1.0, (40,)),), (0,) * 4, -1, 60.0)
    client, index, keys, messages, loads = FakeClient(), FakeIndex([REF, bass]), FakeSoundKeys(), [], []
    choice = SoundChoice({None: tone(178)}, {}, {}, {}, False)
    chosen = {"ID1": 4}
    f = Follower(client, index, keys, status=messages.append,  # type: ignore[arg-type]
                 load=lambda ref: loads.append(ref.persistent_id) or (bass_notes if ref is bass else NOTES),
                 voices_for=choice.voices, parts_for=lambda played: [(r, chosen[r.persistent_id])
                                                                     for r in (REF, bass) if r.persistent_id in chosen])
    client.state = GameState(4, 10.05, "X", "ID1")
    f.step(0.0)
    assert messages[-1] == "Artist - Song (Lead): 2 notes; tone 178 SAW LEAD 1"
    assert keys.played == [(11, (60,), 100)] and loads == ["ID1"]

    chosen.update(ID1=2, ID2=2)  # the bass along, on channels of its own, two keys each
    f.set_parts(choice.voices, f._parts_for)
    assert messages[-1] == ("Artist - Song (Lead): Lead lighting 2 keys, tone 178 SAW LEAD 1; "
                            "Bass lighting 2 keys, tone 178 SAW LEAD 1")
    assert (14, 81, 2) in keys.voices
    f.step(0.05)
    assert keys.lit == {60, 40} and sorted(keys.played[1:]) == [(11, (60,), 100), (14, (40,), 100)]

    del chosen["ID1"]  # just the bass, and its notes aren't read again
    f.set_parts(choice.voices, f._parts_for)
    f.step(0.06)
    assert keys.lit == {40} and loads == ["ID1", "ID2"]
    assert messages[-1] == "Artist - Song (Lead): Bass lighting 2 keys, tone 178 SAW LEAD 1"


def test_a_show_on_the_keys_gives_way_to_the_songs_lights():
    client, index, keys, messages = FakeClient(), FakeIndex([REF]), FakeKeys(), []
    f = follower(client, index, keys, messages)
    client.state = GameState(4, 9.0, "X", "ID1")  # a second before the first note, at 10.0
    f.step(0.0)
    f.show([(0.0, (50, 52)), (0.5, (53,)), (3.0, ())])
    f.step(0.01)
    assert keys.lit == {50, 52}
    client.state = GameState(4, 9.6, "X", "ID1")
    f.step(0.6)
    assert keys.lit == {53}
    client.state = GameState(4, 9.9, "X", "ID1")
    f.step(0.9)  # the first note's light is less than SHOW_GAP away: the show is over
    assert keys.lit == set()
    client.state = GameState(4, 10.0, "X", "ID1")
    f.step(1.0)
    assert keys.lit == {60}
    f.show([(0.0, (50,)), (1.0, ())])  # the song's key is lit: no room for a show
    f.step(1.01)
    assert keys.lit == {60}
