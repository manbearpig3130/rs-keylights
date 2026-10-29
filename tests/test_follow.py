import json

import pytest

from rs_keylights.follow import Follower, GameState, OverlayError, SongClock, parse_state
from rs_keylights.library import ArrangementRef
from rs_keylights.sng import Arrangement, Note


def test_parse_state_reads_the_readout_before_the_song_details():
    head = {"memoryReadout": {"songTimer": 12.5, "songID": "AmonPurs", "arrangementID": "4DC3"}, "currentState": 4}
    body = json.dumps(head)[:-1].encode() + b',"songDetails":{"albumArt":"' + b"x" * 1000 + b'"}}'
    assert parse_state(body) == GameState(4, 12.5, "AmonPurs", "4DC3")
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
        self.tones = []

    def set_tone(self, program, bank=None):
        self.tones.append((program, bank))


def test_with_sound_each_song_gets_its_tone_and_the_trace_records_the_polls():
    from rs_keylights.tones import tone

    client, index, keys, messages, trace = FakeClient(), FakeIndex([REF]), FakeSoundKeys(), [], []
    f = Follower(client, index, keys, status=messages.append, load=lambda ref: NOTES,  # type: ignore[arg-type]
                 tone_for=lambda ref: tone(178), trace=trace.append)
    client.state = GameState(4, 9.8, "X", "ID1")
    f.step(0.0)
    assert keys.tones == [(81, 2)]
    assert messages[-1] == "Artist - Song (Lead): 2 notes, tone 178 SAW LEAD 1"
    assert trace[0] == {"t": 0.0, "state": 4, "timer": 9.8, "song": "X", "arrangement": "ID1", "estimate": None}
    assert trace[1] == {"t": 0.0, "playing": 9.8, "rate": 1.0}
    f.step(1.0)  # the timer hasn't moved for a second: paused
    assert {"t": 1.0, "stopped": True} in trace
