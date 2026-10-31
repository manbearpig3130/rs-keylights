from rs_keylights.keyshow import KeyShow
from rs_keylights.lights import Light
from rs_keylights.player import Player, Track

FRAMES = [(0.0, (50, 52)), (1.0, (53,)), (2.0, ())]  # two frames, two seconds


class Keys:
    def __init__(self):
        self.lit, self.presses = set(), 0

    def light(self, keys):
        self.lit |= set(keys)

    def unlight(self, keys):
        self.lit -= set(keys)

    def all_off(self):
        self.lit.clear()

    def pressed(self):
        pressed, self.presses = self.presses > 0, 0
        return pressed


def test_it_repeats_until_a_key_is_pressed():
    keys, show = Keys(), KeyShow(FRAMES)
    keys.presses = 1  # pressed before it began: that doesn't stop it
    show.step(keys, 10.0)
    assert keys.lit == {50, 52}
    show.step(keys, 11.2)
    assert keys.lit == {53}
    show.step(keys, 12.3)  # round again
    assert keys.lit == {50, 52} and not show.done
    keys.presses = 1
    show.step(keys, 12.4)
    assert show.done and keys.lit == set()


def test_once_through_it_ends():
    keys, show = Keys(), KeyShow(FRAMES, repeat=False)
    show.step(keys, 0.0)
    show.step(keys, 2.0)
    assert show.done and keys.lit == set()


def test_it_gives_way_to_a_songs_lights():
    keys = Keys()
    player = Player([Track([Light(5.0, 6.0, (60,))])], keys)  # type: ignore[arg-type]
    show = KeyShow(FRAMES)
    show.step(keys, 0.0, player, song=4.0)  # the song's next light is a second away
    assert keys.lit == {50, 52}
    show.step(keys, 0.5, player, song=4.9)  # less than SHOW_GAP away
    assert show.done and keys.lit == set()
