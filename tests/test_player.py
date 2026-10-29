from rs_keylights.lights import Light
from rs_keylights.player import Player, Track, play


class FakeKeys:
    def __init__(self):
        self.events = []
        self.lit = set()

    def light(self, keys):
        self.events.append(("on", keys))
        self.lit |= set(keys)

    def unlight(self, keys):
        self.events.append(("off", keys))
        self.lit -= set(keys)

    def play(self, channel, notes, velocity):
        self.events.append(("play", channel, notes, velocity))

    def stop(self, channel, notes):
        self.events.append(("stop", channel, notes))

    def all_off(self):
        self.events.append(("all off",))
        self.lit.clear()


LIGHTS = [Light(1.0, 1.4, (60,)), Light(1.5, 1.9, (64,)), Light(2.0, 2.5, (60, 67))]


def test_advance_lights_and_unlights_in_time():
    keys = FakeKeys()
    player = Player([Track(LIGHTS)], keys)
    player.advance(0.5)
    assert keys.events == [("all off",)]
    player.advance(1.2)
    player.advance(1.45)
    assert keys.events[1:] == [("on", (60,)), ("off", (60,))]
    player.advance(2.1)
    assert keys.lit == {60, 67}
    assert player.next_time() == 2.5


def test_starting_or_seeking_mid_note_lights_it():
    keys = FakeKeys()
    player = Player([Track(LIGHTS)], keys)
    player.advance(1.6)
    assert keys.lit == {64}
    player.advance(2.2)
    player.advance(1.2)  # back to the first note (riff repeat)
    assert keys.lit == {60}
    assert keys.events[-2:] == [("all off",), ("on", (60,))]


def test_a_small_step_back_is_ignored():
    keys = FakeKeys()
    player = Player([Track(LIGHTS)], keys)
    player.advance(1.2)
    player.advance(1.15)
    player.advance(1.45)
    assert keys.events == [("all off",), ("on", (60,)), ("off", (60,))]


def test_stop_turns_everything_off():
    keys = FakeKeys()
    player = Player([Track(LIGHTS)], keys)
    player.advance(1.2)
    player.stop()
    assert keys.lit == set()
    assert player.position is None


def test_play_runs_to_the_end_at_speed():
    keys = FakeKeys()
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    play(Player([Track(LIGHTS)], keys), start=1.0, speed=2.0, clock=lambda: now[0], sleep=sleep)
    assert [e for e in keys.events if e[0] == "on"] == [("on", (60,)), ("on", (64,)), ("on", (60,)), ("on", (67,))]
    assert keys.lit == set()
    assert 0.74 <= now[0] <= 0.80  # 1.5 song seconds at double speed


class FakeVoices:
    def pick(self, light):
        return (13 if light.keys == (64,) else 11), light.keys, 100


def test_with_voices_each_light_plays_and_stops_its_sound():
    keys = FakeKeys()
    player = Player([Track(LIGHTS, FakeVoices())], keys)
    player.advance(1.2)  # starts mid-note: lit and sounding
    player.advance(1.45)
    player.advance(1.6)
    assert keys.events == [("all off",), ("on", (60,)), ("play", 11, (60,), 100), ("off", (60,)), ("stop", 11, (60,)),
                           ("on", (64,)), ("play", 13, (64,), 100)]
    player.stop()
    assert keys.events[-1] == ("all off",)
    player.advance(1.95)  # nothing left sounding to stop from before
    assert ("stop", 13, (64,)) not in keys.events


def test_parts_together_one_lit_and_both_sounding():
    keys = FakeKeys()
    rhythm = [Light(1.0, 2.0, (48,))]
    player = Player([Track(LIGHTS, FakeVoices()), Track(rhythm, FakeVoices(), lit=False)], keys)
    player.advance(0.5)
    player.advance(1.2)
    assert ("on", (60,)) in keys.events and ("on", (48,)) not in keys.events  # the rhythm isn't lit
    assert ("play", 11, (60,), 100) in keys.events and ("play", 11, (48,), 100) in keys.events
    player.advance(2.1)
    assert ("stop", 11, (48,)) in keys.events
    player.advance(0.5)  # back to the start: everything off at once
    assert keys.events[-1] == ("all off",)


def test_a_light_that_ends_as_it_starts_is_never_lit():
    keys = FakeKeys()
    player = Player([Track([Light(1.0, 1.0, (60,)), Light(1.5, 1.9, (64,))])], keys)
    player.advance(0.5)
    player.advance(1.2)
    player.advance(2.0)
    assert keys.lit == set()
    assert ("on", (60,)) not in keys.events


def test_two_parts_on_the_same_key_light_it_as_one():
    keys = FakeKeys()
    lead = [Light(1.0, 1.2, (60,)), Light(1.3, 1.5, (60,))]
    rhythm = [Light(1.01, 1.27, (60,)), Light(1.31, 1.6, (60,))]  # a few ms off the lead, held a bit longer
    player = Player([Track(lead), Track(rhythm)], keys)
    for t in (0.5, 1.05, 1.28, 1.35, 1.55, 1.65):
        player.advance(t)
    # One strike per pair: lit at 1.0, dark before the second strike, lit again until the longer note ends
    assert keys.events == [("all off",), ("on", (60,)), ("off", (60,)), ("on", (60,)), ("off", (60,))]


def test_retime_makes_the_keys_timeline_again_and_goes_on_from_where_it_is():
    keys = FakeKeys()
    fast = [Light(i * 0.05, i * 0.05 + 0.04, (60,)) for i in range(8)]
    player = Player([Track(fast)], keys)
    assert len(player.display) == 4  # too fast to see each: every other one
    player.advance(0.12)
    player.retime(rate=0.5)  # half speed: every one flashes
    assert len(player.display) == 8 and player.position == 0.12
    assert keys.events[-2:] == [("all off",), ("on", (60,))]  # re-lit where it is (the note at 0.1)
