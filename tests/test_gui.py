import json
import math
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
from builders import N, Song, song_archive
from test_pedal import backup

from rs_keylights.gui import (GAME_SONGS_EVERY, GAME_SONGS_OWN, GAME_TIME_EVERY, Gui, GuiError, GuiServer, _guitar,
                              default_lights)
from rs_keylights.sng import PINCH_HARMONIC
from rs_keylights.songtones import KINDS as TONE_KINDS
from rs_keylights.keyboard import Keyboard
from rs_keylights.tones import tone
from rs_keylights.watch import TONE_NUMBERS as TONE_ORDER
from rs_keylights.library import SongIndex


class FakeMidiOut:
    def __init__(self):
        self.sent = []

    def get_ports(self):
        return ["IAC Driver Bus 1", "CASIO USB-MIDI"]

    def open_port(self, port=0, name=None):
        pass

    def send_message(self, message):
        self.sent.append(message)

    def close_port(self):
        pass


class FakeHearing:
    """The warm-up's listener (OnsetListener), without an audio input: a test plays its notes (heard)."""

    made = []

    def __init__(self, heard, device="", clock=None, expected=None, lowest=70.0, channel=1):
        self.heard, self.expected, self.device, self.lowest, self.channel = heard, expected, device, lowest, channel
        self.hearing, self.error, self.level, self.stopped = False, None, 0.0, False
        FakeHearing.made.append(self)

    def start(self):
        self.hearing = True

    def stop(self):
        self.hearing, self.stopped = False, True


class FakeOverlay(ThreadingHTTPServer):
    """RS Companion's overlay API, with the game's state as a test sets it (the menus at first)."""

    def __init__(self):
        self.state = {"memoryReadout": {"songTimer": 0, "songID": "", "arrangementID": ""}, "currentState": 1}
        super().__init__(("127.0.0.1", 0), _OverlayHandler)


class _OverlayHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(self.server.state).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def server(tmp_path):
    dlc = tmp_path / "game" / "dlc"
    dlc.mkdir(parents=True)
    notes = [N(1.0 + i * 0.25, fret=i % 5) for i in range(40)]
    (dlc / "song_m.psarc").write_bytes(song_archive(
        "song", {"lead": ("L", Song(levels={0: notes}, chords=[(0, 2, 2, -1, -1, -1)], chord_names=["Em"],
                                    shapes={0: [(0, 1.0, 2.0)]}, beats=[i * 0.5 for i in range(121)], sections=[
            ("intro", 0.0, 10.0), ("verse", 10.0, 20.0), ("chorus", 20.0, 30.0), ("verse", 30.0, 40.0),
            ("chorus2", 40.0, 60.0)])), "rhythm": ("R", Song(levels={0: notes}))},
        song="The Pursuit", artist="Band"))
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    outs = []

    def factory(port, channel):
        out = FakeMidiOut()
        outs.append(out)
        return Keyboard(port, channel, midi_out_factory=lambda: out)

    overlay = FakeOverlay()
    Thread(target=overlay.serve_forever, daemon=True).start()
    gui = Gui(index, factory, tmp_path / "gui.json", overlay_port=overlay.server_address[1],
              list_ports=lambda: ["CASIO USB-MIDI"], other_follow=lambda: False, onset_listener=FakeHearing)
    assert gui.play_options["real_pitch"] and gui.follow_settings["real_pitch"]  # (on, as it starts)
    gui.play_options["real_pitch"] = gui.follow_settings["real_pitch"] = False  # (these tests: the lit keys' pitch)
    srv = GuiServer(gui, 0)
    Thread(target=srv.serve_forever, daemon=True).start()
    srv.outs = outs
    srv.overlay = overlay
    yield srv
    gui.close()
    srv.shutdown()
    srv.server_close()
    overlay.shutdown()
    overlay.server_close()


def page(srv, path, headers=None):
    request = urllib.request.Request(srv.url.rstrip("/") + path, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def wait_for(get, ready, seconds=3.0):
    deadline = time.monotonic() + seconds
    while True:
        value = get()
        if ready(value) or time.monotonic() > deadline:
            return value
        time.sleep(0.02)


def call(srv, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(srv.url.rstrip("/") + path, data=data, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"null") if path.startswith("/api") else response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def test_page_library_song_and_tones(server):
    status, page = call(server, "/")
    assert status == 200 and b"rs-keylights" in page
    _, library = call(server, "/api/library")
    [song] = library["songs"]
    assert (song["artist"], song["song"], song["file"], song["parts"]) == ("Band", "The Pursuit", "song_m.psarc", ["Lead", "Rhythm"])
    _, details = call(server, f"/api/song?id={urllib.request.quote(song['id'])}")
    assert [(p["part"], p["notes"], p["tuning"]) for p in details["parts"]] == [("lead", 40, "0 0 0 0 0 0"), ("rhythm", 40, "0 0 0 0 0 0")]
    assert details["length"] == 60.0
    assert details["guitar"] == {"strings": [40, 45, 50, 55, 59, 64], "capo": 0, "tuning": "E standard", "bass": False}
    _, tones = call(server, "/api/tones")
    assert len(tones) == 426 and tones[177] == {"number": 178, "name": "SAW LEAD 1", "group": "SYNTH-LEAD"}


def first_song(server):
    return call(server, "/api/library")[1]["songs"][0]["id"]


def sounds(server):
    return [p["sound"] for p in call(server, "/api/state")[1]["song"]["parts"]]


def test_play_pause_seek_resume_stop(server):
    sid = first_song(server)
    call(server, "/api/song/settings", {"song": sid, "parts": {
        "lead": {"lights": 2, "tone": 178}, "rhythm": {"play": True, "lights": 2, "mute_tone": 77}}})
    status, state = call(server, "/api/play", {"song": sid, "start": 1.5, "speed": 1})
    assert status == 200 and state["mode"] == "playing"
    assert sounds(server) == ["tone 178 SAW LEAD 1", "tone 079 DISTORTION GT, palm mutes 077 MUTE GUITAR"]
    _, state = call(server, "/api/pause", {})
    assert state["mode"] == "paused"
    _, state = call(server, "/api/seek", {"position": 5})
    assert state["position"] == 5
    _, state = call(server, "/api/resume", {})
    assert state["mode"] == "playing"
    _, state = call(server, "/api/stop", {})
    assert state["mode"] == "idle"
    sent = server.outs[0].sent
    assert [0xCA, 81] in sent and [0xCC, 28] in sent  # the tones, on the parts' channels (11; then 12, its palm
    # mutes 13: as many as each one's voices need)
    assert sent[-1] == [0xB3, 7, 100]  # closed: the lights' channel back to normal


def test_a_songs_parts_share_the_keys_at_first():
    assert default_lights(["lead"]) == [1] and default_lights(["lead", "rhythm", "bass"]) == [1, 1, 1]  # (all lit:
    assert default_lights(["lead", "lead", "rhythm", "bass"]) == [1, 1, 1, 1]  # sharing the 4 keys)
    assert default_lights(["lead", "rhythm", "bass", "lead", "rhythm"]) == [1, 1, 1, 0, 0]  # more than 4: the first


def test_requests_that_are_refused(server):
    sid = first_song(server)
    assert call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"lights": 5}}})[0] == 400
    assert call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"tone": 999}}})[0] == 400
    assert call(server, "/api/song/settings", {"song": sid, "parts": {"solo": {"play": True}}})[1]["error"] == (
        "No part solo in this song")
    call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"play": False}, "rhythm": {"play": False}}})
    assert call(server, "/api/play", {"song": sid})[1]["mode"] == "playing"  # every part muted: it plays (they can
    # be played again as it goes)
    call(server, "/api/stop", {})
    assert call(server, "/api/state", headers={"Host": "evil.example:80"})[0] == 403
    assert call(server, "/api/state", headers={"Host": f"192.168.1.20:{server.port}"})[0] == 403  # not without lan
    assert call(server, "/api/stop", {}, headers={"Origin": "http://evil.example"})[0] == 403
    request = urllib.request.Request(server.url + "api/stop", data=b"{}", headers={"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=5)
    assert refused.value.code == 415


def test_settings_are_kept(server, tmp_path):
    _, settings = call(server, "/api/settings")
    assert settings["midi_port"] == "CASIO USB-MIDI" and settings["channel"] == 4 and settings["ports"] == ["CASIO USB-MIDI"]
    assert (settings["lights_early_ms"], settings["fastest_flash_ms"]) == (15, 70)
    _, settings = call(server, "/api/settings", {"midi_port": "CASIO USB-MIDI", "channel": 11})
    assert json.loads((tmp_path / "gui.json").read_text()) == {
        "midi_port": "CASIO USB-MIDI", "channel": 11, "lights_early_ms": 15, "fastest_flash_ms": 70, "launchkey": True,
        "keys_sound": True, "keys_lights": True, "ring": True, "overlap_ms": 120, "listen": False,
        "listen_device": "Rocksmith USB Guitar Adapter", "listen_channel": 1, "tone_changes": True, "tone_map": TONE_KINDS, "techniques": True, "outputs": {}, "outputs_known": {},
        "beat_lights": False,
        "keys_tone": 1, "keys_volume": 100, "keys_output": None, "volumes": [100] * 8, "pedal_backup": "",
        "pedal_favourites": [51, 187], "pinch": {"up": 19, "velocity": 127, "note_velocity": 70, "scoop": 0.5,
                                                 "scoop_ms": 80.0, "width": 1.05, "rate": 15.0, "grows_ms": 200.0,
                                                 "vibrato": "always"}, "drum_set": 0, "warmup_speeds": {},
                                                 "warmup_shown": True, "warmup_ladder": True, "warmup_what": "routine",
                                                 "warmup_tuning": "E standard",
                                                 "warmup_scale": {"kind": "pentatonic", "tonic": None, "start": None},
                                                 "beta": {"melody": False, "sides": False}, "pedal_input": None,
                                                 "favorites_pending": {}, "split_server": None}
    status, answer = call(server, "/api/play", {"song": first_song(server)})
    assert status == 400 and "The lights' channel (11)" in answer["error"]


def test_a_songs_choices_are_kept_on_the_mac_for_every_page(server, tmp_path):
    sid = first_song(server)
    _, song = call(server, f"/api/song?id={urllib.request.quote(sid)}")
    assert [(p["part"], p["play"], p["lights"], p["tones"]) for p in song["parts"]] == [
        ("lead", True, 1, {}), ("rhythm", True, 1, {})]  # every part plays, sharing the keys
    assert (song["start"], song["saved"]) == (0, False)
    _, song = call(server, "/api/song/settings", {"song": sid, "start": 12, "parts": {
        "lead": {"lights": 3, "tone": 296}, "rhythm": {"play": True, "lights": 1, "pinch_tone": 0}}})
    assert [(p["play"], p["lights"], p["tones"]) for p in song["parts"]] == [
        (True, 3, {"tone": 296}), (True, 1, {"pinch_tone": 0})]  # (pinch harmonics on its own tone)
    assert (song["start"], song["saved"]) == (12, True)
    _, options = call(server, "/api/play/options", {"speed": 0.5, "real_pitch": True, "song": sid})
    assert (options["speed"], options["real_pitch"], options["silent"], options["song"]) == (0.5, True, False, sid)
    assert call(server, "/api/play/options", {"speed": 9})[0] == 400
    again = Gui(server.gui.index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    assert again.song(sid)["parts"][1]["tones"] == {"pinch_tone": 0} and again.song(sid)["start"] == 12
    assert again.play_options_info()["speed"] == 0.5 and again.play_options_info()["song"] == sid


def test_a_songs_choices_are_kept_by_its_place_in_the_game_folder_and_follow_it(tmp_path):
    def game(name, files):
        dlc = tmp_path / name / "dlc"
        dlc.mkdir(parents=True)
        for file, key in files:
            (dlc / file).write_bytes(song_archive(key, {"lead": (f"L{key}", Song(levels={0: [N(1.0)]}))}))
        index = SongIndex(tmp_path / name, tmp_path / f"{name}.json")
        index.refresh()
        return index

    # Kept by an earlier version, by whole paths on a drive since given up; one song now has another name (copied in
    # while the game ran), and one isn't there at all
    old = "/Volumes/DRIVE/SteamLibrary/steamapps/common/Rocksmith2014/dlc"
    (tmp_path / "songs.json").write_text(json.dumps({
        f"{old}/a_m.psarc#A": {"start": 1}, f"{old}/b_m.psarc#B": {"start": 2}, f"{old}/gone_m.psarc#Gone": {"start": 3},
        "/elsewhere/c_m.psarc#C": {"start": 4}}))
    (tmp_path / "gui.json").write_text(json.dumps({"play": {"song": f"{old}/a_m.psarc#A"}}))
    index = game("game", [("a_m.psarc", "A"), ("b renamed_m.psarc", "B")])
    here = tmp_path / "game" / "dlc"
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    assert gui.song(f"{here}/a_m.psarc#A")["start"] == 1 and gui.song(f"{here}/b renamed_m.psarc#B")["start"] == 2
    assert gui.play_options["song"] == f"{here}/a_m.psarc#A"
    # The files keep them by their place in the game folder now (the rest as they were: nothing's lost)
    assert json.loads((tmp_path / "songs.json").read_text()) == {
        "dlc/a_m.psarc#A": {"start": 1}, "dlc/b renamed_m.psarc#B": {"start": 2}, "dlc/gone_m.psarc#Gone": {"start": 3},
        "/elsewhere/c_m.psarc#C": {"start": 4}}
    assert json.loads((tmp_path / "gui.json").read_text())["play"]["song"] == "dlc/a_m.psarc#A"

    # The game folder moved: they're found there
    for file in ("songs.json", "gui.json"):
        (tmp_path / "moved").mkdir(exist_ok=True)
        (tmp_path / "moved" / file).write_text((tmp_path / file).read_text())
    index = game("other", [("a_m.psarc", "A")])
    gui = Gui(index, settings_file=tmp_path / "moved" / "gui.json", list_ports=list, other_follow=lambda: False)
    assert gui.song(f"{tmp_path / 'other' / 'dlc'}/a_m.psarc#A")["start"] == 1
    assert gui.play_options["song"] == f"{tmp_path / 'other' / 'dlc'}/a_m.psarc#A"


def test_the_library_is_in_the_games_order_sorted_by_artist(tmp_path):
    # (artist, its sort name, song, its sort name), as some songs have them: in the order the game lists them
    games = [("Amon Amarth ", "Amon Amarth ", "Shield Wall", "Shield Wall"),
             ("Amon Amarth", "Amon Amarth", "The Hero", "Hero"),
             ("Amon Amarth", "Amon Amarth", "Raven's Flight", "Raven's Flight"),
             ("Arctic Monkeys", "Arc", "Do I Wanna Know", "Do"),
             ("Arcade", "Arcade", "Double Dragon", "Double Dragon"),
             ("Dire Straits", "Dire Straits", "Sultans Of Swing", "Sultans Of Swing"),
             ("Dire Straits", "DIRE", "Brothers in Arms", "BROT"),
             ("Rob Zombie", "R", "Dragula", "D"),
             ("R.E.M.", "R.E.M.", "Everybody Hurts", "Everybody Hurts"),
             ("The Rolling Stones", "R", "Honky Tonk Women", "H"),
             ("Wolfmother", "", "Woman", ""),  # (no sort names: its names)
             ("Wolfmother", "", "Woman", "")]  # (two copies of a song: as the game last had them)
    dlc = tmp_path / "game" / "dlc"
    dlc.mkdir(parents=True)
    for i, (artist, artist_sort, song, song_sort) in enumerate(reversed(games)):
        (dlc / f"s{i}_m.psarc").write_bytes(song_archive(f"S{i}", {"lead": (f"L{i}", Song(levels={0: [N(1.0)]}))},
                                                         {"ArtistNameSort": artist_sort, "SongNameSort": song_sort},
                                                         song=song, artist=artist))
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()

    def library():
        gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
        return gui, [song["id"].rsplit("#", 1)[-1] for song in gui.library()["songs"]]

    gui, keys = library()
    assert [song["song"] for song in gui.library()["songs"]] == [song for _a, _s, song, _ss in games]
    assert [song["letter"] for song in gui.library()["songs"]] == list("aaaaaddrrrww")  # (knob 8's: by sort names)
    assert keys[-2:] == ["S0", "S1"]  # (the copies: by file, until the game's order is known)
    game = [*keys[:-2], "S1", "S0"]
    gui._keep_game_order(game[::-1])  # (the game's list sorted another way: not kept)
    assert library()[1] == keys
    gui._keep_game_order(game)  # (seen following: kept for the next start)
    assert library()[1] == game


def test_five_arrangements_can_play_at_once_as_many_as_the_keyboards_channels_take(server, tmp_path):
    names = ("lead", "rhythm", "bass", "combo", "lead2")
    (tmp_path / "game" / "dlc" / "five_m.psarc").write_bytes(song_archive(
        "five", {name: (name[0].upper() + name[-1], Song(levels={0: [N(1.0, mask=PINCH_HARMONIC)]})) for name in names},
        song="Down in a Hole", artist="A Band"))
    server.gui.index.refresh()
    server.gui._library = None
    sid = next(song["id"] for song in call(server, "/api/library")[1]["songs"] if song["song"] == "Down in a Hole")
    status, _ = call(server, "/api/song/settings", {"song": sid, "parts": {name: {"play": True} for name in names}})
    assert status == 200 and len(call(server, f"/api/song?id={urllib.request.quote(sid)}")[1]["parts"]) == 5
    status, said = call(server, "/api/song/settings", {"song": sid, "parts": {  # three channels each: 15 of 12
        name: {"pinch_tone": 33, "mute_tone": 29} for name in names}})
    assert status == 400 and "channels are used up" in said["error"]
    lit = {"song": sid, "parts": {name: {"lights": 1} for name in names}}  # all five lighting keys: sharing the 4
    assert call(server, "/api/song/settings", lit)[0] == 200


def test_the_lit_parts_share_the_four_lit_keys_while_a_song_plays(server):
    sid = first_song(server)
    call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"lights": 1}, "rhythm": {"play": True, "lights": 1}}})
    call(server, "/api/play", {"song": sid, "start": 1})
    player = server.gui._session.player
    assert player.most_keys == 4 and [t.priority for t in player.tracks] == [0, 1]  # (the keys shared, in turn)
    call(server, "/api/stop", {})


def test_changes_while_playing_apply_where_the_song_is(server):
    sid = first_song(server)
    call(server, "/api/play", {"song": sid, "start": 10})
    call(server, "/api/pause", {})
    version = call(server, "/api/state")[1]["play_version"]
    status, _song = call(server, "/api/song/settings", {"song": sid, "parts": {
        "lead": {"lights": 2, "tone": 178}, "rhythm": {"play": True, "lights": 2, "mute_tone": 77}}})
    _, state = call(server, "/api/state")
    assert status == 200 and state["mode"] == "paused" and 10 <= state["position"] < 10.5
    assert state["play_version"] > version  # other pages know to show it again
    assert sounds(server) == ["tone 178 SAW LEAD 1", "tone 079 DISTORTION GT, palm mutes 077 MUTE GUITAR"]
    assert [0xCA, 81] in server.outs[-1].sent and [0xCC, 28] in server.outs[-1].sent  # the new tones, at once
    assert len(server.outs) == 1  # the same keyboard: nothing stopped
    assert call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"lights": 5}}})[0] == 400
    assert call(server, "/api/state")[1]["song"]["parts"][0]["lights"] == 2  # unchanged
    call(server, "/api/play/options", {"silent": True, "speed": 0.5})  # lights only, and slower, at once
    _, state = call(server, "/api/state")
    assert sounds(server) == ["silent", "silent"] and state["speed"] == 0.5
    call(server, "/api/follow/settings", {"lead": {"tone": 30}})  # new defaults: the song takes them too
    call(server, "/api/play/options", {"silent": False})
    assert sounds(server)[1] == "tone 079 DISTORTION GT, palm mutes 077 MUTE GUITAR"
    call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"tone": None}}})
    assert sounds(server)[0] == "tone 030 VIBRAPHONE 2"
    call(server, "/api/stop", {})


def test_the_scale_climbs_the_keys_until_stopped(server):
    sid = first_song(server)
    _, state = call(server, "/api/scale", {"song": sid})  # nothing playing: a keyboard of its own
    assert state["showing"]
    shown = wait_for(lambda: [m for m in server.outs[-1].sent if m[0] == 0x93 and m[2] > 0], bool)
    assert shown  # keys lit on the lights' channel
    _, state = call(server, "/api/scale/stop", {})
    assert not state["showing"] and server.outs[-1].sent[-1] == [0xB3, 7, 100]  # its keyboard closed
    call(server, "/api/play", {"song": sid, "start": 30})  # after the song's last note: the keys are free
    _, state = call(server, "/api/scale", {})
    assert state["showing"]
    keyboard = server.outs[-1]
    lit = len(wait_for(lambda: [m for m in keyboard.sent if m[0] == 0x93 and m[2] > 0], bool))
    assert lit and call(server, "/api/scale/stop", {})[1]["showing"] is False
    call(server, "/api/stop", {})


def test_a_remote_follows_the_song_playing_or_picked(server):
    gui, sid = server.gui, first_song(server)
    assert gui.remote_info()["song"] is None and gui.remote_info()["mode"] == "idle"
    call(server, "/api/play/options", {"song": sid})  # picked on the page
    info = gui.remote_info()
    assert info["song"]["title"] == "Band - The Pursuit" and info["sound"] is True
    assert [(a["id"], a["play"], a["lights"]) for a in info["song"]["arrangements"]] == [("lead", True, 1), ("rhythm", True, 1)]
    gui.change_part("lead", {"lights": 2})
    gui.change_part("rhythm", {"play": True, "lights": 2, "tone": 178})
    assert [(a["play"], a["lights"]) for a in gui.remote_info()["song"]["arrangements"]] == [(True, 2), (True, 2)]
    assert gui.toggle_sound() is False and gui.play_options["silent"] is True
    call(server, "/api/play", {"song": sid, "start": 20})
    assert gui.remote_info()["mode"] == "play" and sounds(server) == ["silent", "silent"]
    assert gui.toggle_sound() is True and sounds(server)[1] == "tone 178 SAW LEAD 1"
    call(server, "/api/stop", {})


def test_follow_settings_are_kept_and_checked(server, tmp_path):
    status, info = call(server, "/api/follow")
    assert status == 200 and info == {"version": 0, "saved": False, "real_pitch": False,
                                      "offset_ms": 0, "show_key": True, "lead": {}, "rhythm": {}, "bass": {}, "song": None}
    _, info = call(server, "/api/follow/settings", {"offset_ms": 20, "lead": {"tone": 178, "pinch_tone": 0},
                                                     "arrangements": {"r": {"mute_tone": 77, "tone": ""}}})
    assert (info["version"], info["saved"], info["offset_ms"]) == (1, True, 20)
    assert info["lead"] == {"tone": 178, "pinch_tone": 0}
    assert json.loads((tmp_path / "gui.json").read_text())["follow"]["arrangements"] == {"R": {"mute_tone": 77}}
    # Field by field: an empty one goes back to the default; an arrangement with none left goes
    _, info = call(server, "/api/follow/settings", {"lead": {"pinch_tone": None}, "arrangements": {"R": {"mute_tone": None}}})
    assert info["lead"] == {"tone": 178}
    assert json.loads((tmp_path / "gui.json").read_text())["follow"]["arrangements"] == {}
    _, info = call(server, "/api/follow/settings", {"arrangements": {"R": {"follow_tones": False}}})  # (its tone stays)
    assert json.loads((tmp_path / "gui.json").read_text())["follow"]["arrangements"] == {"R": {"follow_tones": False}}
    _, info = call(server, "/api/follow/settings", {"arrangements": {"R": {"follow_tones": None}}})
    assert json.loads((tmp_path / "gui.json").read_text())["follow"]["arrangements"] == {}
    for bad in ({"rhythm": {"tone": 999}}, {"bass": {"pinch_tone": 999}}, {"offset_ms": 5000},
                {"arrangements": {"../x": {}}}, {"arrangements": []}, {"bass": "loud"}):
        assert call(server, "/api/follow/settings", bad)[0] == 400, bad
    assert call(server, "/api/follow")[1]["version"] == 4  # none of those changed anything
    again = Gui(server.gui.index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    assert (again.follow_info()["offset_ms"], again.follow_info()["lead"]) == (20, {"tone": 178})  # kept
    call(server, "/api/settings", {"channel": 11})
    status, answer = call(server, "/api/follow", {})  # (with sound: the one switch, on)
    assert status == 400 and "The lights' channel (11)" in answer["error"]


def test_following_shows_the_songs_arrangements_and_their_tones_apply_at_once(server):
    server.overlay.state = {"memoryReadout": {"songTimer": 5.0, "songID": "song", "arrangementID": "L"}, "currentState": 4}
    call(server, "/api/follow/settings", {"sound": True, "lead": {"tone": 77}})
    _, state = call(server, "/api/follow", {})
    assert state["mode"] == "following"
    info = wait_for(lambda: call(server, "/api/follow")[1], lambda i: i["song"])
    assert [(a["id"], a["name"], a["playing"], a["tones"]) for a in info["song"]["arrangements"]] == [
        ("L", "Lead", True, {}), ("R", "Rhythm", False, {})]
    state = call(server, "/api/state")[1]
    assert (state["follow"]["song_id"], state["follow"]["arrangement"]) == (info["song"]["id"], "L")
    out = server.outs[-1]
    assert [0xCA, 28] in out.sent  # the lead parts' tone (077) on channel 11

    sent = len(out.sent)
    _, info = call(server, "/api/follow/settings", {"arrangements": {"L": {"tone": 178}}})
    assert info["song"]["arrangements"][0]["tones"] == {"tone": 178}
    assert [0xCA, 81] in out.sent[sent:]  # the lead's own tone, at once
    messages = call(server, "/api/state")[1]["follow"]["messages"]
    assert messages[-1] == "Band - The Pursuit (Lead): tone 178 SAW LEAD 1"
    assert sum(" notes" in m for m in messages) == 1  # not loaded again

    sent = len(out.sent)
    call(server, "/api/follow/settings", {"arrangements": {"R": {"tone": 30}}, "offset_ms": 40})
    assert not [m for m in out.sent[sent:] if m[0] & 0xF0 == 0xC0]  # the rhythm's tone: the lead sounds the same
    assert server.gui._session.follower.offset == 0.04

    server.overlay.state = {"memoryReadout": {"songTimer": 0.0, "songID": "", "arrangementID": ""}, "currentState": 0}
    ended = wait_for(lambda: call(server, "/api/follow")[1], lambda i: i["song"] and i["song"]["ended"])  # (the menus)
    assert ended["song"]["id"] == info["song"]["id"] and ended["song"]["played"] == "L"  # the last one stays shown,
    assert not any(a["playing"] for a in ended["song"]["arrangements"])  # nothing playing, until the next
    state = call(server, "/api/state")[1]
    assert (state["follow"]["song_id"], state["follow"]["arrangement"], state["follow"]["song"]) == (info["song"]["id"], "L", None)
    assert server.gui.remote_info()["song"]["id"] == info["song"]["id"]  # (the Launchkey shows it too)
    call(server, "/api/stop", {})


def test_other_arrangements_play_along_while_following(server, tmp_path):
    def playing(lead_or_rhythm):
        server.overlay.state = {"memoryReadout": {"songTimer": 5.0, "songID": "song", "arrangementID": lead_or_rhythm},
                                "currentState": 4}
        return wait_for(lambda: call(server, "/api/follow")[1], lambda i: i["song"] and i["song"]["played"] == lead_or_rhythm)

    def parts(info):
        return [(a["id"], a["play"], a["lights"]) for a in info["song"]["arrangements"]]

    def kept():
        return json.loads((tmp_path / "gui.json").read_text())["follow"]["parts"]

    call(server, "/api/follow/settings", {"sound": True})
    call(server, "/api/follow", {})
    info = playing("L")
    assert parts(info) == [("L", True, 4), ("R", False, 0)]  # just the arrangement played, on every key

    _, info = call(server, "/api/follow/settings", {"arrangements": {"R": {"tone": 178}}, "parts": {"L": {"R": {"play": True}}}})
    assert parts(info) == [("L", True, 4), ("R", True, 0)]
    assert [0xCB, 81] in server.outs[-1].sent  # the rhythm's tone, on the second part's channel (12: the lead's one
    # channel before it)
    table = call(server, "/api/state")[1]["follow"]["parts"]
    assert [(p["name"], p["lights"], p["tone"]) for p in table] == [("Lead", 4, "079 DISTORTION GT"), ("Rhythm", 0, "178 SAW LEAD 1")]
    assert call(server, "/api/state")[1]["follow"]["messages"][-1] == (
        "Band - The Pursuit (Lead): Lead lighting 4 keys, tone 079 DISTORTION GT; Rhythm lighting no keys, tone 178 SAW LEAD 1")
    assert call(server, "/api/follow/settings", {"parts": {"L": {"R": {"lights": 1}}}})[0] == 200  # (sharing the 4)
    _, info = call(server, "/api/follow/settings", {"parts": {"L": {"L": {"lights": 2}, "R": {"lights": 2}}}})
    assert parts(info) == [("L", True, 2), ("R", True, 2)]
    assert kept() == {"L": {"L": {"lights": 2}, "R": {"play": True, "lights": 2}}}

    info = playing("R")  # the parts chosen are for playing the lead: the rhythm alone
    assert parts(info) == [("L", False, 0), ("R", True, 4)]
    _, info = call(server, "/api/follow/settings", {"parts": {"R": {"R": {"play": False}}}})
    assert parts(info) == [("L", False, 0), ("R", False, 4)]
    assert call(server, "/api/state")[1]["follow"]["messages"][-1] == "Band - The Pursuit (Rhythm): nothing plays"
    _, info = call(server, "/api/follow/settings", {"parts": {"R": {"R": {"play": True}}, "L": {"L": {"lights": 4}, "R": {"play": False, "lights": 0}}}})
    assert kept() == {}  # all back to the defaults
    assert call(server, "/api/follow/settings", {"parts": {"NOPE": {"R": {"play": True}}}})[0] == 400
    call(server, "/api/stop", {})


def test_lead_rhythm_and_bass_parts_have_default_tones_for_playing_too(server, tmp_path):
    sid = first_song(server)
    assert [p["kind"] for p in call(server, f"/api/song?id={urllib.request.quote(sid)}")[1]["parts"]] == ["lead", "rhythm"]
    call(server, "/api/follow/settings", {"lead": {"tone": 178}, "rhythm": {"tone": 30, "mute_tone": 77}})
    call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"lights": 2}, "rhythm": {"play": True, "lights": 2, "tone": 34}}})
    call(server, "/api/play", {"song": sid})
    assert sounds(server) == ["tone 178 SAW LEAD 1", "tone 034 SOFT MARIMBA, palm mutes 077 MUTE GUITAR"]
    call(server, "/api/stop", {})
    # Settings from before lead and rhythm parts had defaults of their own: the guitar's are both
    (tmp_path / "old.json").write_text(json.dumps({"follow": {"guitar": {"tone": 297, "mute_tone": 295}, "bass": {"tone": 305}}}))
    old = Gui(server.gui.index, settings_file=tmp_path / "old.json", list_ports=list, other_follow=lambda: False).follow_info()
    assert (old["lead"], old["rhythm"], old["bass"]) == ({"tone": 297, "mute_tone": 295},) * 2 + ({"tone": 305},)


def test_the_kind_of_part_picks_the_default_tones():
    from rs_keylights.gui import kind
    from rs_keylights.library import ArrangementRef

    def ref(sng, name):
        return ArrangementRef("ID", "/x.psarc", f"songs/bin/macos/{sng}.sng", "X", name)

    assert [kind(ref(s, n)) for s, n in [("x_lead", "Lead"), ("x_lead2", "Lead"), ("x_rhythm2", "Rhythm"),
                                          ("x_combo", "Combo"), ("x_combo2", "Rhythm"), ("x_bass2", "Bass")]] == [
        "lead", "lead", "rhythm", "lead", "rhythm", "bass"]


def test_on_the_network_browsers_need_the_key(tmp_path):
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    srv = GuiServer(gui, 0, lan=True, address="127.0.0.1")
    srv.names = {"rocksmith-mac.local"}
    srv.trusted = lambda client: False  # these requests come from 127.0.0.1: as if from a phone
    Thread(target=srv.serve_forever, daemon=True).start()
    try:
        key = json.loads((tmp_path / "gui.json").read_text())["lan_key"]
        assert len(key) == 12 and Gui(index, settings_file=tmp_path / "gui.json").lan_key() == key  # kept
        assert call(srv, "/api/state")[0] == 403
        assert page(srv, "/")[0] == 403 and page(srv, "/?key=wrong")[0] == 403
        status, headers, body = page(srv, f"/?key={key}")
        assert status == 200 and b"rs-keylights" in body
        assert headers["Set-Cookie"].startswith(f"rsk_key={key}; Max-Age=") and "HttpOnly; SameSite=Lax" in headers["Set-Cookie"]
        cookie = {"Cookie": f"other=1; rsk_key={key}"}
        port = srv.port
        for host in (f"rocksmith-mac.local:{port}", f"192.168.1.20:{port}", f"localhost:{port}"):
            assert call(srv, "/api/state", headers={**cookie, "Host": host})[0] == 200, host
        assert call(srv, "/api/state", headers={**cookie, "Host": f"evil.example:{port}"})[0] == 403
        assert call(srv, "/api/state", headers={"Cookie": "rsk_key=wrong"})[0] == 403
        at = {**cookie, "Host": f"192.168.1.20:{port}"}
        assert call(srv, "/api/stop", {}, headers={**at, "Origin": f"http://192.168.1.20:{port}"})[0] == 200
        assert call(srv, "/api/stop", {}, headers={**at, "Origin": f"http://127.0.0.1:{port}"})[0] == 403
        _, settings = call(srv, "/api/settings", headers=cookie)
        assert settings["lan"] is True and all(u.endswith(f":{port}/?key={key}") for u in settings["phone"])
    finally:
        gui.close()
        srv.shutdown()
        srv.server_close()


def test_the_lights_timing_applies_to_what_is_playing(server):
    _, library = call(server, "/api/library")
    sid = library["songs"][0]["id"]
    call(server, "/api/play", {"song": sid, "start": 2})
    player = server.gui._session.player
    assert (player.lead, player.min_period) == (0.015, 0.07)
    status, _ = call(server, "/api/settings", {"lights_early_ms": 20, "fastest_flash_ms": 90})
    assert status == 200
    assert (server.gui._session.player.lead, server.gui._session.player.min_period) == (0.02, 0.09)
    assert call(server, "/api/settings", {"lights_early_ms": 500})[0] == 400
    call(server, "/api/stop", {})


def test_a_remote_plays_pauses_moves_and_follows(server, no_real_game_loop):
    gui, sid = server.gui, first_song(server)
    with pytest.raises(ValueError, match="Pick a song"):
        gui.remote_play()
    call(server, "/api/play/options", {"song": sid})
    gui.remote_seek(20)  # nothing playing: where the song picked starts
    assert gui.song(sid)["start"] == 20 and gui.remote_info()["position"] == 20
    assert gui.remote_play() == "playing"
    _, state = call(server, "/api/state")
    assert state["mode"] == "playing" and 20 <= state["position"] < 21
    assert gui.remote_play() == "paused" and call(server, "/api/state")[1]["mode"] == "paused"
    gui.remote_seek(45)
    assert call(server, "/api/state")[1]["position"] == 45
    assert gui.remote_play() == "playing"
    assert call(server, "/api/state")[1]["remote"] == {"speed": None, "position": None, "busy": False}
    gui.remote_dialling({"speed": 1.2, "position": None})  # its strip moving: the page shows it as it goes
    assert call(server, "/api/state")[1]["remote"] == {"speed": 1.2, "position": None, "busy": True}
    assert gui.toggle_follow() is True and gui.remote_info()["mode"] == "follow"
    assert gui.remote_info()["connected"] is not True  # (no RS Companion here: not reached, or not tried yet)
    game = no_real_game_loop[-1]  # (the game's riffloop mod: on a song's screen, playing it)
    assert gui.remote_info()["state"] == "playing" and gui.remote_info()["paused"] is False
    assert gui.remote_play() == "paused" and game.sent[-1] == ("pause",)  # following: the game's song paused
    assert gui.remote_info()["state"] == "paused" and gui.remote_info()["paused"] is True  # (however it was)
    assert gui.remote_play() == "playing" and game.sent[-1] == ("resume",)
    assert gui.remote_info()["state"] == "playing"
    game.screen = "RiffRepeater_Pause"  # (Riff Repeater's pause screen)
    assert gui.remote_play() == "playing" and game.sent[-1] == ("resume",)
    game.screen = "LearnASong_RiffRepeater"  # (its menu: no song playing)
    with pytest.raises(ValueError, match=r"isn't playing a song \(LearnASong_RiffRepeater\)"):
        gui.remote_play()
    gui.game_key("down")  # Rocksmith's menus: its keys
    assert game.sent[-1] == ("key", "down")
    game.screen = "MainMenu"  # (no song)
    time.sleep(GAME_TIME_EVERY)  # (asked again)
    assert gui.remote_info()["state"] is None
    with pytest.raises(ValueError, match=r"isn't playing a song \(MainMenu\)"):
        gui.remote_play()
    game.screen = None  # (a mod that doesn't say)
    with pytest.raises(ValueError, match="too old"):
        gui.remote_play()
    game.status = lambda: None  # (no game)
    with pytest.raises(ValueError, match="didn't answer"):
        gui.remote_play()
    assert gui.toggle_follow() is False and call(server, "/api/state")[1]["mode"] == "idle"
    with pytest.raises(ValueError, match="Follow Rocksmith first"):
        gui.game_key("down")


def test_following_on_the_games_song_list_a_remote_goes_through_it_and_the_library_follows(server, tmp_path,
                                                                                          no_real_game_loop):
    gui = server.gui
    for key, title, artist in (("alpha", "Alpha Song", "Alpha Band"), ("zulu", "Zulu Song", "Zulu Band")):
        (tmp_path / "game" / "dlc" / f"{key}_m.psarc").write_bytes(song_archive(
            key, {"lead": ("L" + key, Song(levels={0: [N(1.0)]}))}, song=title, artist=artist))
    gui.index.refresh()
    gui._library = None
    assert gui.toggle_follow() is True
    game = no_real_game_loop[-1]
    assert gui.game_songs() is None and gui.game_browse() is None  # (not on its song list)
    game.song_list = ["alpha", "ghost", "song", "zulu"]  # its order (a song the library hasn't: its key)
    time.sleep(GAME_SONGS_EVERY)
    assert gui.game_songs().key == "alpha"
    moved = gui.game_browse()  # (its highlight, first seen: the library goes to it)
    assert moved["song"] == "Alpha Song" and moved["place"] == 0 and moved["of"] == 4
    moved = gui.browse_move(2)  # the strip: two songs on, in the game's order, its highlight moved there too
    assert moved["song"] == "The Pursuit" and moved["place"] == 2 and game.sent[-1] == ("focus", "song")
    assert gui.state()["browse"]["song"].endswith("#song")  # (the page's library on it)
    moved = gui.browse_move(-1)
    assert moved == {"id": "#ghost", "song": "ghost", "artist": "", "place": 1, "of": 4}
    assert game.sent[-1] == ("focus", "ghost")
    time.sleep(GAME_SONGS_EVERY)
    assert gui.game_browse() is None  # (its own move: the remote's)
    time.sleep(GAME_SONGS_OWN)
    game.highlighted = 3  # its keys: the library follows
    time.sleep(GAME_SONGS_EVERY)
    assert gui.game_browse()["song"] == "Zulu Song" and gui.state()["browse"]["song"].endswith("#zulu")
    assert gui.browse_letter(-1)["song"] == "The Pursuit"  # knob 8: a letter back, by the library's artists
    # ("Band": B; ghost: G; Alpha Band: A)
    assert gui.browse_letter(-1)["song"] == "ghost" and game.sent[-1] == ("focus", "ghost")
    assert call(server, "/api/state")[1]["game_list"] is True  # (the page: a song clicked is highlighted there)
    zulu = next(song["id"] for song in gui.library()["songs"] if song["song"] == "Zulu Song")
    status, moved = call(server, "/api/game/focus", {"song": zulu})
    assert status == 200 and moved["song"] == "Zulu Song" and game.sent[-1] == ("focus", "zulu")
    assert gui.state()["browse"]["song"] == zulu
    game.song_list, game.highlighted = ["alpha", "song"], 0  # (filtered: zulu isn't in it)
    time.sleep(GAME_SONGS_EVERY)
    with pytest.raises(ValueError, match="hasn't this song"):
        gui.game_focus({"song": zulu})
    assert gui.toggle_follow() is False and gui.game_songs() is None
    assert call(server, "/api/state")[1]["game_list"] is False


def test_switching_songs_and_going_through_the_library(server, tmp_path):
    gui, first = server.gui, first_song(server)
    notes = [N(1.0 + i * 0.5, fret=i % 3) for i in range(20)]
    (tmp_path / "game" / "dlc" / "other_m.psarc").write_bytes(song_archive(
        "other", {"lead": ("O", Song(levels={0: notes}))}, song="Another Song", artist="Another Band",
        extra={"SongYear": 1999, "AlbumName": "Album", "SongLength": 30.0, "Tuning": {f"string{i}": -1 for i in range(6)}}))
    _, library = call(server, "/api/reindex", {})
    other = library["songs"][0]
    assert (other["artist"], other["year"], other["album"], other["length"], other["tuning"]) == (
        "Another Band", 1999, "Album", 30.0, "Eb standard")
    call(server, "/api/play", {"song": first, "start": 5})
    _, state = call(server, "/api/switch", {"song": first})  # the song playing: it plays on
    assert state["mode"] == "playing" and state["active"] == first
    _, state = call(server, "/api/switch", {"song": other["id"]})  # another: the one playing stops
    assert state["mode"] == "idle" and state["active"] == other["id"]
    assert gui.remote_info()["song"]["title"] == "Another Band - Another Song"
    assert gui.browse_move(0)["id"] == other["id"]  # from the active song
    assert gui.browse_move(1)["id"] == first and gui.browse_move(5)["id"] == first  # no further than the last
    assert wait_for(lambda: {first, other["id"]} <= set(gui._art), bool)  # the art around it got ready meanwhile
    browse = call(server, "/api/state")[1]["browse"]
    assert browse["song"] == first and browse["version"] == 3
    assert gui.browse_select()["id"] == first
    _, state = call(server, "/api/state")
    assert state["active"] == first and state["browse"]["chosen"] == first
    assert gui.browse_at(other["id"])["id"] == other["id"]  # back to a song
    assert gui.browse_at(None)["id"] == first and gui.browse_at("gone")["id"] == first  # the active one
    browse = call(server, "/api/state")[1]["browse"]
    assert browse["song"] == first and browse["version"] == 7 and browse["chosen"] is None
    assert gui.toggle_real_pitch() is True and gui.play_options["real_pitch"] is True
    _, key = call(server, f"/api/key?id={urllib.request.quote(first)}")
    assert key["name"] and key["notes"]


def test_a_part_turned_on_takes_the_keys_left_and_a_remote_stops(server):
    gui, sid = server.gui, first_song(server)
    call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"play": False}, "lead": {"lights": 1}}})
    call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"lights": 1}}})  # off: its lights wait
    _, song = call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"play": True}}})  # (on: both lit,
    assert [(p["play"], p["lights"]) for p in song["parts"]] == [(True, 1), (True, 1)]  # sharing the 4 keys)
    assert call(server, f"/api/art?id={urllib.request.quote(sid)}")[0] == 404  # no album art in this one
    assert gui.remote_stop() == "nothing playing"
    call(server, "/api/play", {"song": sid, "start": 20})
    assert gui.remote_stop() == "stopped" and call(server, "/api/state")[1]["mode"] == "idle"


def test_a_remotes_keys_play_the_keyboard_and_light_first(server):
    gui, sid = server.gui, first_song(server)
    gui.play_keys([0x84, 60, 0])  # a key let go with nothing open: nothing opens
    gui._keys_queue.join()  # (played by their own thread)
    assert server.outs == []
    gui.play_keys([0x94, 60, 90])  # nothing playing: a keyboard of their own, the keys' tone on its own channel
    gui._keys_queue.join()
    idle = server.outs[-1].sent
    assert idle[-5:] == [[0x93, 60, 100], [0xB0, 0, 2], [0xC0, 0], [0xB0, 7, 100], [0x90, 60, 90]]
    gui.play_keys([0x84, 60, 0])
    gui._keys_queue.join()
    assert idle[-2:] == [[0x83, 60, 0], [0x80, 60, 0]]
    assert call(server, "/api/keys", {"tone": 79, "lights": False})[1] == {
        "sound": True, "lights": False, "tone": 79, "name": "079 DISTORTION GT", "volume": 100, "output": None,
        "output_there": True}
    call(server, "/api/keys", {"tone": 1})
    gui.play_keys([0x94, 62, 90])  # (its tone on its channel)
    gui.play_keys([0x84, 62, 0])
    gui._keys_queue.join()
    call(server, "/api/keys", {"volume": 80})  # its volume: at once on its channel
    assert idle[-1] == [0xB0, 7, 80]
    gui.set_play_options({"silent": True})  # muted: its keys too (the one mute)
    gui.play_keys([0x94, 64, 90])
    gui._keys_queue.join()
    assert [0x90, 64, 90] not in idle
    gui.set_play_options({"silent": False})
    gui.play_keys([0x84, 64, 0])
    call(server, "/api/keys", {"tone": 79})
    assert call(server, "/api/state")[1]["keys"]["tone"] == 79 and gui.remote_info()["keys"]["tone"] == 79
    assert call(server, "/api/keys", {"tone": 999})[0] == 400
    call(server, "/api/keys", {"lights": True})
    assert call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"lights": 4}, "rhythm": {"play": False}}})[0] == 200
    call(server, "/api/play", {"song": sid, "start": 1.0})  # the keyboard of their own closes first
    assert idle[-1] == [0xB3, 7, 100] and len(server.outs) == 2
    playing = server.outs[-1]
    wait_for(lambda: [m for m in playing.sent if m[0] == 0x93 and m[2] > 0], bool)
    gui.play_keys([0x94, 84, 90])  # the song's keyboard: the new tone, its key lit before the song's
    gui._keys_queue.join()
    assert [0xC0, 30] in playing.sent and [0x93, 84, 100] in playing.sent and [0x90, 84, 90] in playing.sent
    keyboard = gui._session.keyboard
    assert 84 in keyboard._on and keyboard.holding
    gui.play_keys([0xB4, 1, 64])  # its modulation strip: the keys' sound's
    gui._keys_queue.join()
    assert playing.sent[-1] == [0xB0, 1, 64]
    gui.release_keys()  # the Launchkey gone: nothing left on (and no modulation)
    gui._keys_queue.join()
    assert 84 not in keyboard._on and not keyboard.holding and playing.sent[-1] == [0xB0, 1, 0]
    call(server, "/api/stop", {})


def test_a_remotes_turns_are_checked_and_saved_once_they_stop_and_go_back_to_the_last_that_fit(server, monkeypatch):
    gui, sid = server.gui, first_song(server)
    call(server, "/api/play", {"song": sid, "start": 1.0})
    planned = []
    real_plan = gui._plan
    monkeypatch.setattr(gui, "_plan", lambda s: planned.append(s) or real_plan(s))
    saved = gui.songs_file.read_text() if gui.songs_file.exists() else ""
    for tone in (30, 31, 32):  # a knob turning through the lead's tones
        gui.change_part("lead", {"tone": tone}, later=0.2)
    lead = next(p for p in gui.song(sid)["parts"] if p["part"] == "lead")
    assert lead["tones"]["tone"] == 32 and not planned  # kept at once (the page shows it), not planned each step
    assert (gui.songs_file.read_text() if gui.songs_file.exists() else "") == saved  # nor saved
    assert wait_for(lambda: planned, bool)  # once they stop: checked, saved and played
    assert wait_for(lambda: gui.songs_file.exists() and '"tone": 32' in gui.songs_file.read_text(), bool)

    def no_fit(s):
        raise ValueError("The parts' sounds need more channels than the keyboard has")
    monkeypatch.setattr(gui, "_plan", no_fit)
    gui.change_part("lead", {"tone": 40}, later=0.2)  # turned to tones that don't fit: back to the last that did
    assert wait_for(lambda: next(p for p in gui.song(sid)["parts"] if p["part"] == "lead")["tones"]["tone"] == 32, bool)
    call(server, "/api/stop", {})


def test_a_remotes_changes_are_kept_at_once_and_the_song_takes_them_once_they_stop(server):
    gui, sid = server.gui, first_song(server)
    call(server, "/api/play", {"song": sid, "start": 1.0})
    player = gui._session.player
    gui.change_part("lead", {"lights": 1}, later=0.2)  # taps on a pad: its lit keys, at once, in what plays
    gui.change_part("lead", {"lights": 2}, later=0.2)
    lead = next(p for p in gui.song(sid)["parts"] if p["part"] == "lead")
    assert lead["lights"] == 2 and gui._session.player is player  # (the notes sounding untouched)
    version = gui.play_version
    gui.change_part("lead", {"tone": 31}, later=0.2)  # turns of a knob: kept at once, played once they stop
    gui.change_part("lead", {"tone": 32}, later=0.2)
    assert gui.play_version == version + 2  # the page shows each
    assert gui._session.player is player  # the notes sounding go on meanwhile
    assert wait_for(lambda: gui._session.player is not player, bool)
    replaced = gui._session.player
    time.sleep(0.3)
    assert gui._session.player is replaced  # played again once, for both
    out = server.outs[-1]
    sent = len(out.sent)
    assert call(server, "/api/volumes", {"volumes": {"0": 80}})[1]["volumes"][:2] == [80, 100]  # the first
    assert gui._session.player is replaced  # arrangement's, in every song: at once on its channels, the notes going on
    assert out.sent[sent:] == [[0xBA, 7, 80]]  # (the channel it sounds on: 11)
    gui.change_part("lead", {"lights": 1})  # other lit keys, another tone: the volume stays
    gui.change_part("lead", {"tone": 30})
    assert next(p for p in gui.song(sid)["parts"] if p["part"] == "lead")["tones"] == {"tone": 30}
    assert wait_for(lambda: [0xCA, 11] in out.sent, bool)  # played again: its new tone,
    assert [0xBA, 7, 80] in out.sent[out.sent.index([0xCA, 11]):]  # and its volume after it
    call(server, "/api/stop", {})


def test_a_songs_sections_and_its_loop_on_its_beats(server):
    sid = first_song(server)
    _, song = call(server, f"/api/song?id={urllib.request.quote(sid)}")
    assert [(x["kind"], x["name"], x["start"]) for x in song["sections"]] == [
        ("intro", "Intro 1", 0.0), ("verse", "Verse 1", 10.0), ("chorus", "Chorus 1", 20.0), ("verse", "Verse 2", 30.0),
        ("chorus", "Chorus 2", 40.0)]  # (chorus2: a chorus)
    call(server, "/api/switch", {"song": sid})
    call(server, "/api/song/settings", {"song": sid, "start": 12.2})  # (stopped: where it starts)
    assert call(server, "/api/loop", {"a": "here"})[1] == {"a": 12.0, "b": None, "on": False}  # the nearest beat
    assert call(server, "/api/loop", {"move_b": 3})[1] == {"a": 12.0, "b": 13.5, "on": False}  # 3 beats on from here
    assert call(server, "/api/loop", {"move_b": -4})[1]["b"] == 13.5  # not to or before the start
    assert call(server, "/api/loop", {"move_b_bars": 1})[1]["b"] == 14.0  # on to the next bar line (a beat a half second,
    assert call(server, "/api/loop", {"move_b_bars": 2})[1]["b"] == 18.0  # 4 to a bar), then a bar a step
    assert call(server, "/api/loop", {"move_b_bars": -3})[1]["b"] == 18.0  # not to or before the start
    call(server, "/api/loop", {"b": 13.5})
    assert call(server, "/api/loop", {"on": True})[1]["on"]
    assert call(server, "/api/loop", {"a": 14.0})[1] == {"a": 14.0, "b": None, "on": False}  # past the end: it goes
    assert call(server, "/api/loop", {"b": 25.0})[1] == {"a": 14.0, "b": 25.0, "on": False}
    assert call(server, "/api/loop", {"b": 13.0, "on": True})[1] == {"a": 10.0, "b": 13.0, "on": True}  # before the
    # start: the start, its section's
    assert call(server, "/api/loop", {"section": True})[1] == {"a": 10.0, "b": 20.0, "on": True}  # the verse it's in
    assert call(server, "/api/loop", {"section": True})[1] == {"a": 10.0, "b": 20.0, "on": False}  # again: off
    assert call(server, "/api/state")[1]["loop"] == {"a": 10.0, "b": 20.0, "on": False}
    call(server, "/api/loop", {"on": True})
    call(server, "/api/play", {"song": sid, "start": 25.0})  # played past its end: back to it at once
    assert server.gui._session.loop == (10.0, 20.0) and server.gui._session.position < 20.0
    call(server, "/api/stop", {})


def test_a_remote_goes_through_the_library_a_letter_at_a_time(tmp_path):
    dlc = tmp_path / "game" / "dlc"
    dlc.mkdir(parents=True)
    for i, artist in enumerate(["Cream", "ABBA", "Bon Jovi", "311", "AC/DC", "Beatles"]):
        (dlc / f"s{i}_m.psarc").write_bytes(song_archive(
            f"s{i}", {"lead": (f"L{i}", Song(levels={0: [N(1.0)]}))}, song="Song", artist=artist))
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    artists = [s["artist"] for s in gui.library()["songs"]]
    assert artists == ["311", "ABBA", "AC/DC", "Beatles", "Bon Jovi", "Cream"]  # (as the page lists them)
    assert [gui.browse_letter(1)["artist"] for _ in range(4)] == ["ABBA", "Beatles", "Cream", "Cream"]  # (the last)
    gui.browse_move(-1)  # Bon Jovi
    assert [gui.browse_letter(-1)["artist"] for _ in range(4)] == ["Beatles", "ABBA", "311", "311"]
    assert gui.browse_letter(2)["artist"] == "Beatles"


def test_palm_mutes_can_have_the_same_tone_over_a_default(server):
    sid = first_song(server)
    call(server, "/api/follow/settings", {"lead": {"mute_tone": 77}})  # a default for lead parts' palm mutes
    _, song = call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"mute_tone": 0}}})
    assert next(p for p in song["parts"] if p["part"] == "lead")["tones"] == {"mute_tone": 0}  # kept
    call(server, "/api/play", {"song": sid})
    assert "palm mutes" not in sounds(server)[0]  # the same tone, over the default
    call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"mute_tone": None}}})
    assert wait_for(lambda: sounds(server)[0], lambda sound: "palm mutes 077" in sound)  # the default again
    call(server, "/api/stop", {})


def test_a_remote_has_each_arrangements_chords_and_where_the_song_is(server):
    sid = first_song(server)
    call(server, "/api/switch", {"song": sid})
    lead, rhythm = server.gui.remote_info()["song"]["arrangements"]
    assert [(c.name, c.start, c.end) for c in lead["chords"]] == [("Em", 1.0, 2.0)] and rhythm["chords"] == ()
    assert server.gui.song_position() is None  # (nothing plays)
    call(server, "/api/play", {"song": sid, "start": 5.0})
    assert 5.0 <= server.gui.song_position() < 6.0
    call(server, "/api/stop", {})


def test_a_remote_has_the_songs_lyrics_from_its_vocals_part(tmp_path):
    dlc = tmp_path / "game" / "dlc"
    dlc.mkdir(parents=True)
    (dlc / "v_m.psarc").write_bytes(song_archive(
        "v", {"lead": ("L", Song(levels={0: [N(1.0)]})), "vocals": ("V", Song(vocals=3))}, song="Song", artist="Band"))
    (dlc / "w_m.psarc").write_bytes(song_archive("w", {"lead": ("W", Song(levels={0: [N(1.0)]}))}, song="Other"))
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    with_lyrics, without = sorted(gui.library()["songs"], key=lambda song: song["song"] != "Song")
    gui.switch({"song": with_lyrics["id"]})
    assert [line.text for line in gui.remote_info()["song"]["lyrics"]] == ["la la la"]
    gui.switch({"song": without["id"]})
    assert gui.remote_info()["song"]["lyrics"] == ()
    page = gui.lyrics(with_lyrics["id"])  # (the page's card: lines, their syllables' starts and places)
    assert [line[2] for line in page["lines"]] == ["la la la"]
    assert [syllable[1:] for syllable in page["lines"][0][3]] == [[0, 2], [3, 5], [6, 8]]
    assert gui.lyrics(without["id"])["lines"] == []


def test_notes_ring_on_unless_turned_off(server):
    sid = first_song(server)
    call(server, "/api/play", {"song": sid})
    gui = server.gui
    assert gui._session.player.tracks[0].voices.ring  # (as they start)
    call(server, "/api/settings", {"ring": False})  # the song playing takes it at once
    assert wait_for(lambda: not gui._session.player.tracks[0].voices.ring, bool)
    assert not json.loads(gui.settings_file.read_text())["ring"]
    call(server, "/api/stop", {})


def test_a_songs_tone_changes_switch_its_parts_tone(tmp_path):
    dlc = tmp_path / "game" / "dlc"
    dlc.mkdir(parents=True)
    tones = [{"Key": "t_clean", "Name": "t_clean"}, {"Key": "t_dist", "Name": "t_dist"}]
    (dlc / "t_m.psarc").write_bytes(song_archive(
        "t", {"lead": ("L", Song(levels={0: [N(1.0 + i) for i in range(20)]}, tone_changes=[(5.0, 1), (9.0, 0)]))},
        song="Toned", artist="Band", extra={"Tone_Base": "t_clean", "Tone_A": "t_clean", "Tone_B": "t_dist",
                                            "Tones": tones}))
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    gui = Gui(index, keyboard_factory=lambda port, channel: Keyboard(port, channel, midi_out_factory=FakeMidiOut),
              settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    sid = gui.library()["songs"][0]["id"]
    gui.switch({"song": sid})
    assert gui.remote_info()["song"]["arrangements"][0]["tone_changes"] == (
        (-math.inf, "clean"), (5.0, "distortion"), (9.0, "its own tone"))  # (first, what it starts with)
    assert gui.song(sid)["parts"][0]["song_tones"] == [{"at": 0.0, "said": "clean", "known": True},
                                                       {"at": 5.0, "said": "distortion", "known": True},
                                                       {"at": 9.0, "said": "clean", "known": True}]
    gui.set_song({"song": sid, "parts": {"lead": {"tone": 30}}})  # its own tone, for its own (clean) one
    gui.play({"song": sid})
    lead = gui._session.player.tracks[0]
    assert [(at, (program, bank)) for at, _channel, program, bank in lead.changes] == [
        (-math.inf, (tone(30).program, tone(30).bank)), (5.0, (tone(297).program, tone(297).bank)),
        (9.0, (tone(30).program, tone(30).bank))]  # (distortion: 297 GM DISTORTION GT)
    gui.set_settings({"tone_map": {"distortion": 80}})  # another tone for distortion: at once
    assert wait_for(lambda: gui._session.player.tracks[0].changes[1][2], lambda program: program == tone(80).program)
    gui.set_song({"song": sid, "parts": {"lead": {"follow_tones": False}}})  # not by this part: its own tone stays
    assert wait_for(lambda: not gui._session.player.tracks[0].changes, bool)
    assert gui._session.player.tracks[0].voices.main.tone == tone(30)
    assert gui.song(sid)["parts"][0]["tones"] == {"tone": 30, "follow_tones": False}
    assert gui.remote_info()["song"]["arrangements"][0]["tone_changes"] == ()
    gui.set_song({"song": sid, "parts": {"lead": {"follow_tones": None}}})  # followed again
    assert wait_for(lambda: gui._session.player.tracks[0].changes, bool)
    assert gui.song(sid)["parts"][0]["tones"] == {"tone": 30}
    gui.set_settings({"tone_changes": False})  # not followed
    assert wait_for(lambda: not gui._session.player.tracks[0].changes, bool)
    assert gui.remote_info()["song"]["arrangements"][0]["tone_changes"] == ()
    gui.stop()


def test_a_remotes_keys_can_play_another_midi_device_and_light_the_keyboard(tmp_path):
    from rs_keylights.keyboard import Synth
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")

    class SynthOut(FakeMidiOut):
        def get_ports(self):
            return ["IAC Driver Bus 1"]

    synth_out, keyboard_outs = SynthOut(), []

    def keyboard(port, channel):
        keyboard_outs.append(FakeMidiOut())
        return Keyboard(port, channel, midi_out_factory=lambda: keyboard_outs[-1])

    gui = Gui(index, keyboard_factory=keyboard, settings_file=tmp_path / "gui.json", list_ports=list,
              other_follow=lambda: False, synth_factory=lambda port: Synth(port, midi_out_factory=lambda: synth_out))
    info = gui.set_keys({"output": {"port": "IAC Driver Bus 1", "channel": 3}})
    assert info["output"] == {"port": "IAC Driver Bus 1", "channel": 3} and info["output_there"]
    assert "output_there" not in gui.keys_info()  # (asked often: not opened to know)
    gui.play_keys([0x90, 60, 90])
    gui.play_keys([0xB0, 64, 127])  # (its sustain pedal: there too)
    gui._keys_queue.join()
    assert synth_out.sent[-2:] == [[0x92, 60, 90], [0xB2, 64, 127]]
    lit = keyboard_outs[-1].sent
    assert [0x93, 60, 100] in lit and [0x90, 60, 90] not in lit  # (its key lit on the keyboard, not sounding there)
    gui.set_keys({"output": None})  # (back to the keyboard: what sounded there stopped, its pedal up)
    assert synth_out.sent[-2:] == [[0x82, 60, 0], [0xB2, 64, 0]]
    gui.play_keys([0x90, 62, 90])
    gui._keys_queue.join()
    assert [0x90, 62, 90] in keyboard_outs[-1].sent
    for bad in ({"port": ""}, {"port": "IAC Driver Bus 1", "channel": 17}, "IAC"):
        with pytest.raises(GuiError):
            gui.set_keys({"output": bad})
    again = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    assert again.keys_info()["output"] is None
    gui.close()


def test_a_kind_of_part_can_sound_on_another_midi_device(tmp_path):
    from rs_keylights.keyboard import Synth
    dlc = tmp_path / "game" / "dlc"
    dlc.mkdir(parents=True)
    (dlc / "b_m.psarc").write_bytes(song_archive(
        "b", {"lead": ("L", Song(levels={0: [N(1.0 + i * 0.5, fret=i % 5) for i in range(20)]})),
              "bass": ("B", Song(levels={0: [N(1.0 + i * 0.5, fret=i % 5) for i in range(20)]}))}, song="Low"))
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()

    class SynthOut(FakeMidiOut):
        def get_ports(self):
            return ["monologue SOUND"]

    synth_out, keyboard_outs = SynthOut(), []

    def keyboard(port, channel):
        keyboard_outs.append(FakeMidiOut())
        return Keyboard(port, channel, midi_out_factory=lambda: keyboard_outs[-1])

    there = [True]
    gui = Gui(index, keyboard_factory=keyboard, settings_file=tmp_path / "gui.json", list_ports=list,
              other_follow=lambda: False,
              synth_factory=lambda port: Synth(port, midi_out_factory=lambda: synth_out if there[0] else FakeMidiOut()))
    gui.set_settings({"outputs": {"bass": {"port": "monologue SOUND", "channel": 1}}})
    assert gui.settings["outputs"] == {"bass": {"port": "monologue SOUND", "channel": 1, "mono": True, "program": None,
                                                "programs": 128, "octave": 0}}
    sid = gui.library()["songs"][0]["id"]
    gui.set_song({"song": sid, "parts": {"bass": {"play": True, "lights": 2}, "lead": {"lights": 2}}})
    gui.play({"song": sid})
    assert wait_for(lambda: [m for m in synth_out.sent if m[0] == 0x90], bool)  # the bass's notes there, channel 1
    first = min(m[1] for m in synth_out.sent if m[0] == 0x90)
    assert not [m for m in keyboard_outs[-1].sent if m[0] in (0x9B, 0x9C, 0x9D)]  # (none on the keyboard's for it)
    assert not [m for m in synth_out.sent if m[0] & 0xF0 == 0xC0]  # (its own sound: no tone)
    gui.set_volumes({1: 90})  # the mixer: its volume on it too
    assert [0xB0, 7, 90] in synth_out.sent
    gui.set_program("bass", 11)  # its program (a remote's knob): kept, sent at once
    assert synth_out.sent[-1] == [0xC0, 11] and gui.settings["outputs"]["bass"]["program"] == 11
    sent = len(synth_out.sent)
    gui.set_settings({"outputs": {"bass": {"port": "monologue SOUND", "channel": 1, "program": 11, "octave": 1}}})
    assert wait_for(lambda: [m for m in synth_out.sent[sent:] if m[0] == 0x90], bool)
    played = [m[1] for m in synth_out.sent[sent:] if m[0] == 0x90]
    assert min(played) == first + 12  # (an octave up)
    gui.stop()
    gui.set_settings({"outputs": {"bass": {"port": "monologue SOUND", "channel": 1, "program": 11, "programs": 100}}})
    assert gui.settings["outputs"]["bass"]["programs"] == 100  # how many it has (a Korg monologue's): the remote
    assert gui._output_of("bass") == {"port": "monologue SOUND", "program": 11, "programs": 100}  # goes round them
    with pytest.raises(GuiError, match="100 programs"):  # (one it hasn't got)
        gui.set_program("bass", 100)
    with pytest.raises(GuiError, match="100 programs"):
        gui.set_settings({"outputs": {"bass": {"port": "monologue SOUND", "program": 120, "programs": 100}}})
    gui.set_program("bass", 99)  # its last
    assert synth_out.sent[-1] == [0xC0, 99]
    assert gui.output_status() == {"bass": True}
    gui.close()  # (closed with the page)
    there[0] = False
    assert gui.output_status() == {"bass": False}  # unplugged: the keyboard plays it
    gui.set_settings({"outputs": {"bass": None}})
    assert gui.settings["outputs"] == {}


def test_tone_changes_first_defaults_the_keyboards_own_tones_become_its_gm_ones(tmp_path):
    from rs_keylights.songtones import FIRST_KINDS
    (tmp_path / "gui.json").write_text(json.dumps({"tone_map": {**FIRST_KINDS, "clean": 76}}))  # (clean: chosen)
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    assert gui.settings["tone_map"]["distortion"] == 297 and gui.settings["tone_map"]["clean"] == 76


def test_the_guitar_heard_lights_its_keys_and_the_song_says_what_it_expects(server, monkeypatch):
    import rs_keylights.gui as gui_module

    started = []

    class FakeListener:  # (not the cable: what's heard is said by the test)
        def __init__(self, heard, expected, device, lowest, channel=1):
            self.device, self.now, self.level, self.error, self.running = device, (), 0.0, None, False
            self.channel = channel
            started.append(self)

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

    monkeypatch.setattr(gui_module, "Listener", FakeListener)
    gui = server.gui
    sid = first_song(server)
    call(server, "/api/play", {"song": sid})
    monkeypatch.setattr(gui, "song_position", lambda: 2.0)
    assert gui._expected_now() == (43, 44)  # (the notes about then: frets 3 and 4 on the low E, G2 and Ab2)
    out = server.outs[-1]
    sent = len(out.sent)
    gui._hear((40, 47))  # heard: lit first
    assert [0x93, 40, 100] in out.sent[sent:] and [0x93, 47, 100] in out.sent[sent:]
    call(server, "/api/settings", {"listen": True})
    assert started and started[-1].running and gui.listening()["on"]
    call(server, "/api/settings", {"listen": False})
    assert not started[-1].running
    call(server, "/api/stop", {})


def test_the_pedal_switches_from_the_page_its_presets_named_by_a_backup(server, tmp_path, no_real_pedal):
    path = tmp_path / "DNAfx GiT Core backup.bhb"
    path.write_bytes(backup({49: "5050 CL", 50: "5050 DS"}))
    _, settings = call(server, "/api/settings", {"pedal_backup": str(path)})
    assert settings["pedal_backup_used"] == str(path) and len(settings["pedal_names"]) == 200
    assert settings["pedal_names"][48:50] == ["5050 CL", "5050 DS"]
    said = call(server, "/api/state")[1]["pedal"]
    assert said.pop("names") == 0.0 and said.pop("names_error") in (None, "no pedal in tests")  # (not read from it)
    assert said == {"slot": None, "name": "", "previous": None, "previous_name": "", "error": None,
                    "favourites": [51, 187], "hold": False, "tone": None}  # (not following)
    status, names = call(server, "/api/pedal/names")
    assert status == 200 and names["names"][48] == "5050 CL" and names["version"] == 0.0  # (the backup's)
    status, said = call(server, "/api/pedal/names", {})
    assert status == 400 and "no pedal in tests" in said["error"]
    from rs_keylights import pedal as pedal_module  # (the hold file: dnafx-preset's listener keeps the tone)
    assert call(server, "/api/pedal", {"hold": True})[1]["hold"] is True and pedal_module.HOLD_FILE.exists()
    assert call(server, "/api/pedal", {"hold": False})[1]["hold"] is False and not pedal_module.HOLD_FILE.exists()
    _, settings = call(server, "/api/settings", {"pedal_favourites": [187, 51, 187, 5]})
    assert settings["pedal_favourites"] == [187, 51, 5]
    assert call(server, "/api/settings", {"pedal_favourites": [0]})[0] == 400
    _, pedal = call(server, "/api/pedal", {"slot": 49})
    assert (pedal["slot"], pedal["name"]) == (49, "5050 CL")
    assert wait_for(lambda: list(no_real_pedal), lambda switched: switched == [49]) == [49]
    _, pedal = call(server, "/api/pedal", {"step": 1})  # (the one after)
    assert wait_for(lambda: list(no_real_pedal), lambda switched: switched == [49, 50]) == [49, 50]
    _, pedal = call(server, "/api/state")
    assert (pedal["pedal"]["slot"], pedal["pedal"]["previous_name"]) == (50, "5050 CL")
    _, pedal = call(server, "/api/pedal", {"back": True})
    assert pedal["slot"] == 49 and wait_for(lambda: list(no_real_pedal), lambda switched: len(switched) == 3)[-1] == 49
    assert call(server, "/api/pedal", {"slot": 201})[0] == 400
    assert server.gui.remote_info()["pedal"]["slot"] == 49  # (the Launchkey's)


def test_the_fretboard_has_the_guitar_a_song_is_played_on():
    lead, rhythm, bass = (SimpleNamespace(bass=False), SimpleNamespace(bass=False), SimpleNamespace(bass=True))
    b_standard, drop_c = SimpleNamespace(tuning=(-5,) * 6, capo=0), SimpleNamespace(tuning=(-4, -2, -2, -2, -2, -2), capo=2)
    assert _guitar([(bass, b_standard), (lead, b_standard)]) == {  # (its guitar's, not its bass's)
        "strings": [35, 40, 45, 50, 54, 59], "capo": 0, "tuning": "B standard", "bass": False}
    assert _guitar([(rhythm, drop_c)])["strings"] == [36, 43, 48, 53, 57, 62]
    assert _guitar([(rhythm, drop_c)])["tuning"] == "Drop C, capo 2"
    assert _guitar([(bass, SimpleNamespace(tuning=(-5,) * 6, capo=0))]) == {  # (a bass's four, with no guitar)
        "strings": [23, 28, 33, 38], "capo": 0, "tuning": "B standard", "bass": True}
    assert _guitar([]) is None


def test_the_fretboard_gets_an_arrangements_notes_where_theyre_played(server):
    [song] = call(server, "/api/library")[1]["songs"]
    sid = urllib.request.quote(song["id"])
    _, details = call(server, f"/api/song?id={sid}")
    lead = next(p for p in details["parts"] if p["part"] == "lead")
    _, board = call(server, f"/api/fretboard?id={sid}&part=rhythm")
    assert (board["part"], board["name"], board["strings"], board["tuning"]) == (
        "rhythm", "Rhythm", [40, 45, 50, 55, 59, 64], "E standard")
    assert len(board["notes"]) == 40 and board["chords"] == []
    for start, end, places in board["notes"]:  # (each one's strings and frets: its pitches, on this guitar)
        assert start <= end and places and all(0 <= string < 6 and 0 <= fret <= 24 for string, fret in places)
    assert len(board["techniques"]) == len(board["notes"])  # (for its tab: each note's mask, slide and bend)
    assert all(len(t) == 3 for t in board["techniques"]) and board["muted"] == []
    bars = call(server, f"/api/fretboard?id={sid}&part=lead")[1]["bars"]  # (its beats charted: bars from them)
    assert len(bars) > 10 and bars == sorted(bars) and board["length"] > 0
    assert call(server, f"/api/fretboard?id={sid}&part={lead['arrangement']}")[1]["part"] == "lead"  # (as the game has it)
    assert call(server, f"/api/fretboard?id={sid}")[1]["part"] == "lead"  # (its first guitar part)


def test_following_the_loop_goes_to_the_games_riff_repeater(server, no_real_game_loop):
    game = no_real_game_loop[-1]
    server.overlay.state = {"memoryReadout": {"songTimer": 21.1, "songID": "song", "arrangementID": "L"}, "currentState": 4}
    call(server, "/api/follow", {})
    wait_for(lambda: call(server, "/api/follow")[1], lambda i: i["song"])
    loop = call(server, "/api/loop", {"a": "here"})[1]  # (where the game is: the nearest beat)
    assert 21.0 <= loop["a"] <= 22.0 and loop["game"] == {"answered": True, "hook": True, "riff": False}
    loop = call(server, "/api/loop", {"move_b": 4})[1]
    assert loop["b"] == loop["a"] + 2.0 and game.sent == [("off",), ("off",)]  # (not on yet: its own phrases)
    call(server, "/api/loop", {"on": True})
    assert game.sent[-1] == ("loop", loop["a"], loop["b"])  # Riff Repeater loops it
    call(server, "/api/loop", {"on": True, "riff": True})  # (Launchkey's pad 1, B: its menu opened on it too)
    assert game.sent[-2:] == [("loop", loop["a"], loop["b"]), ("riff",)] and game.screen == "LearnASong_RiffRepeater"
    game.screen = "LearnASong_Game"
    assert server.gui.remote_info()["loop"]["on"]  # (the Launchkey shows it)
    game.range = None  # the game started again: its mod has no range,
    time.sleep(0.06)
    call(server, "/api/state")
    assert game.sent[-1] == ("loop", loop["a"], loop["b"]) and game.range == (loop["a"], loop["b"])  # so it's sent again
    _, riff = call(server, "/api/riff", {"speed": 70, "accelerator": True, "passes": 2})  # its settings too
    assert riff == {"speed": 70, "accelerator": True, "passes": 2, "answered": True}
    assert game.sent[-3:] == [("speed", 70), ("accel", True), ("passes", 2)]
    assert server.gui.remote_info()["speed"] == 0.7 and call(server, "/api/riff", {"speed": 500})[0] == 400
    call(server, "/api/stop", {})  # following stopped: Riff Repeater's own phrases again
    call(server, "/api/state")
    assert game.sent[-1] == ("off",)


def test_following_the_games_tone_mixer_is_sent_the_volumes_and_sets_them(server):
    gui, game = server.gui, server.gui._riff_loop
    call(server, "/api/follow", {})
    gui.remote_info()
    keys_row = lambda rows: next(r for r in rows if r[0] == "keys")[:4]  # noqa: E731
    assert keys_row(game.kmix_rows) == ("keys", round(gui.settings["keys_volume"] * 100 / 127, 1), False, "KEYS")
    sent = len(game.sent)
    gui.remote_info()
    assert not [s for s in game.sent[sent:] if s[0] == "kmix"]  # (unchanged: not sent again)
    game.screen, game.kmix_moved = "RSKeylights_Mixer", {"keys": 50.0}  # its MIXER open, a fader moved there
    gui._game_time = (-math.inf, None)
    info = gui.remote_info()
    assert gui.settings["keys_volume"] == 64 and info["game_screen"] == "RSKeylights_Mixer"
    gui.remote_info()
    assert keys_row(game.kmix_rows) == ("keys", 50.4, False, "KEYS")  # (and the mod's told the volume as it's now)
    game.kmix_moved = {"keys": "mute"}  # Return on its row: muted (the mod shows it at once: nothing sent back)
    gui._game_time = (-math.inf, None)
    gui.remote_info()
    assert not gui.settings["keys_sound"] and keys_row(gui._game_mixer_sent) == ("keys", 50.4, True, "KEYS")
    game.kmix_moved = {"keys": "mute"}  # again, asked between looks (the Launchkey's loop, each time round): back on
    gui.game_mixer_step()
    assert gui.settings["keys_sound"] and keys_row(gui._game_mixer_sent) == ("keys", 50.4, False, "KEYS")


def test_following_the_games_tones_page_is_sent_the_tones_and_steps_them(server, no_real_game_loop):
    game = no_real_game_loop[-1]
    server.overlay.state = {"memoryReadout": {"songTimer": 21.1, "songID": "song", "arrangementID": "L"}, "currentState": 4}
    call(server, "/api/follow", {})
    wait_for(lambda: call(server, "/api/follow")[1], lambda i: i["song"])
    gui = server.gui
    gui.remote_info()
    keys = gui.keys_info()
    first = gui.remote_info()["song"]["arrangements"][0]
    played = gui._part_tone(first)
    row = game.ktone_rows[0]  # (its tone on the knobs' page: number, group, name; its kind, following, column, page)
    assert row[:2] == ("a0", str(first["name"]).upper()) and row[2].startswith(f"{played:03d}|")
    assert row[2].endswith(f"|{tone(played).name}") and row[3] == first["kind"] and row[5:] == (0, 0)
    assert game.ktone_rows[-1][:2] == ("keys", "KEYS") and game.ktone_rows[-1][2].startswith(f"{keys['tone']:03d}|")
    assert game.ktone_rows[-1][5] == 6  # (the Launchkey keys': its 7th, as knob 7)
    game.screen, game.kmix_moved = "RSKeylights_Tones", {"a0": "tone:1", "keys": "tone:-1"}  # Right, and Left
    gui.game_mixer_step()
    stepped = TONE_ORDER[(TONE_ORDER.index(played) + 1) % len(TONE_ORDER)]
    assert gui.remote_info()["song"]["arrangements"][0]["tones"]["tone"] == stepped  # (its own now: kept)
    assert gui.keys_info()["tone"] == TONE_ORDER[TONE_ORDER.index(keys["tone"]) - 1]
    gui.remote_info()
    assert game.ktone_rows[0][2].startswith(f"{stepped:03d}|")  # (and the mod's told)
    game.kmix_moved = {"field": 1.0}  # its up arrow: palm mutes (the Launchkey's page too)
    gui.game_mixer_step()
    assert gui.remote_info()["tone_field"] == {"turns": 1, "field": 1} and game.ktone_rows[0][6] == 1
    assert call(server, "/api/state")[1]["tone_field"] == 1  # (the page's rows show their palm mutes' tones)
    gui.set_tone_field(2)  # (the Launchkey's up arrow: harmonics)
    assert call(server, "/api/state")[1]["tone_field"] == 2
    gui.set_tone_field(1)
    game.kmix_moved = {"a0": "tone:1"}  # (a step on that page: its palm mutes' tone, from where its knob is)
    gui.game_mixer_step()
    assert "mute_tone" in gui.remote_info()["song"]["arrangements"][0]["tones"]
    assert gui.remote_info()["song"]["arrangements"][0]["tones"]["tone"] == stepped  # (its tone as it was)
    game.kmix_moved = {"a0": "follow"}  # its pad below: its tone kept, not following the song's tone changes
    gui.game_mixer_step()
    assert gui.remote_info()["song"]["arrangements"][0]["tones"]["follow_tones"] is False
    game.kmix_moved = {"a0": "follow"}  # (and following them again)
    gui.game_mixer_step()
    assert "follow_tones" not in gui.remote_info()["song"]["arrangements"][0]["tones"]


def test_the_games_new_mixer_gets_lit_keys_kinds_music_and_all_and_its_pads_act_as_the_launchkeys(server):
    gui, game = server.gui, server.gui._riff_loop
    call(server, "/api/follow", {})
    changed = []
    gui.change_part = lambda part, fields, *a, **k: changed.append((part, fields))
    followed = {"arrangements": [
        {"id": "L", "name": "Lead", "kind": "lead", "play": True, "lights": 4, "tones": {}},
        {"id": "B", "name": "Bass", "kind": "bass", "play": True, "lights": 0, "tones": {}},
        {"id": "drums", "name": "Drums", "kind": "drums", "play": True, "made": True, "drum_set": None, "volume": 90}]}
    said = game.status()  # (the game's Music 80, as the fake has it)
    gui._game_mixer(followed, said)
    rows = {r[0]: r for r in game.kmix_rows}
    assert rows["a0"][4:] == (1, "lead", 0) and rows["a1"][4:] == (0, "bass", 1) and rows["drums"][4:] == (0, "drums", 2)
    assert rows["keys"][3:] == ("KEYS", 1, "keys", 6)  # (the tone mixer's columns 1 to 8, as the Launchkey's knobs)
    assert rows["all"][1] == max(rows[k][1] for k in ("a0", "a1", "drums", "keys")) and rows["all"][5:] == ("all", 7)
    assert rows["music"] == ("music", 80.0, False, "MUSIC", -1, "music", 14)  # (the stem mixer's knob 7)
    assert rows["smaster"] == ("smaster", 0.0, False, "MASTER", -1, "all", 15)  # (and its 8th: its parts together)
    assert rows["gbass"] == ("gbass", 0.0, False, "BASS", -1, "stem:bass:off", 9)  # (no Song Stems: off)
    game.kmix_moved = {"a1": "lights", "drums": "lights", "keys": "lights", "all": "mute", "music": 55.0}
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert changed == [("B", {"lights": 1})]  # (lit too: sharing the 4 keys)
    assert gui.settings["beat_lights"] and not gui.settings["keys_lights"] and gui.play_options["silent"]
    assert game.sent[-1] == ("music", 55)
    game.kmix_moved = {"smaster": "mute"}  # (the stem mixer's 8th: its parts' master mute, not the sound's; no
    with gui._game_mixer_lock:             # Song Stems: nothing)
        gui._game_mixer_apply()
    assert gui.play_options["silent"]
    followed["arrangements"][1]["lights"] = 1  # (lit now: its pad turns it off)
    gui._game_mixer(followed, said)
    game.kmix_moved = {"a1": "lights"}
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert changed[-1] == ("B", {"lights": 0})


def test_the_games_stem_mixer_has_its_songs_bass_and_guitar_as_song_stems_has_them(server):
    gui, game = server.gui, server.gui._riff_loop
    call(server, "/api/follow", {})
    ini = gui.index.game_dir / "RSMods.ini"
    ini.write_text("[Toggle Switches]\nSongStems = on\n\n[Mod Settings]\nSongStemsGuitarLevel = 80\nSongStemsBassLevel = 60\n")
    followed = {"arrangements": [
        {"id": "L", "name": "Lead", "kind": "lead", "play": True, "playing": True, "lights": 1, "tones": {}},
        {"id": "B", "name": "Bass", "kind": "bass", "play": True, "playing": False, "lights": 0, "tones": {}}]}
    gui._game_mixer(followed, game.status())
    rows = {r[0]: r for r in game.kmix_rows}
    assert rows["gguitar"] == ("gguitar", 80.0, False, "GUITAR", -1, "stem:guitar", 10)  # (played: taken out)
    assert rows["gbass"] == ("gbass", 60.0, False, "BASS", -1, "stem:bass:wait", 9)  # (waiting its turn)
    assert rows["smaster"][1:3] == (80.0, False) and rows["smaster"][5:] == ("all", 15)  # (MASTER: the louder, the
    # Music apart)
    game.kmix_moved = {"gbass": 45.0, "gguitar": "mute"}  # its fader moved; its pad: muted, its level kept
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert gui.game_stems_info() == {"on": True, "guitar": 80, "bass": 45, "muted": {"guitar": True, "bass": False}}
    gui._game_mixer(followed, game.status())
    assert next(r for r in game.kmix_rows if r[0] == "gguitar")[1:3] == (80.0, True)  # (greyed at its level)
    game.kmix_moved = {"gguitar": 55.0}  # (moved, muted: kept for when it's back)
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    gui._game_mixer(followed, game.status())
    game.kmix_moved = {"gguitar": "mute"}  # (and back: at it)
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert gui.game_stems_info() == {"on": True, "guitar": 55, "bass": 45, "muted": {"guitar": False, "bass": False}}
    game.kmix_moved = {"music": "mute"}  # its Music's pad: muted, then back where it was
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert game.sent[-1] == ("music", 0)
    gui._game_mixer(followed, game.status())
    assert next(r for r in game.kmix_rows if r[0] == "music")[1:3] == (80.0, True)  # (greyed at its level)
    game.kmix_moved = {"music": "mute"}
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert game.sent[-1] == ("music", 80)
    game.kmix_moved = {"smaster": "mute"}  # MASTER's pad: both muted (the Music, the sound apart), then both back
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert gui.game_stems_info() == {"on": True, "guitar": 55, "bass": 45, "muted": {"guitar": True, "bass": True}}
    assert "SongStemsGuitarLevel = 0" in ini.read_text() and not gui.play_options["silent"]
    gui._game_mixer(followed, game.status())
    assert next(r for r in game.kmix_rows if r[0] == "smaster")[2]  # (muted: all of them)
    game.kmix_moved = {"smaster": "mute"}
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert gui.game_stems_info() == {"on": True, "guitar": 55, "bass": 45, "muted": {"guitar": False, "bass": False}}


def test_the_games_mixer_pages_turned_there_are_said_and_the_launchkeys_go_there(server):
    gui, game = server.gui, server.gui._riff_loop
    call(server, "/api/follow", {})
    assert gui.remote_info()["game_mixer_page"] == {"turns": 0, "page": None}
    game.kmix_moved = {"page": "stems"}  # ▼ there
    with gui._game_mixer_lock:
        assert gui._game_mixer_apply()
    assert gui.remote_info()["game_mixer_page"] == {"turns": 1, "page": "stems"}
    gui.set_game_mixer_page("parts")  # the Launchkey's ▲
    assert game.sent[-1] == ("kpage", "parts")


def test_the_games_new_mixer_has_a_drum_part_to_make_as_the_launchkeys_pad_makes_it(server):
    gui, game = server.gui, server.gui._riff_loop
    call(server, "/api/follow", {})
    changed = []
    gui.change_part = lambda part, fields, *a, **k: changed.append((part, fields))
    drums = {"id": "drums", "name": "Drums", "kind": "drums", "play": False, "made": False, "making": False}
    followed = {"arrangements": [{"id": "L", "name": "Lead", "kind": "lead", "play": True, "lights": 1, "tones": {}},
                                 drums]}
    gui._game_mixer(followed, game.status())
    assert next(r for r in game.kmix_rows if r[0] == "drums") == ("drums", 0.0, False, "DRUMS", -1, "drums:make", 1)
    game.kmix_moved = {"drums": "mute"}  # its pad: made
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert changed == [("drums", {"make": True})]
    drums.update(making=True, progress=0.42)
    gui._game_mixer(followed, game.status())
    assert next(r for r in game.kmix_rows if r[0] == "drums")[5] == "drums:42%"
    game.kmix_moved = {"drums": "mute"}  # (again while it's made: nothing)
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert changed == [("drums", {"make": True})]


def test_the_games_tones_page_has_the_keyboards_drum_kit_and_steps_it(server):
    from rs_keylights.tones import DRUM_SET_BANK, DRUM_SETS, TONES
    gui, game = server.gui, server.gui._riff_loop
    changed = []
    gui.change_part = lambda part, fields, *a, **k: changed.append((part, fields))
    kits = [program for _name, program in DRUM_SETS]
    followed = {"arrangements": [
        {"id": "L", "name": "Lead", "kind": "lead", "play": True, "tones": {}},
        {"id": "drums", "name": "Drums", "kind": "drums", "play": True, "made": True, "drum_set": kits[-1], "volume": 90}]}
    gui._game_mixer(followed, None)
    assert [r[0] for r in game.ktone_rows] == ["a0", "drums", "keys"]
    kit = next(t for t in TONES if t.bank == DRUM_SET_BANK and t.program == kits[-1])
    assert game.ktone_rows[1] == ("drums", "DRUMS", f"{kit.number:03d}|Drum kit|{kit.name.title()}", "drums", -1, 1, 0)
    game.kmix_moved = {"drums": "tone:1"}  # Right on it, in the game: the next kit (round to the first)
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert changed[-1] == ("drums", {"kit": kits[0]})
    followed["arrangements"][1]["drum_set"] = None  # (on another device: no kit of the keyboard's to step)
    gui._game_mixer(followed, None)
    assert [r[0] for r in game.ktone_rows] == ["a0", "keys"]


def test_following_a_part_muted_in_the_games_tone_mixer_doesnt_hold_up_the_rest(server, no_real_game_loop):
    game = no_real_game_loop[-1]
    server.overlay.state = {"memoryReadout": {"songTimer": 21.1, "songID": "song", "arrangementID": "L"}, "currentState": 4}
    call(server, "/api/follow", {})
    wait_for(lambda: call(server, "/api/follow")[1], lambda i: i["song"])
    gui = server.gui
    gui.remote_info()
    played = gui.remote_info()["song"]["arrangements"][0]["play"]
    assert next(r for r in game.kmix_rows if r[0] == "a0")[2] == (not played)
    game.screen, game.kmix_moved = "RSKeylights_Mixer", {"a0": "mute"}  # Return on its row, in the game
    gui._game_time = (-math.inf, None)
    looked = threading.Thread(target=gui.remote_info, daemon=True)
    looked.start()
    looked.join(10)
    assert not looked.is_alive()  # (the part changed looks at the state again, which sends the rows: no deadlock)
    assert gui.remote_info()["song"]["arrangements"][0]["play"] == (not played)
    assert next(r for r in gui._game_mixer_sent if r[0] == "a0")[2] == played


def test_following_the_games_music_volume_is_read_and_set_through_its_mod(server):
    gui, game = server.gui, server.gui._riff_loop
    with pytest.raises(Exception, match="Follow Rocksmith first"):
        gui.set_game_music({"step": 1})
    call(server, "/api/follow", {})
    assert gui.remote_info()["music"] == 80  # (as the game has it)
    assert call(server, "/api/game/music", {"step": -6})[1] == {"music": 74, "muted": False}
    assert game.sent[-1] == ("music", 74)
    assert gui.remote_info()["music"] == 74
    assert gui.set_game_music({"step": 90}) == {"music": 100, "muted": False}
    assert gui.set_game_music({"level": 35}) == {"music": 35, "muted": False}
    assert call(server, "/api/game/music", {"level": 101})[0] == 400
    game.music_level = None  # (a mod without it)
    gui._game_time = (-math.inf, None)
    assert gui.remote_info()["music"] is None
    assert "update the riffloop mod" in call(server, "/api/game/music", {"step": 1})[1]["error"]


def test_following_riff_repeaters_speed_is_the_games_whoever_set_it(server):
    gui, game = server.gui, server.gui._riff_loop
    call(server, "/api/follow", {})
    assert gui.remote_info()["speed"] == 1.0  # (a mod that doesn't say: as set here)
    game.riff_speed = 62.0  # (the game's own slider moved)
    gui._game_time = (-math.inf, None)
    assert gui.remote_info()["speed"] == 0.62 and gui.remote_info()["riff"]["speed"] == 62
    assert call(server, "/api/riff", {"speed": 63})[1]["speed"] == 63  # (set here: its, at once, whatever the mod
    assert gui.remote_info()["speed"] == 0.63  # says of the one before)
    game.riff_accelerator = True  # (switched on in the game's menu)
    gui._game_time = (-math.inf, None)
    assert gui.remote_info()["riff"]["accelerator"] is True
    assert call(server, "/api/riff", {"accelerator": False})[1]["accelerator"] is False
    assert gui.remote_info()["riff"]["accelerator"] is False


def test_the_keys_card_has_what_the_keyboard_plays_on_its_keys(server):
    sid = first_song(server)
    status, roll = call(server, f"/api/roll?id={urllib.request.quote(sid)}")
    assert status == 200 and (roll["lowest"], roll["highest"], roll["most_lit"]) == (36, 96, 4)
    assert [(p["part"], p["kind"], p["lights"]) for p in roll["parts"]] == [("lead", "lead", 1), ("rhythm", "rhythm", 1)]
    assert roll["notes"] and all(36 <= key <= 96 and end > start for start, end, key, _part, _lit in roll["notes"])
    assert {part for *_rest, part, _lit in roll["notes"]} == {0, 1} and any(lit for *_rest, lit in roll["notes"])
    assert [n[0] for n in roll["notes"]] == sorted(n[0] for n in roll["notes"])  # (in time)
    call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"play": False}}})  # (muted, its keys lit
    _, muted = call(server, f"/api/roll?id={urllib.request.quote(sid)}")                     # still: just them)
    assert [(p["part"], p["muted"]) for p in muted["parts"]] == [("lead", False), ("rhythm", True)]
    assert all(lit for *_rest, part, lit in muted["notes"] if part == 1)  # (not heard: its lit notes alone)
    call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"lights": 0}}})  # (and not lit: gone)
    assert [p["part"] for p in call(server, f"/api/roll?id={urllib.request.quote(sid)}")[1]["parts"]] == ["lead"]
    _, alone = call(server, f"/api/roll?id={urllib.request.quote(sid)}&part=rhythm")  # (one alone, muted or not)
    assert [(p["part"], p["lights"]) for p in alone["parts"]] == [("rhythm", 4)] and not alone["pooled"]
    assert call(server, f"/api/roll?id={urllib.request.quote(sid)}&part=bass")[0] == 400


def test_the_keys_card_shows_a_change_before_the_song_playing_takes_it(server):
    sid = first_song(server)
    call(server, "/api/play", {"song": sid, "start": 1})
    server.gui.set_song({"song": sid, "parts": {"rhythm": {"play": False, "lights": 0}}}, later=30)  # (a
    # Launchkey's taps: the song playing takes them once the taps stop)
    _, roll = call(server, f"/api/roll?id={urllib.request.quote(sid)}")
    assert [p["part"] for p in roll["parts"]] == ["lead"]  # (as chosen now, not as it still plays)
    server.gui.set_song({"song": sid, "parts": {"lead": {"lights": 0}}}, later=30)
    _, roll = call(server, f"/api/roll?id={urllib.request.quote(sid)}")
    assert roll["parts"][0]["lights"] == 0 and not any(lit for *_rest, lit in roll["notes"])


def test_a_songs_drum_part_plays_along_on_the_drum_channel_and_making_one_says_why_it_cant(server):
    from rs_keylights import drums
    sid = first_song(server)
    gui = server.gui
    _, song = call(server, f"/api/song?id={urllib.request.quote(sid)}")
    assert song["drums"] == {"play": False, "volume": 100, "made": False, "hits": 0, "making": None, "progress": None, "error": None,
                             "tools": False, "kit": gui.settings["drum_set"], "device": None,
                             "drum_sets": [[0, "Standard Set"], [8, "Room Set"], [16, "Power Set"], [25, "Synth Set"],
                                           [40, "Brush Set"], [48, "Orchestra Set"]], "lights": False}
    status, info = call(server, "/api/drums", {"song": sid, "lights": True})  # (its bulb: the remote's beat lights)
    assert status == 200 and info["lights"] and gui.settings["beat_lights"]
    assert not call(server, "/api/drums", {"song": sid, "lights": False})[1]["lights"] and not gui.settings["beat_lights"]
    ref = gui._load(sid)[0][0]
    drums.keep(ref, [drums.Hit(1.5, drums.KICK, 110), drums.Hit(1.75, drums.SNARE, 90)])
    status, info = call(server, "/api/drums", {"song": sid, "play": True, "volume": 90})
    assert status == 200 and (info["made"], info["hits"], info["play"], info["volume"]) == (True, 2, True, 90)
    call(server, "/api/play", {"song": sid, "start": 1.4})
    wait_for(lambda: [m for out in server.outs for m in out.sent if m[0] == 0x99], lambda sent: len(sent) >= 2)
    sent = [m for out in server.outs for m in out.sent if m[0] in (0x99, 0xB9)]
    assert [0x99, drums.KICK, 110] in sent and [0x99, drums.SNARE, 90] in sent and [0xB9, 7, 90] in sent  # (its volume)
    call(server, "/api/stop", {})
    call(server, "/api/drums", {"song": sid, "make": True})  # (no tools here: why, on the page)
    info = wait_for(lambda: gui.drums_info(sid), lambda d: d["error"] is not None)
    assert "drums-setup.sh" in info["error"] and info["making"] is None and info["made"]  # (the one made stays)


def test_a_remote_has_a_songs_drum_part_as_one_more_arrangement(server):
    from rs_keylights import drums
    sid = first_song(server)
    gui = server.gui
    call(server, "/api/play/options", {"song": sid})
    assert [a["id"] for a in gui.remote_info()["song"]["arrangements"]] == ["lead", "rhythm"]  # (none made)
    drums.keep(gui._load(sid)[0][0], [drums.Hit(1.0, drums.KICK, 100)])
    arrangements = gui.remote_info()["song"]["arrangements"]
    assert [a["id"] for a in arrangements] == ["lead", "rhythm", "drums"] and arrangements[2]["volume"] == 100
    gui.change_part("drums", {"volume": 70})  # (the Launchkey's: the drum part's own)
    gui.change_part("drums", {"play": True})
    assert gui.drums_info(sid)["volume"] == 70 and gui.drums_info(sid)["play"]
    assert gui.remote_info()["song"]["arrangements"][2]["play"]
    assert [a["id"] for a in gui._with_drums(sid, [{"id": str(i)} for i in range(5)])] == ["0", "1", "2", "3", "drums",
                                                                                         "4"]  # (5: its fifth place)


def test_a_remote_can_make_a_songs_drum_part_from_its_place_and_then_it_plays(server, monkeypatch):
    from rs_keylights import drums
    sid = first_song(server)
    gui = server.gui
    call(server, "/api/play/options", {"song": sid})
    assert [a["id"] for a in gui.remote_info()["song"]["arrangements"]] == ["lead", "rhythm"]  # (no tools: no place)
    monkeypatch.setattr(drums, "setup_done", lambda tools=None: True)
    made = []

    def make(ref, beats, game_dir, say=lambda _t: None, **_kw):
        say("transcribing them")
        made.append(ref.song_key)
        hits = [drums.Hit(1.0, drums.KICK, 100)]
        drums.keep(ref, hits)
        return hits

    monkeypatch.setattr(drums, "make", make)
    a = gui.remote_info()["song"]["arrangements"][2]
    assert (a["id"], a["made"], a["play"], a["making"]) == ("drums", False, False, False)
    version = gui.play_version
    gui.change_part("drums", {"make": True})  # (its pad on top)
    assert gui.play_version > version  # (every page shows it's being made, not just once it's made)
    a = wait_for(lambda: gui.remote_info()["song"]["arrangements"][2], lambda x: x["made"])
    assert made and a["play"] and gui.drums_info(sid)["play"]  # (made: playing, as it would after Make on the page)


def test_the_drums_card_has_the_songs_drum_part_and_a_drum_struck_sounds_on_the_drum_channel(server):
    from rs_keylights import drums
    sid = first_song(server)
    gui = server.gui
    _, part = call(server, f"/api/drums/part?id={urllib.request.quote(sid)}")
    assert (part["made"], part["hits"], part["device"], part["channel"]) == (False, [], None, 10)
    assert part["names"]["36"] == "kick" and part["notes"]["49"] == 49  # (as General MIDI has them, on the keyboard)
    drums.keep(gui._load(sid)[0][0], [drums.Hit(1.0, drums.KICK, 110), drums.Hit(1.5, drums.CRASH, 80)])
    _, part = call(server, f"/api/drums/part?id={urllib.request.quote(sid)}")
    assert part["made"] and part["hits"] == [[1.0, 36, 110], [1.5, 49, 80]]
    status, said = call(server, "/api/drums/hit", {"note": 38})
    assert status == 200 and said == {"played": 38}
    sent = wait_for(lambda: [m for out in server.outs for m in out.sent if m[0] in (0x99, 0x89)],
                    lambda m: any(x[0] in (0x99, 0x89) and x[2] == 0 or x[0] == 0x89 for x in m))
    assert [0x99, 38, 100] in sent  # (struck; let go a moment later)


def test_the_drums_on_a_circuit_have_a_kit_its_drums_and_samples_and_a_drum_struck_chooses_its_sample(tmp_path):
    from rs_keylights import drums
    from rs_keylights.keyboard import Synth
    dlc = tmp_path / "game" / "dlc"
    dlc.mkdir(parents=True)
    (dlc / "s_m.psarc").write_bytes(song_archive("s", {"lead": ("L", Song(levels={0: [N(1.0)]}))}))
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    class CircuitOut(FakeMidiOut):
        def get_ports(self):
            return ["Circuit"]

    circuit = CircuitOut()
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False,
              synth_factory=lambda port: Synth(port, midi_out_factory=lambda: circuit))
    gui.set_settings({"outputs": {"drums": {"port": "Circuit", "channel": 10, "map": "circuit"}}})
    kit = gui.settings["outputs"]["drums"]["kit"]
    assert kit["36"] == [1, None] and kit["51"] == [3, None] and kit["43"] == [4, None]  # (the kit as it starts)
    gui.set_settings({"outputs": {"drums": {"port": "Circuit", "channel": 10, "map": "circuit", "kit": {"43": [4, 21]}}}})
    assert gui.settings["outputs"]["drums"]["kit"]["43"] == [4, 21]
    with pytest.raises(Exception, match="sample"):
        gui.set_settings({"outputs": {"drums": {"port": "Circuit", "channel": 10, "map": "circuit", "kit": {"43": [4, 65]}}}})
    sid = gui.library()["songs"][0]["id"]
    part = gui.drum_part(sid)
    assert part["device"] == "Circuit" and part["notes"]["43"] == 65 and part["kit"]["43"] == [4, 21]
    assert gui.hit_drum({"note": drums.TOMS[0]}) == {"played": 65}
    assert circuit.sent[-2:] == [[0xB9, 50, 20], [0x99, 65, 100]]  # (Drum 4's patch select: sample 21; then it)
    assert part["shared"] == {}  # (none shared)
    casio = {"36": [1, 2], "38": [2, 4], **{n: [0, int(n) - 34] for n in ("42", "46", "49", "51", "43", "47", "50")}}
    gui.set_settings({"outputs": {"drums": {"port": "Circuit", "channel": 10, "map": "circuit", "kit": casio}}})
    assert gui.drum_part(sid)["shared"]["49"] == [3, 4]  # (the crash: on Drum 3 or 4, hit by hit)
    circuit_drums = gui.settings["outputs"]["drums"]
    gui.set_settings({"outputs": {"drums": None}})  # the keyboard's drums, then the Circuit chosen again by itself:
    assert "drums" not in gui.settings["outputs"]
    gui.set_settings({"outputs": {"drums": {"port": "Circuit"}}})
    assert gui.settings["outputs"]["drums"] == circuit_drums  # (its notes, channel and kit as they were)
    gui.set_settings({"outputs": {"drums": {"port": "Other"}}})  # (a device it hasn't had: the defaults)
    assert gui.settings["outputs"]["drums"]["map"] == "gm" and gui.settings["outputs"]["drums"]["channel"] == 10
    gui.close()
    again = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    again.set_settings({"outputs": {"drums": {"port": "Circuit"}}})  # (remembered after a restart too)
    assert again.settings["outputs"]["drums"] == circuit_drums
    again.close()


def test_a_songs_theory_its_chords_as_numbers_its_sections_and_why_its_notes_work(server):
    sid = first_song(server)
    status, th = call(server, f"/api/theory?id={urllib.request.quote(sid)}")
    assert status == 200 and th["key"] is not None and th["part"] == "lead"
    assert [p["part"] for p in th["parts"]] == ["lead", "rhythm"]
    assert th["chords"] and all(len(c) == 6 for c in th["chords"])  # (start, end, name, numeral, what it does, tag)
    assert [s["name"] for s in th["sections"]][:3] == ["Intro 1", "Verse 1", "Chorus 1"]
    assert th["notes"] and {n[4] for n in th["notes"]} <= {"chord", "scale", "outside"}
    assert call(server, f"/api/theory?id={urllib.request.quote(sid)}&part=rhythm")[1]["part"] == "rhythm"


def test_pinch_harmonics_settings_are_kept_checked_and_tried(server, tmp_path, monkeypatch):
    from rs_keylights import gui as gui_module
    _, settings = call(server, "/api/settings", {"pinch": {"width": 1.1, "vibrato": "charted"}})
    assert settings["pinch"]["width"] == 1.1 and settings["pinch"]["vibrato"] == "charted"
    assert settings["pinch"]["up"] == 19  # (the rest as they were)
    assert json.loads((tmp_path / "gui.json").read_text())["pinch"]["width"] == 1.1
    status, said = call(server, "/api/settings", {"pinch": {"scoop": 9}})
    assert status == 400 and "scoop goes from 0 to 2" in said["error"]
    again = Gui(server.gui.index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    assert again._squeal().width == 1.1  # (kept)
    tried = []
    monkeypatch.setattr(gui_module, "_pinch_demo", lambda *args: tried.append(args))
    _, said = call(server, "/api/pinch/try", {"pinch": {"up": 24}})
    time.sleep(0.05)
    assert said == {"playing": True} and tried[0][1].up == 24 and tried[0][1].width == 1.1  # (as given, over the kept)
    assert tried[0][2].number == 79 and tried[0][3].number == 298  # (the lead parts' tones: as they start)
    assert server.gui._squeal().up == 19  # (tried: not kept)


def test_the_songs_own_audio_plays_with_it_where_it_is(server, monkeypatch):
    import numpy as np
    from rs_keylights import gui as gui_module
    gui, sid = server.gui, first_song(server)
    streams = []

    class Stream:
        def __init__(self, **kwargs):
            self.kwargs, self.closed = kwargs, False
            streams.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            self.closed = True

    decoded = []
    monkeypatch.setattr(gui_module, "song_samples",
                        lambda ref, game: decoded.append(ref) or (np.ones((44100 * 30, 2), dtype=np.float32) * 0.1, 44100))
    gui._audio_stream = Stream
    _, options = call(server, "/api/play/options", {"audio": True, "audio_volume": 50})
    assert options["audio"] and options["audio_volume"] == 50 and options["audio_error"] is None
    call(server, "/api/play", {"song": sid, "start": 1.0})
    assert wait_for(lambda: gui._song_audio, bool)
    audio = gui._song_audio
    assert audio.volume == 0.5 and streams[-1].kwargs["samplerate"] == 44100
    assert audio.fill(441).any()  # (playing: where the song is)
    call(server, "/api/pause", {})
    assert not audio.fill(441).any()  # (paused: silence)
    call(server, "/api/play/options", {"audio_volume": 20})
    assert audio.volume == 0.2
    call(server, "/api/play/options", {"audio": False})  # (off as it plays)
    assert gui._song_audio is None and streams[-1].closed
    call(server, "/api/play/options", {"audio": True})
    call(server, "/api/resume", {})
    assert wait_for(lambda: gui._song_audio, bool) and len(decoded) == 1  # (decoded once: kept)
    call(server, "/api/play/options", {"audio_device": "Speakers"})  # (another output: played there at once)
    assert wait_for(lambda: gui._song_audio and streams[-1].kwargs.get("device") == "Speakers", bool)
    monkeypatch.setattr(gui_module, "AUDIO_KEPT_FOR", 0.05)
    call(server, "/api/stop", {})
    assert gui._song_audio is None and streams[-1].closed
    assert wait_for(lambda: gui._audio_kept, lambda kept: kept is None) is None  # (stopped a while: its memory back)
    assert len(decoded) == 1
    assert call(server, "/api/play/options", {"audio_volume": 101})[0] == 400
    assert call(server, "/api/play/options", {"audio_device": 3})[0] == 400
    again = Gui(gui.index, settings_file=gui.settings_file, list_ports=list, other_follow=lambda: False)
    assert not again.play_options["audio"] and again.play_options["audio_device"] == "Speakers"  # (off as it starts)


def test_a_songs_parts_are_split_and_each_plays_at_a_volume_of_its_own(server, monkeypatch):
    import numpy as np
    from rs_keylights import gui as gui_module, stems
    gui, sid = server.gui, first_song(server)
    song = call(server, f"/api/song?id={urllib.request.quote(sid)}")[1]
    assert song["stems"] == {"made": False, "made_by": [], "by": None, "separator": "roformer", "making": False,
                             "making_by": None, "progress": None, "waiting": False, "error": None, "can": False,
                             "can_by": {"roformer": False, "demucs": False}, "names": list(stems.STEMS),
                             "separators": [{"id": "roformer", "name": "BS-RoFormer SW"},
                                            {"id": "demucs", "name": "Demucs"}], "server": None,
                             "chart_offset": None, "sides": None}
    made, loaded = [], []  # (the separators it's split by; whose parts are read)
    monkeypatch.setattr(stems, "make", lambda ref, game, progress, separator, server=None, kit_out=None, melody=False:
                        made.append(separator) or progress(1.0) or [])
    monkeypatch.setattr(stems, "made", lambda ref, cache=None, separator="demucs": separator in made)
    mix = (np.ones((44100 * 20, 2)) * 1000).astype(np.int16)
    monkeypatch.setattr(stems, "load", lambda ref, samples, cache=None, separator="demucs", parts=stems.SEPARATED:
                        loaded.append(separator) or {s: samples // 6 for s in parts} if separator in made else None)
    monkeypatch.setattr(gui_module, "song_samples", lambda ref, game: (mix, 44100))

    class Stream:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    gui._audio_stream = Stream
    call(server, "/api/play/options", {"audio": True})
    call(server, "/api/play", {"song": sid, "start": 1.0})
    assert wait_for(lambda: gui._song_audio, bool) and "song" in gui._song_audio.weights  # (whole: not split yet)
    info = call(server, "/api/stems", {"song": sid, "make": True, "separator": "demucs"})[1]
    assert wait_for(lambda: made and gui._song_audio and "drums" in gui._song_audio.weights, bool)  # (split: at once)
    shown = call(server, f"/api/song?id={urllib.request.quote(sid)}")[1]["stems"]
    assert shown["made"] and shown["made_by"] == ["demucs"] and shown["by"] == "demucs"  # (BS-RoFormer's not made:
    # Demucs' play)
    assert made == ["demucs"] and loaded[-1] == "demucs"
    call(server, "/api/stems", {"song": sid, "make": True})  # (by the one chosen: BS-RoFormer SW)
    assert wait_for(lambda: loaded and loaded[-1] == "roformer", bool)  # (its parts play at once)
    shown = call(server, f"/api/song?id={urllib.request.quote(sid)}")[1]["stems"]
    assert shown["made_by"] == ["roformer", "demucs"] and shown["by"] == "roformer"
    call(server, "/api/play/options", {"separator": "demucs"})  # (the other chosen: its parts play)
    assert wait_for(lambda: loaded[-1] == "demucs", bool)
    assert call(server, "/api/play/options", {"separator": "spleeter"})[0] == 400
    assert call(server, "/api/stems", {"song": sid, "make": True, "separator": "spleeter"})[0] == 400
    assert wait_for(lambda: gui._song_audio and "drums" in gui._song_audio.weights, bool)
    audio = gui._song_audio

    def level(stem):  # (a part as it's heard: the song mixed at other's level, each part at its own less that)
        return round(audio.weights["song"] + (audio.weights[stem] if stem != "other" else 0.0), 6)

    _, options = call(server, "/api/play/options", {"stem_volumes": {"drums": 0, "vocals": 40}})
    assert options["stem_volumes"]["drums"] == 0 and options["stem_volumes"]["bass"] == 100
    assert level("drums") == 0.0 and level("vocals") == 0.4 and level("other") == 1.0
    _, options = call(server, "/api/play/options", {"stem_mutes": {"vocals": True}})  # (muted: its volume kept)
    assert options["stem_mutes"]["vocals"] and options["stem_volumes"]["vocals"] == 40 and level("vocals") == 0.0
    remote = gui.remote_info()["audio"]  # (the Launchkey's stem mixer: what it shows)
    assert remote["on"] and remote["made"] and remote["volumes"]["drums"] == 0 and remote["mutes"]["vocals"]
    call(server, "/api/play/options", {"stem_mutes": {"vocals": False}})
    assert level("vocals") == 0.4
    assert call(server, "/api/play/options", {"stem_mutes": {"kazoo": True}})[0] == 400
    assert call(server, "/api/play/options", {"stem_volumes": {"kazoo": 10}})[0] == 400
    assert call(server, "/api/play/options", {"stem_volumes": {"bass": 120}})[0] == 400
    call(server, "/api/stop", {})
    assert info["making"] or made


def test_shared_circuit_drums_go_on_the_drums_none_is_on_with_their_own_samples():
    from rs_keylights.gui import _drum_voicing
    kit = {"36": [1, 2], "38": [2, 4], "42": [0, 8], "46": [0, 12], "49": [0, 15], "51": [0, None], "43": [0, 9],
           "47": [0, 13], "50": [0, 16]}
    voicing = _drum_voicing({"map": "circuit", "kit": kit})
    assert voicing["pool"] == {64: 44, 65: 50} and voicing["shared"][42] == 7 and 51 not in voicing["shared"]
    assert voicing["notes"][36] == 60 and voicing["patches"][36] == (8, 1) and voicing["notes"][51] is None
    # (the ride: no sample, not played)
    kept = _drum_voicing({"map": "circuit", "kit": {**kit, "43": [4, None]}})
    assert kept["pool"] == {64: 44} and kept["notes"][43] == 65  # (a tom kept on Drum 4: the rest share 3)


def test_the_games_song_parts_are_set_in_its_mods_settings_muted_and_back_where_they_were(tmp_path):
    (tmp_path / "game").mkdir()
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    assert gui.game_stems_info() is None
    with pytest.raises(GuiError, match="rearmed-mods"):
        gui.set_game_stems({"part": "guitar", "level": 50})
    ini = tmp_path / "game" / "RSMods.ini"
    ini.write_text("[Toggle Switches]\nSongStems = off\n\n[Mod Settings]\nSongStemsGuitarLevel = 100\n")
    with pytest.raises(GuiError, match="Song Stems is off"):
        gui.set_game_stems({"part": "guitar", "level": 50})
    ini.write_text(ini.read_text().replace("= off", "= on"))
    assert gui.set_game_stems({"part": "guitar", "level": 70}) == {"on": True, "guitar": 70, "bass": 100,
                                                                   "muted": {"guitar": False, "bass": False}}
    assert gui.set_game_stems({"part": "guitar", "step": -5})["guitar"] == 65
    muted = gui.set_game_stems({"part": "guitar", "mute": True})  # (its level kept here, 0 in the file)
    assert muted["guitar"] == 65 and muted["muted"]["guitar"] and "SongStemsGuitarLevel = 0" in ini.read_text()
    assert gui.set_game_stems({"part": "guitar", "level": 40})["muted"]["guitar"]  # (moved, muted: kept, still 0)
    assert "SongStemsGuitarLevel = 0" in ini.read_text()
    back = gui.set_game_stems({"part": "guitar", "mute": False})
    assert back["guitar"] == 40 and not back["muted"]["guitar"] and "SongStemsGuitarLevel = 40" in ini.read_text()
    gui.set_game_stems({"part": "guitar", "mute": True})
    ini.write_text(ini.read_text().replace("SongStemsGuitarLevel = 0", "SongStemsGuitarLevel = 30"))  # (the game's
    assert gui.game_stems_info()["guitar"] == 30 and not gui.game_stems_info()["muted"]["guitar"]   # own key: back)
    with pytest.raises(GuiError, match="guitar or bass"):
        gui.set_game_stems({"part": "drums", "level": 0})


def test_songs_are_split_one_at_a_time_the_others_waiting_their_turn(server, monkeypatch):
    from rs_keylights import stems
    gui = server.gui
    (gui.index.game_dir / "dlc" / "other_m.psarc").write_bytes(
        song_archive("other", {"lead": ("O", Song(levels={0: [N(1.0)]}))}, song="Another", artist="Band"))
    gui.reindex()
    first, second = [s["id"] for s in call(server, "/api/library")[1]["songs"]][:2]
    go, started = threading.Event(), []

    def make(ref, game, progress, separator, server=None, kit_out=None, melody=False):  # (a split taking a while: until it's let
        started.append(ref)                                                # go)
        assert go.wait(5)
        return []

    monkeypatch.setattr(stems, "make", make)
    gui.set_stems({"song": first, "make": True})
    assert wait_for(lambda: len(started), bool) == 1
    info = gui.set_stems({"song": second, "make": True})
    assert info["making"] and info["waiting"] and len(started) == 1  # (its turn once the first's done)
    assert gui.set_stems({"song": second, "make": True})["waiting"] and len(gui._split_queue) == 1  # (once only)
    go.set()
    assert wait_for(lambda: len(started) == 2 and not gui.stems_info(second)["making"], bool)
    assert not gui._split_running


def test_a_warm_up_clicks_on_the_keyboard_its_speeds_are_kept_and_a_song_played_stops_it(server, monkeypatch):
    from rs_keylights import warmup
    gui, sid = server.gui, first_song(server)
    preview = call(server, f"/api/warmup?id={urllib.request.quote(sid)}")[1]
    assert preview["now"] is None and len(preview["drills"]) == 6 and preview["song"]["id"] == sid
    assert preview["drills"][4]["key"] == "scale" and preview["drills"][4]["name"].endswith("pentatonic")
    assert all(d["speed"] == warmup.SLOWEST and d["per"] == 1 for d in preview["drills"])  # (the slowest, at first)
    assert preview["shown"] and 240 < preview["seconds"] < 600  # (at the slowest, each pass played through)
    assert call(server, "/api/warmup")[1]["drills"][4]["name"] == "A minor pentatonic"  # (no song: A minor)
    monkeypatch.setattr(warmup, "READY_SECONDS", 0.0)  # (shown at once)
    _, started = call(server, "/api/warmup", {"start": True, "song": sid})
    assert started["now"]["drill"] == 0 and started["song"]["id"] == sid
    state = call(server, "/api/state")[1]["warmup"]
    assert state["run"] == 1 and state["phase"] in ("ready", "show") and not state["paused"]

    def sent(status):
        return [m for out in server.outs for m in out.sent if m[0] == status and m[2] > 0]

    assert wait_for(lambda: sent(0x99) and sent(0x9A), bool)  # (a click, and its first note on the guitar)
    assert sent(0x99)[0][1] == warmup.ACCENT and sent(0x9A)[0][1] == 45
    call(server, "/api/warmup", {"your_turn": True})
    assert call(server, "/api/state")[1]["warmup"]["phase"] == "count"
    _, faster = call(server, "/api/warmup", {"step": 1})
    assert faster["now"]["speed"] == 44 and gui.settings["warmup_speeds"] == {"chromatic": 44}
    assert json.loads(gui.settings_file.read_text())["warmup_speeds"] == {"chromatic": 44}  # (kept)
    assert call(server, "/api/warmup", {"step": 5})[0] == 400
    call(server, "/api/warmup", {"go": 4})
    assert call(server, "/api/state")[1]["warmup"]["drill"] == 4
    assert call(server, "/api/warmup", {"go": 6})[0] == 400
    call(server, "/api/warmup", {"pause": True})
    assert call(server, "/api/state")[1]["warmup"]["paused"]
    _, off = call(server, "/api/warmup", {"shown": False, "ladder": False})
    assert not off["shown"] and not gui.settings["warmup_shown"] and not gui._warmup.shown
    assert not off["ladder"] and not gui.settings["warmup_ladder"] and not gui._warmup.ladder
    hearing = FakeHearing.made[-1]
    assert call(server, "/api/state")[1]["warmup"]["hearing"] == {"on": True, "error": None, "level": 0.0}
    assert hearing.device == gui.settings["listen_device"] and hearing.expected() == ()
    call(server, "/api/play", {"song": sid})  # (a song played: the warm-up stops, and its listening)
    assert call(server, "/api/state")[1]["warmup"] is None and hearing.stopped
    call(server, "/api/stop", {})
    again = Gui(gui.index, settings_file=gui.settings_file, list_ports=list, other_follow=lambda: False)
    assert again.settings["warmup_speeds"] == {"chromatic": 44} and not again.settings["warmup_shown"]
    assert not again.settings["warmup_ladder"]
    assert again.warmup_info()["drills"][0]["speed"] == 44 and not again.warmup_info()["shown"]
    _, started = call(server, "/api/warmup", {"start": True})
    assert started["now"]["speed"] == 44 and call(server, "/api/state")[1]["warmup"]["run"] == 2
    call(server, "/api/warmup", {"stop": True})
    assert call(server, "/api/state")[1]["warmup"] is None


def test_warm_up_speeds_kept_wrong_are_left_out():
    from rs_keylights.gui import _warmup_speeds
    assert _warmup_speeds({"chromatic": 80, "spider": "fast", "kazoo": 90, "legato": 5, "scale": True,
                           "gallops": 81, "skipping": 600}) == {"chromatic": 80}
    assert _warmup_speeds([1, 2]) == {}


def test_a_warm_up_hears_your_playing_and_the_ladder_keeps_the_speed_it_changes(server, monkeypatch):
    from rs_keylights import warmup
    gui = server.gui
    monkeypatch.setattr(warmup, "READY_SECONDS", 0.0)
    gui.settings["warmup_shown"] = False
    gui.settings["warmup_speeds"] = gui._kept["warmup_speeds"] = {"chromatic": 952}  # (fast: a round soon)
    call(server, "/api/warmup", {"start": True})
    hearing, session = FakeHearing.made[-1], gui._warmup

    def play_along():
        played = set()
        while not session.done and gui._warmup is session and session.speed == 952:
            for due in list(session._due):
                if id(due) not in played:
                    played.add(id(due))
                    hearing.heard(due.time + warmup.CLICK_HEARD, due.pitch)
            time.sleep(0.005)

    Thread(target=play_along, daemon=True).start()
    assert wait_for(lambda: gui.settings["warmup_speeds"].get("chromatic"), lambda s: s == 960, 8.0)
    state = call(server, "/api/state")[1]["warmup"]
    assert state["round"]["verdict"] == "faster" and state["marks"] and state["marks"][0][2] == "ok"
    assert wait_for(lambda: json.loads(gui.settings_file.read_text()).get("warmup_speeds"), bool)["chromatic"] == 960
    assert call(server, "/api/state")[1]["warmup"]["name"] == "Chromatic 1-2-3-4"
    assert gui.remote_info()["warmup"]["speed"] == 960  # (the Launchkey's too)
    call(server, "/api/warmup", {"stop": True})
    logged = [json.loads(line) for line in (gui.settings_file.parent / "warmups.jsonl").read_text().splitlines()]
    assert len(logged) == 1 and logged[0]["drill"] == "chromatic" and logged[0]["tuning"] == "E standard"
    assert logged[0]["start"] == 952 and logged[0]["speed"] == 960 and logged[0]["notes"] >= 32
    assert logged[0]["ok"] == logged[0]["notes"] and logged[0]["seconds"] > 1 and logged[0]["at"][:4] == "2026"
    progress = call(server, "/api/warmup/progress")[1]
    assert progress["streak"] == 1 and progress["rows"][0]["speed"] == 960 and progress["rows"][0]["first"] == 952


def test_one_drill_or_a_scale_of_your_own_in_a_tuning_of_your_own_is_practised_on_its_own(server):
    from rs_keylights import warmup
    gui = server.gui
    preview = call(server, "/api/warmup")[1]
    assert preview["what"] == "routine" and preview["tuning"] == "E standard" and len(preview["drills"]) == 6
    assert "B standard" in preview["choices"]["tunings"] and "natural minor" in preview["choices"]["scales"]
    assert preview["choices"]["what"][0][0] == "routine" and len(preview["choices"]["what"]) == 7
    _, chosen = call(server, "/api/warmup", {"what": "scale", "tuning": "B standard",
                                             "scale": {"kind": "natural minor", "tonic": 11, "start": None}})
    assert [d["name"] for d in chosen["drills"]] == ["B natural minor"] and chosen["seconds"] is None
    assert chosen["drills"][0]["strings"] == [35, 40, 45, 50, 54, 59]
    kept = json.loads(gui.settings_file.read_text())
    assert kept["warmup_what"] == "scale" and kept["warmup_tuning"] == "B standard"
    assert kept["warmup_scale"] == {"kind": "natural minor", "tonic": 11, "start": None}
    for wrong in ({"what": "kazoo"}, {"tuning": "Q standard"}, {"scale": {"kind": "bebop"}},
                  {"scale": {"kind": "major", "tonic": 12}}, {"scale": {"kind": "major", "start": 30}}):
        assert call(server, "/api/warmup", wrong)[0] == 400, wrong
    _, started = call(server, "/api/warmup", {"start": True})
    hearing = FakeHearing.made[-1]
    assert started["now"]["until_stopped"] and started["now"]["left"] is None and started["now"]["drills"] == 1
    assert 50 < hearing.lowest < 61.7  # (B standard's low B, 61.7 Hz, listened for)
    call(server, "/api/warmup", {"stop": True})
    again = Gui(gui.index, settings_file=gui.settings_file, list_ports=list, other_follow=lambda: False)
    assert again.settings["warmup_what"] == "scale" and again.warmup_info()["drills"][0]["name"] == "B natural minor"
    call(server, "/api/warmup", {"what": "routine", "tuning": "E standard", "scale": {"kind": "pentatonic"}})
    assert len(call(server, "/api/warmup")[1]["drills"]) == 6


def test_the_progress_log_is_each_drills_speed_a_day_and_the_days_in_a_row(server):
    from datetime import date, timedelta
    gui = server.gui
    today = date.today()

    def at(days_ago, hour=9):
        return f"{(today - timedelta(days=days_ago)).isoformat()}T{hour:02d}:00:00"

    lines = [{"at": at(40), "drill": "spider", "name": "Spider (1-3, 2-4)", "tuning": "E standard", "start": 40,
              "speed": 40, "notes": 10, "ok": 5, "seconds": 30},  # (too long ago to show)
             {"at": at(2), "drill": "chromatic", "name": "Chromatic 1-2-3-4", "tuning": "E standard", "start": 40,
              "speed": 48, "notes": 20, "ok": 15, "seconds": 60},
             {"at": at(2, 18), "drill": "chromatic", "name": "Chromatic 1-2-3-4", "tuning": "E standard", "start": 48,
              "speed": 44, "notes": 20, "ok": 5, "seconds": 60},  # (later that day, slower: the day's fastest kept)
             {"at": at(1), "drill": "chromatic", "name": "Chromatic 1-2-3-4", "tuning": "B standard", "start": 48,
              "speed": 56, "notes": 0, "ok": 0, "seconds": 90},
             {"at": at(0), "drill": "scale", "name": "B natural minor", "tuning": "B standard", "start": 40,
              "speed": 52, "notes": 30, "ok": 27, "seconds": 120},
             {"at": at(0), "drill": "scale", "name": "A minor pentatonic", "tuning": "E standard", "start": 40,
              "speed": 40, "notes": 0, "ok": 0, "seconds": 30}]
    (gui.settings_file.parent / "warmups.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\nnot json\n{}\n")
    progress = call(server, "/api/warmup/progress")[1]
    assert progress["streak"] == 3 and progress["today_minutes"] == 2.5 and progress["today"] == today.isoformat()
    rows = {row["key"]: row for row in progress["rows"]}
    assert list(rows) == ["chromatic", "scale|A minor pentatonic|E standard", "scale|B natural minor|B standard"]
    chromatic = rows["chromatic"]  # (any tuning: the same frets)
    assert chromatic["points"] == [[(today - timedelta(days=2)).isoformat(), 48, 50],
                                   [(today - timedelta(days=1)).isoformat(), 56, None]]
    assert chromatic["speed"] == 56 and chromatic["first"] == 40 and chromatic["tuning"] is None  # (from its start)
    assert rows["scale|B natural minor|B standard"]["points"][0][1:] == [52, 90]
    assert rows["scale|B natural minor|B standard"]["tuning"] == "B standard"


def test_a_part_or_the_drum_part_muted_as_a_song_plays_goes_at_once_and_the_rest_play_on(server):
    from rs_keylights import drums
    gui, sid = server.gui, first_song(server)
    drums.keep(gui._load(sid)[0][0], [drums.Hit(2.0, drums.KICK, 110)])  # (made, not played)
    call(server, "/api/play", {"song": sid, "start": 1.0})
    player = gui._session.player
    assert player.muted == {"drums"} and "drums" in player.names  # (there, to come in at once)
    out = server.outs[-1]
    sent = len(out.sent)
    gui.change_part("rhythm", {"play": False})  # (the Launchkey's pad)
    assert gui._session.player is player and player.muted == {"rhythm", "drums"}
    assert not [m for m in out.sent[sent:] if m[0] & 0xF0 == 0xB0 and m[1] == 123]  # (no notes off everywhere)
    assert [p["part"] for p in gui._playing["parts"]] == ["lead"]
    call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"play": True}}})  # (the page's)
    assert gui._session.player is player and player.muted == {"drums"}
    gui.change_part("drums", {"play": True})
    assert gui._session.player is player and player.muted == frozenset()
    gui.change_part("lead", {"tone": 30})  # (anything else that changes its sound: played again with it)
    assert wait_for(lambda: gui._session.player is not player, bool)
    call(server, "/api/stop", {})


def test_following_a_part_muted_and_back_goes_at_once_in_the_player_playing(server):
    server.overlay.state = {"memoryReadout": {"songTimer": 5.0, "songID": "song", "arrangementID": "L"},
                            "currentState": 4}
    call(server, "/api/follow/settings", {"sound": True, "parts": {"L": {"R": {"play": True}}}})
    call(server, "/api/follow", {})
    gui = server.gui
    follower = wait_for(lambda: gui._session.follower, lambda f: f._player is not None and len(f.planned) == 2)
    player = follower._player
    call(server, "/api/follow/settings", {"parts": {"L": {"R": {"play": False}}}})  # (muted: there still)
    assert follower._player is player and player.muted == {"rhythm"}
    assert [p["name"] for p in call(server, "/api/state")[1]["follow"]["parts"]] == ["Lead"]
    call(server, "/api/follow/settings", {"parts": {"L": {"R": {"play": True}}}})  # (back, at once)
    assert follower._player is player and player.muted == frozenset()
    call(server, "/api/stop", {})


def test_a_key_clicked_on_the_page_plays_as_the_launchkeys_and_the_keys_held_are_told_as_they_change(server):
    gui = server.gui
    first = call(server, "/api/keys/held?since=-1")[1]  # (at once: its version not the one asked for)
    assert first["keys"] == []
    answered = []
    asking = threading.Thread(target=lambda: answered.append(call(server, f"/api/keys/held?since={first['version']}")[1]))
    asking.start()  # (waits for a change)
    time.sleep(0.2)
    assert not answered
    assert call(server, "/api/keys/press", {"key": 60, "down": True})[1] == {"held": [60]}
    asking.join(timeout=5)
    assert answered and answered[0]["keys"] == [60]
    gui._keys_queue.join()
    assert [0x90, 60, 100] in server.outs[-1].sent  # (on the keys' channel, as the Launchkey's)
    gui.play_keys([0x95, 64, 80])  # (the Launchkey's own: held too)
    assert gui.held_keys(wait=0)["keys"] == [60, 64]
    gui.play_keys([0xB5, 64, 127])  # (its sustain pedal: not a key)
    assert gui.held_keys(wait=0)["keys"] == [60, 64]
    call(server, "/api/keys/press", {"key": 60, "down": False})
    gui._keys_queue.join()
    assert [0x80, 60, 0] in server.outs[-1].sent and gui.held_keys(wait=0)["keys"] == [64]
    gui.release_keys()  # (the Launchkey gone: none held)
    assert gui.held_keys(wait=0)["keys"] == []
    assert call(server, "/api/keys/press", {"key": 200})[0] == 400


def test_a_fretboards_note_sounds_in_its_arrangements_tone_though_the_keys_own_sound_is_off(server):
    from rs_keylights.tones import tone
    gui = server.gui
    gui.settings["keys_sound"] = False  # (the Launchkey's keys silent: a fretboard's note sounds anyway)
    guitar = tone(292)
    assert call(server, "/api/keys/press", {"key": 52, "down": True, "tone": 292})[0] == 200
    gui._keys_queue.join()
    sent = server.outs[-1].sent
    assert [0xC0, guitar.program] in sent and [0x90, 52, 100] in sent  # (its tone on the keys' channel, then it)
    call(server, "/api/keys/press", {"key": 52, "down": False, "tone": 292})
    gui._keys_queue.join()
    assert [0x80, 52, 0] in server.outs[-1].sent
    assert call(server, "/api/keys/press", {"key": 52, "tone": 99999})[0] == 400


def test_following_a_split_song_its_parts_play_in_the_games_song_at_the_stem_mixers_levels(server, monkeypatch, tmp_path):
    import struct
    import wave as wave_module

    import numpy as np
    from rs_keylights import drums, gameparts, stems
    gui, game, sid = server.gui, server.gui._riff_loop, first_song(server)
    monkeypatch.setattr(gameparts, "folder", lambda: tmp_path / "game-parts")
    monkeypatch.setattr(stems, "made", lambda ref, cache=None, separator="demucs": separator == "roformer")
    fmt = struct.pack("<HHIIHH", 0xFFFF, 2, 48000, 0, 0, 0)
    wem = b"RIFF" + struct.pack("<I", 0) + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    monkeypatch.setattr(drums, "song_audio_file", lambda ref, game_dir: (4242, wem))
    decoded = []

    def decode(ref, work, cache=None, separator="demucs", parts=stems.SEPARATED):
        decoded.append(separator)
        out = {}
        for k, stem in enumerate(parts):
            out[stem] = work / f"{stem}.wav"
            with wave_module.open(str(out[stem]), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(48000)
                w.writeframes((np.ones((4800, 2)) * (k + 1) * 1000).astype(np.int16).tobytes())
        return out
    monkeypatch.setattr(stems, "decode", decode)
    ini = gui.index.game_dir / "RSMods.ini"
    ini.write_text("[Toggle Switches]\nSongStems = on\n\n[Mod Settings]\nSongStemsGuitarLevel = 0\nSongStemsBassLevel = 60\n")
    followed = {"id": sid, "arrangements": [
        {"id": "L", "name": "Lead", "kind": "lead", "play": True, "playing": True, "lights": 1, "tones": {}}]}
    gui._game_parts_follow(followed, game.status())  # (the song started: made ready, in the background)
    assert wait_for(lambda: gui._game_parts, bool)["names"] == list(stems.SEPARATED) and decoded == ["roformer"]
    path = gui._game_parts["path"]
    assert ("kparts", path) in game.sent and game.gains == (1.0, 0.0, 0.0, 0.0, 0.0, 0.0)  # (as it is, all up)
    assert gameparts.header(tmp_path / "game-parts" / path.rsplit("/", 1)[-1])["file_id"] == 4242
    assert "SongStemsGuitarLevel = 100" in ini.read_text() and "SongStemsBassLevel = 100" in ini.read_text()  # (aside)
    assert gui._game_parts_now(followed) == {"on": True, "names": list(stems.SEPARATED), "by": "roformer"}
    gui.set_play_options({"stem_mutes": {"drums": True}, "stem_volumes": {"guitar": 50}})  # (the stem mixer: at once)
    assert game.gains == (1.0, -1.0, 0.0, -0.5, 0.0, 0.0)
    gui._game_mixer(followed, game.status())  # (the game's STEM MIXER: the song's parts, as the Launchkey's knobs)
    rows = {r[0]: r for r in game.kmix_rows}
    assert rows["pdrums"] == ("pdrums", 100.0, True, "DRUMS", -1, "stem:drums", 8)
    assert rows["ppiano"][3:] == ("KEYS", -1, "stem:keys", 12) and rows["pother"][6] == 13 and "gguitar" not in rows
    game.kmix_moved = {"pvocals": "mute", "pbass": 30.0}  # (there: a pad pressed, a fader moved)
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert gui.play_options["stem_mutes"]["vocals"] and gui.play_options["stem_volumes"]["bass"] == 30
    game.kmix_moved = {"smaster": "mute"}  # (MASTER's pad: all of them muted)
    with gui._game_mixer_lock:
        gui._game_mixer_apply()
    assert all(gui.play_options["stem_mutes"][s] for s in stems.STEMS) and game.gains[0] == 0.0
    game.parts_id = 0  # (the mod loaded again: lost them; handed to it again)
    gui._game_parts_sent = -math.inf
    sent = len(game.sent)
    gui._game_parts_follow(followed, game.status())
    assert ("kparts", path) in game.sent[sent:]
    again = len(decoded)
    gui._game_parts_follow(None, None)  # (following stopped: none, and Song Stems' levels back)
    assert wait_for(lambda: ("kparts", None) in game.sent, bool)
    assert wait_for(lambda: "SongStemsGuitarLevel = 0" in ini.read_text(), bool) and gui._game_parts is None
    gui._game_parts_follow(followed, game.status())  # (played again: made before, there at once)
    assert wait_for(lambda: gui._game_parts, bool) and len(decoded) == again


def test_a_bs_roformer_split_makes_the_drum_part_again_from_its_kits_pieces(server, monkeypatch):
    from rs_keylights import stems
    gui, sid = server.gui, first_song(server)

    def make(ref, game, progress, separator, server=None, kit_out=None, melody=False):  # (a split server with DrumSep)
        for piece in stems.KIT:
            (kit_out / f"{piece}.wav").write_bytes(b"x")
        return list(stems.KIT)
    monkeypatch.setattr(stems, "make", make)
    asked = []
    monkeypatch.setattr(gui, "_make_drums", lambda sid, loaded, pieces=None, kit=None: asked.append((sid, pieces, kit)))
    gui.set_stems({"song": sid, "make": True, "separator": "roformer"})
    sid_made, pieces, kit = wait_for(lambda: asked, bool)[0]
    assert sid_made == sid and set(pieces) == set(stems.KIT) and all(p.parent == kit and p.exists() for p in pieces.values())
    monkeypatch.setattr(stems, "make", lambda ref, game, progress, separator, server=None, kit_out=None, melody=False: [])
    gui.set_stems({"song": sid, "make": True, "separator": "demucs"})  # (Demucs': the quick one, no drum part)
    assert wait_for(lambda: not gui.stems_info(sid)["making"], bool) and len(asked) == 1


def test_a_parts_lit_keys_turned_on_or_off_as_a_song_plays_change_the_keys_alone(server):
    gui, sid = server.gui, first_song(server)
    call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"lights": 1}, "rhythm": {"lights": 0}}})
    call(server, "/api/play", {"song": sid, "start": 1.0})
    player = gui._session.player
    out = server.outs[-1]
    wait_for(lambda: [m for m in out.sent if m[0] & 0xF0 == 0x90 and m[2] > 0], bool)
    sent = len(out.sent)
    assert [lit for _lights, lit, _rank in player._shown[:2]] == [True, False]
    gui.change_part("rhythm", {"lights": 1})  # (the Launchkey's pad below it: lit too, at once)
    assert gui._session.player is player and [lit for _l, lit, _r in player._shown[:2]] == [True, True]
    gui.change_part("lead", {"lights": 0})  # (and the lead's off)
    assert gui._session.player is player and [lit for _l, lit, _r in player._shown[:2]] == [False, True]
    assert not [m for m in out.sent[sent:] if m[0] & 0xF0 == 0xB0 and m[1] == 123]  # (nothing stopped everywhere)
    assert [p["lights"] for p in gui.song(sid)["parts"]] == [0, 1]
    call(server, "/api/stop", {})


def test_following_a_parts_lit_keys_changed_go_at_once_in_the_player_playing(server):
    server.overlay.state = {"memoryReadout": {"songTimer": 5.0, "songID": "song", "arrangementID": "L"},
                            "currentState": 4}
    call(server, "/api/follow/settings", {"sound": True, "parts": {"L": {"R": {"play": True}}}})
    call(server, "/api/follow", {})
    follower = wait_for(lambda: server.gui._session.follower, lambda f: f._player is not None and len(f.planned) == 2)
    player = follower._player
    call(server, "/api/follow/settings", {"parts": {"L": {"R": {"lights": 2}}}})  # (the rhythm lit too: at once)
    assert follower._player is player and [lit for _l, lit, _r in player._shown[:2]] == [True, True]
    assert [p.part.lights for p in follower.planned] == [4, 2]
    call(server, "/api/stop", {})


def test_stop_goes_back_to_the_songs_start_and_the_page_hears_how_far_things_being_made_are(server):
    from rs_keylights.tones import DRUM_SETS
    gui, sid = server.gui, first_song(server)
    call(server, "/api/play/options", {"song": sid})
    call(server, "/api/song/settings", {"song": sid, "start": 30})  # (a start set before)
    call(server, "/api/play", {"song": sid})
    assert gui.remote_stop() == "stopped" and gui.song(sid)["start"] == 0  # (back to the start: plays from there)
    call(server, "/api/song/settings", {"song": sid, "start": 12})
    assert gui.remote_stop() == "back to the start" and gui.song(sid)["start"] == 0  # (stopped already: just that)
    gui._splitting[sid] = {"making": True, "progress": 0.42}
    gui._making[sid] = {"step": "transcribing them", "progress": 0.5}
    assert call(server, "/api/state")[1]["making"] == {"stems": {sid: 0.42}, "drums": {sid: 0.5}, "melody": {}}
    gui._splitting.clear()
    gui._making.clear()
    version = gui.play_version
    kit = next(program for _name, program in DRUM_SETS if program != gui.settings["drum_set"])
    gui.set_drums({"song": sid, "kit": kit})  # (the Launchkey's knob: every page shows it)
    assert gui.play_version > version and gui.drums_info(sid)["kit"] == kit


def test_a_drum_part_made_again_as_its_song_plays_is_played_with_its_new_hits(server):
    from rs_keylights import drums
    gui, sid = server.gui, first_song(server)
    ref = gui._load(sid)[0][0]
    drums.keep(ref, [drums.Hit(2.0, drums.KICK, 110)])
    gui.set_drums({"song": sid, "play": True})
    call(server, "/api/play", {"song": sid, "start": 1.0})
    player = gui._session.player
    assert gui._mute_now(sid) and gui._session.player is player  # (the same drum part: muted or not, at once)
    drums.keep(ref, [drums.Hit(2.0, drums.KICK, 110), drums.Hit(2.5, drums.SNARE, 90)])  # (made again)
    gui._drums_on(sid)
    assert gui._session.player is not player  # (played again with its new hits, not the old ones)
    assert sum(len(t.lights) for t, name in zip(gui._session.player.tracks, gui._session.player.names)
               if name == "drums") == 2
    call(server, "/api/stop", {})


def test_a_part_muted_that_lights_its_keys_lights_them_still_its_sound_alone_gone(server):
    gui, sid = server.gui, first_song(server)
    call(server, "/api/song/settings", {"song": sid, "parts": {"lead": {"lights": 1}, "rhythm": {"lights": 1}}})
    call(server, "/api/play", {"song": sid, "start": 1.0})
    player = gui._session.player
    gui.change_part("rhythm", {"play": False})  # (its stem heard instead, say: its keys lit still)
    assert gui._session.player is player and "rhythm" in player.muted and player.names[:2] == ("lead", "rhythm")
    assert [lit for _l, lit, _r in player._shown[:2]] == [True, True]  # (both lit)
    call(server, "/api/stop", {})


def test_the_guitar_is_heard_on_one_of_an_interfaces_inputs(server, monkeypatch):
    from rs_keylights import gui as gui_module
    gui = server.gui
    monkeypatch.setattr(gui_module, "_audio_input_channels", lambda: {"Blender": 12, "Mic": 1})
    started = []

    class FakeListener:
        def __init__(self, heard, expected, device, lowest, channel=1):
            self.device, self.channel, self.now, self.level, self.error, self.running = device, channel, (), 0.0, None, False
            started.append(self)

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

    monkeypatch.setattr(gui_module, "Listener", FakeListener)
    call(server, "/api/settings", {"listen": True, "listen_device": "Blender", "listen_channel": 7})
    assert (started[-1].device, started[-1].channel) == ("Blender", 7) and gui.listening()["channel"] == 7
    assert call(server, "/api/settings", {"listen_channel": 13})[0] == 400  # (it has 12)
    call(server, "/api/settings", {"listen_device": "Mic"})  # (another device: its first input)
    assert (started[-1].device, started[-1].channel) == ("Mic", 1)
    assert call(server, "/api/settings")[1]["input_channels"]["Blender"] == 12


def test_a_songs_vocal_melody_once_made_is_its_vocals_part_off_till_its_turned_on(server, monkeypatch, tmp_path):
    from rs_keylights import gui as gui_module, melody
    gui = server.gui
    monkeypatch.setattr(melody, "cache_dir", lambda: tmp_path / "melody")
    (gui.index.game_dir / "dlc" / "sung_m.psarc").write_bytes(song_archive(
        "sung", {"lead": ("SL", Song(levels={0: [N(1.0)]})), "vocals": ("SV", Song(vocals=4))},
        song="Sung", artist="Band"))
    gui.reindex()
    sid = next(s["id"] for s in call(server, "/api/library")[1]["songs"] if s["song"] == "Sung")
    assert [p["part"] for p in gui.song(sid)["parts"]] == ["lead"]  # (no melody yet: its vocals, just lyrics)
    assert gui.song(sid)["melody"] == {"made": False, "notes": 0, "making": None, "progress": None, "error": None,
                                       "can": False, "beta": False}
    status, said = call(server, "/api/melody", {"song": sid, "make": True})  # (a beta feature, off: not made)
    assert status == 400 and "beta" in said["error"]
    call(server, "/api/settings", {"beta": {"melody": True}})
    info = call(server, "/api/melody", {"song": sid, "make": True})[1]  # (no split server: why not, said)
    assert wait_for(lambda: gui.melody_info(sid), lambda m: m["error"] is not None)["error"].startswith(
        "A vocal melody needs a split server")
    vocals = next(r for r in gui.index.arrangements() if r.song_key == "sung" and r.part == "vocals")
    melody.keep(vocals, [(1.0, 1.4, 64), (1.5, 2.0, 67)])
    gui._melody_made(sid)
    parts = gui.song(sid)["parts"]
    assert [(p["part"], p["kind"], p["play"], p["lights"]) for p in parts] == [("lead", "lead", True, 1),
                                                                             ("vocals", "vocals", False, 0)]
    call(server, "/api/settings", {"beta": {"melody": False}})  # (off: its melody kept, not a part)
    assert [p["part"] for p in gui.song(sid)["parts"]] == ["lead"] and melody.kept(vocals) is not None
    call(server, "/api/settings", {"beta": {"melody": True}})
    assert [p["part"] for p in gui.song(sid)["parts"]] == ["lead", "vocals"]
    assert gui_module.default_lights(["lead", "rhythm", "vocals"]) == [1, 1, 0]
    call(server, "/api/song/settings", {"song": sid, "parts": {"vocals": {"play": True, "lights": 1}}})
    call(server, "/api/play", {"song": sid, "start": 0.5})
    planned = {pp.part.ref.part: pp for pp in gui._planned_now}
    assert [n.pitches for n in planned["vocals"].part.arrangement.notes] == [(64,), (67,)]
    assert planned["vocals"].voices.main.tone.number == 196  # (VOICE LEAD, its own default)
    call(server, "/api/stop", {})
    assert info["made"] is False


def test_with_lead_and_rhythm_apart_on_a_split_songs_guitar_is_its_lead_and_rhythm(server, monkeypatch):
    import numpy as np

    from rs_keylights import gui as gui_module, sides, stems
    gui = server.gui
    sid = first_song(server)
    monkeypatch.setattr(stems, "made", lambda ref, cache=None, separator="demucs": separator == "roformer")
    found: list = [None]  # (as measured: none yet)
    measured, loaded = [], []
    monkeypatch.setattr(sides, "kept", lambda ref, separator, cache=None: found[0])
    monkeypatch.setattr(sides, "made", lambda ref, separator, cache=None: bool(found[0] and found[0].apart))

    def make(ref, separator, lead, rhythm, offset=0.0, cache=None):
        measured.append((separator, len(lead.notes) > 0, len(rhythm.notes) > 0))
        found[0] = sides.Sides(6.8, -7.8, 600, 500, True)
        return found[0]
    monkeypatch.setattr(sides, "make", make)
    mix = (np.ones((44100 * 20, 2)) * 1000).astype(np.int16)
    monkeypatch.setattr(stems, "load", lambda ref, samples, cache=None, separator="demucs", parts=stems.SEPARATED:
                        loaded.append(list(parts)) or {s: samples // 6 for s in parts})
    monkeypatch.setattr(gui_module, "song_samples", lambda ref, game: (mix, 44100))
    shown = gui.stems_info(sid)
    assert shown["sides"] is None and shown["names"] == list(stems.STEMS) and not measured  # (the beta's off)
    call(server, "/api/settings", {"beta": {"sides": True}})
    assert gui.stems_info(sid)["sides"] in ({"state": "measuring"}, {"state": "apart", "lead": 6.8, "rhythm": -7.8})
    assert wait_for(lambda: gui.stems_info(sid)["sides"], lambda s: s["state"] == "apart")
    assert measured == [("roformer", True, True)] and gui.stems_info(sid)["names"] == list(stems.WITH_SIDES)
    assert gui.audio_info(sid)["names"] == list(stems.WITH_SIDES)  # (the Launchkey's columns)
    _samples, parts, _rate = gui._song_samples(sid, gui._load(sid)[0][0])
    assert loaded[-1] == ["drums", "bass", "lead", "rhythm", "vocals"] and set(parts) == set(loaded[-1])
    call(server, "/api/play/options", {"stem_mutes": {"rhythm": True}, "stem_volumes": {"lead": 40}})
    weights = stems.mix_weights(gui_module._stem_weights(gui.play_options, stems.ALL))
    assert weights["rhythm"] == -1.0 and weights["lead"] == -0.6 and weights["song"] == 1.0
    call(server, "/api/settings", {"beta": {"sides": False}})  # (off: its guitar as one again, the parts kept)
    assert gui.stems_info(sid)["names"] == list(stems.STEMS) and found[0].apart


def test_a_tone_check_records_the_pedal_as_the_song_plays_then_compares_it_with_the_records_guitar(server, monkeypatch,
                                                                                                   tmp_path):
    import wave

    import numpy as np
    from test_tonecheck import FakeStream

    from rs_keylights import stems, tonecheck
    gui = server.gui
    monkeypatch.setattr(tonecheck, "_input_stream", lambda device, channels: FakeStream(channels))
    monkeypatch.setattr(tonecheck, "cache_dir", lambda: tmp_path / "checks")
    status, said = call(server, "/api/tonecheck", {"action": "start"})
    assert status == 400 and "pedal" in said["error"]
    call(server, "/api/settings", {"pedal_input": {"device": "Fake Interface", "channel": 2}})
    assert gui.settings["pedal_input"] == {"device": "Fake Interface", "channel": 2}
    meters = call(server, "/api/meters")[1]
    assert meters["pedal"]["device"] == "Fake Interface" and meters["pedal"]["channel"] == 2
    status, said = call(server, "/api/tonecheck", {"action": "start"})
    assert status == 400 and "Play a song" in said["error"]
    sid = first_song(server)
    call(server, "/api/play", {"song": sid, "start": 1.0})
    status, said = call(server, "/api/tonecheck", {"action": "start"})
    assert status == 400 and "Split the song" in said["error"]
    monkeypatch.setattr(stems, "made", lambda ref, cache=None, separator="demucs": separator == "roformer")

    def decode(ref, work, cache=None, separator="demucs", parts=stems.SEPARATED):
        with wave.open(str(work / "guitar.wav"), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes((np.sin(np.arange(48000 * 70) / 7) * 8000).astype(np.int16).repeat(2).tobytes())
        return {"guitar": work / "guitar.wav"}
    monkeypatch.setattr(stems, "decode", decode)
    info = call(server, "/api/tonecheck", {"action": "start"})[1]
    assert info["state"] == "recording" and info["part"] == "lead" and info["title"]  # (its lead, by default)
    assert wait_for(lambda: call(server, "/api/tonecheck")[1]["seconds"], lambda s: s >= 1.1)
    info = call(server, "/api/tonecheck", {"action": "stop"})[1]
    assert info["state"] in ("analysing", "idle")
    done = wait_for(lambda: call(server, "/api/tonecheck")[1], lambda t: t["state"] == "idle")
    assert done.get("error") is None, done.get("error")
    last = done["last"]
    assert last["song"] and last["part"] == "lead" and last["stem"] == "guitar" and not last["enough"]
    assert [c["recording"] for c in done["checks"]] == [last["recording"]]
    assert (tmp_path / "checks" / last["recording"]).exists()
    call(server, "/api/stop", {})


def test_a_song_favoured_here_goes_into_the_games_profile_at_once_or_once_its_closed(server, monkeypatch, tmp_path):
    import os
    from rs_keylights import favorites
    from test_favorites import _profile
    path = _profile(tmp_path)
    os.utime(path, (time.time() - 60, time.time() - 60))  # (written a while ago: quiet)
    monkeypatch.setattr(favorites, "profile_path", lambda root=None: path)
    monkeypatch.setattr(favorites, "BACKUPS", tmp_path / "backups")
    running = [True]
    monkeypatch.setattr(favorites, "game_running", lambda: running[0])
    assert call(server, "/api/favorites")[1] == {"keys": ["NiAmonAGodsO", "AmonPurs"], "waiting": False, "error": None}
    status, info = call(server, "/api/favorites", {"song": "dlc/radio_m.psarc#CusRammsteinRADIO", "on": True})
    assert status == 200 and info["keys"] == ["NiAmonAGodsO", "AmonPurs", "CusRammsteinRADIO"] and info["waiting"]
    assert favorites.read(path) == ["NiAmonAGodsO", "AmonPurs"]  # (the game open: it waits)
    running[0] = False
    info = call(server, "/api/favorites")[1]
    assert not info["waiting"] and favorites.read(path) == ["NiAmonAGodsO", "AmonPurs", "CusRammsteinRADIO"]
    info = call(server, "/api/favorites", {"song": "dlc/purs_m.psarc#AmonPurs", "on": False})[1]  # (at once, closed)
    assert info["keys"] == favorites.read(path) == ["NiAmonAGodsO", "CusRammsteinRADIO"] and not info["waiting"]
    assert len(list((tmp_path / "backups").iterdir())) == 2  # (saved before each write)
    assert server.gui.settings["favorites_pending"] == {}


def test_a_songs_plays_come_from_rocksmiths_profile_by_its_arrangements_and_the_paths_icons_from_the_game(
        server, monkeypatch, tmp_path):
    from rs_keylights import favorites
    from rs_keylights.gui import song_id
    gui = server.gui
    sid = first_song(server)
    refs = [r for r in gui.index.arrangements() if song_id(r) == sid]
    played = {refs[0].persistent_id.upper(): {"played": "2026-11-24 01:56:06", "plays": 4, "mastery": 0.5}}
    path = tmp_path / "ABC_PRFLDB"
    path.write_bytes(b"x")
    monkeypatch.setattr(favorites, "profile_path", lambda root=None: path)
    monkeypatch.setattr(favorites, "stats", lambda p: played)
    monkeypatch.setattr("rs_keylights.gui.ICON_CACHE", tmp_path / "icons")
    songs = call(server, "/api/song-stats")[1]["songs"]
    assert list(songs) == [sid] and songs[sid]["played"] == "2026-11-24 01:56:06" and songs[sid]["plays"] == 4
    library = next(s for s in call(server, "/api/library")[1]["songs"] if s["id"] == sid)
    assert len(songs[sid]["mastery"]) == len(library["parts"]) == len(library["kinds"]) and 0.5 in songs[sid]["mastery"]
    assert set(library["kinds"]) <= {"lead", "rhythm", "bass"}
    assert call(server, "/api/path-icon?kind=lead")[0] == 404  # (no game's cache.psarc here)
    assert call(server, "/api/path-icon?kind=../etc")[0] == 404


def test_the_split_servers_script_downloads_from_the_page(server):
    with urllib.request.urlopen(server.url.rstrip("/") + "/api/split-server-script", timeout=5) as response:
        body = response.read()
        assert "attachment" in response.headers["Content-Disposition"]
        assert "rs-keylights-split-server.py" in response.headers["Content-Disposition"]
    assert body.startswith(b"#!") or b"split" in body[:2000]
