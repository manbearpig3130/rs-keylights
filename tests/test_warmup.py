from rs_keylights import warmup
from rs_keylights.sng import HAMMER_ON, PALM_MUTE, PULL_OFF
from rs_keylights.songkey import Key
from rs_keylights.warmup import (ACCENT, CLICK, COUNT_IN, DRUM_CHANNEL, E_STANDARD, SHOW_CHANNEL, SLOWEST, Warmup,
                                 routine)


class Keys:
    def __init__(self, clock):
        self.clock, self.events, self.closed, self.voices = clock, [], False, []
        self.lit, self.lights = set(), []  # (the keys lit now, and each one as it lit)

    def set_voice(self, channel, program, bank=None, ours=True, volume=100):
        self.voices.append((channel, program, bank))

    def play(self, channel, notes, velocity=100):
        self.events.append((round(self.clock(), 3), channel, notes[0], velocity))

    def stop(self, channel, notes):
        pass

    def light(self, keys):
        self.lit |= set(keys)
        self.lights.append((round(self.clock(), 3), keys[0]))

    def unlight(self, keys):
        self.lit -= set(keys)

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def run(self, session, until, step=0.005):
        while self.now < until - 1e-9:
            self.now = round(self.now + step, 6)
            session.step(self.now)


def test_each_drill_is_notes_on_the_guitar_in_its_passes_beats():
    for drill in routine():
        assert drill.passes and drill.key in warmup.DRILL_KEYS and drill.grid in (0.25, 0.5)
        assert 0 < drill.demo <= len(drill.passes[0])
        for steps in drill.passes:
            beats = [s.beat for s in steps]
            assert beats == sorted(beats) and len(set(beats)) == len(beats), drill.key
            assert all(0 <= s.beat < s.beat + s.length <= drill.beats for s in steps), drill.key
            assert all(abs(s.beat / drill.grid - round(s.beat / drill.grid)) < 1e-9 for s in steps), drill.key
            assert all(0 <= s.string < 6 and 0 <= s.fret <= 20 and 0 <= s.finger <= 4 for s in steps), drill.key
            assert all((s.fret == 0) == (s.finger == 0) for s in steps), drill.key
    chromatic = warmup.chromatic().passes[0]
    assert len(chromatic) == 48 and [s.fret for s in chromatic[:4]] == [5, 6, 7, 8]
    assert [s.finger for s in chromatic[:4]] == [1, 2, 3, 4] and chromatic[24].string == 5
    legato = warmup.legato().passes[0][:8]
    assert [s.fret for s in legato] == [5, 7, 5, 7, 5, 8, 5, 8]
    assert [s.mask for s in legato] == [0, HAMMER_ON, PULL_OFF, HAMMER_ON, PULL_OFF, HAMMER_ON, PULL_OFF, HAMMER_ON]
    assert all(s.mask == PALM_MUTE and s.string == 0 for p in warmup.gallops().passes for s in p)


def test_the_scale_is_the_songs_pentatonic_from_its_root_on_the_low_string():
    a = warmup.scale_drill()  # (no song: A minor)
    assert a.name == "A minor pentatonic" and a.names == ("A", "C", "D", "E", "G")
    places = [(s.string, s.fret) for s in a.passes[0][:12]]
    assert places == [(0, 5), (0, 8), (1, 5), (1, 7), (2, 5), (2, 7), (3, 5), (3, 7), (4, 5), (4, 8), (5, 5), (5, 8)]
    assert [s.string for s in a.passes[0][12:]] == [5, 5, 4, 4, 3, 3, 2, 2, 1, 1, 0, 0]  # (and back down)
    for key in (Key(4, "minor"), Key(7, "major"), Key(1, "Dorian"), Key(5, "Mixolydian")):
        drill = warmup.scale_drill(key)
        steps = drill.passes[0]
        pitches = [E_STANDARD[s.string] + s.fret for s in steps[:12]]
        assert len(steps) == 24 and pitches == sorted(pitches) and len(set(pitches)) == 12
        assert {p % 12 for p in pitches} == set(drill.scale) and pitches[0] % 12 == key.tonic
        assert max(s.fret for s in steps) <= 17 and drill.scale[0] == key.tonic
    open_e = warmup.scale_drill(Key(4, "minor")).passes[0]
    assert [(s.string, s.fret, s.finger) for s in open_e[:4]] == [(0, 0, 0), (0, 3, 3), (1, 0, 0), (1, 2, 2)]  # (E
    # minor: open position)
    e_major = warmup.scale_drill(Key(4, "major")).passes[0][0]  # (E major's pentatonic: up the neck, its root under
    assert (e_major.fret, e_major.finger) == (12, 2)  # finger 2, not behind the nut)
    assert "major" in warmup.scale_drill(Key(7, "major")).name


def test_speeds_start_slowest_a_click_each_note_then_two_and_four_notes_a_click():
    chromatic, scale = warmup.chromatic(), warmup.scale_drill()
    assert warmup.per_click(chromatic, SLOWEST) == 1 and SLOWEST == 40
    assert [warmup.per_click(chromatic, s) for s in (120, 124, 240, 248, 960)] == [1, 2, 2, 4, 4]
    assert warmup.per_click(scale, 960) == 2 and warmup.fastest(scale) == 480 and warmup.fastest(chromatic) == 960
    speed, seen = SLOWEST, []
    for _ in range(40):  # (faster: 4 clicks a minute a step, the click halving as the notes a click double)
        speed = warmup.stepped(chromatic, speed, 1)
        seen.append((speed, speed // warmup.per_click(chromatic, speed)))
    assert seen[:3] == [(44, 44), (48, 48), (52, 52)] and (120, 120) in seen and (124, 62) in seen
    assert (132, 66) in seen and (244, 61) in seen  # (going up a step at 2 notes a click: 8 notes a minute)
    assert all(60 <= click <= 120 for s, click in seen if s > 120)
    back = [warmup.stepped(chromatic, s, -1) for s in (124, 128, 248, 44, SLOWEST)]
    assert back == [120, 120, 240, SLOWEST, SLOWEST]  # (slower: back where faster came from; never below 40)
    assert all(warmup.stepped(chromatic, warmup.stepped(chromatic, s, 1), -1) == s for s, _c in seen[:-1])
    assert warmup.stepped(chromatic, 960, 1) == 960
    assert warmup.kept_speed(chromatic, 64) == 64 and warmup.kept_speed(chromatic, 63) == SLOWEST
    assert warmup.kept_speed(chromatic, True) == SLOWEST and warmup.kept_speed(scale, 600) == SLOWEST


def test_a_drill_is_read_shown_on_the_keyboards_guitar_then_counted_in_and_played_with_a_click_each_note():
    clock = Clock()
    keys = Keys(clock)
    drills = routine()
    session = Warmup(drills[:2], keys, clock=clock, seconds=6.0, ready=1.0)  # (40 notes a minute: 1.5 s a note)
    assert keys.voices == [(DRUM_CHANNEL, warmup.CLICK_KIT, warmup.DRUM_SET_BANK), (SHOW_CHANNEL, 27, 2),
                           (warmup.SHOW_MUTE_CHANNEL, 28, 2)]
    info = session.info()
    assert info["phase"] == "ready" and info["speed"] == SLOWEST and info["per"] == 1 and info["click"] == 40
    clock.run(session, 100.99)
    assert keys.events == []  # (reading it)
    clock.run(session, 101.0 + 1.5 * 8 - 0.01)  # (its first 8 notes shown, a click with each)
    shown = [e for e in keys.events if e[1] == SHOW_CHANNEL]
    clicks = [e for e in keys.events if e[1] == DRUM_CHANNEL]
    assert [e[2] for e in shown] == [drills[0].pitch(s) for s in drills[0].passes[0][:8]]  # (E 5 6 7 8, A 5 6 7 8)
    assert [e[0] for e in shown] == [e[0] for e in clicks] == [101.0 + 1.5 * i for i in range(8)]
    assert [e[2] for e in clicks] == [ACCENT, CLICK, CLICK, CLICK] * 2  # (each string's first note accented)
    assert session.info()["phase"] == "show"
    clock.run(session, 113.0 + 1.5 * 4)  # (shown: counted in, 4 clicks, then your turn's first note's)
    info = session.info()
    assert info["phase"] == "play" and abs(info["beat"]) < 0.01
    clicks = [e for e in keys.events if e[1] == DRUM_CHANNEL][8:]
    assert [e[0] for e in clicks] == [113.0, 114.5, 116.0, 117.5, 119.0] and clicks[0][2] == ACCENT
    assert len([e for e in keys.events if e[1] == SHOW_CHANNEL]) == 8  # (your turn: nothing played for you)
    clock.run(session, 119.0 + 60.0)  # (its time up, but its pass going on: up the strings, and back down)
    assert session.index == 0 and session.info()["ending"] == 12
    clock.run(session, 119.0 + 72.1)  # (its pass played to its end: on to the next)
    assert session.index == 1 and session.info()["phase"] == "ready"


def test_shown_or_not_and_your_turn_at_once():
    clock = Clock()
    keys = Keys(clock)
    session = Warmup(routine(), keys, shown=False, clock=clock, seconds=30.0, ready=0.5)
    clock.run(session, 100.6)
    assert session.info()["phase"] == "count" and keys.events[0][1] == DRUM_CHANNEL  # (not shown: counted in)
    session.go(4)
    session.shown = True
    clock.run(session, 101.2)
    assert session.info()["phase"] == "show" and keys.events[-1][1] == SHOW_CHANNEL
    session.your_turn()
    assert session.info()["phase"] == "count" and session.info()["beat"] == -COUNT_IN * session.drill.grid


def test_paused_it_stands_still_and_a_speed_changes_from_the_beat_its_on():
    clock = Clock()
    keys = Keys(clock)
    session = Warmup(routine(), keys, {"chromatic": 60}, shown=False, clock=clock, seconds=30.0, ready=0.5)
    clock.run(session, 100.5 + 4 + 2.0)  # (counted in at 60 notes a minute, then two notes)
    beat = session.info()["beat"]
    assert abs(beat - 0.5) < 0.01  # (two sixteenths)
    session.pause()
    clicks = len(keys.events)
    clock.run(session, 120.0)
    assert len(keys.events) == clicks and session.info()["beat"] == beat and session.info()["paused"]
    session.resume()
    assert session.change_speed(1) == 64 and session.speeds["chromatic"] == 64
    clock.run(session, 121.0)
    assert abs(session.info()["beat"] - (beat + 64 * 0.25 / 60)) < 0.01  # (faster, from where it was)


def test_it_goes_on_and_back_and_is_done_after_the_last_drill():
    clock = Clock()
    keys = Keys(clock)
    session = Warmup(routine(), keys, {"gallops": 480}, shown=False, clock=clock, seconds=2.0, ready=0.5)
    session.go(5)
    assert session.drill.key == "gallops" and session.info()["phase"] == "ready"
    assert session.info()["per"] == 4 and session.info()["click"] == 120
    session.go(9)  # (no such drill: where it was)
    assert session.index == 5
    clock.run(session, 100.0 + 0.5 + 2.0 + 4.1)  # (counted in, 4 beats; played 2 s, to its bar's end)
    info = session.info()
    assert session.done and info["phase"] == "done" and info["left"] == 0.0
    session.go(0)  # (done: stays done)
    assert session.done


def test_its_thread_clicks_and_closes_the_keyboard_as_it_stops():
    import time
    keys = Keys(time.monotonic)
    session = Warmup(routine(), keys, {"chromatic": 480}, ready=0.0)
    session.start()
    time.sleep(0.3)
    assert session.running and keys.events and keys.events[0][2] == ACCENT
    session.stop()
    assert not session.running and keys.closed


def test_a_drill_for_the_page_is_its_passes_notes_as_a_songs_are():
    data = warmup.drill_data(warmup.legato(), 132)
    assert data["speed"] == 132 and data["per"] == 2 and data["click"] == 66 and data["grid"] == 0.25
    assert data["beats"] == 12 and data["strings"] == list(E_STANDARD)
    first = data["passes"][0][1]
    assert first == [0.25, 0.5, [[0, 7]], [HAMMER_ON, 0, 0], 3]


def test_each_notes_key_lights_as_it_comes_the_first_one_while_its_read_and_counted_in():
    clock = Clock()
    keys = Keys(clock)
    drill = routine()[0]
    session = Warmup([drill], keys, clock=clock, seconds=30.0, ready=1.0)
    assert keys.lit == {45}  # (reading it: where it starts, E's 5th fret)
    clock.run(session, 101.0 + 1.5 * 2 + 0.1)  # (shown: three notes, 1.5 s apart)
    assert keys.lit == {47} and [k for _t, k in keys.lights][-3:] == [45, 46, 47]
    clock.run(session, 101.0 + 1.5 * 2 + 1.3)  # (out before the next, so the same key again would flash)
    assert keys.lit == set()
    session.your_turn()
    turn = clock.now
    assert keys.lit == {45}  # (counted in: where it starts)
    clock.run(session, turn + 6.0 + 1.5 * 3 + 0.1)  # (your turn: four notes)
    assert keys.lit == {48} and [k for _t, k in keys.lights][-4:] == [45, 46, 47, 48]


def play(clock, session, notes, early=0.0, wrong=(), skip=(), until=None):
    """Plays a session's notes as they're due (each start heard a little after: the click's, your own), some wrong
    or missed."""
    played = []
    end = until if until is not None else clock.now + 20
    while clock.now < end:
        before = len(session._due)
        clock.now = round(clock.now + 0.005, 6)
        session.step(clock.now)
        for due in session._due[before:] if len(session._due) > before else []:
            n = len(played)
            played.append(due)
            if n in skip:
                continue
            session.heard(due.time + warmup.CLICK_HEARD + early, due.pitch + (2 if n in wrong else 0))
    return played


def test_your_notes_are_judged_and_a_clean_round_speeds_it_up_two_sloppy_ones_slow_it_down():
    clock = Clock()
    keys = Keys(clock)
    told = []
    session = Warmup(routine(), keys, {"chromatic": 120}, shown=False, clock=clock, seconds=60.0, ready=0.0,
                     hearing=lambda: True, on_speed=lambda key, speed: told.append((key, speed)))
    clock.run(session, 100.0 + 2.0)  # (counted in: 4 clicks at 120)
    play(clock, session, None, early=0.02, until=100.0 + 2.0 + 0.5 * 8 + 0.6)  # (a round: 8 notes, a little late)
    info = session.info()
    assert info["round"]["verdict"] == "faster" and info["round"]["ok"] == 8 and info["round"]["off_ms"] == 20
    assert [m[2] for m in info["marks"]][:8] == ["ok"] * 8
    clock.run(session, clock.now + 2.1)  # (the next group: faster, and told)
    assert session.speed == 124 and told == [("chromatic", 124)] and session.info()["per"] == 2
    played = play(clock, session, None, wrong=set(range(0, 99, 2)), until=clock.now + 8.0)  # (half wrong)
    assert session.info()["round"]["verdict"] in ("sloppy", "slower") and len(played) >= 16
    verdicts = [m[2] for m in session.info()["marks"]]
    assert "wrong" in verdicts and "ok" in verdicts
    play(clock, session, None, skip=set(range(0, 99, 2)), until=clock.now + 8.0)  # (half missed: sloppy again)
    assert session.speed == 120 and told[-1] == ("chromatic", 120)  # (two sloppy rounds: slower)
    assert "missed" in [m[2] for m in session.info()["marks"]]
    speed = session.speed
    clock.run(session, clock.now + 12.0)  # (nothing played: not judged, no slower)
    assert session.info()["round"]["verdict"] == "nothing heard" and session.speed == speed
    assert session.tallies["chromatic"][0] > 30


def test_not_hearing_the_guitar_nothing_is_judged_and_the_ladder_off_keeps_the_speed():
    clock = Clock()
    keys = Keys(clock)
    session = Warmup(routine(), keys, shown=False, clock=clock, seconds=60.0, ready=0.0)  # (not hearing)
    clock.run(session, 120.0)
    assert session.info()["marks"] == [] and session.info()["round"] is None
    clock = Clock()
    session = Warmup(routine(), Keys(clock), {"chromatic": 120}, shown=False, clock=clock, seconds=60.0, ready=0.0,
                     hearing=lambda: True, ladder=False)
    clock.run(session, 102.0)
    play(clock, session, None, until=clock.now + 4.6)
    assert session.info()["round"]["verdict"] == "clean" and session.speed == 120


def test_any_scale_in_any_tuning_from_its_root_or_a_fret_of_its_own():
    b_standard = warmup.tuning_named("B standard")
    assert b_standard == (35, 40, 45, 50, 54, 59) and warmup.tuning_named("Drop D")[0] == 38
    assert warmup.tuning_named("kazoo") == E_STANDARD
    b_minor = warmup.scale_drill(kind="natural minor", tonic=11, tuning=b_standard)
    up = b_minor.passes[0][:len(b_minor.passes[0]) // 2]
    pitches = [b_minor.pitch(s) for s in up]
    assert b_minor.name == "B natural minor" and b_minor.names == ("B", "C#", "D", "E", "F#", "G", "A")
    assert pitches == sorted(pitches) and len(set(pitches)) == len(pitches) and pitches[0] % 12 == 11
    assert up[0].fret == 0 and up[0].finger == 0  # (B on B standard's low string: open position)
    assert {p % 12 for p in pitches} == set(b_minor.scale) and len(pitches) >= 15  # (two octaves, and more)
    for steps in warmup.SCALES.values():  # (every scale, every key, in every tuning: no note left out)
        for tonic in range(12):
            for _name, tuning in warmup.TUNINGS:
                places, start = warmup.scale_shape(steps, tonic, tuning)
                heard = [tuning[s] + f for s, f, _ in places]
                assert heard == sorted(heard) and len(set(heard)) == len(heard)
                scale = sorted({(tonic + step) % 12 for step in steps})
                lowest, highest = heard[0], heard[-1]
                assert [p for p in range(lowest, highest + 1) if p % 12 in scale] == heard  # (none missed)
                assert all(1 <= finger <= 4 or fret == 0 for _s, fret, finger in places)
    g_major, start = warmup.scale_shape(warmup.SCALES["major"], 7)
    assert start == 2 and [f for s, f, _ in g_major if s == 0] == [2, 3, 5]  # (G's root under finger 2)
    assert [finger for s, _f, finger in g_major if s == 0] == [1, 2, 4]
    drop_d, _ = warmup.scale_shape(warmup.SCALES["minor pentatonic"], 2, warmup.tuning_named("Drop D"))
    assert drop_d[0] == (0, 12, 1)  # (D on Drop D: the low string's root under finger 1, as E standard's box)
    assert warmup.scale_shape(warmup.SCALES["minor pentatonic"], 4, warmup.tuning_named("Drop D"))[0][0] == (0, 2, 1)
    at_7, start = warmup.scale_shape(warmup.SCALES["minor pentatonic"], 9, start=7)
    assert start == 7 and all(7 <= f <= 11 for _s, f, _fi in at_7)  # (a fret of its own: A minor's 2nd shape)


def test_a_drill_on_its_own_in_its_tuning_goes_on_until_its_stopped():
    drills = routine(tuning=warmup.tuning_named("B standard"), what="spider")
    assert [d.key for d in drills] == ["spider"] and drills[0].tuning[0] == 35
    assert [d.key for d in routine(what="kazoo")] == list(warmup.DRILL_KEYS)  # (no such drill: all of them)
    scale = routine(scale={"kind": "major", "tonic": 0, "start": 7}, what="scale")[0]
    assert scale.name == "C major" and scale.passes[0][0].fret >= 7
    clock = Clock()
    keys = Keys(clock)
    session = Warmup(drills, keys, {"spider": 480}, shown=False, clock=clock, seconds=1.0, ready=0.0,
                     until_stopped=True)
    clock.run(session, 100.0 + 60.0)
    info = session.info()
    assert not session.done and info["phase"] == "play" and info["left"] is None and info["drill_left"] is None
    assert info["beat"] > 3 * drills[0].beats  # (round and round)
    assert min(keys.lit | {99}) >= 36  # (B standard's lowest, lit up an octave: the keys there are)
