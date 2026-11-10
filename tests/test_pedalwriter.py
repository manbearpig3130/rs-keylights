import json

from dnafx_preset.presetdata import Preset

from rs_keylights import pedalwriter
from rs_keylights.pedalwriter import PedalWriter
from rs_keylights.rig import Gear, RigArrangement, RigTone
from rs_keylights.tonemessages import SongRig, SongTones, Tone

DIST = RigTone("amontwil_dist", {"Amp": Gear("Amp_HG100", {"Gain": 96, "Bass": 48, "Mid": 77, "Treble": 96}),
                                 "Cabinet": Gear("Cab_Marshall1960AX_57_Cone")})
LEAD = RigTone("amontwil_lead", {"Amp": Gear("Amp_MarshallJCM800", {"Gain": 50}), "Cabinet": Gear("Cab_Marshall1960a_57_Edge")})
CLEAN = RigTone("amontwil_clean", {"Amp": Gear("Amp_CA38", {"Gain": 5}), "Cabinet": Gear("Cab_CA215C_57_Edge")})


def rig(path="Lead"):
    a = RigArrangement("amontwil_lead", path, ("amontwil_dist", "amontwil_lead", "amontwil_clean", "", ""),
                       (DIST, LEAD, CLEAN))
    return SongRig((a,), "Twilight of the Thunder God", "Amon Amarth")


class FakePedal:
    def __init__(self):
        self.written, self.reads, self.answer = [], 0, [(0x43, b"")]
        self.presets = [Preset(slot, f"P{slot}") for slot in range(1, 201)]

    def upload(self, preset):
        self.written.append(preset)
        return self.answer

    def read_presets(self):
        self.reads += 1
        return self.presets


def test_a_songs_tones_whose_amps_the_dnafx_has_written_once_in_the_song_slots(tmp_path):
    pedal, said = FakePedal(), []
    writer = PedalWriter(lambda: pedal, status=said.append)
    keys = ["amontwil_dist", "amontwil_clean", "amontwil_lead"]
    assert writer.write(rig(), "amontwil_lead", keys) == {"amontwil_dist": 194, "amontwil_lead": 195}  # (CA38: none)
    assert [(p.slot, p.name, p.blocks["AMP"].model) for p in pedal.written] == [(194, "AMNTDIST", 36), (195, "AMNTLEAD", 6)]
    saved = sorted(p.name for p in (tmp_path / "DNAfx backups").glob("*/*.bin"))
    assert saved == [f"{slot}-P{slot}.bin" for slot in range(194, 200)] and pedal.reads == 1  # (before the first write)
    assert writer.write(rig(), "amontwil_lead", keys) == {"amontwil_dist": 194, "amontwil_lead": 195}
    assert len(pedal.written) == 2 and pedal.reads == 1  # (there already: not written again)
    writer.forget()
    writer.write(rig(), "amontwil_lead", keys)
    assert len(pedal.written) == 4 and len(json.loads(pedalwriter.STATE.read_text())) == 2
    assert writer.write(rig("Bass"), "amontwil_lead", keys) == {}


def test_a_pedal_that_doesnt_answer_or_isnt_there_leaves_the_tones_to_the_rules():
    pedal, said, now = FakePedal(), [], [0.0]
    pedal.answer = []
    writer = PedalWriter(lambda: pedal, status=said.append, clock=lambda: now[0])
    assert writer.write(rig(), "amontwil_lead", ["amontwil_dist"]) == {}
    assert "not written" in said[-1]

    def missing():
        raise RuntimeError("no DNAfx")
    gone = PedalWriter(missing, status=said.append, clock=lambda: now[0])
    assert gone.write(rig(), "amontwil_lead", ["amontwil_dist"]) == {} and "no DNAfx" in said[-1]
    now[0] = 30.0
    assert gone.write(rig(), "amontwil_lead", ["amontwil_dist"]) == {} and "no DNAfx" in said[-1]
    said.clear()
    assert gone.write(rig(), "amontwil_lead", ["amontwil_dist"]) == {} and not said  # (a minute before trying again)


def test_the_song_tone_message_says_each_tones_preset():
    song = SongTones("S", "A", "s_lead", "Metal", (), None, None, None, 8,
                     (Tone("s_dist", "Heavy", "tags", {}, 194), Tone("s_clean", "Clean", "tags")))
    tones = json.loads(song.json())["tones"]
    assert tones[0]["pedal"] == 194 and "pedal" not in tones[1]


def test_names_read_from_the_pedal_win_over_the_backups_and_written_ones_join_them(monkeypatch):
    from rs_keylights import pedal
    from rs_keylights.pedal import Pedal, cached_names, remember_names
    p = Pedal(switcher=lambda slot: None)
    assert p.names_version == 0.0 and p.name(194) == ""  # (no backup, not read)
    remember_names({194: "AMNTDIST"})
    assert cached_names() is None  # (not read from the pedal yet: a few names alone aren't kept)
    monkeypatch.setattr(pedal, "read_pedal_names", lambda: (remember_names({n: f"P{n}" for n in range(1, 201)}, every=True)
                                                            or {n: f"P{n}" for n in range(1, 201)}))
    p.refresh_names()
    assert p.name(194) == "P194" and p.names_version > 0
    remember_names({194: "AMNTDIST"})
    assert p.name(194) == "AMNTDIST" and p.name(195) == "P195"
