import math

import rs_keylights.watch as watch_module

from rs_keylights.bitmap import (arrangements_picture, keys_picture, lyrics_picture, mixer_picture, parts_picture,
                                 pedal_picture, playback_picture, song_picture, with_note)
from rs_keylights.launchkey import PLAY, RECORD, Button, Pad, ScaleMode, Strip, Turn
from rs_keylights.lyrics import lyric_lines
from rs_keylights.sng import Chord, Syllable
from rs_keylights.tones import DRUM_SETS, tone
from rs_keylights.watch import (BROWSE_SHOWN, CONTROLS, GUITAR_COLUMN, KEYS_COLUMN, LOOP_KNOBS, NOT_FOLLOWING_KEYS, PAD_WAIT,
                                PEDAL_WAIT, PICTURE_GAP, PICTURE_SHOWN, SECTION_KNOB, SEEK_KNOB, SPEED_KNOB, TURN_WAIT, Watch,
                                _pedal_tone_said)


STEM_NAMES = ("drums", "bass", "guitar", "vocals", "piano", "other")


class FakeGui:
    def __init__(self, mode="follow"):
        self.settings = {"launchkey": True}
        self.follow_settings = {"show_key": True}
        self.changes, self.shows, self.scales, self.stops, self.sound = [], 0, [], 0, True
        self.plays, self.seeks, self.following = [], [], mode == "follow"
        self.real_pitch, self.speeds, self.browsed, self.chosen = False, [], 0.0, []
        self.game_keys, self.game_waiting = [], 1  # (Rocksmith's, following)
        self.game_moved = None  # (its song list highlighting another song by itself)
        self.stopped, self.dialling, self.letters = 0, [], []
        self.live = None  # where the song is now (playing or followed; None: the info's position)
        self.programs = []
        self.volumes, self.volume_changes = [100] * 8, []
        self.loop, self.loop_calls = {"a": None, "b": None, "on": False}, []
        self.keys, self.keys_played, self.keys_released = {"sound": True, "lights": True, "tone": 1, "volume": 100}, [], 0
        self.library = [{"id": f"s{i}", "song": f"Song {i}", "artist": "Band"} for i in range(300)]
        self.listen, self.pedal_calls = False, []
        self.game = {"answered": True, "hook": True, "riff": True}  # (following: the game's riffloop mod)
        self.riff, self.riff_calls = {"speed": 100, "accelerator": False, "passes": 1}, []
        self.music = None  # (following: the game's Music volume; None: its mod hasn't it)
        self.game_stems, self.game_stem_calls = None, []  # (the game's song's parts: rearmed-mods' Song Stems)
        self.audio = {"on": False, "volume": 80, "volumes": {s: 100 for s in STEM_NAMES},  # (the song's own audio)
                      "mutes": {s: False for s in STEM_NAMES}, "made": True, "making": False, "progress": None,
                      "can": True, "there": True}
        self.options, self.splits = [], []
        self.pedal = {"slot": 49, "previous": None, "error": None, "favourites": [51, 187], "hold": False}
        self.song = {
            "id": "song", "title": "Amon Amarth - Twilight of the Thunder God", "played": "L" if mode == "follow" else None,
            "key": {"name": "C# minor", "tonic": 1, "mode": "minor", "scale": [1, 3, 4, 6, 8, 9, 11], "other": [2],
                    "notes": ["C#", "D#", "E", "F#", "G#", "A", "B"], "played_as": "F# minor in B standard"},
            "arrangements": [
                {"id": "L", "name": "Lead", "kind": "lead", "playing": mode == "follow", "play": True, "lights": 4,
                 "tones": {}},
                {"id": "R", "name": "Rhythm", "kind": "rhythm", "playing": False, "play": False, "lights": 0, "tones": {}},
                {"id": "B", "name": "Bass", "kind": "bass", "playing": False, "play": False, "lights": 0,
                 "tones": {"tone": 305}},
            ],
            "sections": [{"kind": kind, "name": name, "start": start, "end": end} for kind, name, start, end in (
                ("intro", "Intro 1", 0.0, 20.0), ("verse", "Verse 1", 20.0, 50.0), ("chorus", "Chorus 1", 50.0, 80.0),
                ("verse", "Verse 2", 80.0, 110.0), ("chorus", "Chorus 2", 110.0, 140.0), ("solo", "Solo 1", 140.0, 180.0),
                ("chorus", "Chorus 3", 180.0, 240.0), ("noguitar", "No guitar 1", 240.0, 260.0))],
        }
        self.info = {"mode": mode, "sound": True, "lead": {"tone": 296, "mute_tone": 295}, "rhythm": {}, "bass": {},
                     "song": None, "position": None if mode == "follow" else 30.0, "length": 260.0, "paused": False,
                     "speed": 1.0}

    def remote_info(self):
        return {**self.info, "sound": self.sound, "real_pitch": self.real_pitch, "keys": self.keys,
                "volumes": list(self.volumes), "loop": dict(self.loop), "listen": self.listen, "pedal": dict(self.pedal),
                "riff": dict(self.riff), "music": self.music, "music_muted": getattr(self, "music_muted", False),
                "game_stems": dict(self.game_stems) if self.game_stems else self.game_stems,
                "game_parts": getattr(self, "game_parts", None),
                "audio": {**self.audio, "volumes": dict(self.audio["volumes"]), "mutes": dict(self.audio["mutes"])}}

    def toggle_listen(self):
        self.listen = not self.listen
        return self.listen

    def set_pedal(self, body, later=0.0):  # (the slot it's going to, at once, as the pedal keeps it)
        self.pedal_calls.append((dict(body), later))
        if "hold" in body:  # (its tone kept, or following the song's tones again)
            self.pedal = {**self.pedal, "hold": bool(body["hold"])}
            return dict(self.pedal)
        slot = self.pedal["previous"] if body.get("back") else body["slot"]
        if slot != self.pedal["slot"]:
            self.pedal = {**self.pedal, "slot": slot, "previous": self.pedal["slot"]}
        return dict(self.pedal)

    def toggle_beat_lights(self):
        self.info["beat_lights"] = not self.info.get("beat_lights", False)
        return self.info["beat_lights"]

    def set_game_music(self, body):  # (following: the game's Music volume, from where it is; muted, its level kept)
        if self.music is None:
            raise ValueError("Music volume: update the riffloop mod")
        if "mute" in body:
            self.music_muted = bool(body["mute"])
        else:
            self.music = min(max(self.music + body["step"], 0), 100)
        return {"music": self.music, "muted": getattr(self, "music_muted", False)}

    def set_game_stems(self, body):  # (the game's song's guitar or bass, as RSMods.ini has them; muted, kept)
        self.game_stem_calls.append(dict(body))
        part = body["part"]
        muted = self.game_stems.setdefault("muted", {"bass": False, "guitar": False})
        if "level" in body:
            self.game_stems[part] = body["level"]
        elif "mute" in body:
            muted[part] = bool(body["mute"])
        return {**self.game_stems, "muted": dict(muted)}

    def set_game_riff(self, body):  # (following: Riff Repeater's settings)
        self.riff_calls.append(dict(body))
        self.riff = {**self.riff, **body}
        return {**self.riff, "answered": True}

    def pedal_name(self, slot):
        return {49: "5050 CL", 50: "5050 DS", 1: "US Clean", 200: "My love"}.get(slot, f"P{slot}")

    def keep_pedal_tone(self, slot=None):  # (following: the tone playing's own preset, or back to automatic)
        tone = self.pedal["tone"]
        back = tone["own"] and tone["pedal"] == slot
        self.pedal = {**self.pedal, "tone": {**tone, "own": not back, "pedal": 51 if back else slot}}
        self.kept = [*getattr(self, "kept", []), slot]
        return "dist: automatic" if back else f"dist: {slot:03d} kept"

    def set_tone_field(self, field):  # (the game's TONES to the Launchkey's tones page)
        self.tone_fields = [*getattr(self, "tone_fields", []), field]

    def set_game_mixer_page(self, page):  # (the game's MIXER to the Launchkey mixer's page)
        self.game_pages = [*getattr(self, "game_pages", []), page]

    def game_mixer_step(self):  # (the game's MIXER or TONES showing: asked each time round)
        self.mixer_steps = getattr(self, "mixer_steps", 0) + 1
        return False

    def set_loop(self, body):  # (on beats every half second)
        self.loop_calls.append(dict(body))
        here = self.info["position"] or 0.0
        if body.get("section"):
            found = next(x for x in self.song["sections"] if x["start"] <= here < x["end"])
            looping = self.loop["on"] and (self.loop["a"], self.loop["b"]) == (found["start"], found["end"])
            self.loop.update(a=found["start"], b=found["end"], on=not looping)
        for end in ("a", "b"):
            if end in body:
                self.loop[end] = here if body[end] == "here" else body[end]
            if f"move_{end}" in body:
                self.loop[end] = (here if self.loop[end] is None else self.loop[end]) + 0.5 * body[f"move_{end}"]
            if f"move_{end}_bars" in body:  # (a bar: 4 beats)
                self.loop[end] = (here if self.loop[end] is None else self.loop[end]) + 2.0 * body[f"move_{end}_bars"]
        if "on" in body:
            self.loop["on"] = body["on"]
        self.loop["on"] = self.loop["on"] and None not in (self.loop["a"], self.loop["b"])
        return {**self.loop, **({"game": dict(self.game)} if self.info["mode"] == "follow" else {})}

    def set_volumes(self, changes):
        self.volume_changes.append(dict(changes))
        for slot, volume in changes.items():
            self.volumes[slot] = volume
        return {"volumes": self.volumes}

    def change_part(self, arrangement, fields, later=0.0):
        self.changes.append((arrangement, fields))
        self.later = later
        for a in self.song["arrangements"]:  # its tones kept at once, as the page does, and whether it plays
            if a["id"] == arrangement:
                a["tones"] = {**a["tones"], **{k: v for k, v in fields.items() if k in ("tone", "mute_tone", "volume")}}
                if "follow_tones" in fields:  # (kept only off, as the page keeps it)
                    a["tones"] = {k: v for k, v in a["tones"].items() if k != "follow_tones"}
                    if fields["follow_tones"] is False:
                        a["tones"]["follow_tones"] = False
                a["play"], a["lights"] = fields.get("play", a["play"]), fields.get("lights", a["lights"])
                if a["kind"] == "drums" and "volume" in fields:  # (a drum part's volume: its own, as the page has it)
                    a["volume"] = fields["volume"]

    def toggle_sound(self):
        self.sound = not self.sound
        return self.sound

    def toggle_game_stems(self):  # (the stems page's master mute: both muted, or back)
        muted = self.game_stems.setdefault("muted", {"bass": False, "guitar": False})
        mute = not all(muted.values())
        for part in ("bass", "guitar"):
            self.set_game_stems({"part": part, "mute": mute})
        return {**self.game_stems, "muted": dict(muted)}

    def toggle_follow(self):
        self.following = not self.following
        return self.following

    def remote_play(self):
        self.plays.append(True)
        self.info["paused"] = not self.info["paused"]
        if self.info["mode"] == "follow":  # (the game's song, as its riffloop mod sees the game's screen)
            self.info["state"] = "paused" if self.info["paused"] else "playing"
        return "paused" if self.info["paused"] else "playing"

    def remote_seek(self, position):
        self.seeks.append(position)
        self.info["position"] = position

    def game_browse(self):
        moved, self.game_moved = self.game_moved, None
        return moved

    def game_key(self, name):
        self.game_keys.append(name)
        return self.game_waiting  # (keys the game has still to type)

    def toggle_real_pitch(self):
        self.real_pitch = not self.real_pitch
        return self.real_pitch

    def set_play_options(self, body):
        self.options.append(dict(body))
        if "speed" in body:
            self.speeds.append(body["speed"])
            self.info["speed"] = body["speed"]
        if "audio" in body:
            self.audio["on"] = body["audio"]
        if "audio_volume" in body:
            self.audio["volume"] = body["audio_volume"]
        self.audio["volumes"].update(body.get("stem_volumes", {}))
        self.audio["mutes"].update(body.get("stem_mutes", {}))
        return body

    def set_stems(self, body):
        self.splits.append(dict(body))
        self.audio["making"], self.audio["progress"] = True, 0.0
        return {}

    def browse_move(self, songs):
        self.browsed = min(max(self.browsed + songs, 0.0), len(self.library) - 1.0)
        return {**self.library[round(self.browsed)], "place": round(self.browsed), "of": len(self.library)}

    def remote_stop(self):
        self.stopped += 1
        self.info["mode"] = "idle"
        return "stopped"

    def browse_at(self, song_id):
        self.browsed = float(next((i for i, s in enumerate(self.library) if s["id"] == song_id), 0))
        return {**self.library[round(self.browsed)], "place": round(self.browsed), "of": len(self.library)}

    def song_position(self):
        return self.live

    def set_program(self, part, program):
        self.programs.append((part, program))
        for a in self.song["arrangements"]:
            if a["kind"] == part and a.get("output"):
                a["output"] = {**a["output"], "program": program}

    def browse_letter(self, steps):  # (25 songs a letter)
        self.letters.append(steps)
        self.browsed = float(min(max((round(self.browsed) // 25 + steps) * 25, 0), len(self.library) - 1))
        return {**self.library[round(self.browsed)], "place": round(self.browsed), "of": len(self.library)}

    def browse_select(self):
        song = self.library[round(self.browsed)]
        self.chosen.append(song["id"])
        return song

    def show_key(self):
        self.shows += 1

    def show_scale(self, body):
        self.scales.append(body)

    def stop_scale(self):
        self.stops += 1

    def remote_dialling(self, dialling):
        self.dialling.append(dialling)

    def set_keys(self, body):
        self.keys = {**self.keys, **body}
        return self.keys

    def play_keys(self, message):
        self.keys_played.append(message)

    def release_keys(self):
        self.keys_released += 1


class FakeLaunchkey:
    def __init__(self):
        self.name, self.calls, self.queued, self.plugged = "Launchkey Mini MK4 25 DAW In", [], [], True
        self.on_keys = None

    def open(self):
        return self.plugged

    def connected(self):
        return self.plugged

    def close(self):
        self.calls.append(("close",))

    def events(self):
        found, self.queued = self.queued, []
        return found

    def pad(self, row, column, colour, pulse=False):
        self.calls.append(("pad", row, column, colour, pulse))

    def pad_rgb(self, row, column, red, green, blue):
        self.calls.append(("rgb", row, column, red, green, blue))

    def button(self, number, colour, brightness):
        self.calls.append(("button", number, colour, brightness))

    def scale(self, tonic, mode):
        self.calls.append(("scale", tonic, mode))

    def show(self, target, title, name, value):
        self.calls.append(("show", target, title, name, value))

    def picture(self, target, data):
        self.calls.append(("picture", target, data))

    def hide(self, target):
        self.calls.append(("hide", target))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def watching(mode="follow"):
    gui, launchkey, clock = FakeGui(mode), FakeLaunchkey(), Clock()
    watch = Watch(gui, lambda: launchkey, clock=clock)  # type: ignore[arg-type]
    watch.look()
    return watch, gui, launchkey, clock


TWILIGHT = ("Twilight of the Thunder God", "Amon Amarth", "C# minor", "C# D# E F# G# A B")
SONG = song_picture(*TWILIGHT).data()  # following
PLAYING = song_picture("Twilight of the Thunder God", "Amon Amarth", "C# minor", "C# D# E F# G# A B",
                       done=30 / 260, sign="playing", beside="0:30 / 4:20").data()  # (playing, 0:30 of 4:20)


def screens(launchkey):
    """What the screen's stationary display was given, in turn: pictures (their data), and text."""
    return [c[2] if c[0] == "picture" else c[2:] for c in launchkey.calls if c[0] in ("show", "picture") and c[1] == 0x20]


def browsed(n):
    """The song the modulation strip is on, in the fake library of 300."""
    return song_picture(f"Song {n}", "Band", "● = select", "", done=n / 299, handle=True, beside="▲ = return",
                        corner=f"{n + 1}/300", knob=("8", "A-Z")).data()


def note(watch):
    """What the screen says for a while at its bottom left: (a line, two lines), if it's shown."""
    return watch._note if watch._clock() < watch._note_until else None


def pictures(launchkey):
    return [c[2] for c in launchkey.calls if c[0] == "picture"]


def pads(launchkey):
    """What each pad shows: the last sent to it."""
    return {(c[1], c[2]): c[3:] for c in launchkey.calls if c[0] in ("pad", "rgb")}


def test_a_song_starting_shows_its_key_and_its_arrangements_on_the_pads():
    watch, gui, launchkey, clock = watching()
    watch.step()
    assert screens(launchkey) == [song_picture("Looking for Rocksmith", "", "Following Rocksmith", "Waiting for a song").data()]
    gui.info["connected"] = False  # (RS Companion's overlay API not reached)
    clock.now = 0.5
    watch.step()
    assert screens(launchkey)[-1] == song_picture("Can't reach Rocksmith", "", "Following Rocksmith",
                                                  "Waiting for a song").data()
    gui.info["connected"] = True
    gui.info["song"] = gui.song
    clock.now = 1.0
    watch.step()
    assert gui.shows == 1  # the scale on the keys as the song starts
    assert screens(launchkey)[-1] == SONG  # its name (over two lines if need be), artist, key and scale
    shown = pads(launchkey)
    assert shown[(0, 0)] == (9, True)  # the lead: on, and played, so pulsing orange
    assert shown[(0, 1)] == (0, 10, 2) and shown[(0, 2)] == (0, 4, 10)  # rhythm and bass: muted, their colours dim
    assert shown[(1, 0)] == (127, 45, 0) and shown[(1, 1)] == (0, 0, 0)  # lit keys: all of them, and none
    assert shown[(0, 3)] == (0, False)  # no fourth arrangement
    assert shown[(0, CONTROLS)] == (21, False)  # the sound: on, green
    assert shown[(1, CONTROLS)] == (37, True)  # following: on
    assert ("scale", 1, "minor") in launchkey.calls  # the Launchkey's own Scale mode: C# minor
    clock.now, launchkey.calls = 2.0, []
    watch.step()
    assert gui.shows == 1 and launchkey.calls == []  # nothing changed: nothing sent


def test_the_sound_pad_turns_the_sound_on_and_off():
    watch, gui, launchkey, clock = watching("idle")
    watch.step()
    assert screens(launchkey) == [song_picture("rs-keylights", "", "Ready", "Pick one on the page").data()]
    launchkey.queued = [Pad(0, CONTROLS)]
    clock.now = 0.5
    watch.step()
    assert gui.sound is False and pads(launchkey)[(0, CONTROLS)] == (5, False)  # red: lights only
    off = song_picture("rs-keylights", "", "Ready", "Pick one on the page", muted=True).data()
    assert note(watch) is None and screens(launchkey)[-1] == off  # the speaker crossed out, in a corner, all along
    launchkey.queued = [Pad(0, CONTROLS)]
    clock.now = 1.0
    watch.step()
    assert screens(launchkey)[-1] == song_picture("rs-keylights", "", "Ready", "Pick one on the page").data()


def test_pads_turn_arrangements_on_and_give_them_lit_keys():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    assert gui.shows == 0  # not following: no key shown by itself
    assert pads(launchkey)[(0, 0)] == (9, False)  # playing a song: no arrangement played in the game
    launchkey.queued = [Pad(0, 2)]  # the bass on
    watch.step()
    assert gui.changes[-1] == ("B", {"play": True})
    assert note(watch) == ("Bass: on", None)  # (in the scale's place)
    launchkey.queued = [Pad(1, 0)]  # the lead's lit keys: off, the song taking it once taps stop
    watch.step()
    assert gui.changes[-1] == ("L", {"lights": 0}) and gui.later == PAD_WAIT
    gui.song["arrangements"][2].update(play=True, lights=0)
    gui.song["arrangements"][0]["lights"] = 1
    launchkey.queued = [Pad(1, 2)]  # the bass's: on too, sharing the 4 keys with the lead's
    clock.now = 1.0
    watch.step()
    assert gui.changes[-1] == ("B", {"lights": 1})
    gui.song["arrangements"][2]["lights"] = 1
    launchkey.queued, before = [Pad(1, 0), Pad(1, 0)], len(gui.changes)  # the lead's, twice quickly: off, on again
    clock.now = 2.0
    watch.step()
    assert gui.changes[before:] == [("L", {"lights": 0}), ("L", {"lights": 1})]
    assert note(watch) == ("Lead: lights on", None)


def test_encoders_step_through_tones_and_send_where_they_stop(monkeypatch):
    monkeypatch.setattr(watch_module, "PICTURE_GAP", 0.05)  # (its steps close together: each drawn)
    watch, gui, launchkey, clock = watching()
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Turn(0, 1), Turn(0, 1)]  # the lead: from its default, 296, a step at a time (in one go)
    clock.now = 0.5
    watch.step()
    assert screens(launchkey)[-1] == parts_picture("Lead tone", 298, "GM Tones", "GM GT HARMONICS", 3, 0).data()
    assert gui.changes == [("L", {"tone": 297}), ("L", {"tone": 298})]  # kept at once: the page shows each
    assert gui.later == TURN_WAIT  # (the song takes it once the turns stop)
    gui.song["arrangements"][0]["tones"] = {"tone": 298}
    launchkey.queued = [Turn(2, -1), Button(51), Turn(0, 1)]  # the bass from its own 305; then the up arrow again:
    # palm mutes, from none of its own (the default, 295) to the first tone
    clock.now = 0.6
    watch.step()
    assert watch.field == 1
    assert gui.changes[2:] == [("B", {"tone": 304}), ("L", {"mute_tone": 1})]
    assert screens(launchkey)[-1] == parts_picture("Lead palm mutes", 1, "Piano", "STEREO GRAND PIANO", 3, 1).data()
    launchkey.queued = [Turn(0, -1)]  # back: none of its own again, its kind's default
    clock.now = 0.65
    watch.step()
    assert gui.changes[-1] == ("L", {"mute_tone": None})
    assert screens(launchkey)[-1] == parts_picture("Lead palm mutes", 295, "Default", "GM MUTE GUITAR", 3, 1).data()
    launchkey.queued = [Turn(0, -1)]  # and before it: the same tone as the rest, over the default
    clock.now = 0.71
    watch.step()
    assert gui.changes[-1] == ("L", {"mute_tone": 0})
    assert screens(launchkey)[-1] == parts_picture("Lead palm mutes", None, "Same tone", "GM GT HARMONICS", 3, 1).data()
    launchkey.queued = [Turn(1, 1), Turn(1, -1)]  # the rhythm's, with no default: the same tone, then the tones
    clock.now = 0.77
    watch.step()
    assert screens(launchkey)[-1] == parts_picture("Rhythm palm mutes", None, "Same tone", "DISTORTION GT", 3, 1).data()
    launchkey.queued = [Turn(CONTROLS, 1), Turn(CONTROLS, -1)]  # knob 8, while a tone shows: a group at a time,
    # none of its own a group by itself
    clock.now = 0.83
    watch.step()
    assert gui.changes[-2:] == [("R", {"mute_tone": 1}), ("R", {"mute_tone": 0})]
    launchkey.queued = [Pad(0, CONTROLS)]  # Mute, while the tone shows: it stays, the speaker in its corner
    clock.now = 0.89
    watch.step()
    assert watch._picture_of == "parts" and pictures(launchkey)[-1][52 * 19 + 120 // 7] & 0x20  # (the speaker's top)
    gui.sound = True
    launchkey.queued = [Button(51), Button(51), Turn(1, 1)]  # twice more: pinch harmonics, then tones again
    clock.now = 0.95
    watch.step()
    assert watch.field == 0 and gui.changes[-1][0] == "R" and "tone" in gui.changes[-1][1]
    clock.now = 0.95 + TURN_WAIT
    launchkey.calls = []
    watch.step()
    assert not [c for c in launchkey.calls if c[0] in ("show", "picture")]  # once they stop: as it was


def test_its_scale_button_shows_the_scale_and_following_the_buttons_left_and_right_of_the_pads_are_rocksmiths():
    watch, gui, launchkey, clock = watching()
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued, launchkey.calls = [ScaleMode(True)], []
    watch.step()
    assert gui.scales == [{"song": "song"}]  # climbing the keys
    assert not [c for c in launchkey.calls if c[0] == "show" and c[1] == 0x21]  # its own display shows the scale
    shown = pads(launchkey)
    assert shown[(0, 0)] == (5, False) and shown[(0, 1)] == (3, False)  # C# (the tonic, red), D#
    assert shown[(1, 0)] == (0, False) and shown[(1, 1)] == (49, False) and shown[(1, 2)] == (3, False)  # C, D, E
    launchkey.queued = [Pad(0, 0)]  # only its Scale button turns it off: its light and screen don't follow a message
    watch.step()
    assert watch.scale_view and gui.changes == [] and gui.stops == 0
    assert note(watch) == (None, ("Pads: the song's scale", "Scale: back to parts"))
    gui.info["song"] = {**gui.song, "id": "next"}  # the next song: its scale climbs the keys too
    clock.now = 1.0
    watch.step()
    assert gui.scales[-1] == {"song": "next"} and gui.shows == 1  # (the first song's key shown as it started)
    launchkey.queued = [ScaleMode(False)]  # off: the arrangements, and the keys stop
    watch.step()
    assert gui.stops == 1 and not watch.scale_view and pads(launchkey)[(0, 0)] == (9, True)
    launchkey.queued = [Button(104), Button(106), Button(107), Button(RECORD)]  # Escape, up, down, Enter
    clock.now = 1.5
    watch.step()
    assert gui.game_keys == ["escape", "up", "down", "enter"] and not gui.real_pitch  # (real pitch: the page's)
    gui.info["mode"] = "play"  # not following: said so (Track back is home)
    clock.now = 2.0
    watch.step()
    launchkey.queued = [Button(104)]
    clock.now = 2.5
    watch.step()
    assert gui.game_keys == ["escape", "up", "down", "enter"] and note(watch) == (None, NOT_FOLLOWING_KEYS)
    assert ("button", 104, 1, 20) in launchkey.calls


def test_its_scale_mode_on_before_a_song_shows_the_songs_scale_when_it_comes():
    watch, gui, launchkey, clock = watching("play")
    watch.step()
    launchkey.queued = [ScaleMode(True)]  # (as it answers when the page starts and asks)
    watch.step()
    assert watch.scale_view and gui.scales == []
    gui.info["song"] = gui.song
    clock.now = 1.0
    watch.step()
    assert gui.scales == [{"song": "song"}] and pads(launchkey)[(0, 0)] == (5, False)


def test_the_down_arrow_puts_the_knobs_on_playing_the_song_knob_5_its_speed():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52)]  # the down arrow beside the knobs
    clock.now = 0.5
    watch.step()
    assert watch.mode == "playback" and screens(launchkey)[-1] == PLAYING  # (the screen as it was)
    assert pads(launchkey)[(0, 0)] == (11, False) and ("button", 52, 3, 127) in launchkey.calls  # the loop's start:
    # none yet
    assert pads(launchkey)[(1, 1)] == (0, 45, 127) and pads(launchkey)[(1, 2)] == (15, 6, 0)  # the verse it's in,
    # bright; the chorus not
    assert pads(launchkey)[(1, 4)] == (0, 0, 0) and pads(launchkey)[(0, 6)] == (0, False)  # (intro, verse, chorus and
    # solo: no fifth kind; no keys)
    launchkey.queued = [Turn(SPEED_KNOB, -2), Turn(SPEED_KNOB, -3)]  # 1% a step, set as it turns, each from the one
    # before
    clock.now = 1.0
    watch.step()
    speed = song_picture(*TWILIGHT[:3], "Speed 95%", done=30 / 260, sign="playing", speed=0.95,
                         beside="0:30 / 4:20").data()
    assert screens(launchkey)[-1] == speed and ("hide", 0x21) in launchkey.calls  # the line: the speed
    assert gui.speeds == [0.98, 0.95]
    launchkey.queued = [Turn(SPEED_KNOB, -100)]  # no slower than 25%
    clock.now = 2.0
    watch.step()
    launchkey.queued = [Turn(SPEED_KNOB, 127), Turn(SPEED_KNOB, 127)]  # and no faster than 200%
    clock.now = 3.0
    watch.step()
    assert gui.speeds == [0.98, 0.95, 0.25, 1.52, 2.0]
    clock.now = 3.0 + PICTURE_SHOWN
    watch.step()
    assert screens(launchkey)[-1] == PLAYING  # a while after: the song again
    launchkey.queued = [Button(52)]  # the down arrow again: what the knobs do, until it's pressed again
    clock.now = 6.2
    watch.step()
    clock.now = 6.2 + PICTURE_SHOWN + 5
    watch.step()
    assert screens(launchkey)[-1] == playback_picture().data()
    launchkey.queued = [Button(52)]
    watch.step()
    assert screens(launchkey)[-1] == PLAYING
    launchkey.queued = [Button(51)]  # the up arrow: the arrangements again, what their knobs do for a while
    clock.now = 15.0
    watch.step()
    assert watch.mode == "parts" and pads(launchkey)[(0, 0)] == (9, False)
    assert screens(launchkey)[-1] == arrangements_picture().data()
    clock.now = 15.0 + PICTURE_SHOWN
    watch.step()
    assert screens(launchkey)[-1] == PLAYING
    launchkey.queued = [Button(51)]  # again: their palm-mute tones (the lead's shown)
    clock.now = 20.0
    watch.step()
    assert watch.field == 1
    assert screens(launchkey)[-1] == parts_picture("Lead palm mutes", 295, "Default", "GM MUTE GUITAR", 3, 1).data()


def pitch(step, at):
    """The pitch strip at a step (of 128), as it reports it (14-bit)."""
    return Strip("pitch", step * 128 + 64, at)


def test_the_pitch_strip_goes_through_the_library_and_record_switches(monkeypatch):
    monkeypatch.setattr(watch_module, "PICTURE_GAP", 0.05)  # (its steps close together: each drawn)
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    song, song_2 = PLAYING, browsed(2)
    launchkey.queued = [pitch(40, 1.0), pitch(44, 1.2), pitch(48, 1.4)]
    clock.now = 1.41
    watch.step()  # slow: a song for each 4 steps
    assert gui.browsed == 2.0 and ("button", RECORD, 3, 127) in launchkey.calls  # a song to switch to
    assert screens(launchkey) == [song, song_2]  # the song it's on, on the screen
    launchkey.queued = [pitch(49, 1.65), Strip("pitch", 49 * 128 + 70, 1.66)]  # a step on the same song, and held
    clock.now = 1.66  # still (wavering a little)
    watch.step()
    assert gui.browsed == 2.25 and screens(launchkey) == [song, song_2]
    launchkey.queued = [Strip("pitch", 8192, 1.7)]  # let go: it springs back to the middle, and the step just before
    clock.now = 1.7  # (a finger coming off) is undone
    watch.step()
    assert gui.browsed == 2.0
    clock.now = 1.66 + BROWSE_SHOWN - 0.01
    watch.step()
    assert screens(launchkey) == [song, song_2]
    clock.now = 1.66 + BROWSE_SHOWN
    watch.step()
    assert screens(launchkey) == [song, song_2, song]  # left a while (since it last moved): the song again
    launchkey.queued = [pitch(100, 8.0)]  # put down somewhere else: nothing moves
    clock.now = 8.0
    watch.step()
    assert gui.browsed == 2.0 and screens(launchkey)[-1] == song_2  # the song it's on again
    launchkey.queued = [pitch(120, 8.01)]  # a fast slide: 20 steps in 10 ms
    clock.now = 8.06
    watch.step()
    assert gui.browsed == 7.0 and screens(launchkey)[-1] == browsed(7)
    launchkey.queued = [Button(RECORD)]
    clock.now = 8.12
    watch.step()
    assert gui.chosen == ["s7"] and note(watch) == ("Switched to this song", None)
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT[:3], "Switched to this song", done=30 / 260,
                                                  sign="playing", beside="0:30 / 4:20").data()  # (its scale in a moment)
    gui.info["song"] = {**gui.song, "id": "s42"}
    clock.now = 9.0
    watch.step()
    launchkey.queued = [Button(106)]  # Track back, home: the song on the screen, and the library back to it
    watch.step()
    home = song_picture(*TWILIGHT, done=30 / 260, sign="playing", beside="0:30 / 4:20").data()
    assert gui.browsed == 42.0 and screens(launchkey)[-1] == home and gui.stopped == 0
    launchkey.queued = [Button(RECORD)]  # on the song already: it stops, the song's picture showing it
    clock.now = 9.1
    watch.step()
    stopped = song_picture(*TWILIGHT, done=30 / 260, sign="stopped", beside="0:30 / 4:20").data()
    assert gui.stopped == 1 and gui.chosen == ["s7"] and screens(launchkey)[-1] == stopped
    assert ("button", RECORD, 1, 20) in launchkey.calls  # nothing to switch to or stop


def test_following_the_library_can_be_gone_through_but_the_game_picks_the_song():
    watch, gui, launchkey, clock = watching()  # following
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [pitch(40, 1.0), pitch(44, 1.02), pitch(48, 1.04)]  # slid: a song for each 4 steps
    clock.now = 1.1
    watch.step()
    assert gui.browsed == 2.0 and ("button", RECORD, 1, 20) in launchkey.calls  # (Record: no song to switch to)
    launchkey.queued = [Button(RECORD)]  # (following: Rocksmith's Enter)
    watch.step()
    assert gui.chosen == [] and gui.stopped == 0 and gui.game_keys == ["enter"]


def test_following_the_games_song_list_moving_by_itself_shows_the_song_it_highlights():
    watch, gui, launchkey, clock = watching()  # following
    gui.info["song"] = gui.song
    watch.step()
    gui.game_moved = {"id": "x#zulu", "song": "Zulu Song", "artist": "Zulu Band", "place": 3, "of": 4}
    clock.now = 0.5
    watch.step()
    assert watch._browsed == "x#zulu" and watch._browsed_text == ("Zulu Song", "Zulu Band")
    assert clock.now < watch._browse_until  # (on the screen for a while)


def test_a_parts_pad_below_turns_its_lights_on_or_off():
    watch, gui, launchkey, clock = watching()
    gui.info["song"] = gui.song
    watch.step()
    assert gui.song["arrangements"][0]["lights"] == 4
    launchkey.queued = [Pad(1, 0)]  # the lead's: off at once (no going through 3, 2, 1)
    clock.now = 0.5
    watch.step()
    assert gui.changes[-1] == ("L", {"lights": 0}) and note(watch) == ("Lead: lights off", None)
    launchkey.queued = [Pad(1, 0)]
    clock.now = 1.0
    watch.step()
    assert gui.changes[-1] == ("L", {"lights": 1}) and note(watch) == ("Lead: lights on", None)
    launchkey.queued = [Pad(1, 1)]  # the rhythm's too, the lead's still on: any number lit (sharing the 4 keys)
    clock.now = 1.5
    watch.step()
    assert gui.changes[-1] == ("R", {"lights": 1}) and note(watch) == ("Rhythm: lights on", None)


def test_a_flick_keeps_going_until_the_strip_is_touched():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [pitch(10 + 6 * i, 1.0 + 0.01 * i) for i in range(11)]  # 6 steps every 10 ms
    clock.now = 1.11
    watch.step()
    flicked_from = gui.browsed
    for i in range(1, 6):  # lifted off: it goes on by itself
        clock.now = 1.11 + 0.1 * i
        watch.step()
    assert gui.browsed > flicked_from + 20
    launchkey.queued = [Strip("pitch", 8192, 1.12), pitch(20, 1.7)]  # (let go: springing back,) touched again: it stops
    clock.now = 1.7
    watch.step()
    stopped_at = gui.browsed
    clock.now = 2.0
    watch.step()
    assert gui.browsed == stopped_at


def test_what_cant_be_done_is_said_on_the_songs_screen():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()

    def refused():
        raise RuntimeError("Following the game: Rocksmith plays the song")

    gui.remote_play = refused
    launchkey.queued = [Button(PLAY)]
    clock.now = 0.5
    watch.step()
    assert note(watch) == (None, ("Following the game:", "Rocksmith plays the song"))  # (over two lines)
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT[:2], "Following the game:", "Rocksmith plays the song",
                                                  done=30 / 260, sign="playing").data()


def test_letting_go_flicks_even_with_a_step_back_and_else_stays_where_it_was_held():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    slide = [pitch(10 + 3 * i, 1.0 + 0.01 * i) for i in range(8)]  # 3 steps every 10 ms (300 a second)
    launchkey.queued = [*slide, pitch(29, 1.075), Strip("pitch", 8192, 1.08)]  # coming off: 2 steps back, let go
    clock.now = 1.08
    watch.step()
    flicked_from = gui.browsed
    clock.now = 1.3
    watch.step()
    assert gui.browsed > flicked_from + 5  # a flick all the same
    launchkey.queued = [pitch(30, 2.0)]  # touched: it stops
    clock.now = 2.0
    watch.step()
    held = gui.browsed
    launchkey.queued = [pitch(32, 2.3), pitch(34, 2.6), pitch(36, 2.95), Strip("pitch", 8192, 3.0)]  # slowly, and the
    clock.now = 3.0  # steps coming off undone
    watch.step()
    assert gui.browsed == held + 1.0


def test_following_the_library_goes_to_the_song_the_game_plays():
    watch, gui, launchkey, clock = watching()  # following, no song yet: it stays where it is
    watch.step()
    assert gui.browsed == 0.0
    gui.info["song"] = {**gui.song, "id": "s12"}
    clock.now = 1.0
    watch.step()
    assert gui.browsed == 12.0
    gui.info["song"] = {**gui.song, "id": "s12", "played": "R"}  # another arrangement of it: no move
    gui.browsed = 20.0
    clock.now = 2.0
    watch.step()
    assert gui.browsed == 20.0


def test_track_on_opens_the_mixer_its_knobs_the_volumes_and_its_pads_mute_each():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(105)]  # Func
    clock.now = 0.5
    watch.step()
    assert watch.mix_knobs and ("button", 105, 3, 127) in launchkey.calls  # lit while it's open
    names, quiet = ["Lead", "Rhythm", "Bass", "Keys"], [False, True, True, False]  # the arrangements', then its
    # keys' (the rhythm and bass muted: not played)
    lights = [1.0, 0.0, 0.0, 1.0]  # their lit keys: the lead's 4, and its keys lighting first

    def mixer(volumes, turned, **shown):  # (in percent: a volume of 100, of 127, 79%)
        return mixer_picture(names, volumes, turned, lights=lights, tops=[100] * len(names), **shown).data()

    assert screens(launchkey)[-1] == mixer([79] * 4, None, quiet=quiet)
    launchkey.queued = [Turn(1, -6)]  # knob 2: the second arrangement's (in every song), 6 steps (6%) down
    clock.now = 1.0
    watch.step()
    assert gui.volume_changes[-1] == {1: 93}
    assert screens(launchkey)[-1] == mixer([79, 73, 79, 79], 1, quiet=quiet)
    launchkey.queued = [Turn(6, 10)]  # knob 7: its keys'
    clock.now = 1.5
    watch.step()
    assert gui.keys["volume"] == 113 and screens(launchkey)[-1] == mixer([79, 73, 79, 89], 3, quiet=quiet)
    launchkey.queued = [Turn(CONTROLS, 30)]  # knob 8: all of them together (none louder than 127: 100%)
    clock.now = 2.0
    watch.step()
    assert gui.volume_changes[-1] == {0: 127, 1: 127, 2: 127, 3: 127, 4: 127, 5: 127, 6: 127, 7: 127}
    assert gui.keys["volume"] == 127
    assert pads(launchkey)[(0, 1)] == (5, False)  # the rhythm's pad on top: muted, red
    launchkey.queued = [Pad(0, 1)]  # tapped: it plays (as outside the mixer), its colour back (nothing said: its
    # slider shows it), its slider the one used
    clock.now = 2.5
    watch.step()
    assert gui.changes[-1] == ("R", {"play": True}) and pads(launchkey)[(0, 1)] == (21, False)
    volumes, quiet = [100, 100, 100, 100], [False, False, True, False]
    assert note(watch) is None and screens(launchkey)[-1] == mixer(volumes, 1, quiet=quiet)
    launchkey.queued = [Pad(0, KEYS_COLUMN)]  # its keys' pad: muted, red, greyed
    clock.now = 2.7
    watch.step()
    quiet = [False, False, True, True]
    assert not gui.keys["sound"] and pads(launchkey)[(0, KEYS_COLUMN)] == (5, False)
    assert screens(launchkey)[-1] == mixer(volumes, 3, quiet=quiet)
    launchkey.queued = [Pad(0, CONTROLS)]  # Mute: all of them (greyed, the speaker in the corner), and it stays
    clock.now = 3.0
    watch.step()
    assert watch.mix_knobs and screens(launchkey)[-1] == mixer(volumes, None, muted=True, quiet=[True] * 4)
    launchkey.queued = [Pad(0, CONTROLS), Pad(0, 1)]
    clock.now = 3.0 + PICTURE_SHOWN + 5
    watch.step()
    assert watch.mix_knobs  # it stays, until closed
    launchkey.queued = [Button(105)]  # again: the song
    clock.now = 12.0
    watch.step()
    assert not watch.mix_knobs and ("button", 105, 1, 20) in launchkey.calls
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT, done=30 / 260, sign="playing",
                                                  beside="0:30 / 4:20").data()  # (what was said gone with it)
    assert pads(launchkey)[(0, 1)] == (0, 10, 2)  # muted in it, and still: dim outside it
    launchkey.queued = [Turn(CONTROLS, -2)]  # knob 8 alone: all the volumes, the mixer up for a while
    clock.now = 13.0
    watch.step()
    assert watch.mix_knobs and gui.keys["volume"] == 124
    clock.now = 13.0 + PICTURE_SHOWN
    watch.step()
    assert not watch.mix_knobs  # gone again
    launchkey.queued = [Turn(CONTROLS, 1), Turn(0, -1), Pad(0, 1)]  # knob 8, then another of its knobs and a pad:
    # up a while longer, and gone (opened by knob 8)
    clock.now = 20.0
    watch.step()
    assert watch.mix_knobs and gui.volume_changes[-1] == {0: 124}
    clock.now = 20.0 + PICTURE_SHOWN
    watch.step()
    assert not watch.mix_knobs
    launchkey.queued = [Button(105)]  # kept
    clock.now = 25.0
    watch.step()
    launchkey.queued = [Pad(1, 0)]  # a pad below: its lit keys, the rails beside its slider (nothing said), and
    # the mixer stays
    clock.now = 30.0
    watch.step()
    lights = [0.0, 0.0, 0.0, 1.0]  # (the lead's: from 4 round to none)
    assert watch.mix_knobs and note(watch) is None and gui.changes[-1] == ("L", {"lights": 0})
    assert screens(launchkey)[-1] == mixer([98, 99, 99, 99], None, quiet=[False, False, True, True])
    launchkey.queued = [Turn(0, 1)]
    clock.now = 30.5
    watch.step()
    assert screens(launchkey)[-1] == mixer([99, 99, 99, 99], 0, quiet=[False, False, True, True])
    launchkey.queued = [Button(104)]  # (Rocksmith's Escape: follow it first) said over it too
    clock.now = 31.0
    watch.step()
    assert watch.mix_knobs and note(watch) == (None, NOT_FOLLOWING_KEYS)
    launchkey.queued = [Button(105), Button(51), Turn(0, 1)]  # Func: the mixer closed; the up arrow again, the
    # knobs on the palm-mute tones (from none of its own, the first)
    clock.now = 31.5
    watch.step()
    assert not watch.mix_knobs and gui.changes[-1] == ("L", {"mute_tone": 1})


def test_the_mixer_has_the_pads_on_the_arrangements_and_gives_playing_the_song_back():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52)]  # the down arrow: playing the song (no arrangements on the pads)
    clock.now = 0.5
    watch.step()
    assert watch.mode == "playback" and pads(launchkey)[(0, 0)] == (11, False)
    launchkey.queued = [Button(105)]  # the mixer: the arrangements on the pads (the up arrow lit)
    clock.now = 1.0
    watch.step()
    assert watch.mode == "parts" and pads(launchkey)[(0, 0)] == (9, False) and ("button", 51, 3, 127) in launchkey.calls
    launchkey.queued = [Button(105)]  # closed: playing the song again
    clock.now = 2.0
    watch.step()
    assert watch.mode == "playback" and pads(launchkey)[(0, 0)] == (11, False)
    launchkey.queued = [Turn(CONTROLS, 1)]  # knob 8: the mixer for a while, and the arrangements
    clock.now = 3.0
    watch.step()
    assert watch.mix_knobs and watch.mode == "parts"
    clock.now = 3.0 + PICTURE_SHOWN
    watch.step()
    assert not watch.mix_knobs and watch.mode == "playback"  # gone: playing the song again


def test_its_keys_play_the_keyboard_column_7_their_own():
    watch, gui, launchkey, clock = watching()  # following, no song yet: its keys all the same
    watch.step()
    launchkey.on_keys([0x94, 60, 100])  # handed on at once, on its MIDI thread
    assert gui.keys_played == [[0x94, 60, 100]]
    assert pads(launchkey)[(0, 6)] == (49, False) and pads(launchkey)[(1, 6)] == (60, 0, 127)  # on, lighting first
    launchkey.queued = [Turn(6, 2)]  # knob 7: two tones on
    clock.now = 0.5
    watch.step()
    assert screens(launchkey)[-1] == keys_picture(3, "Piano", "BRIGHT PIANO").data()  # the tone, what the knobs do
    assert gui.changes == [] and gui.keys["tone"] == 3  # at once
    launchkey.queued = [Turn(CONTROLS, 3)]  # knob 8, while the tone shows: three groups on
    clock.now = 1.0
    watch.step()
    assert gui.keys["tone"] == 43  # ORGAN's first
    assert screens(launchkey)[-1] == keys_picture(43, "Organ", tone(43).name).data()
    launchkey.queued = [Pad(0, 6), Pad(1, 6)]  # its pads: off, and not lighting the keys first
    clock.now = 2.0
    watch.step()
    assert (gui.keys["sound"], gui.keys["lights"]) == (False, False)
    assert pads(launchkey)[(0, 6)] == (5, 0, 10) and note(watch) == ("Keys light first: off", None)  # (off: dim)
    launchkey.plugged = False  # unplugged: no notes left on for keys held as it went
    watch.look()
    assert gui.keys_released == 1


def test_it_lets_go_of_a_launchkey_unplugged_and_finds_it_again():
    watch, gui, launchkey, clock = watching()
    assert watch.status == "Launchkey Mini MK4 25 DAW In"
    launchkey.plugged = False
    watch.look()
    assert watch.launchkey is None and ("close",) in launchkey.calls and watch.status == "not found"
    launchkey.plugged = True
    watch.look()
    assert watch.launchkey is launchkey
    gui.settings["launchkey"] = False
    watch.look()
    assert watch.launchkey is None and watch.status == "off"


def test_the_right_column_plays_follows_and_moves_through_the_song():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    assert ("button", PLAY, 3, 127) in launchkey.calls and screens(launchkey) == [PLAYING]  # playing: lit, and ▶
    launchkey.queued = [Button(PLAY)]
    clock.now = 0.1
    watch.step()
    paused = song_picture("Twilight of the Thunder God", "Amon Amarth", "C# minor", "C# D# E F# G# A B",
                          done=30 / 260, sign="paused", beside="0:30 / 4:20").data()
    assert gui.plays == [True] and screens(launchkey)[-1] == paused  # no message: the song, paused
    assert ("hide", 0x21) in launchkey.calls and not [c for c in launchkey.calls if c[:2] == ("show", 0x21)]
    launchkey.queued = [Button(PLAY)]
    clock.now = 0.2
    watch.step()
    assert screens(launchkey)[-1] == PLAYING  # playing again
    launchkey.queued = [Button(52), Turn(SEEK_KNOB, 5), Turn(SEEK_KNOB, 3)]  # playing it: knob 1, 8 seconds on
    clock.now = 1.0
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT, done=38 / 260, sign="playing", handle=True,
                                                 beside="0:38 / 4:20").data()  # (a handle, the time moving)
    assert ("hide", 0x21) in launchkey.calls and gui.seeks == []  # (over Play's message)
    sent = len(pictures(launchkey))
    clock.now = 1.0 + TURN_WAIT + 0.01
    watch.step()
    assert gui.seeks == [38.0] and len(pictures(launchkey)) == sent  # (the same)
    gui.info["position"] = 40.0  # it plays on: the picture follows
    clock.now = 1.5
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT, done=40 / 260, sign="playing", handle=True,
                                                 beside="0:40 / 4:20").data()
    clock.now = 1.0 + PICTURE_SHOWN
    watch.step()
    assert screens(launchkey)[-1] == song_picture("Twilight of the Thunder God", "Amon Amarth", "C# minor",
                                                 "C# D# E F# G# A B", done=40 / 260, sign="playing",
                                                 beside="0:40 / 4:20").data()
    launchkey.queued = [Pad(1, CONTROLS)]
    watch.step()
    assert gui.following and note(watch) == ("Following: on", None)


def test_a_toggle_is_said_at_the_bottom_left_of_whatever_the_screen_shows():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Turn(0, 1)]  # knob 1: the lead's tone, on the screen
    clock.now = 0.5
    watch.step()
    tone_shown = parts_picture("Lead tone", 297, "GM Tones", tone(297).name, 3, 0)
    launchkey.queued = [Pad(1, CONTROLS)]  # following: said over it, the tone staying
    clock.now = 1.0
    watch.step()
    assert gui.following and screens(launchkey)[-1] == with_note(tone_shown, ("Following: on",)).data()
    keys = gui.keys["lights"]
    launchkey.queued = [Pad(1, KEYS_COLUMN)]  # the keys' lighting first: said over it too
    clock.now = 1.5
    watch.step()
    said = (f"Keys light first: {'off' if keys else 'on'}",)
    assert screens(launchkey)[-1] == with_note(tone_shown, said).data()
    clock.now = 0.5 + PICTURE_SHOWN  # the tone gone, still said, over the song
    watch.step()
    assert screens(launchkey)[-1] == with_note(song_picture(*TWILIGHT, done=30 / 260, sign="playing",
                                                            beside="0:30 / 4:20"), said).data()


def test_playing_the_song_knob_3_and_the_pads_below_go_through_its_sections():
    watch, gui, launchkey, clock = watching("play")  # at 0:30, in the first verse
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52)]
    clock.now = 0.5
    watch.step()
    launchkey.queued = [Turn(SECTION_KNOB, 1)]  # knob 3: the next section, the screen saying which
    clock.now = 1.0
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT[:3], "Chorus 1", done=50 / 260, sign="playing",
                                                  handle=True, beside="0:50 / 4:20").data()
    clock.now = 1.0 + TURN_WAIT + 0.01
    watch.step()
    assert gui.seeks == [50.0]  # once it stops turning
    gui.info["position"] = 65.0
    clock.now = 1.5
    watch.step()
    launchkey.queued = [Turn(SECTION_KNOB, -1)]  # back, 15 seconds into it: its start first
    clock.now = 2.0
    watch.step()
    launchkey.queued = [Turn(SECTION_KNOB, -1)]  # and on back: the verse
    watch.step()
    clock.now = 2.0 + TURN_WAIT + 0.01
    watch.step()
    assert gui.seeks == [50.0, 20.0]
    gui.info["position"] = 65.0
    clock.now = 2.5
    watch.step()
    launchkey.queued = [Pad(1, 2), Pad(1, 2)]  # the chorus pad, twice: the next chorus, then the one after
    clock.now = 3.0
    watch.step()
    clock.now = 3.0 + TURN_WAIT + 0.01
    watch.step()
    assert gui.seeks == [50.0, 20.0, 180.0] and pads(launchkey)[(1, 2)] == (127, 50, 0)  # in a chorus: bright
    launchkey.queued = [Pad(1, 2)]  # after the last: the first
    clock.now = 4.0
    watch.step()
    clock.now = 4.0 + TURN_WAIT + 0.01
    watch.step()
    assert gui.seeks[-1] == 50.0
    launchkey.queued = [Pad(1, 5)]  # no sixth kind: nothing
    watch.step()
    assert gui.seeks[-1] == 50.0


def test_playing_the_song_the_pads_on_top_and_knobs_4_and_5_set_its_loop():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    gui.info["position"] = 65.0
    watch.step()
    launchkey.queued = [Button(52), Pad(0, 0)]  # its start where the song is
    clock.now = 0.5
    watch.step()
    assert gui.loop_calls == [{"a": "here"}] and note(watch) == ("Loop from 1:05.0", None)
    assert pads(launchkey)[(0, 0)] == (9, False) and pads(launchkey)[(0, 1)] == (0, False)  # (pad 2: nothing)
    gui.info["position"] = 72.5
    launchkey.queued = [Pad(0, 0)]  # pad 1 again: its end, looping
    clock.now = 1.0
    watch.step()
    assert note(watch) == ("Loop 1:05.0-1:12.5: on", None) and pads(launchkey)[(0, 2)] == (21, True)
    launchkey.queued = [Pad(0, 2)]  # off
    clock.now = 1.5
    watch.step()
    assert note(watch) == ("Loop 1:05.0-1:12.5: off", None) and pads(launchkey)[(0, 2)] == (23, False)
    launchkey.queued = [Turn(LOOP_KNOBS[0], 2)]  # knob 4: its start 2 beats on, the song's screen showing it
    clock.now = 2.0
    watch.step()
    assert gui.loop_calls[-1] == {"move_a": 2} and screens(launchkey)[-1] == song_picture(
        *TWILIGHT[:3], "Loop A 1:06.0", done=72.5 / 260, sign="playing", beside="1:12 / 4:20",
        loop=(66 / 260, 72.5 / 260, False)).data()
    launchkey.queued = [Pad(0, 3)]  # the section it's in: looped
    clock.now = 3.0
    watch.step()
    assert note(watch) == ("Loop: Chorus 1", None) and pads(launchkey)[(0, 3)] == (37, False)
    launchkey.queued = [Pad(0, 3)]  # again: off
    clock.now = 3.5
    watch.step()
    assert note(watch) == ("Loop: off", None) and pads(launchkey)[(0, 3)] == (39, False)


def test_following_the_loop_is_riff_repeaters_and_the_sections_the_games():
    watch, gui, launchkey, clock = watching()
    gui.info["song"], gui.info["position"] = gui.song, 21.0
    watch.step()
    launchkey.queued = [Button(52), Pad(0, 0), Pad(0, 0)]  # ▼, then pad 1 twice: A and B where the game is
    clock.now = 0.5
    watch.step()
    assert gui.loop_calls == [{"a": "here"}, {"b": "here", "on": True, "riff": True}]  # (the gui sends it to the
    # game, its Riff Repeater opening on it)
    assert note(watch) == (None, ("Loop 0:21.0-0:21.0: on", "Riff Repeater: Start"))
    assert pads(launchkey)[(0, 2)][1] and pads(launchkey)[(1, 1)] == (0, 0, 0)  # on, pulsing; no section pads
    assert pads(launchkey)[(1, 0)] == (23, False)  # (but the Accelerator's)
    gui.game = {"answered": False, "hook": False, "riff": False}  # (the game without the mod)
    launchkey.queued = [Pad(0, 3)]  # the section it's in
    clock.now = 1.0
    watch.step()
    assert note(watch) == (None, ("Loop: Verse 1", "Game: no riffloop mod"))
    gui.game = {"answered": True, "hook": False, "riff": False}  # (an RS Re-ARMed without mods)
    launchkey.queued = [Pad(0, 2)]  # off
    clock.now = 1.5
    watch.step()
    launchkey.queued = [Pad(0, 2)]  # and on again
    clock.now = 2.0
    watch.step()
    assert note(watch)[1][1] == "Game: needs RS Re-ARMed 1.2+"


def test_playing_the_song_its_controls_take_the_screen_from_the_library():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52), pitch(40, 1.0), pitch(60, 1.01), pitch(80, 1.02), Strip("pitch", 8192, 1.03)]
    clock.now = 1.05  # flicked through the library: going on
    watch.step()
    assert watch._flick and screens(launchkey)[-1] == browsed(round(gui.browsed))
    launchkey.queued = [Pad(1, 2)]  # the chorus pad: the song's line takes the screen, the flick stopped
    clock.now = 1.2
    watch.step()
    chorus = song_picture(*TWILIGHT[:3], "Chorus 1", done=50 / 260, sign="playing", handle=True,
                          beside="0:50 / 4:20").data()
    assert not watch._flick and screens(launchkey)[-1] == chorus
    clock.now = 1.3
    watch.step()
    assert screens(launchkey)[-1] == chorus


def test_knob_8_goes_through_the_library_a_letter_at_a_time_while_it_shows():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [pitch(40, 1.0), pitch(44, 1.1)]  # the library on the screen
    clock.now = 1.2
    watch.step()
    assert screens(launchkey)[-1] == browsed(1)
    launchkey.queued = [Turn(CONTROLS, 2)]  # two letters on
    clock.now = 1.5
    watch.step()
    assert gui.letters == [2] and not watch.mix_knobs and screens(launchkey)[-1] == browsed(50)
    clock.now = 1.5 + BROWSE_SHOWN  # gone: knob 8 the volumes again
    watch.step()
    launchkey.queued = [Turn(CONTROLS, 1)]
    watch.step()
    assert gui.letters == [2] and watch.mix_knobs


def test_in_the_mixer_the_pitch_strip_moves_the_slider_last_used():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(105), pitch(64, 1.0), pitch(70, 1.01), pitch(75, 1.02)]  # all of them: 11 up
    clock.now = 1.1
    watch.step()
    assert gui.volume_changes[-1][0] == 114 and gui.keys["volume"] == 114 and gui.browsed == 0.0  # (not the library)
    launchkey.queued = [Strip("pitch", 8192, 1.2), pitch(20, 1.3), pitch(18, 1.31)]  # let go, put down again: from
    # there, 2 down
    clock.now = 1.4
    watch.step()
    assert gui.volume_changes[-1][0] == 112
    launchkey.queued = [Strip("pitch", 8192, 1.5), Pad(0, 1), pitch(20, 2.0), pitch(25, 2.01)]  # the rhythm's pad:
    # its slider
    clock.now = 2.1
    watch.step()
    assert gui.volume_changes[-1] == {1: 118} and watch.mix_knobs
    launchkey.queued = [Strip("pitch", 8192, 2.2), Turn(KEYS_COLUMN, 1), pitch(25, 2.5), pitch(23, 2.51)]  # knob 7:
    # its keys'
    clock.now = 2.6
    watch.step()
    assert gui.keys["volume"] == 110 and gui.volume_changes[-1] == {1: 118}
    launchkey.queued = [Strip("pitch", 8192, 2.7), Pad(0, CONTROLS), pitch(23, 3.0), pitch(26, 3.01)]  # Mute: all
    # of them again
    clock.now = 3.1
    watch.step()
    assert gui.volume_changes[-1][0] == 116 and gui.keys["volume"] == 114


def test_the_song_screen_shows_the_chord_played_from_the_arrangement_lighting_the_most_keys():
    watch, gui, launchkey, clock = watching("play")  # at 0:30; the lead lights 4 keys
    lead, rhythm, bass = gui.song["arrangements"]
    lead["chords"] = (Chord("E5", 29.0, 31.0), Chord("G5", 40.0, 41.0))
    rhythm["chords"] = (Chord("Em7", 28.0, 32.0), Chord("C", 35.0, 38.0))
    gui.info["song"] = gui.song

    def shown(chord, tag, at="0:30"):
        return song_picture(*TWILIGHT, done=float(at[2:]) / 260, sign="playing", beside=f"{at} / 4:20",
                            corner=chord, tag=tag).data()

    watch.step()
    assert screens(launchkey)[-1] == shown("E5", "L")  # (in the corner, the scale making room; whose before it)
    rhythm.update(play=True, lights=4)  # as many lit: the rhythm's first
    clock.now = 1.0
    watch.step()
    assert screens(launchkey)[-1] == shown("Em7", "R")
    gui.info["position"] = 36.0  # the lead has none there: the rhythm's
    clock.now = 2.0
    watch.step()
    assert screens(launchkey)[-1] == shown("C", "R", "0:36")
    gui.info["position"] = 39.0  # between chords: the last one
    clock.now = 3.0
    watch.step()
    assert screens(launchkey)[-1] == shown("C", "R", "0:39")
    gui.live = 39.9  # where it plays now, a little ahead: the lead's next
    clock.now = 4.0
    watch.step()
    assert screens(launchkey)[-1] == shown("G5", "L", "0:39")
    bass["kind"] = "lead"  # two leads: numbered
    clock.now = 5.0
    watch.step()
    assert screens(launchkey)[-1] == shown("G5", "L1", "0:39")
    gui.live, gui.info["position"] = None, 10.0  # before the first: none
    clock.now = 6.0
    watch.step()
    assert screens(launchkey)[-1] == shown(None, None, "0:10")


def test_following_the_chord_stays_while_the_game_pauses():
    watch, gui, launchkey, clock = watching()
    gui.song["arrangements"][0]["chords"] = (Chord("A5", 10.0, 12.0),)
    gui.info["song"], gui.live = gui.song, 10.5
    watch.step()
    followed = song_picture(*TWILIGHT, done=10.5 / 260, beside="0:10 / 4:20", corner="A5", tag="L").data()
    assert screens(launchkey)[-1] == followed  # (where the game is, and its time)
    gui.live = None  # paused
    clock.now = 1.0
    watch.step()
    assert screens(launchkey)[-1] == followed


def test_following_play_pauses_the_games_song_and_shows_it_however_it_was_paused():
    watch, gui, launchkey, clock = watching()
    gui.info["song"], gui.live = gui.song, 10.5
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT, done=10.5 / 260, beside="0:10 / 4:20").data()
    assert ("button", PLAY, 1, 20) in launchkey.calls  # (on a menu, or not known: no sign, Play dim)
    assert all(("button", number, 3, 127) in launchkey.calls for number in (104, 106, 107))  # (Rocksmith's keys)
    gui.info["state"] = "playing"  # the game's song playing
    clock.now = 0.5
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT, done=10.5 / 260, sign="playing",
                                                 beside="0:10 / 4:20").data()
    assert [c for c in launchkey.calls if c[:2] == ("button", PLAY)][-1] == ("button", PLAY, 3, 127)  # lit
    launchkey.queued = [Button(PLAY)]  # Play: paused
    clock.now = 1.0
    watch.step()
    assert gui.plays == [True] and screens(launchkey)[-1] == song_picture(
        *TWILIGHT, done=10.5 / 260, sign="paused", beside="0:10 / 4:20").data()
    assert [c for c in launchkey.calls if c[:2] == ("button", PLAY)][-1] == ("button", PLAY, 1, 20)
    gui.info["state"] = "playing"  # resumed in the game (its keyboard)
    clock.now = 1.5
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT, done=10.5 / 260, sign="playing",
                                                 beside="0:10 / 4:20").data()
    assert [c for c in launchkey.calls if c[:2] == ("button", PLAY)][-1] == ("button", PLAY, 3, 127)


def test_following_with_the_game_paused_knobs_3_and_4_step_through_its_song_as_its_keys_do():
    watch, gui, launchkey, clock = watching()
    gui.info["song"], gui.live, gui.info["state"] = gui.song, 10.5, "playing"
    watch.step()
    launchkey.queued = [Button(52), Turn(SEEK_KNOB, 1)]  # playing: not while it plays
    clock.now = 0.5
    watch.step()
    assert gui.game_keys == [] and note(watch) == (None, ("Rocksmith: pause first", "(Play), then step"))
    gui.info["state"] = "paused"  # (paused, however)
    clock.now = 1.0
    watch.step()
    launchkey.queued = [Turn(SEEK_KNOB, 3)]  # on: its right arrow, a step each
    clock.now = 1.5
    watch.step()
    assert gui.game_keys == ["right"] * 3 and note(watch) == ("Step on x3", None)
    launchkey.queued = [Turn(SEEK_KNOB, -20)]  # back, a fast turn: 8 at most
    clock.now = 2.0
    watch.step()
    assert gui.game_keys[3:] == ["left"] * 8
    launchkey.queued = [Turn(SECTION_KNOB, -1), Turn(SECTION_KNOB, 2)]  # knob 3: its phrases
    clock.now = 2.5
    watch.step()
    assert gui.game_keys[11:] == ["pagedown", "pageup", "pageup"] and note(watch) == ("Phrase on x2", None)
    gui.game_waiting = 6  # turned too fast: the game behind, none sent until it's caught up
    launchkey.queued = [Turn(SEEK_KNOB, 5)]
    clock.now = 3.0
    watch.step()
    assert gui.game_keys[14:] == ["right"]
    launchkey.queued = [Turn(SEEK_KNOB, 5)]
    clock.now = 3.05
    watch.step()
    assert gui.game_keys[14:] == ["right"]
    gui.game_waiting = 1
    launchkey.queued = [Turn(SEEK_KNOB, 2)]
    clock.now = 3.0 + 3 * 2 / 60
    watch.step()
    assert gui.game_keys[14:] == ["right"] * 3


def test_the_games_tone_mixer_brings_up_the_mixer_until_it_closes():
    watch, gui, launchkey, clock = watching()
    gui.info["song"], gui.info["position"] = gui.song, 21.0
    watch.step()
    assert not watch.mix_knobs
    gui.info["game_screen"] = "RSKeylights_Mixer"  # opened in the game
    clock.now = 0.5
    watch.step()
    assert watch.mix_knobs and watch.mix_page == "parts"
    clock.now = 5.0
    watch.step()
    assert watch.mix_knobs and gui.mixer_steps >= 1  # (kept up while it shows; what's done there asked each time round)
    gui.info["game_screen"] = "RSKeylights"  # closed there
    clock.now = 5.5
    watch.step()
    assert not watch.mix_knobs


def test_the_tones_on_the_screen_the_pads_below_follow_the_songs_tone_changes_or_not():
    watch, gui, launchkey, clock = watching("play")
    gui.song["arrangements"][0]["tone_changes_known"] = True  # (the lead has tone changes; the others none)
    gui.info["song"] = gui.song
    watch.step()
    lit = pads(launchkey)[(1, 0)]  # (its lit keys, as ever)
    launchkey.queued = [Turn(0, 1)]  # knob 1: the tones on the screen
    clock.now = 0.5
    watch.step()
    assert pads(launchkey)[(1, 0)] == (127, 45, 0) and pads(launchkey)[(1, 1)] == (0, 0, 0)  # (following; none)
    launchkey.queued = [Pad(1, 0)]  # its tone kept: dim
    clock.now = 1.0
    watch.step()
    assert gui.changes[-1] == ("L", {"follow_tones": False}) and pads(launchkey)[(1, 0)] == (19, 7, 0)
    assert gui.song["arrangements"][0]["lights"] == 4  # (its lit keys as they were)
    changes = len(gui.changes)
    launchkey.queued = [Pad(1, 1)]  # (none to follow: said, nothing changed)
    clock.now = 1.5
    watch.step()
    assert len(gui.changes) == changes and note(watch) == (None, ("Rhythm", "has no tone changes"))
    launchkey.queued = [Pad(1, 0)]  # (following again)
    clock.now = 2.0
    watch.step()
    assert gui.changes[-1] == ("L", {"follow_tones": None}) and pads(launchkey)[(1, 0)] == (127, 45, 0)
    clock.now = 2.0 + PICTURE_SHOWN  # the tones gone: its lit keys again
    watch.step()
    assert pads(launchkey)[(1, 0)] == lit


def test_master_mute_dims_the_arrangements_pads_outside_the_mixer_too():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    assert pads(launchkey)[(0, 0)] == (9, False)
    launchkey.queued = [Pad(0, CONTROLS)]  # Mute
    clock.now = 0.5
    watch.step()
    assert not gui.sound and pads(launchkey)[(0, 0)] == (25, 9, 0)  # (sounding: dimmed)
    assert pads(launchkey)[(0, 1)] == (0, 10, 2)  # (muted by itself: as dim as ever)


def test_the_mixers_6th_column_is_a_6th_arrangements_the_guitars_outside_it():
    watch, gui, launchkey, clock = watching("play")
    extra = [{"id": f"X{k}", "name": f"Alt {k}", "kind": "lead", "playing": False, "play": True, "lights": 0,
              "tones": {}} for k in (4, 5, 6)]
    gui.song["arrangements"] = gui.song["arrangements"][:3] + extra  # (six of them)
    gui.info["song"] = gui.song
    watch.step()
    assert pads(launchkey)[(0, GUITAR_COLUMN)] != (9, False)  # (outside the mixer: the guitar's)
    launchkey.queued = [Button(105)]  # Func: the mixer, six arrangements
    clock.now = 0.5
    watch.step()
    assert pads(launchkey)[(0, GUITAR_COLUMN)] == (9, False)  # (the 6th: a lead's)
    launchkey.queued = [Turn(GUITAR_COLUMN, -3)]  # knob 6: its volume
    clock.now = 1.0
    watch.step()
    assert gui.volume_changes[-1] == {5: gui.volumes[5]} and 5 in gui.volume_changes[-1]
    launchkey.queued = [Pad(0, GUITAR_COLUMN)]  # its pad: muted
    clock.now = 1.5
    watch.step()
    assert gui.changes[-1] == ("X6", {"play": False}) and pads(launchkey)[(0, GUITAR_COLUMN)] == (5, False)
    launchkey.queued = [Pad(0, CONTROLS)]  # Mute: those sounding dimmed (greyed above them), the muted red
    clock.now = 2.0
    watch.step()
    assert not gui.sound and pads(launchkey)[(0, 0)] == (25, 9, 0) and pads(launchkey)[(0, GUITAR_COLUMN)] == (5, False)


def test_the_games_mixer_and_the_launchkeys_turn_each_others_pages():
    watch, gui, launchkey, clock = watching()
    gui.info["song"], gui.info["position"] = gui.song, 21.0
    gui.game_stems = {"on": True, "guitar": 100, "bass": 100}  # (the stems page there: Song Stems on)
    gui.info["game_screen"], gui.info["game_mixer_page"] = "RSKeylights_Stems", {"turns": 3, "page": "parts"}
    watch.step()  # its STEM MIXER opened: on its stems page (a turn from before, not)
    assert watch.mix_knobs and watch.mix_page == "stems"
    gui.info["game_mixer_page"] = {"turns": 4, "page": "parts"}  # ▲ there
    clock.now = 0.5
    watch.step()
    assert watch.mix_page == "parts" and not getattr(gui, "game_pages", [])
    launchkey.queued = [Button(52)]  # the Launchkey's ▼: there too
    clock.now = 1.0
    watch.step()
    assert watch.mix_page == "stems" and gui.game_pages == ["stems"]
    gui.info["game_screen"] = "RSKeylights"  # closed there; its MIXER opened: on the parts page
    clock.now = 1.5
    watch.step()
    gui.info["game_screen"] = "RSKeylights_Mixer"
    clock.now = 2.0
    watch.step()
    assert watch.mix_knobs and watch.mix_page == "parts"


def test_something_done_in_the_games_mixer_is_looked_at_again_at_once():
    watch, gui, launchkey, clock = watching()
    gui.info["song"], gui.info["position"] = gui.song, 21.0
    gui.info["game_screen"] = "RSKeylights_Mixer"
    watch.step()
    looks, real = [], gui.remote_info
    gui.remote_info = lambda: looks.append(1) or real()
    clock.now = 0.05  # (before the next look's time)
    watch.step()
    assert not looks
    gui.game_mixer_step = lambda: True  # a pad pressed there
    clock.now = 0.1
    watch.step()
    assert looks  # (its rows sent back, and the Launchkey shown it, now: not a quarter of a second on)


def test_the_games_tones_keeps_the_tones_up_here_on_its_page_and_a_tone_stepped_there_shows():
    watch, gui, launchkey, clock = watching()
    gui.info["song"], gui.info["position"] = gui.song, 21.0
    gui.info["tone_field"] = {"turns": 0, "field": 0}
    gui.info["game_screen"] = "RSKeylights_Tones"  # opened in the game: the tones up here, kept, on this page
    watch.step()
    assert watch._picture_of == "parts" and watch._picture_until == math.inf and gui.tone_fields == [0]
    gui.song["arrangements"][2]["tones"] = {"tone": 306}  # the bass's stepped there: its, shown
    clock.now = 0.5
    watch.step()
    assert watch._picture_of == "parts" and watch._part_turned == 2 and watch._picture_until == math.inf
    gui.keys["tone"] = 2  # its keys': theirs a while, then the tones again
    clock.now = 1.0
    watch.step()
    assert watch._picture_of == "keys"
    clock.now = 1.0 + PICTURE_SHOWN + 0.1
    watch.step()
    clock.now += 0.3
    watch.step()
    assert watch._picture_of == "parts"
    gui.info["tone_field"] = {"turns": 1, "field": 1}  # its up arrow there: palm mutes here too
    clock.now += 0.3
    watch.step()
    assert watch.field == 1 and watch._picture_of == "parts"
    launchkey.queued = [Button(51)]  # the up arrow here: harmonics there too
    clock.now += 0.3
    watch.step()
    assert watch.field == 2 and gui.tone_fields[-1] == 2
    gui.song["arrangements"][0]["tones"] = {"pinch_tone": 300}  # the lead's harmonics stepped there: its, shown
    clock.now += 0.3
    watch.step()
    assert watch._part_turned == 0 and watch._picture_of == "parts"
    kits = [program for _name, program in DRUM_SETS]
    gui.song["arrangements"].append({"id": "drums", "name": "Drums", "kind": "drums", "playing": False, "play": True,
                                     "lights": 0, "tones": {}, "drum_set": kits[0]})
    clock.now += 0.3
    watch.step()
    gui.song["arrangements"][3]["drum_set"] = kits[1]  # its drum kit stepped there: shown whatever the page
    clock.now += 0.3
    watch.step()
    assert watch._picture_of == "parts" and watch._part_turned == 3
    gui.info["game_screen"] = "RSKeylights"  # closed there: the tones gone with it, and a change isn't its
    gui.song["arrangements"][2]["tones"] = {"tone": 307}
    watch._part_turned = 2
    clock.now += 0.3
    watch.step()
    assert watch._picture_of is None and watch._part_turned == 2


def test_pad_1_sets_the_loops_start_then_its_end_then_none():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52), Pad(0, 0)]  # playing the song: A here
    clock.now = 0.5
    watch.step()
    gui.info["position"] = 40.0
    launchkey.queued = [Pad(0, 0)]  # again: B, looping
    clock.now = 1.0
    watch.step()
    assert gui.loop["on"] and gui.loop["a"] == 30.0 and gui.loop["b"] == 40.0
    assert gui.loop_calls[-1] == {"b": "here", "on": True, "riff": True}  # (following, Riff Repeater would open)
    launchkey.queued = [Pad(0, 0)]  # a third time: none
    clock.now = 2.0
    watch.step()
    assert gui.loop_calls[-1] == {"a": None, "b": None, "on": False} and note(watch) == ("Loop cleared", None)
    launchkey.queued = [Pad(0, 0)]  # and again: A
    clock.now = 2.4
    watch.step()
    assert gui.loop_calls[-1] == {"a": "here"}
    launchkey.queued = [Pad(0, 1)]  # pad 2: nothing now
    clock.now = 3.0
    watch.step()
    assert len(gui.loop_calls) == 4


LYRICS = lyric_lines([Syllable("Hey", 20.0, 20.5), Syllable("lit-", 20.5, 21.0), Syllable("tle", 21.0, 21.5),
                      Syllable("kid+", 21.5, 22.0), Syllable("Did", 28.0, 28.5), Syllable("you", 29.0, 29.5),
                      Syllable("wake+", 30.0, 31.0), Syllable("Up+", 40.0, 41.0)])


def test_pad_6_shows_the_lyrics_as_theyre_sung_and_knob_6_goes_through_them():
    watch, gui, launchkey, clock = watching("play")  # at 0:30, in the first verse
    gui.song["lyrics"] = LYRICS
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52), Pad(0, 5)]  # playing the song: pad 6, the lyrics
    clock.now = 0.5
    watch.step()
    assert watch.lyrics_view and pads(launchkey)[(0, 5)] == (53, False)
    assert screens(launchkey)[-1] == lyrics_picture("Hey little kid", "Did you wake", "Up", sung=(8, 12), done=30 / 260,
                                                    sign="playing", time="0:30", said="Verse 1").data()
    launchkey.queued = [Turn(5, 1)]  # knob 6: the next line, Record taking the song there
    clock.now = 1.0
    watch.step()
    gone = lyrics_picture("Did you wake", "Up", "", done=30 / 260, sign="playing", time="0:30", said="● = go here")
    assert screens(launchkey)[-1] == gone.data()
    launchkey.queued = [Button(RECORD)]
    clock.now = 1.5
    watch.step()
    clock.now = 1.5 + TURN_WAIT + 0.01
    watch.step()
    assert gui.seeks == [39.0] and gui.stopped == 0  # (a second before it)
    launchkey.queued = [Turn(5, -1)]
    clock.now = 2.0
    watch.step()
    clock.now = 2.0 + 4.0  # a while after: with the song again
    watch.step()
    assert screens(launchkey)[-1] == lyrics_picture("Did you wake", "Up", "", done=39 / 260, sign="playing",
                                                    time="0:39", said="Verse 1").data()  # (the next line, waiting)
    launchkey.queued = [pitch(48, 7.0), pitch(40, 7.01)]  # the pitch strip: through them too (8 steps down: 2 lines
    # back)
    clock.now = 7.1
    watch.step()
    assert watch._lyric_line == 0.0 and gui.browsed == 0.0  # (not the library)
    gui.library[42]["id"] = "song"  # (the song, in the library)
    launchkey.queued = [Button(106)]  # Track back: the lyrics as they're sung, and the library back to the song
    clock.now = 7.5
    watch.step()
    live = lyrics_picture("Did you wake", "Up", "", done=39 / 260, sign="playing", time="0:39", said="Verse 1").data()
    assert watch.lyrics_view and screens(launchkey)[-1] == live and gui.browsed == 42.0
    launchkey.queued = [Turn(6, 1), Button(106)]  # (something else between: a first press again)
    clock.now = 7.6
    watch.step()
    assert watch.lyrics_view
    launchkey.queued = [Button(106)]  # pressed again: the song
    clock.now = 7.7
    watch.step()
    assert not watch.lyrics_view and screens(launchkey)[-1] == song_picture(
        *TWILIGHT, done=39 / 260, sign="playing", beside="0:39 / 4:20").data()
    launchkey.queued = [Pad(0, 5), Pad(0, 5)]  # pad 6, twice: the lyrics, then the song
    clock.now = 8.0
    watch.step()
    assert not watch.lyrics_view


def test_a_song_without_lyrics_says_so_and_following_they_show_too():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52), Pad(0, 5)]
    clock.now = 0.5
    watch.step()
    assert not watch.lyrics_view and note(watch) == ("No lyrics in this song", None)
    assert pads(launchkey)[(0, 5)] == (0, False)  # (no pad for them)
    watch, gui, launchkey, clock = watching()  # following: the game's place
    gui.song["lyrics"], gui.live = LYRICS, 21.0
    gui.info.update(song=gui.song, length=None)  # (as following has it: the game's)
    watch.step()
    launchkey.queued = [Button(52), Pad(0, 5)]
    clock.now = 0.5
    watch.step()
    assert screens(launchkey)[-1] == lyrics_picture("", "Hey little kid", "Did you wake", sung=(7, 10), time="0:21",
                                                    said="Verse 1").data()


def test_in_the_lyrics_view_playing_the_songs_knobs_show_there_too():
    watch, gui, launchkey, clock = watching("play")  # at 0:30
    gui.song["lyrics"] = LYRICS
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52), Pad(0, 5), Turn(5, -1), Turn(SEEK_KNOB, -8)]  # the lyrics, a line back, then
    # knob 1: 8 seconds back (the line gone back to forgotten)
    clock.now = 0.5
    watch.step()
    assert watch.lyrics_view and screens(launchkey)[-1] == lyrics_picture(
        "", "Hey little kid", "Did you wake", sung=(11, 14), done=22 / 260, sign="playing", time="0:22",
        said="Verse 1", handle=True).data()  # (where it's going: its line, a handle on the progress line)
    clock.now = 0.5 + TURN_WAIT + 0.01
    watch.step()
    assert gui.seeks == [22.0]
    launchkey.queued = [Turn(SPEED_KNOB, -5)]  # knob 2: its speed, where the section was, the progress line turned
    # into it
    clock.now = 1.0
    watch.step()
    assert screens(launchkey)[-1] == lyrics_picture("", "Hey little kid", "Did you wake", sung=(11, 14), done=22 / 260,
                                                    sign="playing", time="0:22", said="Speed 95%", speed=0.95).data()
    launchkey.queued = [Pad(0, 0), Turn(LOOP_KNOBS[1], 4)]  # the loop's start here, its end 4 beats on: brackets
    clock.now = 1.5
    watch.step()
    assert screens(launchkey)[-1] == lyrics_picture("", "Hey little kid", "Did you wake", sung=(11, 14), done=22 / 260,
                                                    sign="playing", time="0:22", said="Loop B 0:24.0",
                                                    loop=(22 / 260, 24 / 260, False)).data()


def test_playing_the_song_knob_7_picks_whose_chords_show():
    watch, gui, launchkey, clock = watching("play")  # at 0:30; the lead lights its keys
    lead, rhythm, _bass = gui.song["arrangements"]
    lead["chords"], rhythm["chords"] = (Chord("E5", 29.0, 31.0),), (Chord("Em7", 28.0, 32.0),)
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52), Turn(6, 2)]  # knob 7, two on: auto, the lead, the rhythm
    clock.now = 0.5
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT[:3], "Chords: Rhythm", done=30 / 260, sign="playing",
                                                  beside="0:30 / 4:20", corner="Em7", tag="R").data()
    launchkey.queued = [Turn(6, 1)]  # the bass: none of its own
    clock.now = 1.0
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT[:3], "Chords: Bass", done=30 / 260, sign="playing",
                                                  beside="0:30 / 4:20").data()
    launchkey.queued = [Turn(6, 1)]  # and round: picked as they go again
    clock.now = 1.5
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT[:3], "Chords: auto", done=30 / 260, sign="playing",
                                                  beside="0:30 / 4:20", corner="E5", tag="L").data()
    launchkey.queued = [Turn(6, -2)]  # the rhythm's; another song: auto again
    clock.now = 2.0
    watch.step()
    gui.info["song"] = {**gui.song, "id": "other"}
    clock.now = 2.5
    watch.step()
    assert watch._chords_from is None


def test_a_tone_change_the_song_passes_is_said():
    watch, gui, launchkey, clock = watching("play")
    gui.song["arrangements"][0]["tone_changes"] = ((-math.inf, "clean"), (40.0, "distortion"), (90.0, "its own tone"))
    gui.info.update(song=gui.song, position=39.5)
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT, done=39.5 / 260, sign="playing", beside="0:39 / 4:20",
                                                  badge="Clean").data()  # (the tone it's on: what it starts with)
    gui.info["position"] = 40.2  # passed: the lead's (the arrangement whose chords show)
    clock.now = 0.3
    watch.step()
    assert note(watch) == ("Lead: distortion", None)
    clock.now = 4.0  # (what was said gone) the tone it's on now
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT, done=40.2 / 260, sign="playing", beside="0:40 / 4:20",
                                                  badge="Dist").data()
    gui.info["position"] = 95.0  # jumped past one: nothing said, but it's on its own tone again (the clean one)
    clock.now = 5.0
    watch.step()
    assert note(watch) is None and screens(launchkey)[-1] == song_picture(
        *TWILIGHT, done=95 / 260, sign="playing", beside="1:35 / 4:20", badge="Clean").data()


def test_an_arrangement_on_another_device_its_knob_steps_its_programs():
    watch, gui, launchkey, clock = watching("play")
    bass = gui.song["arrangements"][2]
    bass["output"] = {"port": "monologue SOUND", "program": None}
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Turn(2, 1)]  # knob 3, the bass's: from its own sound, the first program
    clock.now = 0.5
    watch.step()
    assert gui.programs == [("bass", 0)] and screens(launchkey)[-1] == parts_picture(
        "Bass program", 1, "Program", "monologue SOUND", 3, 0).data()
    launchkey.queued = [Turn(CONTROLS, 2)]  # knob 8: ten at a time
    clock.now = 1.0
    watch.step()
    assert gui.programs[-1] == ("bass", 20)


def test_picked_with_no_chords_an_arrangements_notes_show():
    from rs_keylights.sng import Note
    watch, gui, launchkey, clock = watching("play")  # at 0:30
    bass = gui.song["arrangements"][2]
    bass["notes"] = (Note(29.0, 0, (31,)), Note(31.0, 0, (34,)))  # G1, then Bb1
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52), Turn(6, 3)]  # knob 7: auto, lead, rhythm, bass
    clock.now = 0.5
    watch.step()
    assert screens(launchkey)[-1] == song_picture(*TWILIGHT[:3], "Chords: Bass", done=30 / 260, sign="playing",
                                                  beside="0:30 / 4:20", corner="G1", tag="B").data()


def test_column_6_is_the_guitar_knob_6_its_pedals_presets():
    watch, gui, launchkey, clock = watching()
    gui.info["song"] = gui.song
    watch.step()
    assert pads(launchkey)[(0, GUITAR_COLUMN)] == (10, 8, 0)  # on none of its favourites: yellow, dim
    assert pads(launchkey)[(1, GUITAR_COLUMN)] == (127, 100, 0)  # following the song's tones: bright
    launchkey.queued = [Turn(GUITAR_COLUMN, 1)]  # knob 6: the next preset, once the knob rests
    clock.now = 0.5
    watch.step()
    assert gui.pedal_calls == [({"slot": 50}, PEDAL_WAIT)]
    assert screens(launchkey)[-1] == pedal_picture(50, "5050 DS", "Pad: 051 / 187").data()
    launchkey.queued = [Turn(CONTROLS, -1)]  # knob 8, while it shows: ten at a time, round from the first
    clock.now = 1.0
    watch.step()
    assert gui.pedal_calls[-1] == ({"slot": 40}, PEDAL_WAIT)
    launchkey.queued = [Turn(GUITAR_COLUMN, -40)]
    clock.now = 1.5
    watch.step()
    assert gui.pedal_calls[-1] == ({"slot": 200}, PEDAL_WAIT)
    assert screens(launchkey)[-1] == pedal_picture(200, "My love", "Pad: 051 / 187").data()


def test_the_guitars_pads_go_through_its_favourites_and_hold_its_tone():
    watch, gui, launchkey, clock = watching()
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Pad(0, GUITAR_COLUMN)]  # on none of them: the first
    clock.now = 0.5
    watch.step()
    assert gui.pedal_calls == [({"slot": 51}, 0.0)]
    assert screens(launchkey)[-1] == pedal_picture(51, "P51", "Pad: 051 / 187").data()
    assert pads(launchkey)[(0, GUITAR_COLUMN)] == (13, False)  # on one of them: bright
    launchkey.queued = [Pad(0, GUITAR_COLUMN)]  # the next
    clock.now = 1.0
    watch.step()
    launchkey.queued = [Pad(0, GUITAR_COLUMN)]  # round to the first
    clock.now = 1.5
    watch.step()
    assert [body["slot"] for body, _later in gui.pedal_calls] == [51, 187, 51]
    launchkey.queued = [Pad(1, GUITAR_COLUMN)]  # the bottom pad: its tone held (not switched with the song's)
    clock.now = 2.0
    watch.step()
    assert gui.pedal["hold"] and note(watch) == ("Pedal: holding this tone", None)
    assert pads(launchkey)[(1, GUITAR_COLUMN)] == (19, 15, 0)  # (dim)
    launchkey.queued = [Pad(1, GUITAR_COLUMN)]  # again: following them
    clock.now = 2.5
    watch.step()
    assert not gui.pedal["hold"] and note(watch) == ("Pedal: following the song's tones", None)
    assert pads(launchkey)[(1, GUITAR_COLUMN)] == (127, 100, 0)
    assert [body for body, _later in gui.pedal_calls][-2:] == [{"hold": True}, {"hold": False}]
    gui.pedal = {**gui.pedal, "error": "The pedal didn't answer: unplug it and plug it in again"}
    launchkey.queued = [Turn(GUITAR_COLUMN, 1)]
    clock.now = 3.0
    watch.step()
    assert screens(launchkey)[-1] == pedal_picture(52, "P52", "Pedal didn't answer").data()
    gui.pedal = {**gui.pedal, "favourites": []}
    clock.now = 3.5
    watch.step()
    launchkey.queued = [Pad(0, GUITAR_COLUMN)]
    clock.now = 4.0
    watch.step()
    assert note(watch) == ("Pedal: no favourites", None) and len(gui.pedal_calls) == 6  # (none more)


def test_following_record_keeps_the_pedals_preset_shown_for_the_tone_playing():
    watch, gui, launchkey, clock = watching()
    gui.info["song"] = gui.song
    gui.info["mode"] = "follow"
    gui.pedal = {**gui.pedal, "tone": {"key": "s_dist", "short": "dist", "pedal": 51, "own": False}}
    watch.step()
    launchkey.queued = [Turn(GUITAR_COLUMN, 1)]  # knob 6: a preset to try
    clock.now = 0.5
    watch.step()
    assert screens(launchkey)[-1] == pedal_picture(50, "5050 DS", "Rec: keep for dist").data()
    launchkey.queued = [Button(RECORD)]  # kept for the tone playing, not the game's Enter
    clock.now = 1.0
    watch.step()
    assert gui.kept == [50] and note(watch) == ("dist: 050 kept", None)
    assert "enter" not in gui.game_keys
    assert _pedal_tone_said(gui.pedal["tone"], 50) == "Rec: dist automatic"  # (under what's said, on the screen)
    assert _pedal_tone_said({"short": "dist", "pedal": 51, "own": False}, 51) == "dist: its preset"
    launchkey.queued = [Button(RECORD)]  # again: back to automatic
    clock.now = 1.5
    watch.step()
    assert gui.kept == [50, 50] and note(watch) == ("dist: automatic", None)
    clock.now = 10.0  # (the pedal's picture gone: Record is the game's Enter again)
    watch.step()
    launchkey.queued = [Button(RECORD)]
    clock.now = 10.5
    watch.step()
    assert gui.kept == [50, 50]


def test_the_guitar_column_is_dark_playing_the_song_and_in_the_mixer():
    watch, gui, launchkey, clock = watching()
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Turn(CONTROLS, 1)]  # knob 8: the mixer, for a while
    clock.now = 0.5
    watch.step()
    assert watch.mix_knobs and pads(launchkey)[(0, GUITAR_COLUMN)] == (0, False)
    assert pads(launchkey)[(1, GUITAR_COLUMN)] == (0, 0, 0)
    launchkey.queued = [Turn(GUITAR_COLUMN, 1)]  # knob 6 in the mixer: not the pedal (the game's music: below)
    clock.now = 1.0
    watch.step()
    assert gui.pedal_calls == []


def test_following_the_games_music_is_on_the_mixers_stems_page_not_knob_6():
    watch, gui, launchkey, clock = watching()
    gui.info["song"], gui.music = gui.song, 76
    watch.step()
    launchkey.queued = [Button(105)]  # Func: the mixer, the arrangements and its keys
    clock.now = 0.5
    watch.step()
    names, quiet, lights = ["Lead", "Rhythm", "Bass", "Keys"], [False, True, True, False], [1, 0, 0, 1]
    assert screens(launchkey)[-1] == mixer_picture(names, [79] * 4, None, quiet=quiet, lights=lights,
                                                   tops=[100] * 4).data()
    launchkey.queued = [Turn(GUITAR_COLUMN, -3)]  # knob 6: where it went, said
    clock.now = 1.0
    watch.step()
    assert gui.music == 76 and gui.pedal_calls == [] and note(watch) == (None, ("The game's Music:", "▼, then knob 7"))
    launchkey.queued = [Button(52), Turn(KEYS_COLUMN, -3)]  # ▼, knob 7 (without Song Stems: its Music still)
    clock.now = 1.5
    watch.step()
    assert gui.music == 73 and screens(launchkey)[-1] == mixer_picture(
        ["Bs", "Gtr", "Music"], [100, 100, 73], 2, quiet=[True, True, False], tops=[100] * 3, divider=2).data()
    watch, gui, launchkey, clock = watching("play")  # not following: no slider for it, knob 6 nothing
    gui.info["song"], gui.music = gui.song, 76
    watch.step()
    launchkey.queued = [Button(105), Turn(GUITAR_COLUMN, 5)]
    clock.now = 0.5
    watch.step()
    assert gui.music == 76 and screens(launchkey)[-1] == mixer_picture(
        names[:3] + ["Keys"], [79] * 4, None, quiet=[False, True, True, False], lights=[1, 0, 0, 1],
        tops=[100] * 4).data()


def test_knob_8_while_the_loop_shows_moves_its_end_a_bar_at_a_time():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52), Turn(LOOP_KNOBS[1], 2)]  # ▼, knob 5: the end, two beats on
    clock.now = 0.5
    watch.step()
    launchkey.queued = [Turn(CONTROLS, 2)]  # knob 8, while the loop shows: two bars on
    clock.now = 1.0
    watch.step()
    assert gui.loop_calls[-1] == {"move_b_bars": 2} and gui.loop["b"] == 35.0 and not watch.mix_knobs


def test_following_knob_2_and_the_pad_below_set_riff_repeaters_speed_and_accelerator():
    watch, gui, launchkey, clock = watching()
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(52), Turn(SPEED_KNOB, -6), Turn(SPEED_KNOB, -3)]  # ▼, knob 5: 1% a step, set as it
    # turns (the game's own slider moving with it), each from the one before
    clock.now = 0.5
    watch.step()
    assert gui.riff_calls == [{"speed": 94}, {"speed": 91}] and gui.speeds == []  # Riff Repeater's, not the page's
    gui.info["speed"] = 0.55  # (moved in the game, by its own slider: from there)
    clock.now = 2.0
    watch.step()
    launchkey.queued = [Turn(SPEED_KNOB, 2)]
    clock.now = 2.1
    watch.step()
    assert gui.riff_calls[-1] == {"speed": 57}
    gui.info["speed"] = 1.99  # (past 100%: to 200%)
    clock.now = 4.0
    watch.step()
    launchkey.queued = [Turn(SPEED_KNOB, 5)]
    clock.now = 4.1
    watch.step()
    assert gui.riff_calls[-1] == {"speed": 200} and len(gui.riff_calls) == 4
    clock.now = 4.1 + TURN_WAIT + 0.05  # (nothing more once it stops turning)
    watch.step()
    assert len(gui.riff_calls) == 4
    assert pads(launchkey)[(1, 0)] == (23, False)  # the Accelerator: off, dim green
    launchkey.queued = [Pad(1, 0)]
    clock.now = 6.5
    watch.step()
    assert gui.riff_calls[-1] == {"accelerator": True} and note(watch) == (None, ("Accelerator: on", "In Riff Repeater"))
    clock.now = 8.0
    watch.step()
    assert pads(launchkey)[(1, 0)] == (21, False)


def test_a_songs_drum_part_is_one_more_arrangement_its_pad_mutes_it_and_the_mixer_sets_its_volume():
    watch, gui, launchkey, clock = watching("play")
    gui.song["arrangements"].append({"id": "drums", "name": "Drums", "kind": "drums", "playing": False, "play": True,
                                     "lights": 0, "tones": {}, "volume": 90, "drum_set": 0})
    gui.info["song"] = gui.song
    watch.step()
    assert pads(launchkey)[(0, 3)] == (3, False) and pads(launchkey)[(1, 3)] == (0, 0, 0)  # (white; no lit keys)
    launchkey.queued = [Pad(0, 3)]  # its pad on top: muted
    clock.now = 0.5
    watch.step()
    assert gui.changes[-1] == ("drums", {"play": False})
    launchkey.queued = [Pad(1, 3)]  # the one below: the beat lights (nothing of the drums changed)
    clock.now = 1.0
    watch.step()
    assert len(gui.changes) == 1 and note(watch) == ("Beat lights: on", None)
    launchkey.queued = [Turn(3, 1)]  # its knob, outside the mixer: the keyboard's drum kits, in turn
    clock.now = 1.5
    watch.step()
    assert gui.changes[-1] == ("drums", {"kit": 8}) and watch._picture_of == "parts"  # (Standard, then Room)
    gui.song["arrangements"][-1]["drum_set"] = 8  # (as the page has it now)
    launchkey.queued = [Turn(3, -2)]  # (back past the first: the last)
    clock.now = 1.6
    watch.step()
    assert gui.changes[-1] == ("drums", {"kit": 48})
    gui.changes.pop()
    gui.changes.pop()
    launchkey.queued = [Button(105), Turn(3, -10)]  # the mixer: its knob, its own volume (not a place's)
    clock.now = 2.0
    watch.step()
    assert gui.changes[-1] == ("drums", {"volume": 77}) and gui.volume_changes == []
    launchkey.queued = [Turn(CONTROLS, 2)]  # knob 8: all of them, its too
    clock.now = 2.5
    watch.step()
    assert gui.changes[-1] == ("drums", {"volume": 80}) and 3 not in gui.volume_changes[-1]


def test_a_songs_drums_not_made_yet_are_a_dim_pad_that_makes_them():
    watch, gui, launchkey, clock = watching("play")
    drums = {"id": "drums", "name": "Drums", "kind": "drums", "playing": False, "play": False, "lights": 0, "tones": {},
             "volume": 100, "made": False, "making": False}
    gui.song["arrangements"].append(drums)
    gui.info["song"] = gui.song
    watch.step()
    assert pads(launchkey)[(0, 3)] == (12, 12, 12) and pads(launchkey)[(1, 3)] == (0, 0, 0)  # (dim white)
    launchkey.queued = [Pad(0, 3)]
    clock.now = 0.5
    watch.step()
    assert gui.changes[-1] == ("drums", {"make": True}) and note(watch) == (None, ("Drums: making them", "(a minute or so)"))
    drums["making"] = True  # (being made: pulsing; a tap says so)
    clock.now = 1.0
    watch.step()
    assert pads(launchkey)[(0, 3)] == (3, True)
    launchkey.queued = [Pad(0, 3), Button(105), Turn(3, 5)]  # (and in the mixer: no volume yet)
    clock.now = 1.5
    watch.step()
    assert len(gui.changes) == 1 and note(watch) == (None, ("Drums: not made yet", "Their pad on top makes them"))
    drums.update(made=True, making=False, play=True)  # (made: as it would be after)
    launchkey.queued = [Button(105)]
    clock.now = 2.0
    watch.step()
    assert pads(launchkey)[(0, 3)] == (3, False)


def test_the_drums_pad_below_turns_beat_lights_on_and_they_flash_on_each_beat():
    watch, gui, launchkey, clock = watching("play")
    gui.song["arrangements"].append({"id": "drums", "name": "Drums", "kind": "drums", "playing": False, "play": True,
                                     "lights": 0, "tones": {}, "volume": 100, "made": True})
    gui.song["beats"], gui.song["bars"] = (1.0, 1.5, 2.0, 2.5, 3.0), (1.0, 3.0)
    gui.info["song"] = gui.song
    gui.live = 1.2
    watch.step()
    assert pads(launchkey)[(1, 3)] == (0, 0, 0)  # (off: dark)
    launchkey.queued = [Pad(1, 3)]
    clock.now = 0.5
    watch.step()
    assert gui.info["beat_lights"] and note(watch) == ("Beat lights: on", None)
    assert pads(launchkey)[(1, 3)] == (8, 8, 8)  # (on, between beats: dim)
    for at, lit in ((1.52, (55, 55, 55)), (1.7, (8, 8, 8)), (3.03, (127, 127, 127))):  # (a beat; between; a bar's first)
        gui.live = at
        clock.now += 0.03
        watch.step()
        assert pads(launchkey)[(1, 3)] == lit, at
    gui.info["paused"] = True  # (paused, as it next looks: no beats)
    gui.live = 2.5
    clock.now += 0.3
    watch.step()
    assert pads(launchkey)[(1, 3)] == (8, 8, 8)


def test_beat_lights_pulse_the_lit_pads_below_dimming_them_between_beats():
    watch, gui, launchkey, clock = watching("play")
    gui.song["arrangements"].append({"id": "drums", "name": "Drums", "kind": "drums", "playing": False, "play": True,
                                     "lights": 0, "tones": {}, "volume": 100, "made": True})
    gui.song["beats"], gui.song["bars"] = (1.0, 1.5, 2.0), (1.0,)
    gui.info["song"], gui.info["beat_lights"], gui.live = gui.song, True, 1.52
    watch.step()

    def play_button():
        return next(c[2:] for c in reversed(launchkey.calls) if c[:2] == ("button", 115))

    sound, follow = pads(launchkey)[(0, 7)], pads(launchkey)[(1, 7)]
    assert sound[0] == 21 and follow == (39, False) and play_button() == (3, 127)  # (on a beat: as they are)
    gui.live, clock.now = 1.7, clock.now + 0.03  # (between: the lit pads below dim, the beat's pad as it is)
    watch.step()
    assert pads(launchkey)[(1, 7)] == (0, 2, 4) and pads(launchkey)[(1, 3)] == (8, 8, 8)
    assert pads(launchkey)[(0, 7)] == sound and play_button() == (3, 127)  # (on top, and the buttons: steady)
    gui.live, clock.now = 2.01, clock.now + 0.03
    watch.step()
    assert pads(launchkey)[(1, 7)] == follow
    gui.info["beat_lights"] = False  # (off, as it next looks: as they are, between beats too)
    gui.live, clock.now = 2.2, clock.now + 0.3
    watch.step()
    assert pads(launchkey)[(1, 7)] == follow


def test_the_screen_is_drawn_at_most_every_picture_gap_the_newest_then():
    """The Launchkey takes ~37 ms to take in a picture, and what's sent after waits: a knob turning quickly in the
    mixer sends a picture at most every PICTURE_GAP (the newest), not one a step."""
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(105)]  # (Func: the mixer)
    clock.now = 0.5
    watch.step()
    drawn = len(screens(launchkey))
    for i in range(1, 51):  # (a second of turning: a step every 20 ms)
        launchkey.queued = [Turn(0, -1)]  # (down: a volume at each step)
        clock.now = 0.5 + i * 0.02
        watch.step()
    sent = len(screens(launchkey)) - drawn
    assert 8 <= sent <= 11, sent
    clock.now += PICTURE_GAP
    watch.step()
    assert screens(launchkey)[-1] == watch._screen(clock.now)[1]().data()  # (where it stopped, shown)


def test_a_drum_part_being_made_shows_how_far_on_the_songs_screen():
    watch, gui, launchkey, clock = watching("play")
    drums = {"id": "drums", "name": "Drums", "kind": "drums", "playing": False, "play": False, "lights": 0, "tones": {},
             "volume": 100, "made": False, "making": True, "progress": 0.416}
    gui.song["arrangements"].append(drums)
    gui.info["song"] = gui.song
    watch.step()
    assert "Drums 42%" in watch._screen(clock.now)[0][0]  # (in the key's notes' place: short, to fit)
    drums.update(making=False, made=True, progress=None)  # (made: the key's notes again)
    clock.now = 0.5
    watch.step()
    assert not any("Drums 4" in str(x) for x in watch._screen(clock.now)[0][0])


def test_the_mixers_other_page_is_the_songs_parts_a_knob_and_a_mute_each():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(105)]  # Func: the mixer, on the arrangements' volumes
    clock.now = 0.5
    watch.step()
    assert watch.mix_page == "parts" and ("button", 51, 3, 127) in launchkey.calls  # (the up arrow lit: its page)
    launchkey.queued = [Button(52)]  # the down arrow: the song's parts'
    clock.now = 1.0
    watch.step()
    assert watch.mix_knobs and watch.mix_page == "stems"
    assert launchkey.calls.index(("button", 52, 3, 127)) > launchkey.calls.index(("button", 51, 3, 127))
    names = ["Dr", "Bs", "Gtr", "Vx", "Ky", "Oth", "Music"]

    def mixer(volumes, turned, quiet):
        return mixer_picture(names, volumes, turned, quiet=quiet, tops=[100] * 7, divider=6).data()  # (a line
        # before its Music: apart)

    off = [False] * 6 + [True]  # (the song's audio: off as it starts)
    assert screens(launchkey)[-1] == mixer([100] * 6 + [80], None, off)
    assert pads(launchkey)[(0, 0)] == (100, 100, 100) and pads(launchkey)[(0, 2)] == (127, 45, 0)  # a part each
    assert pads(launchkey)[(0, KEYS_COLUMN)] == (5, False) and pads(launchkey)[(0, CONTROLS)] == (21, False)  # (its
    assert pads(launchkey)[(1, 0)] == (0, 0, 0)  # (none below)                         Music off; its parts sounding)
    launchkey.queued = [Turn(2, -30)]  # knob 3: the guitar's volume
    clock.now = 1.5
    watch.step()
    assert gui.options[-1] == {"stem_volumes": {"guitar": 70}}
    assert screens(launchkey)[-1] == mixer([100, 100, 70, 100, 100, 100, 80], 2, off)
    launchkey.queued = [Turn(2, -90)]  # (to none, no lower)
    clock.now = 2.0
    watch.step()
    assert gui.audio["volumes"]["guitar"] == 0
    launchkey.queued = [Pad(0, 0)]  # the drums' pad on top: muted (dimmed, greyed: its volume kept)
    clock.now = 2.5
    watch.step()
    assert gui.options[-1] == {"stem_mutes": {"drums": True}} and pads(launchkey)[(0, 0)] == (8, 8, 8)
    assert screens(launchkey)[-1] == mixer([100, 100, 0, 100, 100, 100, 80], 0, [True] + off[1:])
    launchkey.queued = [Pad(0, 0)]  # again: back
    clock.now = 3.0
    watch.step()
    assert gui.audio["mutes"]["drums"] is False and pads(launchkey)[(0, 0)] == (100, 100, 100)
    launchkey.queued = [Pad(0, KEYS_COLUMN)]  # its Music's pad: the song's audio on (teal), not the keyboard's mute
    clock.now = 3.5
    watch.step()
    assert gui.audio["on"] and gui.sound and pads(launchkey)[(0, KEYS_COLUMN)] == (0, 90, 100)
    launchkey.queued = [Turn(KEYS_COLUMN, 15)]  # knob 7: its volume
    clock.now = 4.0
    watch.step()
    assert gui.options[-1] == {"audio_volume": 95}
    assert screens(launchkey)[-1] == mixer([100, 100, 0, 100, 100, 100, 95], 6, [False] * 7)
    launchkey.queued = [Turn(CONTROLS, -10)]  # knob 8: its parts together (its Music apart)
    clock.now = 4.2
    watch.step()
    assert gui.options[-1] == {"stem_volumes": {"drums": 90, "bass": 90, "guitar": 0, "vocals": 90, "piano": 90,
                                                "other": 90}}
    launchkey.queued = [Pad(0, CONTROLS)]  # the right pad: the page's master mute, its parts (not the keyboard's)
    clock.now = 4.3
    watch.step()
    assert all(gui.audio["mutes"].values()) and gui.sound and gui.audio["on"] and pads(launchkey)[(0, CONTROLS)] == (5, False)
    launchkey.queued = [Pad(0, CONTROLS)]  # (and back)
    clock.now = 4.4
    watch.step()
    assert not any(gui.audio["mutes"].values()) and pads(launchkey)[(0, CONTROLS)] == (21, False)
    options = len(gui.options)
    launchkey.queued = [Pad(1, 1)]  # a pad below: what the page does
    clock.now = 4.5
    watch.step()
    assert len(gui.options) == options and gui.changes == []
    assert note(watch) == (None, ("Pads on top: mute a part", "Knobs: its volume, 8: all")) and watch.mix_knobs
    launchkey.queued = [Button(51)]  # the up arrow: the arrangements' volumes again
    clock.now = 5.0
    watch.step()
    assert watch.mix_page == "parts" and watch.mix_knobs and pads(launchkey)[(0, 0)] == (9, False)  # (the lead's)
    launchkey.queued = [Button(52), Button(105)]  # its parts', then Func: closed
    clock.now = 5.5
    watch.step()
    assert not watch.mix_knobs and watch.mode == "parts"
    launchkey.queued = [Button(105)]  # opened again: on the page it was kept on
    clock.now = 6.0
    watch.step()
    assert watch.mix_page == "stems"
    launchkey.queued = [Button(105), Turn(CONTROLS, 1)]  # closed; knob 8 alone: the arrangements' volumes, as ever
    clock.now = 6.5
    watch.step()
    assert watch.mix_knobs and watch.mix_page == "parts" and gui.keys["volume"] == 102
    launchkey.queued = [Button(52)]  # (up for a while, not kept: the arrow's the knobs' on playing the song, as ever)
    clock.now = 7.0
    watch.step()
    assert not watch.mix_knobs and watch.mode == "playback"


def test_a_song_not_split_is_split_from_a_parts_pad_and_not_while_following():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    gui.audio.update(made=False)
    watch.step()
    launchkey.queued = [Button(105), Button(52)]
    clock.now = 0.5
    watch.step()
    assert note(watch) == (None, ("Song not split yet:", "a pad on top splits it"))
    assert pads(launchkey)[(0, 1)] == (0, 5, 15)  # (its parts' pads: dim)
    launchkey.queued = [Turn(0, 5)]  # a part's knob: nothing to turn yet
    clock.now = 1.0
    watch.step()
    assert gui.options == [] and note(watch) == (None, ("Song not split yet:", "a pad on top splits it"))
    launchkey.queued = [Pad(0, 3)]  # a part's pad: it's split (a minute or two: pulsing)
    clock.now = 1.5
    watch.step()
    assert gui.splits == [{"song": "song", "make": True}] and pads(launchkey)[(0, 3)] == (3, True)
    assert note(watch) is None and watch._screen_shown[1] == ("progress", "Splitting", 0)  # (its progress, at once,
    gui.audio.update(making=True, progress=0.42)                                           # kept at the bottom)
    clock.now = 2.0
    watch.step()  # (what the page has now)
    launchkey.queued = [Pad(0, 3)]  # again: not started twice, nothing said over it
    clock.now = 2.1
    watch.step()
    assert len(gui.splits) == 1 and note(watch) is None and watch._screen_shown[1] == ("progress", "Splitting", 21)
    launchkey.queued = [Turn(KEYS_COLUMN, -10)]  # (its Music, the song's own volume: split or not)
    clock.now = 2.5
    watch.step()
    assert gui.options[-1] == {"audio_volume": 70}
    gui.audio.update(made=False, making=False, can=False)  # (the separator not rebuilt)
    clock.now = 3.0
    watch.step()
    launchkey.queued = [Pad(0, 0)]
    clock.now = 3.1
    watch.step()
    assert len(gui.splits) == 1 and note(watch) == (None, ("To split songs, run", "tools/drums-setup.sh"))

    watch, gui, launchkey, clock = watching()  # following: the game plays the song
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(105), Button(52)]
    clock.now = 0.5
    watch.step()
    assert watch.mix_knobs and watch.mix_page == "stems"  # (its page still: the game's Music on it)
    assert note(watch) == (None, ("The game's song parts", "need rearmed-mods"))  # (no RSMods.ini)
    assert pads(launchkey)[(0, 0)] == (0, False) and pads(launchkey)[(0, 2)] == (0, False)  # (nothing to split
    launchkey.queued = [Pad(0, 0)]                                                          # here, none to mute)
    clock.now = 0.7
    watch.step()
    assert gui.splits == []
    gui.game_stems = {"on": False, "guitar": 100, "bass": 100}  # (rearmed-mods, its Song Stems off)
    clock.now = 1.0
    watch.step()
    launchkey.queued = [Button(52)]
    clock.now = 1.5
    watch.step()
    assert watch.mix_page == "stems" and note(watch) == (None, ("Song Stems is off:", "turn it on in the mods"))


def test_following_the_mixers_other_page_is_the_games_song_its_bass_and_guitar_and_its_music():
    watch, gui, launchkey, clock = watching()
    gui.info["song"] = gui.song  # (the lead playing: the guitar's taken out of the game's song)
    gui.game_stems, gui.music = {"on": True, "guitar": 100, "bass": 60}, 80
    watch.step()
    launchkey.queued = [Button(105), Button(52)]
    clock.now = 0.5
    watch.step()
    assert watch.mix_page == "stems"

    def mixer(volumes, turned, quiet):
        return mixer_picture(["Bs", "Gtr", "Music"], volumes, turned, quiet=quiet, tops=[100] * 3, divider=2).data()

    assert screens(launchkey)[-1] == mixer([60, 100, 80], None, [True, False, False])  # (the bass waits its turn)
    assert pads(launchkey)[(0, 2)] == (127, 45, 0) and pads(launchkey)[(0, 1)] == (0, 11, 32)  # (bright, dim)
    assert pads(launchkey)[(0, KEYS_COLUMN)] == (0, 90, 100)  # (its Music, knob 7's)
    assert pads(launchkey)[(0, 0)] == (0, False) and pads(launchkey)[(0, CONTROLS)] == (21, False)  # (none else;
    # the page's master mute: its parts sounding)
    launchkey.queued = [Turn(2, -40)]  # knob 3: the guitar in the game's song
    clock.now = 1.0
    watch.step()
    assert gui.game_stem_calls[-1] == {"part": "guitar", "level": 60}
    assert screens(launchkey)[-1] == mixer([60, 60, 80], 1, [True, False, False])
    launchkey.queued = [Pad(0, 2)]  # its pad: muted (dimmed, greyed, its level kept), then back
    clock.now = 1.5
    watch.step()
    assert gui.game_stems["guitar"] == 60 and gui.game_stems["muted"]["guitar"] and pads(launchkey)[(0, 2)] == (10, 4, 0)
    assert screens(launchkey)[-1] == mixer([60, 60, 80], 1, [True, True, False])
    launchkey.queued = [Pad(0, 2)]
    clock.now = 2.0
    watch.step()
    assert not gui.game_stems["muted"]["guitar"] and pads(launchkey)[(0, 2)] == (127, 45, 0)
    launchkey.queued = [Turn(KEYS_COLUMN, -5)]  # knob 7: the game's Music volume
    clock.now = 2.5
    watch.step()
    assert gui.music == 75 and screens(launchkey)[-1] == mixer([60, 60, 75], 2, [True, False, False])
    launchkey.queued = [Pad(0, KEYS_COLUMN)]  # its pad: muted (red, greyed, its level kept), then back
    clock.now = 2.6
    watch.step()
    assert gui.music == 75 and gui.music_muted and pads(launchkey)[(0, KEYS_COLUMN)] == (5, False)
    assert screens(launchkey)[-1] == mixer([60, 60, 75], 2, [True, False, True])
    launchkey.queued = [Pad(0, KEYS_COLUMN)]
    clock.now = 2.7
    watch.step()
    assert not gui.music_muted and pads(launchkey)[(0, KEYS_COLUMN)] == (0, 90, 100)
    launchkey.queued = [Turn(CONTROLS, -5)]  # knob 8: the bass and guitar together, the Music apart
    clock.now = 2.8
    watch.step()
    assert (gui.game_stems["bass"], gui.game_stems["guitar"]) == (55, 55) and gui.music == 75
    launchkey.queued = [Pad(0, CONTROLS)]  # its pad: the page's master mute, both (not the keyboard's, nor the Music)
    clock.now = 2.9
    watch.step()
    assert gui.game_stems["muted"] == {"bass": True, "guitar": True} and gui.sound and not gui.music_muted
    assert (gui.game_stems["bass"], gui.game_stems["guitar"]) == (55, 55) and pads(launchkey)[(0, CONTROLS)] == (5, False)
    assert pads(launchkey)[(0, 1)] == (0, 4, 10) and pads(launchkey)[(0, 2)] == (10, 4, 0)  # (both dimmed)
    launchkey.queued = [Pad(0, CONTROLS)]  # (and back)
    clock.now = 2.95
    watch.step()
    assert gui.game_stems["muted"] == {"bass": False, "guitar": False} and pads(launchkey)[(0, CONTROLS)] == (21, False)
    calls = len(gui.game_stem_calls)
    launchkey.queued = [Turn(0, 5)]  # the drums' knob: not in the game's song
    clock.now = 3.0
    watch.step()
    assert len(gui.game_stem_calls) == calls and note(watch) == (None, ("In the game's song:", "just the bass and guitar"))


def warming_up(watch, gui, **changes):
    """A warm-up going (its info, as the page's), with the page's warm-up calls kept."""
    gui.warmup_calls = []
    gui.info["warmup"] = {"drill": 1, "drills": 6, "done": False, "paused": False, "name": "Spider (1-3, 2-4)",
                          "names": [], "phase": "play", "ready_left": 0.0, "beat": 3.0, "speed": 40, "click": 40,
                          "per": 1, "rate": 40 * 0.25 / 60, "shown": True, "ladder": True, "drill_left": 20.0,
                          "drill_length": 40.0, "beats": 12, "left": 200.0, "ending": None, "until_stopped": False,
                          "marks": [[0, i, v] for i, v in enumerate(["ok", "late", "missed", "ok"])],
                          "round": {"notes": 8, "right": 7, "ok": 6, "verdict": "steady", "off_ms": 20},
                          "hearing": {"on": True, "error": None, "level": 0.01}, **changes}

    def set_warmup(body):
        gui.warmup_calls.append(body)
        return {}

    gui.set_warmup = set_warmup


def test_a_warm_up_takes_the_launchkey_its_drills_on_top_its_notes_below():
    from rs_keylights.bitmap import warmup_picture
    from rs_keylights.launchkey import TRACK_BUTTONS
    from rs_keylights.watch import WARMUP_HINT, WARMUP_NOW, WARMUP_STOP, WARMUP_VERDICTS
    watch, gui, launchkey, clock = watching("play")
    warming_up(watch, gui)
    watch.step()
    shown = pads(launchkey)
    assert shown[(0, 1)] == (WARMUP_NOW, True)  # (the drill going: pulsing)
    assert shown[(0, 0)] == (0, 20, 0) and shown[(0, 2)] == (14, 14, 14)  # (done, to come)
    assert [shown[(1, c)] for c in range(3, 7)] == [WARMUP_VERDICTS[v] for v in ("ok", "late", "missed", "ok")]
    assert shown[(1, 0)] == (0, 0, 0) and shown[(1, CONTROLS)] == (WARMUP_STOP, False)
    assert screens(launchkey)[-1] == warmup_picture("Spider (1-3, 2-4)", "40/min, a click a note", "6/8 right, nearly",
                                                    0.5, "playing", WARMUP_HINT).data()
    for event in (Button(PLAY), Turn(0, 2), Button(TRACK_BUTTONS[1]), Pad(0, 4), Pad(0, CONTROLS), Pad(1, CONTROLS)):
        launchkey.queued.append(event)
        watch.step()
    assert gui.warmup_calls == [{"pause": True}, {"step": 1}, {"step": 1}, {"go": 2}, {"go": 4}, {"ladder": False},
                                {"stop": True}]
    gui.info["warmup"].update(phase="show")
    launchkey.queued.append(Button(RECORD))
    clock.now += 1.0
    watch.step()
    assert gui.warmup_calls[-1] == {"your_turn": True}
    launchkey.queued.append(Button(TRACK_BUTTONS[0]))  # (just shown: back to the drill before)
    watch.step()
    assert gui.warmup_calls[-1] == {"go": 0}
    gui.info["warmup"] = None  # (over: the Launchkey's the song's again)
    clock.now += 1.0
    watch.step()
    assert screens(launchkey)[-1] != warmup_picture("Spider (1-3, 2-4)", "", "", 0.0, "playing", WARMUP_HINT).data()
    assert pads(launchkey)[(1, CONTROLS)] != (WARMUP_STOP, False)


def test_following_a_split_song_whose_parts_play_in_the_game_the_stems_page_has_them_and_knob_7_its_music():
    watch, gui, launchkey, clock = watching()
    gui.info["song"], gui.music = gui.song, 76
    gui.game_stems = {"on": True, "guitar": 80, "bass": 60}  # (Song Stems there: the parts instead)
    gui.game_parts, gui.audio["made"] = {"on": True, "names": ["drums", "bass", "guitar", "vocals"], "by": "roformer"}, True
    watch.step()
    launchkey.queued = [Button(105), Button(52)]  # Func, ▼: the stems page
    clock.now = 0.5
    watch.step()
    launchkey.queued = [Turn(0, -5), Pad(0, 3), Turn(KEYS_COLUMN, -3)]  # knob 1: the drums down; the vocals' pad;
    clock.now = 1.0                                                     # knob 7: the game's Music
    watch.step()
    assert gui.audio["volumes"]["drums"] == 100 - 5 * watch_module.STEM_STEP and gui.audio["mutes"]["vocals"]
    assert gui.game_stem_calls == [] and gui.music == 73  # (not Song Stems': the song's parts in the game)
    shown = screens(launchkey)[-1]
    names = [label for _stem, label, _rgb in watch_module.STEMS] + ["Music"]
    volumes = [100 - 5 * watch_module.STEM_STEP, 100, 100, 100, 100, 100, 73]
    quiet = [False, False, False, True, False, False, False]
    assert shown == mixer_picture(names, volumes, 6, muted=False, quiet=quiet, tops=[100] * 7, divider=6).data()


def test_play_and_stop_leave_the_mixer_up_and_stop_drops_a_move_not_made_yet():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    watch.step()
    launchkey.queued = [Button(105)]  # Func: the mixer, kept up
    clock.now = 0.5
    watch.step()
    assert watch._picture_of == "mixer"
    launchkey.queued = [Button(PLAY)]  # (paused or going on: the mixer stays)
    clock.now = 1.0
    watch.step()
    assert gui.plays and watch._picture_of == "mixer" and watch.mix_knobs
    launchkey.queued = [Button(105)]  # (closed)
    clock.now = 1.5
    watch.step()
    watch._moving_to, watch._turned_at = 90.0, clock.now  # (knob 3 turned a moment ago: not made yet)
    launchkey.queued = [Button(RECORD)]
    clock.now = 1.6
    watch.step()
    clock.now = 5.0
    watch.step()
    assert gui.stopped == 1 and watch._moving_to is None and 90.0 not in gui.seeks  # (back to the start, not there)


def test_a_split_and_its_drum_part_show_how_far_they_are_at_the_bottom_till_theyre_done_notes_coming_over_it():
    watch, gui, launchkey, clock = watching("play")
    gui.info["song"] = gui.song
    gui.audio.update(made=False, making=True, progress=0.3)  # (split from the page: shown anyway)
    watch.step()
    assert watch._screen_shown[1] == ("progress", "Splitting", 15)  # (the song's own screen)
    watch._say(clock.now, line="Lead: muted")  # (said a while: over it)
    clock.now = 0.5
    watch.step()
    assert watch._screen_shown[1] == ("Lead: muted",)
    clock.now = 4.0  # (said: back to how far it is)
    watch.step()
    assert watch._screen_shown[1] == ("progress", "Splitting", 15)
    gui.audio.update(made=True, making=False)  # (split: its drum part made again from it)
    drums = {"id": "drums", "name": "Drums", "kind": "drums", "play": False, "made": True, "making": True,
             "progress": 0.6, "lights": 0, "tones": {}, "volume": 100, "playing": False}
    gui.song["arrangements"] = [*gui.song["arrangements"], drums]
    clock.now = 4.5
    watch.step()
    assert watch._screen_shown[1] == ("progress", "Making drums", 30)
    drums["making"] = False  # (done: what was there before)
    clock.now = 5.0
    watch.step()
    assert watch._screen_shown[1] is None
