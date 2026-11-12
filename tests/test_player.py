import math
from dataclasses import replace

from rs_keylights.lights import Light
from rs_keylights.player import Player, Track, play


class FakeKeys:
    def __init__(self):
        self.events = []
        self.lit = set()

    def set_voice(self, channel, program, bank=None, ours=True, volume=100):
        self.events.append(("voice", channel, program, bank))

    def bend(self, channel, semitones):
        self.events.append(("bend", channel, round(semitones, 2)))

    def modulate(self, channel, depth):
        self.events.append(("vibrato", channel, depth))

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
    def __init__(self, ring=True, techniques=True):
        self.ring, self.volume, self.techniques = ring, 100, techniques

    def pick(self, light):
        return (13 if light.keys == (64,) else 11), light.keys, 100

    def notes_for(self, light):
        return light.keys or light.pitches  # (an extra sound: no keys)


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


def test_each_note_sounds_as_long_as_it_rings():
    keys = FakeKeys()
    chord = Light(1.0, 1.1, (40, 47, 52), rings=(3.0, 2.0, 3.0))  # its light short; its second string played again
    again = Light(2.0, 2.1, (47,), rings=(2.5,))
    player = Player([Track([chord, again], FakeVoices(), lit=False)], keys)
    player.advance(0.5)
    player.advance(1.5)
    assert keys.events[-1] == ("play", 11, (40, 47, 52), 100)  # (its light off: still sounding)
    player.advance(2.2)
    assert keys.events[-2:] == [("stop", 11, (47,)), ("play", 11, (47,), 100)]  # (just it, played again)
    player.advance(2.7)
    player.advance(3.1)
    assert keys.events[-2:] == [("stop", 11, (47,)), ("stop", 11, (40, 52))]
    player.seek(1.5)  # from there: the notes still sounding there
    assert keys.events[-1] == ("play", 11, (40, 47, 52), 100)
    keys.events.clear()
    player = Player([Track([chord, again], FakeVoices(ring=False), lit=False)], keys)  # not letting them ring
    player.advance(0.5)
    player.advance(1.5)
    assert keys.events[-1] == ("stop", 11, (40, 47, 52))  # (as its light)


def test_a_note_sounding_twice_at_once_stops_once_both_are_done():
    keys = FakeKeys()
    low, high = Light(1.0, 1.1, (48,), rings=(3.0,)), Light(1.5, 1.6, (48,), rings=(2.0,))  # (two strings, a note)
    player = Player([Track([low, high], FakeVoices(), lit=False)], keys)
    player.advance(0.5)
    player.advance(2.5)
    assert ("stop", 11, (48,)) not in keys.events
    player.advance(3.1)
    assert keys.events[-1] == ("stop", 11, (48,))


def test_a_parts_tone_switches_as_the_song_does():
    keys = FakeKeys()
    changes = [(-math.inf, 11, 75, 0), (5.0, 11, 79, 0), (9.0, 11, 75, 0)]  # clean, distortion at 5, clean at 9
    player = Player([Track([Light(1.0, 1.1, (60,))], FakeVoices(), lit=False, changes=changes)], keys)
    player.advance(0.5)
    assert ("voice", 11, 75, 0) in keys.events  # (from the start: as it starts)
    player.advance(5.5)
    assert keys.events[-1] == ("voice", 11, 79, 0)
    player.seek(10.0)  # moved: the tone there
    assert ("voice", 11, 75, 0) in keys.events[-3:]
    player.seek(6.0)
    assert ("voice", 11, 79, 0) in keys.events[-3:]


def test_bends_and_vibrato_on_the_notes_channel_and_extras_sounding_unlit():
    keys = FakeKeys()
    bent = Light(1.0, 1.1, (60,), (60,), rings=(2.0,), bends=((1.5, 2.0),), vibrato=True)
    step = Light(2.5, 2.5, (), (61,), rings=(2.8,), extra=True)
    player = Player([Track([bent], FakeVoices(), extras=[step])], keys)
    player.advance(0.5)
    player.advance(1.2)
    assert ("vibrato", 11, 64) in keys.events and not [e for e in keys.events if e[0] == "bend"]  # (held, then
    # bent, as it nears the bend's point)
    player.advance(1.6)
    assert keys.events[-1] == ("bend", 11, 2.0)
    player.advance(2.1)
    assert keys.events[-3] == ("stop", 11, (60,)) and set(keys.events[-2:]) == {("bend", 11, 0.0), ("vibrato", 11, 0)}
    # (back as it stops)
    player.advance(2.6)
    assert keys.events[-1] == ("play", 11, (61,), 100) and (61,) not in [e[1] for e in keys.events if e[0] == "on"]
    player.seek(1.7)  # into the bend: bent again there
    assert ("bend", 11, 2.0) in keys.events[-4:] and ("vibrato", 11, 64) in keys.events[-4:]
    keys = FakeKeys()
    player = Player([Track([bent], FakeVoices(techniques=False))], keys)  # (techniques off: none)
    player.advance(0.5)
    player.advance(2.1)
    assert not [e for e in keys.events if e[0] in ("bend", "vibrato")]


def test_a_part_can_sound_on_another_device_its_lights_on_the_keyboard():
    keys, synth = FakeKeys(), FakeKeys()
    player = Player([Track([Light(1.0, 1.5, (40,), (40,), rings=(1.5,))], FakeVoices(), sound=synth)], keys)
    player.advance(0.5)
    player.advance(1.2)
    assert ("on", (40,)) in keys.events and ("play", 11, (40,), 100) in synth.events  # (lit here, sounding there)
    assert not [e for e in keys.events if e[0] == "play"]
    player.advance(1.6)
    assert synth.events[-1] == ("stop", 11, (40,))
    player.stop()
    assert keys.events[-1] == ("all off",) and synth.events[-1] == ("all off",)


def test_a_strummed_chords_strings_sound_one_after_another():
    chord = Light(1.0, 1.5, (40, 47, 52), (40, 47, 52), rings=(2.0, 2.0, 2.0), strum=(0.0, 0.01, 0.02))

    def sounds(*positions):
        keys = FakeKeys()
        player = Player([Track([chord], FakeVoices())], keys)
        for position in positions:
            player.advance(position)
        return [e for e in keys.events if e[0] in ("play", "stop")]

    assert sounds(0.9, 1.0, 1.015, 1.03, 2.01) == [("play", 11, (40,), 100), ("play", 11, (47,), 100),
                                                  ("play", 11, (52,), 100), ("stop", 11, (40, 47, 52))]
    assert sounds(1.015, 1.03, 2.01) == [("play", 11, (40, 47), 100), ("play", 11, (52,), 100),
                                        ("stop", 11, (40, 47, 52))]  # (from mid-strum: those started, then the rest)
    short = Light(1.0, 1.015, (40, 47, 52), (40, 47, 52), strum=(0.0, 0.01, 0.02))  # (not ringing: as lit)
    keys = FakeKeys()
    player = Player([Track([short], FakeVoices(ring=False))], keys)
    for position in (0.9, 1.0, 1.01, 1.02, 1.1):
        player.advance(position)
    assert [e for e in keys.events if e[0] in ("play", "stop")] == [
        ("play", 11, (40,), 100), ("play", 11, (47,), 100), ("stop", 11, (40, 47))]  # (the last never starts)


def test_a_pinch_harmonics_squeal_scoops_up_into_its_pitch_with_a_wide_vibrato():
    from rs_keylights.player import SCOOP, SCOOP_TIME, SQUEAL_GROWS, SQUEAL_VIBRATO, SQUEAL_WIDTH
    from rs_keylights.sng import PINCH_HARMONIC
    squeal = Light(1.0, 1.0, (), (71,), PINCH_HARMONIC, rings=(2.0,), vibrato=True, extra=True)

    def controls(voices, light=squeal):
        keys = FakeKeys()
        player = Player([Track([], voices, extras=[light])], keys)
        position = 0.9
        while position < 2.1:
            player.advance(position)
            position += 0.01
        return [e for e in keys.events if e[0] in ("vibrato", "bend", "play")]

    own = FakeVoices()
    own.squeal = object()  # (a channel of its own)
    events = controls(own)
    bends = [e[2] for e in events if e[0] == "bend"]
    assert events[0] == ("bend", 11, -SCOOP) and events[1][0] == "play"  # (flat as it starts: the attack at once)
    assert bends[len(bends) // 2] != 0 and bends[-1] == 0  # (the vibrato going on, none as it ends)
    later = bends[round((SCOOP_TIME + SQUEAL_GROWS) / 0.02) + 1:-1]
    assert round(max(later), 1) == SQUEAL_WIDTH and round(min(later), 1) == -SQUEAL_WIDTH  # (wide, either way)
    assert not [e for e in events if e[0] == "vibrato"]  # (pitch bend: not the chart's Modulation)
    bent = replace(squeal, bends=((1.5, 2.0),))  # (the chart's bend: a whole step, the vibrato over it)
    assert max(e[2] for e in controls(own, bent) if e[0] == "bend") > 2.0
    vibrato = [e[2] for e in controls(FakeVoices()) if e[0] == "vibrato"]  # (on the note's channel: Modulation)
    assert vibrato[0] == 0 and SQUEAL_VIBRATO in vibrato and vibrato[-1] == 0
    assert not [e for e in controls(FakeVoices()) if e[0] == "bend"]
