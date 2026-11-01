from __future__ import annotations

import re

from .sng import Arrangement, SongTone

# The kinds of tone a song's arrangements switch between, from their names ("amonpurs_dist", after the song's own
# part), else their gear (an acoustic emulator, a distortion or a drive pedal), and the keyboard's tone for each.

KINDS = {  # each kind, and the keyboard's tone for it as they start: its General MIDI ones, at the notes' pitch (the
    # LK-170's own guitar tones sound an octave lower)
    "clean": 294, "clean lead": 294, "drive": 296, "distortion": 297, "lead": 297, "acoustic": 292, "nylon": 291,
    "12-string": 292, "piano": 267, "electric piano": 271, "organ": 283, "synth": 348, "bass": 300, "driven bass": 301,
    "synth bass": 305,
}
FIRST_KINDS = {  # (what they were at first: the keyboard's own tones, kept as they were, are moved to the above)
    "clean": 75, "clean lead": 76, "drive": 78, "distortion": 79, "lead": 80, "acoustic": 70, "nylon": 69,
    "12-string": 71, "piano": 2, "electric piano": 15, "organ": 43, "synth": 178, "bass": 85, "driven bass": 91,
    "synth bass": 89,
}
# Words in a tone's name for each kind, the first found (a short one as a whole word, a longer one in any)
NAMED = (
    ("synth bass", ("synthbass", "synbass", "synth bass")),
    ("12-string", ("12str", "12 str", "twelve")),
    ("nylon", ("nylon", "classical")),
    ("acoustic", ("acoustic", "acou", "acc", "unplugged")),
    ("electric piano", ("epiano", "rhodes", "wurli", "e piano")),
    ("piano", ("piano",)),
    ("organ", ("organ",)),
    ("synth", ("synth",)),
    ("clean lead", ("cleanlead", "clean lead", "clean solo")),
    ("clean", ("clean", "cln")),
    ("lead", ("lead", "solo")),
    ("distortion", ("dist", "heavy", "metal", "fuzz", "highgain", "hi gain")),
    ("drive", ("drive", "crunch", "od", "overdrive")),
    ("bass", ("bass",)),
)
GEARED = (  # and in its gear's (the pedals)
    ("acoustic", ("Acoustic",)),
    ("distortion", ("Distortion", "Fuzz", "Buzz", "ShredZone", "Metal")),
    ("drive", ("Drive", "Booster", "Overdrive")),
)


def tone_kind(tone: SongTone, bass: bool = False) -> str | None:
    """What kind of tone a song's tone is (one of KINDS; a bass part's are its bass kinds), or None if neither its
    name nor its gear says."""
    words = [w for w in re.split(r"[^a-z0-9]+", tone.name.lower()) if w]
    words = words[1:] if len(words) > 1 else words  # (the song's own part: "amonpurs")
    said = " ".join(words)
    kind = next((kind for kind, found in NAMED for word in found
                 if (word in words if len(word) <= 3 else word in said)), None)
    if kind in (None, "bass"):  # (no more than that: its gear)
        kind = next((kind for kind, found in GEARED for word in found for gear in tone.gear if word in gear), kind)
    if not bass or kind in ("synth bass", "acoustic"):
        return kind
    return ("synth bass" if kind == "synth" else "driven bass" if kind in ("drive", "distortion", "lead")
            else "bass")


def song_tones(arrangement: Arrangement, bass: bool = False) -> list[tuple[float, str, bool]]:
    """An arrangement's tones as the chart switches between them, for showing: from the start (0) its first, then
    each change: when, its kind (or, of no kind known, the tone's own name), and whether it's a kind known. None
    without changes."""
    if not arrangement.tone_changes or arrangement.base_tone is None:
        return []

    def said(tone: SongTone) -> tuple[str, bool]:
        kind = tone_kind(tone, bass)
        return (kind, True) if kind is not None else (tone.name, False)

    out = [(0.0, *said(arrangement.base_tone))]
    for at, slot in arrangement.tone_changes:
        tone = arrangement.tones[slot] if 0 <= slot < len(arrangement.tones) else None
        if tone is not None:
            out.append((at, *said(tone)))
    return out


def kind_changes(arrangement: Arrangement, bass: bool = False) -> list[tuple[float, str | None]]:
    """When an arrangement switches tone, and to what kind (None: back to its own tone, the one it starts with);
    tones of no kind it knows are left out."""
    out: list[tuple[float, str | None]] = []
    for at, slot in arrangement.tone_changes:
        tone = arrangement.tones[slot] if 0 <= slot < len(arrangement.tones) else None
        if tone is None:
            continue
        if tone == arrangement.base_tone:
            out.append((at, None))
        elif (kind := tone_kind(tone, bass)) is not None:
            out.append((at, kind))
    return out
