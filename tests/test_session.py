import time

from builders import N, Song, song_archive

from rs_keylights.keyboard import Keyboard
from rs_keylights.library import read_arrangements
from rs_keylights.player import Player, Track
from rs_keylights.lights import Light
from rs_keylights.session import Part, Playback, plan
from rs_keylights.sound import SoundChoice
from rs_keylights.tones import tone


class FakeMidiOut:
    def __init__(self):
        self.sent = []

    def get_ports(self):
        return ["CASIO USB-MIDI"]

    def open_port(self, port=0, name=None):
        pass

    def send_message(self, message):
        self.sent.append(message)

    def close_port(self):
        self.sent.append("closed")


def keyboard():
    out = FakeMidiOut()
    k = Keyboard("casio", 4, midi_out_factory=lambda: out)
    k.open()
    return k, out


def test_plan_shares_the_lit_parts_shift_and_gives_each_part_its_channels(tmp_path):
    path = tmp_path / "a_m.psarc"
    low = Song(levels={0: [N(1.0, string=0, fret=0)]}, tuning=(-5,) * 6)  # B1: moved up an octave
    high = Song(levels={0: [N(1.0, string=5, fret=12)]})  # E5
    path.write_bytes(song_archive("a", {"lead": ("L", high), "rhythm": ("R", low)}))
    refs = {r.part: r for r in read_arrangements(path)}
    parts = [Part(refs["lead"], refs["lead"].load(), 2), Part(refs["rhythm"], refs["rhythm"].load(), 2)]
    choice = SoundChoice({"lead": tone(178)}, {}, {}, {}, False)
    planned = plan(parts, choice)
    assert planned[0].shift == planned[1].shift == 12
    assert [v.channel for v in planned[0].voices.all()] == [11]
    assert planned[1].voices.main.channel == 14 and planned[1].voices.main.tone.number == 79
    assert plan(parts, None)[0].voices is None


def test_playback_position_pause_seek_and_speed():
    now = [100.0]
    k, _out = keyboard()
    p = Playback(Player([Track([Light(1.0, 2.0, (60,))])], k), k, length=10.0, start=2.0, clock=lambda: now[0])
    now[0] = 101.0
    assert p.position == 3.0
    p.set_speed(0.5)
    now[0] = 103.0
    assert p.position == 4.0
    p.pause()
    now[0] = 110.0
    assert p.position == 4.0 and p.paused
    p.resume()
    now[0] = 112.0
    assert p.position == 5.0
    p.seek(9.0)
    now[0] = 130.0
    assert p.position == 10.0  # not past the end
    p.seek(-3)
    assert p.position == 0.0


def test_playback_runs_to_the_end_and_closes_the_keyboard_when_stopped():
    k, out = keyboard()
    p = Playback(Player([Track([Light(0.05, 0.1, (60,))])], k), k, length=0.2)
    p.start()
    deadline = time.monotonic() + 3
    while not p.ended and time.monotonic() < deadline:
        time.sleep(0.02)
    assert p.ended and p.paused and p.running
    assert [0x93, 60, 100] in out.sent and [0x83, 60, 0] in out.sent
    p.stop()
    assert not p.running and out.sent[-1] == "closed"


def test_replace_goes_on_from_where_the_song_is():
    now = [100.0]
    k, out = keyboard()
    p = Playback(Player([Track([Light(1.0, 9.0, (60,))])], k), k, length=10.0, start=2.0, clock=lambda: now[0])
    p.player.advance(2.0)  # what the thread would do
    assert [0x93, 60, 100] in out.sent
    del out.sent[:]
    now[0] = 101.0
    p.replace(Player([Track([Light(1.0, 9.0, (64,))])], k))
    assert p.position == 3.0
    assert out.sent[:2] == [[0x83, 60, 0], [0xB3, 123, 0]] and [0x93, 64, 100] in out.sent  # old off, new lit at once
    p.replace(Player([Track([Light(1.0, 2.0, (67,))])], k), length=2.5)
    assert p.position == 2.5 and p.length == 2.5
