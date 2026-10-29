from rs_keylights.tones import TONES, find_tones, tone


def test_the_table_covers_every_tone_once():
    assert [t.number for t in TONES] == list(range(1, 427))
    assert all(0 <= t.program <= 127 and t.bank in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 32, 63, 120) for t in TONES)
    assert len({(t.program, t.bank) for t in TONES}) == len(TONES)


def test_spot_checks_against_casios_list():
    assert (tone(1).name, tone(1).program, tone(1).bank) == ("STEREO GRAND PIANO", 0, 2)
    assert (tone(79).name, tone(79).program, tone(79).bank) == ("DISTORTION GT", 30, 2)
    assert (tone(178).name, tone(178).program, tone(178).bank) == ("SAW LEAD 1", 81, 2)
    assert (tone(266).name, tone(266).program, tone(266).bank) == ("KOTO", 107, 2)
    assert (tone(297).name, tone(297).program, tone(297).bank) == ("GM DISTORTION GT", 30, 0)
    assert (tone(394).name, tone(394).program, tone(394).bank) == ("GM GUNSHOT", 127, 0)
    assert (tone(398).name, tone(398).program, tone(398).bank) == ("SYNTH SET", 25, 120)
    assert (tone(426).name, tone(426).program, tone(426).bank) == ("FUNNY 3 S5", 25, 63)


def test_find_by_number_name_or_part_of_it():
    assert find_tones("79") == [tone(79)]
    assert find_tones("079") == [tone(79)]
    assert find_tones("999") == []
    assert find_tones("saw lead 1") == [tone(178)]
    assert find_tones("jazz  organ") == [tone(49), tone(50)]
    assert [t.number for t in find_tones("bass")][:3] == [82, 83, 84]
    assert tone(206) in find_tones("synth-pad")  # a group
    assert find_tones("60's e.piano") == [tone(17)]
