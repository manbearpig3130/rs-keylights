#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9,<3.14"
# dependencies = ["mido>=1.3", "python-rtmidi>=1.5"]
# ///
"""
Use the Launchkey Mini MK4's Modulation strip as a custom control while
keeping the screen for your own content.

How it works (Launchkey MK4 Programmer's Reference Guide v2, plus testing):
  1. Put the Launchkey in DAW mode (Note On, ch 16, note 0Ch, vel 7Fh, sent to
     the DAW port). The host only gets control of the screen in DAW mode.
  2. Write our own text to the stationary display (target 20h).
  3. Read the strip from the MIDI port (performance controls such as the
     strips and keys keep reporting there in DAW mode): CC 1, plus CC 33 as
     the fine part if the strip is set to 14-bit in Novation Components.
  4. The firmware always draws its own "CC #001 / MIDI Channel" pop-up when
     the strip moves; none of the documented display settings turn it off for
     the strip (it ignores display targets 00h-1Fh and 21h). So we fight it:
     immediately and 1-40 ms after every strip message, cancel the pop-up and
     bring our display back. The pop-up is already drawn by the time we hear
     about a movement, so a brief flicker remains, mostly on slow movements.
     Once the strip goes still, restore a few more times, because the
     Launchkey sometimes redraws its pop-up after the first restore.
  5. On exit, leave DAW mode so the Launchkey goes back to normal.

Run with:  uv run launchkey_mod_slider.py      (Ctrl+C to quit)
"""
import queue
import sys
import threading
import time

import mido

DEVICE = "Launchkey Mini MK4"
# SysEx header after F0 (mido adds F0/F7). 13h = Mini SKUs; regular Launchkey MK4 uses 14h.
SYSEX_HEADER = [0x00, 0x20, 0x29, 0x02, 0x13]

MOD_CC = 1
MOD_FINE_CC = MOD_CC + 32  # 14-bit LSB, sent only when the strip is set to 14-bit

# Display targets
TARGET_MOD_STRIP = MOD_CC  # 00h-1Fh: temporary display of the analogue control with that CC
TARGET_STATIONARY = 0x20
TARGET_GLOBAL_TEMP = 0x21

# <config> byte for "Configure display"
CANCEL = 0x00
SHOW = 0x7F
ARRANGE_TITLE_NAME_VALUE = 2  # 3 lines: title, name, text value

# Seconds after EVERY strip message at which to put our display back (while moving).
# Restoring early matters most; this beat 8/20/40 ms and 4/8/20/40 ms on slow movements.
FIGHT_DELAYS = (0.0, 0.001, 0.002, 0.004, 0.008, 0.02, 0.04)
# Seconds after the strip's LAST message at which to put it back again (after letting go).
RELEASE_DELAYS = (0.06, 0.25, 0.6)


def find_port(names, *words):
    for name in names:
        if DEVICE in name and all(w in name for w in words):
            return name
    sys.exit(f"No '{DEVICE}' port containing {words} found in {names}. Is it plugged in?")


def to_display_bytes(text):
    # Screen takes ASCII 20h-7Eh (plus a few symbols at 1Bh-1Eh).
    return [ord(c) if 0x20 <= ord(c) <= 0x7E else ord("?") for c in text]


class Launchkey:
    def __init__(self):
        ins, outs = mido.get_input_names(), mido.get_output_names()
        self.daw_out = mido.open_output(find_port(outs, "DAW", "In"))
        self.midi_in = mido.open_input(find_port(ins, "MIDI", "Out"))
        self._send_lock = threading.Lock()  # sends come from several timer threads

    def send(self, msg):
        with self._send_lock:
            self.daw_out.send(msg)

    def sysex(self, *data):
        self.send(mido.Message("sysex", data=SYSEX_HEADER + list(data)))

    def set_daw_mode(self, on):
        self.send(mido.Message("note_on", channel=15, note=0x0C, velocity=0x7F if on else 0))

    def configure_display(self, target, config):
        self.sysex(0x04, target, config)

    def set_text(self, target, field, text):
        self.sysex(0x06, target, field, *to_display_bytes(text))

    def show_lines(self, title, name, value):
        """Put three lines of our own text on the stationary (home) display."""
        self.configure_display(TARGET_STATIONARY, ARRANGE_TITLE_NAME_VALUE)
        for field, text in enumerate((title, name, value)):
            self.set_text(TARGET_STATIONARY, field, text)
        self.configure_display(TARGET_STATIONARY, SHOW)

    def restore_display(self):
        """Drop any pop-up and bring the stationary display back.

        Re-showing the stationary display alone doesn't beat the pop-up; the
        cancels are what make this work.
        """
        self.configure_display(TARGET_GLOBAL_TEMP, CANCEL)
        self.configure_display(TARGET_MOD_STRIP, CANCEL)
        self.configure_display(TARGET_STATIONARY, SHOW)

    def close(self):
        self.set_daw_mode(False)
        self.daw_out.close()
        self.midi_in.close()


def on_mod_change(lk, value, fine):
    """Your control logic goes here.

    value is 0-127. fine is 0-16383 when the strip is set to 14-bit in
    Novation Components (otherwise it's just value * 128). In 14-bit mode the
    strip keeps sending while your finger rests on it, jittering slightly;
    smooth or ignore small changes if your control needs to be steady.
    """
    print(f"mod strip: {value:3d}   (14-bit: {fine:5d})")


def main():
    lk = Launchkey()
    values = queue.Queue()  # hands strip values to the main thread, off the MIDI thread
    release_timers = []
    coarse = fine_lsb = 0
    fine_mode = False  # becomes True once the strip sends CC 33, i.e. it's set to 14-bit

    def on_midi(msg):
        nonlocal coarse, fine_lsb, fine_mode
        if msg.type != "control_change" or msg.control not in (MOD_CC, MOD_FINE_CC):
            return
        # Fight the pop-up first; everything else can wait.
        for delay in FIGHT_DELAYS:
            if delay == 0:
                lk.restore_display()
            else:
                threading.Timer(delay, lk.restore_display).start()
        for timer in release_timers:
            timer.cancel()
        release_timers[:] = [threading.Timer(d, lk.restore_display) for d in RELEASE_DELAYS]
        for timer in release_timers:
            timer.start()

        if msg.control == MOD_CC:
            coarse, fine_lsb = msg.value, 0  # a new MSB resets the LSB (MIDI spec)
            if fine_mode:
                return  # in 14-bit mode the matching CC 33 follows within a millisecond
        else:
            fine_mode, fine_lsb = True, msg.value
        values.put((coarse, coarse << 7 | fine_lsb))

    lk.set_daw_mode(True)
    try:
        lk.show_lines("Hello from", "Python", "Mod strip is quiet")
        lk.midi_in.callback = on_midi
        print("DAW mode on. Move the Mod strip; Ctrl+C to quit.")
        while True:
            # A timeout keeps Ctrl+C responsive while waiting.
            try:
                value, fine = values.get(timeout=0.5)
            except queue.Empty:
                continue
            on_mod_change(lk, value, fine)
    except KeyboardInterrupt:
        pass
    finally:
        lk.midi_in.callback = None
        for timer in release_timers:
            timer.cancel()
        time.sleep(max(FIGHT_DELAYS) + 0.05)  # let in-flight restores finish before closing the port
        lk.close()
        print("\nLeft DAW mode.")


if __name__ == "__main__":
    main()

