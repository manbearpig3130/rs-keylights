import json
import urllib.error
import urllib.request
from threading import Thread

import pytest
from builders import N, Song, song_archive

from rs_keylights.gui import Gui, GuiServer
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


@pytest.fixture
def server(tmp_path):
    dlc = tmp_path / "game" / "dlc"
    dlc.mkdir(parents=True)
    notes = [N(1.0 + i * 0.25, fret=i % 5) for i in range(40)]
    (dlc / "song_m.psarc").write_bytes(song_archive(
        "song", {"lead": ("L", Song(levels={0: notes})), "rhythm": ("R", Song(levels={0: notes}))},
        song="The Pursuit", artist="Band"))
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    outs = []

    def factory(port, channel):
        out = FakeMidiOut()
        outs.append(out)
        return Keyboard(port, channel, midi_out_factory=lambda: out)

    gui = Gui(index, factory, tmp_path / "gui.json", list_ports=lambda: ["CASIO USB-MIDI"], other_follow=lambda: False)
    srv = GuiServer(gui, 0)
    Thread(target=srv.serve_forever, daemon=True).start()
    srv.outs = outs
    yield srv
    gui.close()
    srv.shutdown()
    srv.server_close()


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


def test_play_pause_seek_resume_stop(server):
    _, library = call(server, "/api/library")
    sid = library["songs"][0]["id"]
    status, state = call(server, "/api/play", {"song": sid, "start": 1.5, "speed": 1, "parts": [
        {"part": "lead", "lights": 2, "tone": 178}, {"part": "rhythm", "lights": 2, "mute_tone": 77}]})
    assert status == 200 and state["mode"] == "playing"
    assert [p["sound"] for p in state["song"]["parts"]] == ["tone 178 SAW LEAD 1", "tone 079 DISTORTION GT, palm mutes 077 MUTE GUITAR"]
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


def test_requests_that_are_refused(server):
    _, library = call(server, "/api/library")
    sid = library["songs"][0]["id"]
    two = [{"part": "lead", "lights": 3}, {"part": "rhythm", "lights": 2}]
    assert call(server, "/api/play", {"song": sid, "parts": two}) == (400, {"error": "The keyboard lights 4 keys at most: give the parts fewer lit keys"})
    assert call(server, "/api/play", {"song": sid, "parts": [{"part": "lead", "tone": 999}]})[0] == 400
    assert call(server, "/api/play", {"song": sid, "parts": []})[1]["error"] == "Choose a part to play"
    assert call(server, "/api/state", headers={"Host": "evil.example:80"})[0] == 403
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
        "midi_port": "CASIO USB-MIDI", "channel": 11, "lights_early_ms": 15, "fastest_flash_ms": 70}
    _, library = call(server, "/api/library")
    status, answer = call(server, "/api/play", {"song": library["songs"][0]["id"], "parts": [{"part": "lead", "lights": 4}]})
    assert status == 400 and "The lights' channel (11)" in answer["error"]


def test_changes_while_playing_apply_where_the_song_is(server):
    _, library = call(server, "/api/library")
    sid = library["songs"][0]["id"]
    status, answer = call(server, "/api/update", {"song": sid, "parts": [{"part": "lead", "lights": 4}]})
    assert status == 400 and answer["error"] == "This song isn't playing"
    call(server, "/api/play", {"song": sid, "start": 10, "parts": [{"part": "lead", "lights": 4}]})
    call(server, "/api/pause", {})
    status, state = call(server, "/api/update", {"song": sid, "parts": [
        {"part": "lead", "lights": 2, "tone": 178}, {"part": "rhythm", "lights": 2, "mute_tone": 77}]})
    assert status == 200 and state["mode"] == "paused" and 10 <= state["position"] < 10.5
    assert [p["sound"] for p in state["song"]["parts"]] == ["tone 178 SAW LEAD 1", "tone 079 DISTORTION GT, palm mutes 077 MUTE GUITAR"]
    assert [0xCA, 81] in server.outs[-1].sent and [0xCF, 28] in server.outs[-1].sent  # the new tones, at once
    assert len(server.outs) == 1  # the same keyboard: nothing stopped
    status, answer = call(server, "/api/update", {"song": sid, "parts": [{"part": "lead", "lights": 5}]})
    assert status == 400 and call(server, "/api/state")[1]["song"]["parts"][0]["lights"] == 2  # unchanged


def test_changes_while_following(server):
    status, answer = call(server, "/api/follow/update", {"sound": True})
    assert status == 400 and answer["error"] == "Not following the game"
    _, state = call(server, "/api/follow", {"sound": False})
    assert state["mode"] == "following"
    status, state = call(server, "/api/follow/update", {"sound": True, "guitar": {"tone": 178}, "offset_ms": 40})
    assert status == 200 and state["mode"] == "following"
    call(server, "/api/stop", {})


def test_the_lights_timing_applies_to_what_is_playing(server):
    _, library = call(server, "/api/library")
    sid = library["songs"][0]["id"]
    call(server, "/api/play", {"song": sid, "start": 2, "parts": [{"part": "lead", "lights": 4}]})
    player = server.gui._session.player
    assert (player.lead, player.min_period) == (0.015, 0.07)
    status, _ = call(server, "/api/settings", {"lights_early_ms": 20, "fastest_flash_ms": 90})
    assert status == 200
    assert (server.gui._session.player.lead, server.gui._session.player.min_period) == (0.02, 0.09)
    assert call(server, "/api/settings", {"lights_early_ms": 500})[0] == 400
    call(server, "/api/stop", {})
