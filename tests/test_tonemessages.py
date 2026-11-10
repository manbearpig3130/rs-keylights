import json
import os

from builders import N, Song, manifest, psarc, sng, song_archive

from rs_keylights.library import read_arrangements
from rs_keylights.rig import (Gear, Presets, RigArrangement, RigTone, amp_drive, band, drive, effects, heaviness,
                              read_tone, tone_def, tone_order)
from rs_keylights.sng import decrypt, parse, tone_changes
from rs_keylights.tonemessages import IDLE, NO_GAME, POLL, RECHECK, Settings, ToneSender, read_rig, song_tones
from rs_keylights.psarc import Archive


def gear(key, **knobs):
    return {"Key": key, "KnobValues": {f"{key}_{name}": value for name, value in knobs.items()}}


def tone(key, tags, **slots):
    return {"Key": key, "Name": key.upper(), "ToneDescriptors": [f"$[1]{t}" for t in tags], "GearList": slots}


CLEAN = tone("s_clean", ["CLEAN"], Amp=gear("Amp_HG500", Gain=10), PrePedal1=gear("Pedal_Chorus", Mix=60, Rate=2.5))
LEAD = tone("s_lead", ["LEAD"], Amp=gear("Amp_HG500", Gain=50),
            PostPedal1=gear("Pedal_AnalogueDelay", Time=400, Mix=30, Feedback=20))
DIST = tone("s_dist", ["DISTORTION"], Amp=gear("Amp_HG500", Gain=80))
RULES = {"rules": [{"any": ["LEAD"], "preset": "Lead"},
                   {"amp": ["HG500"], "any": ["DISTORTION"], "gain": 70, "preset": "Distortion"},
                   {"any": ["DISTORTION"], "preset": "Crunch"},
                   {"any": ["CLEAN"], "preset": "Clean"}],
         "categories": {"Distortion": "Heavy"}}


def song_file(tmp_path, lead_changes=((20.0, 0), (40.0, 1), (45.0, 0))):
    """A song: its lead goes clean, lead, dist, lead; its rhythm mostly dist (a heavy song: Metal)."""
    parts = {
        "lead": (Song(levels={0: [N(1.0)]}, tone_changes=list(lead_changes)),
                 {"Tone_Base": "s_clean", "Tone_A": "s_lead", "Tone_B": "s_dist", "Tones": [CLEAN, LEAD, DIST]}),
        "rhythm": (Song(levels={0: [N(1.0)]}, tone_changes=[(50.0, 0)]),
                   {"Tone_Base": "s_dist", "Tone_A": "s_clean", "Tones": [DIST, CLEAN]}),
        "vocals": (Song(vocals=1), {}),
    }
    files = {}
    for part, (song, extra) in parts.items():
        files[f"songs/bin/macos/s_{part}.sng"] = sng(song)
        files[f"manifests/songs_dlc_s/s_{part}.json"] = manifest(
            part.upper(), f"s_{part}", part.capitalize(), song="Song", artist="Band", song_key="s",
            extra={"SongLength": 60.0, "SongAverageTempo": 120.0, "Tuning": {"string0": 0}, **extra})
    path = tmp_path / "s_m.psarc"
    path.write_bytes(psarc(files))
    return path


def presets(tmp_path, data=RULES, **ai):
    folder = tmp_path / "companion"
    folder.mkdir(exist_ok=True)
    (folder / "presets.json").write_text(json.dumps(data))
    for name, content in ai.items():
        (folder / f"{name}.json").write_text(json.dumps(content))
    return folder


def test_tone_changes_reads_just_the_tone_changes_as_parse_does(tmp_path):
    song = Song(levels={0: [N(1.0, chord=0, chord_notes=0)]}, chords=[(0, 2, 2, -1, -1, -1)],
                chord_notes=[{"masks": (0,) * 6, "slides": (-1,) * 6, "slides_off": (-1,) * 6, "vibratos": (0,) * 6,
                              "bends": {}}], tone_changes=[(3.0, 1), (7.5, 0)])
    path = tmp_path / "a_m.psarc"
    path.write_bytes(song_archive("a", {"lead": ("L", song)}))
    with Archive(path) as archive:
        data = decrypt(archive.read("songs/bin/macos/a_lead.sng"))
    assert tone_changes(data) == parse(data).tone_changes == ((3.0, 1), (7.5, 0))


def test_drive_from_the_amps_gain_and_the_pedals_before_it():
    assert band(amp_drive("Amp_HG500", 10)) == "clean" and band(amp_drive("Amp_HG500", 50)) == "crunch"
    assert band(amp_drive("Amp_HG500", 80)) == "heavy" and band(amp_drive("Amp_MarshallPlexi", 100)) == "crunch"
    plexi = Gear("Amp_MarshallPlexi", {"Loudness1": 20, "Loudness2": 45})  # (the higher of its two)
    assert drive(RigTone("t", {"Amp": plexi})) == amp_drive("Amp_MarshallPlexi", 45)
    boosted = RigTone("t", {"Amp": plexi, "PrePedal1": Gear("Pedal_SuperDrive", {"Gain": 100})})
    assert drive(boosted) == amp_drive("Amp_MarshallPlexi", 45) + 0.15
    metal = RigTone("t", {"Amp": Gear("Amp_HG500", {"Gain": 0}), "PrePedal1": Gear("Pedal_MetalDistortion", {"Gain": 100})})
    assert drive(metal) == 0.95
    acoustic = RigTone("t", {"Amp": Gear("Amp_HG500", {"Gain": 100}), "PrePedal1": Gear("Pedal_AcousticEmulator")})
    assert drive(acoustic) == 0.25 and drive(RigTone("t")) is None


def test_a_tones_effects_the_most_mixed_of_each_kind():
    t = read_tone(tone("t", [], Amp=gear("Amp_HG500", Gain=10), PrePedal1=gear("Pedal_Chorus", Mix=30, Rate=2),
                       PrePedal2=gear("Pedal_Flanger", Mix=50), Rack1=gear("Rack_StudioVerb", Mix=25),
                       PostPedal1=gear("Pedal_TapeEcho", Time=300, Feedback=40)))
    assert t.descriptors == () and t.gear["PrePedal1"].knobs == {"Mix": 30, "Rate": 2}
    fx = effects(t)
    assert (fx["modulation"].kind, fx["modulation"].gear) == ("flanger", "Pedal_Flanger")
    assert (fx["delay"].kind, dict(fx["delay"].knobs)) == ("tape", {"time_ms": 300, "feedback": 40})
    assert fx["reverb"].gear == "Rack_StudioVerb"


def test_a_songs_heaviness_is_its_rhythms_longest_tone_and_a_low_tuning(tmp_path):
    rig = read_rig(song_file(tmp_path), "s_lead")
    assert [a.name for a in rig.arrangements] == ["s_lead", "s_rhythm"] and (rig.song, rig.artist) == ("Song", "Band")
    lead = rig.arrangement("S_LEAD")
    assert lead is not None and lead.slots == ("s_clean", "s_lead", "s_dist", "", "")
    assert tone_order(lead) == [("s_clean", 20.0), ("s_lead", 35.0), ("s_dist", 5.0)]
    assert [lead.key_at(t) for t in (0, 20, 41, 46)] == ["s_clean", "s_lead", "s_dist", "s_lead"]
    assert round(heaviness(list(rig.arrangements)) or 0, 4) == round(2 / 3 + 10 / 30 / 3, 4)  # (the rhythm's dist)
    low = RigArrangement("x_rhythm", "Rhythm", ("t",), (RigTone("t", {"Amp": Gear("Amp_HG500", {"Gain": 10})}),),
                         length=10.0, low_string=-4.0)
    assert heaviness([low]) == amp_drive("Amp_HG500", 10) + 0.1
    named = RigArrangement("x_lead", "Lead", ("Hotel California",), (RigTone("HotelCalifornia"),))
    assert tone_def(named, "Hotel California") == RigTone("HotelCalifornia")


def test_presets_map_a_tone_as_rs_companion_does(tmp_path):
    p = Presets(presets(tmp_path, {**RULES, "songs": {"s_lead": {"A": "Crunch"}}, "tones": {"S_CLEAN": "Lead"},
                                   "artists": {" Band ": {"Heavy": "Crunch"}}}))
    assert p.match("s_lead", "A", "s_lead", ("LEAD",)).reason == "song override"
    assert p.match("s_rhythm", "A", "s_clean", ("CLEAN",)).category == "Lead"  # (its key's)
    heavy = p.match("s_rhythm", "Base", "s_dist", ("DISTORTION",), amp="Amp_HG500", gain=80)
    assert (heavy.preset, heavy.category, heavy.reason) == ("Distortion", "Heavy", "tags DISTORTION, amp hg500 gain 80")
    assert p.match("s_rhythm", "Base", "s_dist", ("DISTORTION",), "Band", "Amp_HG500", 80).category == "Crunch"
    assert p.match("s_rhythm", "Base", "s_dist", ("DISTORTION",), amp="Amp_HG500", gain=60).category == "Crunch"
    assert p.match("x_lead", "Base", "x", ()).reason == "default"


def test_the_ais_answer_and_suggestions_change_a_category_and_a_style(tmp_path):
    folder = presets(tmp_path, ai_classified={"s": {"style": "Classic rock", "distortion": "crunch", "confidence": "high"}},
                     ai_suggestions={"artists": {"Other": {"Heavy": "Clean"}}, "song_styles": {"Other": "Soft"}})
    p = Presets(folder)
    assert p.match("s_rhythm", "Base", "s_dist", ("DISTORTION",), "Band", "Amp_HG500", 80).reason.endswith(", AI")
    assert p.match("s_rhythm", "Base", "s_dist", ("DISTORTION",), "Band", "Amp_HG500", 80).category == "Crunch"
    assert p.match("o_rhythm", "Base", "o", ("DISTORTION",), "Other", "Amp_HG500", 80).category == "Clean"
    assert p.style(0.9, "Band", "s") == "Classic rock" and p.style(0.9, "Other", "o") == "Soft"
    assert p.style(0.9, "Nobody", "n") == "Metal" and p.style(0.5, "Nobody", "n") == "Classic rock"
    assert p.style(None, "Nobody", "n") == "Soft"
    (folder / "presets.json").write_text(json.dumps({**RULES, "ai_rejected": ["S"], "song_styles": {"n": "Jazz"}}))
    os.utime(folder / "presets.json", (1, 1))
    assert p.reload() and not p.reload()
    assert p.match("s_rhythm", "Base", "s_dist", ("DISTORTION",), "Band", "Amp_HG500", 80).category == "Heavy"
    assert p.style(0.9, "Band", "s") == "Metal" and p.style(0.1, "Nobody", "n") == "Jazz"
    assert p.style_names == ["Soft", "Classic rock", "Metal", "Jazz"]


def test_an_arrangements_message_as_rs_companion_sends_it(tmp_path):
    rig = read_rig(song_file(tmp_path), "s_lead")
    made = song_tones(rig, "s_lead", Presets(presets(tmp_path)))
    assert made is not None
    message = made.sysex()
    assert message[:2] == [0xF0, 0x7D] and message[-1] == 0xF7 and max(message[1:-1]) < 0x80
    body = json.loads(bytes(message[2:-1]))
    assert {k: body[k] for k in ("v", "kind", "song", "artist", "arrangement", "style", "heaviness", "tempo",
                                 "low_string")} == {
        "v": 2, "kind": "song-tones", "song": "Song", "artist": "Band", "arrangement": "s_lead", "style": "Metal",
        "heaviness": 0.78, "tempo": 120, "low_string": 0}
    assert body["styles"] == ["Soft", "Classic rock", "Metal"]
    assert body["tones"] == [
        {"program": 8, "key": "s_clean", "category": "Clean",
         "effects": {"modulation": {"kind": "chorus", "gear": "Pedal_Chorus", "mix": 60, "rate": 2.5}}},
        {"program": 9, "key": "s_lead", "category": "Lead",
         "effects": {"delay": {"kind": "analog", "gear": "Pedal_AnalogueDelay", "time_ms": 400, "mix": 30, "feedback": 20}}},
        {"program": 10, "key": "s_dist", "category": "Heavy", "effects": {}},
    ]
    assert made.summary() == ("Metal (0.78): 8 s_clean Clean +modulation chorus, 9 s_lead Lead +delay analog, "
                              "10 s_dist Heavy")
    assert song_tones(rig, "s_bass", Presets(presets(tmp_path))) is None


class FakeGame:
    def __init__(self):
        self.said = None

    def time(self, wait=0.02):
        return self.said


class FakeIndex:
    def __init__(self, path):
        self.refs = {r.sng.rsplit("/", 1)[-1][:-4]: r for r in read_arrangements(path)}
        self.refreshed = 0

    def by_sng(self, name):
        return self.refs.get(name.lower())

    def refresh(self, progress=None):
        self.refreshed += 1
        return 0


class FakeOut:
    def __init__(self):
        self.sent = []

    def send_message(self, message):
        self.sent.append(message)

    def taken(self):
        sent, self.sent = self.sent, []
        return [m if m[0] != 0xF0 else json.loads(bytes(m[2:-1]))["arrangement"] for m in sent]


def sender(tmp_path, settings=None):
    folder = presets(tmp_path)
    if settings is not None:
        (folder / "rscompanion.json").write_text(json.dumps(settings))
    game, out, said, now = FakeGame(), FakeOut(), [], [0.0]
    index = FakeIndex(song_file(tmp_path))
    s = ToneSender(game, index, out, folder, status=said.append, clock=lambda: now[0])

    def at(timer, arrangement="s_lead"):
        game.said = None if timer == "gone" else (timer, "LearnASong_Game", arrangement)
        return s.step()
    return s, at, out, said, now, folder, index


def test_the_song_tones_follow_the_game_each_tone_its_program_a_little_early(tmp_path):
    s, at, out, said, now, _folder, _index = sender(tmp_path)
    assert at(None, None) == IDLE and out.taken() == []
    assert at(None) == POLL and out.taken() == ["s_lead", [0xC0, 8]]  # (described, then its first tone)
    assert at(19.85) == POLL and out.taken() == []
    at(19.95)  # (100 ms before its change: RS Companion's lead)
    assert out.taken() == [[0xC0, 9]]
    at(21.0)
    assert out.taken() == []  # (sent once)
    at(41.0)
    at(5.0)  # (Riff Repeater, back to its start)
    assert out.taken() == [[0xC0, 10], [0xC0, 8]]
    at(1.0, "s_rhythm")  # (its first tone's program, 8, sent already: the description says it)
    assert out.taken() == ["s_rhythm"]
    assert any(line.startswith("Song: Song by Band, s_rhythm (SysEx") for line in said)
    assert at(55.0, "s_rhythm") == POLL and out.taken() == [[0xC0, 9]]
    assert at(2.0, None) == IDLE and said[-1] == "No song"
    assert at("gone") == NO_GAME and said[-1].startswith("Rocksmith isn't answering")
    assert at(1.0, "") == NO_GAME and "make install" in said[-1]


def test_a_changed_mapping_describes_the_song_again_and_an_unknown_arrangement_is_looked_for_once(tmp_path):
    s, at, out, said, now, folder, index = sender(tmp_path)
    at(41.0)
    assert out.taken() == ["s_lead", [0xC0, 10]]
    (folder / "presets.json").write_text(json.dumps({**RULES, "songs": {"s_lead": {"B": "Clean"}}}))
    os.utime(folder / "presets.json", (1, 1))
    at(41.1)
    assert out.taken() == []  # (looked at once a second)
    now[0] += RECHECK
    at(41.2)
    assert out.taken() == ["s_lead"] and s.song is not None and s.song.tones[2].category == "Clean"
    at(1.0, "other_lead")
    assert index.refreshed == 1 and said[-1] == "other_lead: not among the game's songs here"
    at(2.0, None)
    at(3.0, "other_lead")  # (left, and back to it)
    assert index.refreshed == 1 and said.count("other_lead: not among the game's songs here") == 1


def test_settings_from_rs_companions_file_the_channel_first_program_lead_and_category_programs(tmp_path):
    s, at, out, _said, _now, _folder, _index = sender(tmp_path, {
        "midi_channel": 3, "song_tone_first_program": 20, "lead_ms": 500, "programs": {"Lead": 7}})
    assert s.settings == Settings(3, 20, 0.5, True, (0, 1, 2, 7))
    at(19.6)
    assert out.taken() == ["s_lead", [0xC2, 21]]
    s, at, out, _said, _now, _folder, _index = sender(tmp_path, {"song_tone_programs": False, "programs": {"Lead": 7}})
    at(30.0)
    assert out.taken() == ["s_lead", [0xC0, 7]]  # (its category's)
    assert Settings.read(tmp_path / "missing.json") == Settings()


def test_no_mapping_found_every_tone_is_clean_and_it_says_so(tmp_path):
    out, said = FakeOut(), []
    s = ToneSender(FakeGame(), FakeIndex(song_file(tmp_path)), out, tmp_path / "none", status=said.append)
    assert said and "every tone is Clean" in said[0]
    s.game.said = (30.0, "LearnASong_Game", "s_lead")  # type: ignore[attr-defined]
    s.step()
    assert s.song is not None and {t.category for t in s.song.tones} == {"Clean"}

