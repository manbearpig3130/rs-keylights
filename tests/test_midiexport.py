import struct
import urllib.request
from threading import Thread
from urllib.parse import quote

from test_tonemessages import song_file

from rs_keylights import drums
from rs_keylights.cli import main
from rs_keylights.drums import Hit
from rs_keylights.gui import Gui, GuiServer, song_id
from rs_keylights.library import SongIndex
from rs_keylights.midiexport import PPQ, ExportPart, Grid, file_name, key_signature, song_midi
from rs_keylights.sng import ACCENT, PALM_MUTE, Arrangement, Chord, Note, Section, SongTone, Syllable


def read(data):
    """A MIDI file's tracks, read here (not by midiexport): each a list of (tick, status or 0xFF, kind, bytes)."""
    assert data[:4] == b"MThd"
    _size, fmt, count, ppq = struct.unpack(">IHHH", data[4:14])
    assert (fmt, ppq) == (1, PPQ)
    i, tracks = 14, []
    for _ in range(count):
        assert data[i:i + 4] == b"MTrk"
        end = i + 8 + struct.unpack(">I", data[i + 4:i + 8])[0]
        j, tick, events = i + 8, 0, []

        def number(j):
            n = 0
            while True:
                n, j = (n << 7) | (data[j] & 0x7F), j + 1
                if not data[j - 1] & 0x80:
                    return n, j
        while j < end:
            delta, j = number(j)
            tick += delta
            if data[j] == 0xFF:
                size, k = number(j + 2)
                events.append((tick, 0xFF, data[j + 1], data[k:k + size]))
                j = k + size
            else:
                size = 1 if data[j] >> 4 in (0xC, 0xD) else 2
                events.append((tick, data[j], None, data[j + 1:j + 1 + size]))
                j += 1 + size
        assert j == end and events[-1][1:3] == (0xFF, 0x2F)
        tracks.append(events)
        i = end
    return tracks


def clock(conductor):
    """Seconds at a tick, from the tempo events."""
    tempos = [(t, int.from_bytes(d, "big")) for t, s, k, d in conductor if s == 0xFF and k == 0x51]

    def at(tick):
        seconds, last, tempo = 0.0, 0, 500000
        for t, value in tempos:
            if t > tick:
                break
            seconds, last, tempo = seconds + (t - last) * tempo / PPQ / 1e6, t, value
        return seconds + (tick - last) * tempo / PPQ / 1e6
    return at


def metas(track, kind):
    return [(t, d.decode()) for t, s, k, d in track if s == 0xFF and k == kind]


def test_the_tempo_map_follows_the_charts_beats_after_a_lead_in_made_of_the_time_before_them():
    beats = [1.5, 2.0, 2.5, 3.0, 3.6, 4.2, 4.8, 5.4, 6.0]  # (bars of 4 from 2.0: the first beat a pickup)
    grid = Grid(beats, [2.0, 4.2], 7.0)
    assert grid.times[:4] == [0.0, 0.5, 1.0, 1.5]  # (1.5 s before the first beat: 3 beats of 0.5)
    assert grid.bar_starts == [0, 4, 8]  # (the lead-in and the pickup: one bar of 4 before 2.0)
    assert grid.tick(0.0) == 0 and grid.tick(2.0) == 4 * PPQ and grid.tick(3.3) == 6 * PPQ + PPQ // 2
    assert grid.tick(6.3) == 11 * PPQ + PPQ // 2  # (after the last beat: its tempo kept)
    assert grid.tempos() == [(0, 500000), (6 * PPQ, 600000)]
    assert grid.signatures() == [(0, 4)]
    odd = Grid([1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0], [1.0, 2.5], 4.0)  # (bars of 3, two lead-in beats)
    assert odd.bar_starts == [0, 2, 5] and odd.signatures() == [(0, 2), (2 * PPQ, 3)]
    steady = Grid([], [], 3.0)  # (no beats: 120 a minute, bars of 4)
    assert steady.tick(1.0) == 2 * PPQ and steady.signatures() == [(0, 4)]


def test_a_song_as_a_midi_file_its_parts_vocals_drums_key_and_sections():
    beats = tuple(1.0 + 0.5 * i for i in range(16))
    lead = Arrangement(
        notes=(Note(1.0, 0.0, (64,)), Note(1.5, 1.0, (67,), bends=((1.75, 1.0), (2.0, 2.0))),
               Note(3.0, 0.5, (52, 59), mask=PALM_MUTE), Note(4.0, 0.2, (60,), mask=ACCENT, vibrato=True)),
        tuning=(0,) * 6, capo=0, length=9.0, beats=beats, bars=beats[::4],
        chords=(Chord("E5", 3.0, 3.5), Chord("E5", 3.5, 4.0)), tone_changes=((3.0, 0),),
        base_tone=SongTone("clean"), tones=(SongTone("dist"),), muted=(Note(5.0, 0.0, (45,)),))
    bass = Arrangement(notes=(Note(1.0, 0.4, (28,)),), tuning=(0,) * 4, capo=0, length=9.0, beats=beats)
    sung = (Syllable("Hel-", 2.0, 2.4, 60), Syllable("lo+", 2.5, 3.0, 62), Syllable("you", 3.0, 3.2, 64),
            Syllable("there", 3.5, 4.0, 65))
    data = song_midi("Band - Song", [ExportPart("Lead", "lead", lead), ExportPart("Bass", "bass", bass)],
                     markers=[(1.0, "Intro 1"), (5.0, "Verse 1")], lyrics=sung, drums=[Hit(1.0, 36, 110)],
                     key=(4, "minor"))
    conductor, lead_track, bass_track, vocals, drum_track = read(data)
    at = clock(conductor)
    assert metas(conductor, 0x03) == [(0, "Band - Song")]
    assert [round(at(t), 3) for t, _n in metas(conductor, 0x06)] == [1.0, 5.0]
    assert [k for _t, s, kind, k in conductor if s == 0xFF and kind == 0x59] == [b"\x01\x01"]  # (E minor: 1 sharp)
    assert metas(lead_track, 0x03) == [(0, "Lead")] and metas(bass_track, 0x03) == [(0, "Bass")]
    ons = [(round(at(t), 3), d[0], d[1], s & 0x0F) for t, s, _k, d in lead_track if s >> 4 == 9]
    assert ons == [(1.0, 64, 96, 0), (1.5, 67, 96, 0), (3.0, 52, 64, 0), (3.0, 59, 64, 0), (4.0, 60, 120, 0),
                   (5.0, 45, 36, 0)]
    offs = {d[0]: round(at(t), 3) for t, s, _k, d in lead_track if s >> 4 == 8}
    assert offs[64] == 1.125 and offs[67] == 2.5 and offs[60] == 4.2  # (a picked note: a 16th)
    bends = [(round(at(t), 3), d[0] | d[1] << 7) for t, s, _k, d in lead_track if s >> 4 == 0xE]
    assert bends[0] == (1.5, 8192) and (2.0, 16383) in bends and bends[-1] == (2.5, 8192)  # (up 2: the most, back)
    assert [round(at(t), 2) for t, s, _k, d in lead_track if s >> 4 == 0xB and d[0] == 1] == [4.0, 4.2]  # vibrato
    assert [(round(at(t), 3), text) for t, text in metas(lead_track, 0x01)] == [
        (0.0, "Tone: clean"), (3.0, "Tone: dist"), (3.0, "E5")]  # (a chord held on: named once)
    assert [d for _t, s, _k, d in lead_track if s >> 4 == 0xC] == [b"\x1d"]  # (overdriven guitar)
    assert [d for _t, s, _k, d in bass_track if s >> 4 == 0xC] == [b"\x21"] and \
        [s & 0x0F for _t, s, _k, _d in bass_track if s >> 4 == 9] == [1]
    assert [text for _t, text in metas(vocals, 0x05)] == ["Hel", "lo\r", "you ", "there "]
    assert [(round(at(t), 2), d[0]) for t, s, _k, d in vocals if s >> 4 == 9] == [(2.0, 60), (2.5, 62), (3.0, 64),
                                                                                (3.5, 65)]
    assert [(round(at(t), 2), s, d[0], d[1]) for t, s, _k, d in drum_track if s >> 4 == 9] == [(1.0, 0x99, 36, 110)]


def test_a_vocals_part_without_a_melody_gives_its_lyrics_alone_and_a_bend_past_2_says_its_range():
    lead = Arrangement(notes=(Note(1.0, 1.0, (64,), bends=((1.5, 3.0),)),), tuning=(0,) * 6, capo=0, length=3.0)
    sung = (Syllable("la", 1.0, 1.5, 60), Syllable("la", 1.5, 2.0, 61), Syllable("la", 2.0, 2.5, 24))
    _conductor, lead_track, vocals = read(song_midi("S", [ExportPart("Lead", "lead", lead)], lyrics=sung))
    assert metas(lead_track, 0x03) == [(0, "Lead (bend range 3)")]
    assert [d[1] for _t, s, _k, d in lead_track if s >> 4 == 0xB and d[0] == 6] == [3]  # (sent as RPN 0 too)
    assert not [e for e in vocals if e[1] >> 4 == 9] and len(metas(vocals, 0x05)) == 3


def test_key_signatures_and_file_names():
    assert key_signature(5, "major") == struct.pack("bB", -1, 0)  # F
    assert key_signature(9, "Dorian") == struct.pack("bB", 1, 0)  # (G major's)
    assert key_signature(1, "minor") == struct.pack("bB", 4, 1)  # C# minor
    assert file_name('AC/DC - What? "Now"', ".mid") == "AC DC - What Now.mid"
    assert file_name("AC/DC - Back: In Black", ".mid") == "AC DC - Back In Black.mid"
    assert file_name(" ... ", ".wav") == "song.wav"


def test_the_page_and_the_command_save_a_song_as_a_midi_file(tmp_path, monkeypatch, capsys):
    game = tmp_path / "game" / "dlc"
    game.mkdir(parents=True)
    path = song_file(game)
    index = SongIndex(tmp_path / "game", tmp_path / "index.json")
    index.refresh()
    gui = Gui(index, settings_file=tmp_path / "gui.json", list_ports=list, other_follow=lambda: False)
    srv = GuiServer(gui, 0)
    Thread(target=srv.serve_forever, daemon=True).start()
    try:
        sid = song_id(index.by_sng("s_lead"))
        with urllib.request.urlopen(srv.url + "api/export/midi?id=" + quote(sid), timeout=5) as response:
            data = response.read()
            assert response.headers["Content-Type"] == "audio/midi"
            assert response.headers["Content-Disposition"] == (
                "attachment; filename=\"Band - Song.mid\"; filename*=UTF-8''Band%20-%20Song.mid")
        tracks = read(data)
        assert [metas(t, 0x03)[0][1] for t in tracks] == ["Band - Song", "Lead", "Rhythm", "Vocals"]
        assert gui.song(sid)["audio_export"] is False
        try:
            urllib.request.urlopen(srv.url + "api/export/audio?id=" + quote(sid), timeout=5)
            raise AssertionError("no decoder: refused")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400 and b"drums-setup.sh" in exc.read()
    finally:
        gui.close()
        srv.shutdown()
        srv.server_close()

    assert main(["midi", str(path), "-o", str(tmp_path)]) == 0
    assert (tmp_path / "Band - Song.mid").read_bytes() == data
    assert "Lead, Rhythm, vocals" in capsys.readouterr().out

    def decoded(ref, game_dir, out):
        out.write_bytes(b"RIFF")
    monkeypatch.setattr(drums, "decode", decoded)
    assert main(["--game-dir", str(tmp_path / "game"), "midi", str(path), "-o", str(tmp_path / "x.mid"), "--audio"]) == 0
    assert (tmp_path / "x.wav").read_bytes() == b"RIFF"
