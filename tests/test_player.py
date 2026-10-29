from rs_keylights.lights import Light
from rs_keylights.player import Player, play


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

    def all_off(self):
        self.events.append(("all off",))
        self.lit.clear()


LIGHTS = [Light(1.0, 1.4, (60,)), Light(1.5, 1.9, (64,)), Light(2.0, 2.5, (60, 67))]


def test_advance_lights_and_unlights_in_time():
    keys = FakeKeys()
    player = Player(LIGHTS, keys)
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
    player = Player(LIGHTS, keys)
    player.advance(1.6)
    assert keys.lit == {64}
    player.advance(2.2)
    player.advance(1.2)  # back to the first note (riff repeat)
    assert keys.lit == {60}
    assert keys.events[-2:] == [("all off",), ("on", (60,))]


def test_a_small_step_back_is_ignored():
    keys = FakeKeys()
    player = Player(LIGHTS, keys)
    player.advance(1.2)
    player.advance(1.15)
    player.advance(1.45)
    assert keys.events == [("all off",), ("on", (60,)), ("off", (60,))]


def test_stop_turns_everything_off():
    keys = FakeKeys()
    player = Player(LIGHTS, keys)
    player.advance(1.2)
    player.stop()
    assert keys.lit == set()
    assert player.position is None


def test_play_runs_to_the_end_at_speed():
    keys = FakeKeys()
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    play(Player(LIGHTS, keys), start=1.0, speed=2.0, clock=lambda: now[0], sleep=sleep)
    assert [e for e in keys.events if e[0] == "on"] == [("on", (60,)), ("on", (64,)), ("on", (60, 67))]
    assert keys.lit == set()
    assert 0.74 <= now[0] <= 0.80  # 1.5 song seconds at double speed
