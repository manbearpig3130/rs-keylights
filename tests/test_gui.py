import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from builders import N, Song, song_archive

from rs_keylights.gui import Gui, GuiServer, default_lights
from rs_keylights.keyboard import Keyboard
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
        "song", {"lead": ("L", Song(levels={0: notes}, beats=[i * 0.5 for i in range(121)], sections=[
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
              list_ports=lambda: ["CASIO USB-MIDI"], other_follow=lambda: False)
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
    assert [0xCA, 81] in sent and [0xCF, 28] in sent  # the tones, on the parts' channels (11, then 14-16)
    assert sent[-1] == [0xB3, 7, 100]  # closed: the lights' channel back to normal


def test_a_songs_parts_share_the_keys_at_first():
    assert default_lights(["lead"]) == [4] and default_lights(["lead", "rhythm"]) == [2, 2]
    assert default_lights(["lead", "rhythm", "bass"]) == [2, 1, 1]  # those left over: lead and rhythm first
    assert default_lights(["lead", "bass"]) == [3, 1] and default_lights(["rhythm", "bass"]) == [3, 1]
    assert default_lights(["bass", "bass"]) == [2, 2]  # (no lead or rhythm part)
    assert default_lights(["lead", "lead", "rhythm", "bass"]) == [1, 1, 1, 1]
    assert default_lights(["lead", "rhythm", "bass", "lead", "rhythm"]) == [2, 1, 1, 0, 0]  # more than 4: the first


def test_requests_that_are_refused(server):
    sid = first_song(server)
    assert call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"lights": 3}}}) == (
        400, {"error": "The keyboard lights 4 keys at most: give the parts fewer lit keys"})  # the lead has 2
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
        "keys_sound": True, "keys_lights": True, "keys_tone": 1, "keys_volume": 100, "volumes": [100] * 8}
    status, answer = call(server, "/api/play", {"song": first_song(server)})
    assert status == 400 and "The lights' channel (11)" in answer["error"]


def test_a_songs_choices_are_kept_on_the_mac_for_every_page(server, tmp_path):
    sid = first_song(server)
    _, song = call(server, f"/api/song?id={urllib.request.quote(sid)}")
    assert [(p["part"], p["play"], p["lights"], p["tones"]) for p in song["parts"]] == [
        ("lead", True, 2, {}), ("rhythm", True, 2, {})]  # every part plays, sharing the keys
    assert (song["start"], song["saved"]) == (0, False)
    _, song = call(server, "/api/song/settings", {"song": sid, "start": 12, "parts": {
        "lead": {"lights": 3, "tone": 296}, "rhythm": {"play": True, "lights": 1, "split": "B1"}}})
    assert [(p["play"], p["lights"], p["tones"]) for p in song["parts"]] == [
        (True, 3, {"tone": 296}), (True, 1, {"split": "B1"})]
    assert (song["start"], song["saved"]) == (12, True)
    _, options = call(server, "/api/play/options", {"speed": 0.5, "real_pitch": True, "song": sid})
    assert (options["speed"], options["real_pitch"], options["silent"], options["song"]) == (0.5, True, False, sid)
    assert call(server, "/api/play/options", {"speed": 9})[0] == 400
    again = Gui(server.gui.index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    assert again.song(sid)["parts"][1]["tones"] == {"split": "B1"} and again.song(sid)["start"] == 12
    assert again.play_options_info()["speed"] == 0.5 and again.play_options_info()["song"] == sid


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
    assert [0xCA, 81] in server.outs[-1].sent and [0xCF, 28] in server.outs[-1].sent  # the new tones, at once
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
    assert [(a["id"], a["play"], a["lights"]) for a in info["song"]["arrangements"]] == [("lead", True, 2), ("rhythm", True, 2)]
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
    _, info = call(server, "/api/follow/settings", {"offset_ms": 20, "lead": {"tone": 178, "split": " B1 "},
                                                     "arrangements": {"r": {"mute_tone": 77, "tone": ""}}})
    assert (info["version"], info["saved"], info["offset_ms"]) == (1, True, 20)
    assert info["lead"] == {"tone": 178, "split": "B1"}
    assert json.loads((tmp_path / "gui.json").read_text())["follow"]["arrangements"] == {"R": {"mute_tone": 77}}
    # Field by field: an empty one goes back to the default; an arrangement with none left goes
    _, info = call(server, "/api/follow/settings", {"lead": {"split": ""}, "arrangements": {"R": {"mute_tone": None}}})
    assert info["lead"] == {"tone": 178}
    assert json.loads((tmp_path / "gui.json").read_text())["follow"]["arrangements"] == {}
    for bad in ({"rhythm": {"tone": 999}}, {"bass": {"split": "H2"}}, {"offset_ms": 5000},
                {"arrangements": {"../x": {}}}, {"arrangements": []}, {"bass": "loud"}):
        assert call(server, "/api/follow/settings", bad)[0] == 400, bad
    assert call(server, "/api/follow")[1]["version"] == 2  # none of those changed anything
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
    assert [0xCD, 81] in server.outs[-1].sent  # the rhythm's tone, on the second part's channel (14)
    table = call(server, "/api/state")[1]["follow"]["parts"]
    assert [(p["name"], p["lights"], p["tone"]) for p in table] == [("Lead", 4, "079 DISTORTION GT"), ("Rhythm", 0, "178 SAW LEAD 1")]
    assert call(server, "/api/state")[1]["follow"]["messages"][-1] == (
        "Band - The Pursuit (Lead): Lead lighting 4 keys, tone 079 DISTORTION GT; Rhythm lighting no keys, tone 178 SAW LEAD 1")
    status, answer = call(server, "/api/follow/settings", {"parts": {"L": {"R": {"lights": 1}}}})
    assert status == 400 and answer["error"] == "The keyboard lights 4 keys at most: give the parts fewer lit keys"
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


def test_a_remote_plays_pauses_moves_and_follows(server):
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
    with pytest.raises(ValueError, match="Rocksmith plays"):
        gui.remote_play()
    assert gui.toggle_follow() is False and call(server, "/api/state")[1]["mode"] == "idle"


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
    call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"play": False}, "lead": {"lights": 4}}})
    call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"lights": 3}}})  # off: its lights wait
    _, song = call(server, "/api/song/settings", {"song": sid, "parts": {"rhythm": {"play": True}}})  # the lead has 4
    assert [(p["play"], p["lights"]) for p in song["parts"]] == [(True, 4), (True, 0)]
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
        "sound": True, "lights": False, "tone": 79, "name": "079 DISTORTION GT", "volume": 100}
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


def test_a_remotes_changes_are_kept_at_once_and_the_song_takes_them_once_they_stop(server):
    gui, sid = server.gui, first_song(server)
    call(server, "/api/play", {"song": sid, "start": 1.0})
    player, version = gui._session.player, gui.play_version
    gui.change_part("lead", {"lights": 1}, later=0.2)  # taps on a pad
    gui.change_part("lead", {"lights": 2}, later=0.2)
    lead = next(p for p in gui.song(sid)["parts"] if p["part"] == "lead")
    assert lead["lights"] == 2 and gui.play_version == version + 2  # the page shows each
    assert gui._session.player is player  # the notes sounding go on meanwhile
    assert wait_for(lambda: gui._session.player is not player, bool)
    replaced = gui._session.player
    time.sleep(0.3)
    assert gui._session.player is replaced  # played again once, for both
    out = server.outs[-1]
    sent = len(out.sent)
    assert call(server, "/api/volumes", {"volumes": {"0": 80}})[1]["volumes"][:2] == [80, 100]  # the first
    assert gui._session.player is replaced  # arrangement's, in every song: at once on its channels, the notes going on
    assert out.sent[sent:] == [[0xBA, 7, 80], [0xBB, 7, 80], [0xBC, 7, 80]]  # (channels 11, 12 and 13)
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
