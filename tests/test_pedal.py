import os
import sys
import time

import pytest

from rs_keylights import pedal as pedal_module
from rs_keylights.pedal import (BACKUP_PRESETS, FIRST_NAME, NEEDS, RECORD, SLOTS, Pedal, PedalError, dnafx_switcher,
                                find_backup, logged_slot, preset_names)


def backup(names):
    """A DNAfx editor's backup: its header, then a record a preset (its number, then its name)."""
    data = bytearray(b"Harley\0\0Harley_HB80".ljust(0x400, b"\0") + BACKUP_PRESETS)
    start = 0x400
    data += bytes(start + FIRST_NAME + SLOTS * RECORD - len(data))
    for i in range(SLOTS):
        at = start + FIRST_NAME + i * RECORD
        data[at - 2:at] = i.to_bytes(2, "little")
        name = names.get(i + 1, f"P{i + 1}").encode()
        data[at:at + len(name)] = name
    return bytes(data)


def wait_for(ready, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not ready() and time.monotonic() < deadline:
        time.sleep(0.01)
    return ready()


def test_a_backup_names_the_presets(tmp_path):
    path = tmp_path / "DNAfx GiT Core backup.bhb"
    path.write_bytes(backup({1: "US Clean", 49: "5050 CL", 200: "My love"}))
    names = preset_names(path)
    assert len(names) == SLOTS and (names[1], names[49], names[200], names[2]) == ("US Clean", "5050 CL", "My love", "P2")
    (tmp_path / "other.bhb").write_bytes(b"not a backup")
    assert preset_names(tmp_path / "other.bhb") == {} and preset_names(tmp_path / "gone.bhb") == {}


def test_the_newest_backup_is_found(tmp_path):
    (tmp_path / "Music").mkdir()
    (tmp_path / "Documents").mkdir()
    older, newer = tmp_path / "Music" / "first.bhb", tmp_path / "Documents" / "later.bhb"
    older.write_bytes(b"")
    newer.write_bytes(b"")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    assert find_backup(tmp_path) == newer
    assert find_backup(tmp_path / "nobody") is None


def test_dnafx_presets_log_says_the_slot_it_switched_to_last(tmp_path):
    log = tmp_path / "sidekick-dnafx-preset.log"
    log.write_text("Song: Victorious March\n  program 8: Distortion, Heavy -> slot 51\nSwitched to slot 51\n"
                   "Switched to slot 68\nthe game quit: stopping\n")
    assert logged_slot(log) == 68
    log.write_text("Listening\n")
    assert logged_slot(log) is None and logged_slot(tmp_path / "gone.log") is None


def test_turning_through_presets_switches_to_the_last_one_once_it_rests(tmp_path):
    switched = []
    pedal = Pedal(switched.append, log=tmp_path / "none.log")
    assert pedal.slot is None and pedal.previous is None
    for slot in (50, 51, 52):
        assert pedal.switch(slot, wait=0.1) == slot
    assert pedal.slot == 52 and switched == []  # (the one it's going to, at once)
    assert wait_for(lambda: switched == [52])
    time.sleep(0.15)
    assert switched == [52] and pedal.previous is None
    pedal.switch(250)  # (no higher than the last)
    assert wait_for(lambda: switched == [52, 200]) and pedal.previous == 52
    assert pedal.back() == 52 and wait_for(lambda: switched == [52, 200, 52]) and pedal.previous == 200


def test_a_pedal_that_cant_be_switched_says_why(tmp_path):
    def unplugged(slot):
        raise OSError("DNAfx device 0483:5703 was not found or could not be opened.")

    pedal = Pedal(unplugged, log=tmp_path / "none.log")
    pedal.switch(49)
    assert wait_for(lambda: pedal.error is not None) and "not found" in (pedal.error or "")
    assert pedal.slot == 49 and pedal.previous is None


def test_the_listener_switching_it_since_is_noticed(tmp_path):
    log, switched = tmp_path / "sidekick-dnafx-preset.log", []
    clock = [1000.0]
    pedal = Pedal(switched.append, log=log, clock=lambda: clock[0])
    log.write_text("Switched to slot 49\n")
    os.utime(log, (900, 900))
    assert pedal.slot == 49  # (switched before this ran: the last known)
    pedal.switch(68)
    assert wait_for(lambda: switched == [68]) and (pedal.slot, pedal.previous) == (68, 49)
    log.write_text("Switched to slot 49\nSwitched to slot 51\n")
    os.utime(log, (1100, 1100))  # (after: the listener's)
    assert (pedal.slot, pedal.previous) == (51, 68)
    assert pedal.back() == 68


def dnafx_preset(monkeypatch, script):
    """dnafx-preset's command, as a stand-in: a script given the slot (sys.argv[1])."""
    monkeypatch.setattr(pedal_module, "SWITCH", (sys.executable, "-c", script))


def test_dnafx_preset_switches_it_in_a_process_of_its_own(monkeypatch, tmp_path):
    said = tmp_path / "slot"
    dnafx_preset(monkeypatch, f"import sys; open({str(said)!r}, 'w').write(sys.argv[1]); print('Switched to slot')")
    dnafx_switcher()(51)
    assert said.read_text() == "51"
    dnafx_preset(monkeypatch, "import sys; sys.stderr.write('Error: DNAfx device 0483:5703 was not found\\n'); "
                              "sys.exit(1)")
    with pytest.raises(PedalError, match="^DNAfx device 0483:5703 was not found$"):
        dnafx_switcher()(51)
    dnafx_preset(monkeypatch, "import sys; sys.stderr.write('No module named dnafx_preset\\n'); sys.exit(1)")
    with pytest.raises(PedalError, match="dnafx-preset") as missing:
        dnafx_switcher()(51)
    assert str(missing.value) == NEEDS


def test_a_pedal_that_hangs_doesnt_hang_this(monkeypatch):
    monkeypatch.setattr(pedal_module, "SWITCH_TIMEOUT", 0.3)
    dnafx_preset(monkeypatch, "import time; time.sleep(30)")
    started = time.monotonic()
    with pytest.raises(PedalError, match="didn't answer"):
        dnafx_switcher()(51)
    assert time.monotonic() - started < 2.0
