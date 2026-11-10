from rs_keylights.dnafxtone import CAB_1960, CITRUS_212, EL34, J800, PLX100, ROCK_VRB_DS, SM57, U47, U87, convert
from rs_keylights.rig import Gear, RigTone


def blocks(converted):
    return {name: (b["TYPE"], b["Data"]) for name, b in converted.preset["effectModule"].items() if b["SWITCH"]}


def test_a_jcm800_into_a_marshall_4x12_is_the_dnafxs_own_with_its_knobs_as_they_are():
    tone = RigTone("RageKilling_dist", {
        "Amp": Gear("Amp_MarshallJCM800", {"Mid": 100, "Gain": 65, "Bass": 100, "Treble": 100, "Pres": 10}),
        "Cabinet": Gear("Cab_Marshall1960a_57_Edge"),
        "PostPedal1": Gear("Pedal_SpringReverb", {"Time": 74, "Depth": 64, "Mix": 20}),
        "PrePedal1": Gear("Pedal_EQ5", {"63": -5, "250": -1, "750": 1, "2200": -1, "5700": -3}),
        "PrePedal2": Gear("Pedal_CaptFuzzle", {"Gain": 50}),
    })
    converted = convert(tone)
    got = blocks(converted)
    assert got["AMP"] == (J800, {"GAIN": 100, "BASS": 100, "MID": 100, "TREBLE": 100, "PRES": 10, "MST": 50})
    assert got["CAB"] == (CAB_1960, {"CEBTER": 70, "DISTANCE": 35, "MIC": SM57, "TUBE": EL34})
    assert got["REVERB"] == (4, {"DECAY": 74, "LEVEL": 20, "P.DELAY": 0, "TONE": 40})  # (SPRING)
    assert got["EQ"][1] == {"100Hz": 11, "250Hz": 15, "630Hz": 17, "1.6KHz": 15, "4KHz": 13}  # (16: flat)
    assert set(got) == {"AMP", "CAB", "REVERB", "EQ", "NS GATE"} and got["NS GATE"] == (0, {"THRES": 16})  # (high gain)
    assert converted.dropped == ["Pedal_CaptFuzzle: left out"] and not converted.faithful
    assert converted.preset["fileInfo"]["schema"] == "HB80 Preset"


def test_a_plexis_gain_is_its_louder_channel_a_rockerverb_its_channel_by_gain_and_mics_the_nearest():
    plexi = convert(RigTone("p", {"Amp": Gear("Amp_MarshallPlexi", {"Loudness1": 33, "Loudness2": 66}),
                                  "Cabinet": Gear("Cab_Marshall1960TV_Condenser_Cone")}))
    assert blocks(plexi)["AMP"][1]["GAIN"] == 66 and blocks(plexi)["AMP"][0] == PLX100
    assert blocks(plexi)["CAB"][1]["MIC"] == U87 and plexi.faithful
    orange = convert(RigTone("o", {"Amp": Gear("Amp_OrangeRockerverb", {"Gain": 51}),
                                   "Cabinet": Gear("Cab_OrangePPC212OB_Ribbon_OffAxis")}))
    assert blocks(orange)["AMP"][0] == ROCK_VRB_DS and blocks(orange)["CAB"][0] == CITRUS_212
    assert blocks(orange)["CAB"][1]["MIC"] == U47 and orange.near and not orange.exact[1:]
    other = convert(RigTone("x", {"Amp": Gear("Amp_CA38"), "Cabinet": Gear("Cab_CA215C_57_Edge")}))
    assert blocks(other) == {} and len(other.dropped) == 2


def test_short_names_the_editor_takes():
    from rs_keylights.dnafxtone import short_name
    assert short_name("RageKilling_dist") == "RGKLDIST" and short_name("lawlesslands") == "LWLSSLND"
    assert short_name("RageKilling_lead2", {"RGKLLEAD"}) == "RGKLLEA2"
    assert all(len(short_name(k)) <= 8 for k in ("AudBe_breakdowndrive", "3eyesemi_drive", "x_y"))


def test_the_jcm800s_gain_as_distorted_and_hg100_the_5150_nearest():
    def gain(value, amp="Amp_MarshallJCM800"):
        return blocks(convert(RigTone("t", {"Amp": Gear(amp, {"Gain": value}),
                                            "Cabinet": Gear("Cab_Marshall1960AX_57_Cone")})))
    assert [gain(g)["AMP"][1]["GAIN"] for g in (1, 35, 65, 90)] == [2, 54, 100, 100]
    hg = gain(96, "Amp_HG100")
    assert hg["AMP"][0] == 36 and hg["AMP"][1]["GAIN"] == 96 and hg["CAB"][1]["TUBE"] == 3


def test_a_converted_tone_as_the_pedal_keeps_it():
    from dnafx_preset.presetdata import Preset
    from rs_keylights.dnafxtone import pedal_preset
    converted = convert(RigTone("amontwil_dist", {
        "Amp": Gear("Amp_HG100", {"Mid": 77, "Treble": 96, "Bass": 48, "Gain": 96}),
        "Cabinet": Gear("Cab_Marshall1960AX_57_Cone"), "Rack1": Gear("Rack_StudioChamber", {"Mix": 9, "Time": 66})}))
    preset = pedal_preset(converted, 190, "AMNTDIST")
    again = Preset.from_bytes(preset.to_bytes())
    assert (again.slot, again.name) == (190, "AMNTDIST")
    assert (again.blocks["AMP"].on, again.blocks["AMP"].model, again.blocks["AMP"].values) == (True, 36, [96, 48, 77, 96, 50, 50])
    assert again.blocks["CAB"].values == [3, 0, 20, 35, 18] and again.blocks["NS GATE"].values == [16, 50, 50]
    assert again.blocks["NS GATE"].on and not again.blocks["DS/OD"].on
    assert again.expression == {"MODULE_CTRL": 2, "PARA_CTRL": 13, "FUN_SWITCH": 1, "VOL_SWITCH": 0, "VOL_MIN": 0,
                                "VOL_MAX": 100}  # (the amp's gain)
