from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Sequence

from .chords import chord_name
from .sng import BASS, GUITAR, Arrangement, Section
from .songkey import SCALES, Key

# A song's theory, for the page's theory card: its chords as numbers in its key (Roman numerals, what each does there,
# and what's borrowed or outside), each section's progression (as a loop, named when it's a well-known one), the scale
# its riffs use, and for a part's notes why each works where it's played (a note of the chord under it, of the key's
# scale, or outside it). From the charts: the chords' names (else their notes), and the notes.

ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII")
LETTERS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
CHORD_NAME = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)(.*?)(?:/([A-Ga-g][#b♯♭]?))?\s*$")
# A note against the key's tonic, and against a chord's root
INTERVALS = ("1", "♭2", "2", "♭3", "3", "4", "♭5", "5", "♭6", "6", "♭7", "7")
CHORD_TONES = {0: "R", 1: "♭9", 2: "9", 3: "♭3", 4: "3", 5: "11", 6: "♭5", 7: "5", 8: "♯5", 9: "6", 10: "♭7",
               11: "7"}
# What a chord does in its key, by its degree (0: the tonic's): home, near it, away from it, or pulling back to it
FUNCTIONS = {0: "home", 2: "near home", 5: "near home", 1: "away", 3: "away", 4: "pulls home", 6: "pulls home"}
# Well-known progressions: each chord's root in semitones above the key's tonic, in a major or a minor key (any
# chord quality: power chords count), and what it's called
PATTERNS = (
    ("major", (0, 7, 9, 5), "I–V–vi–IV: the pop progression"),
    ("major", (0, 9, 5, 7), "I–vi–IV–V: the '50s progression"),
    ("major", (2, 7, 0), "ii–V–I: the jazz turnaround"),
    ("major", (0, 5, 7), "I–IV–V: the three-chord trick"),
    ("major", (0, 10, 5), "I–♭VII–IV: the rock (Mixolydian) progression"),
    ("major", (8, 10, 0), "♭VI–♭VII–I: the 'Mario cadence'"),
    ("minor", (0, 8, 3, 10), "i–♭VI–♭III–♭VII: the 'epic' progression"),
    ("minor", (0, 10, 8, 7), "i–♭VII–♭VI–V: the Andalusian cadence"),
    ("minor", (0, 8, 10), "i–♭VI–♭VII: the Aeolian climb"),
    ("minor", (0, 10, 8, 10), "i–♭VII–♭VI–♭VII: the Aeolian vamp"),
    ("minor", (0, 5, 7), "i–iv–v: the minor three-chord trick"),
    ("minor", (0, 1), "i–♭II: the Phrygian riff"),
    ("minor", (0, 3, 5), "i–♭III–iv: the minor rock climb"),
)
MINORISH = ("minor", "Dorian", "Phrygian")
# The scales a section's riffs might use, from its root: the fewest notes that cover what it plays win
RIFF_SCALES = (
    ("minor pentatonic", (0, 3, 5, 7, 10)),
    ("major pentatonic", (0, 2, 4, 7, 9)),
    ("blues scale", (0, 3, 5, 6, 7, 10)),
    ("natural minor", (0, 2, 3, 5, 7, 8, 10)),
    ("major", (0, 2, 4, 5, 7, 9, 11)),
    ("Phrygian", (0, 1, 3, 5, 7, 8, 10)),
    ("Dorian", (0, 2, 3, 5, 7, 9, 10)),
    ("Mixolydian", (0, 2, 4, 5, 7, 9, 10)),
    ("harmonic minor", (0, 2, 3, 5, 7, 8, 11)),
    ("Phrygian dominant", (0, 1, 4, 5, 7, 8, 10)),
    ("Locrian", (0, 1, 3, 5, 6, 8, 10)),
    ("Lydian", (0, 2, 4, 6, 7, 9, 11)),
)
COVERS = 0.9  # a scale covering this much of a section's notes fits it
SMALLER = 0.012  # a note fewer in a scale counts this much (the simpler wins, covering about as much)
OWN_ROOT = 0.02  # the key's tonic as the riffs' root counts this much
OUTSIDE = 0.04  # a note this much of a section's, outside its scale, is said ("+ ♭5")
HELD = 2.0  # seconds a chord's harmony holds on after its shape ends, at most (the notes after it still over it)


@dataclass(frozen=True)
class Chorded:
    """A chord as the theory card has it: when, its name, its root (None: unknown) and notes (pitch classes), its
    numeral in the key, what it does, and what's borrowed or outside about it."""
    start: float
    end: float
    name: str
    root: int | None
    notes: frozenset[int]
    numeral: str
    function: str
    tag: str


def parse_chord(name: str) -> tuple[int, str] | None:
    """A chord name's root (pitch class) and kind: "major", "minor", "dim", "aug", "power" or "sus"."""
    found = CHORD_NAME.match(name or "")
    if not found:
        return None
    letter, accidental, rest, _bass = found.groups()
    root = (LETTERS[letter.upper()] + (1 if accidental in "#♯" and accidental else -1 if accidental else 0)) % 12
    rest = rest.strip()
    if rest.startswith(("maj", "M", "Δ")):
        kind = "major"
    elif rest.startswith(("dim", "°", "m7b5", "ø")):
        kind = "dim"
    elif rest.startswith(("aug", "+")):
        kind = "aug"
    elif rest.startswith(("m", "-", "min")):
        kind = "minor"
    elif rest.startswith("5"):
        kind = "power"
    elif rest.startswith("sus"):
        kind = "sus"
    else:
        kind = "major"
    return root, kind


MAJOR = (0, 2, 4, 5, 7, 9, 11)


def numeral(key: Key, root: int, kind: str) -> tuple[str, str, str]:
    """A chord's Roman numeral in a key, against the major scale whatever the key's mode (i–♭VI–♭VII, as rock's are
    written: songs compare, however their mode is heard), lower case for minor and diminished; what it does there
    (FUNCTIONS; outside the key: "colour"); and what's borrowed or outside about it ("" if nothing). A power chord
    takes the case the key's own chord there has."""
    steps = SCALES[key.mode]
    rel = (root - key.tonic) % 12
    minor_key = key.mode in MINORISH
    if rel in MAJOR:
        number, accidental = MAJOR.index(rel), ""
    elif (rel + 1) % 12 in MAJOR:
        number, accidental = MAJOR.index((rel + 1) % 12), "♭"
    else:
        number, accidental = MAJOR.index((rel - 1) % 12), "♯"
    inside = rel in steps
    own = None
    if inside:
        degree = steps.index(rel)
        third = (steps[(degree + 2) % 7] - steps[degree]) % 12
        fifth = (steps[(degree + 4) % 7] - steps[degree]) % 12
        own = "minor" if (third, fifth) == (3, 7) else "dim" if (third, fifth) == (3, 6) else \
            "aug" if (third, fifth) == (4, 8) else "major"
    if kind in ("power", "sus"):
        lower = own in ("minor", "dim")
        text = accidental + (ROMAN[number].lower() if lower else ROMAN[number])
    else:
        text = accidental + (ROMAN[number].lower() if kind in ("minor", "dim") else ROMAN[number]) + \
            ("°" if kind == "dim" else "+" if kind == "aug" else "")
    if not inside:
        if rel == 1:
            tag = "outside the key: ♭II, the dark Phrygian sound"
        elif rel == 6:
            tag = "outside the key: the tritone, as far from home as it gets"
        elif not minor_key and rel in (3, 8, 10):
            tag = f"borrowed from {key.spell(key.tonic)} minor"
        elif minor_key and rel == 11:
            tag = "outside the key: the leading tone's chord (harmonic minor)"
        elif minor_key and rel == 9:
            tag = "outside the key: Dorian's raised 6th"
        elif minor_key and rel == 4:
            tag = f"borrowed from {key.spell(key.tonic)} major"
        else:
            tag = "outside the key"
        return text, "colour", tag
    degree = steps.index(rel)
    tag = ""
    if kind in ("major", "minor") and own in ("major", "minor") and kind != own:
        if minor_key and rel == 7 and kind == "major":
            tag = "major V: from harmonic minor, pulling hard to i"
        elif minor_key and rel == 5 and kind == "major":
            tag = "major IV: borrowed from Dorian"
        elif not minor_key and kind == "minor":
            tag = f"minor here: borrowed from {key.spell(key.tonic)} minor"
        else:
            tag = f"{kind} here (the key's is {own}): borrowed"
    return text, FUNCTIONS[degree], tag


def chord_pitches(arrangement: Arrangement, frets: Sequence[int], bass: bool) -> list[int]:
    """A chord shape's pitches (each string's fret, -1 not played), in its arrangement's tuning."""
    open_strings = BASS if bass else GUITAR
    offsets = tuple(arrangement.tuning) + (0,) * (len(open_strings) - len(arrangement.tuning))
    capo = arrangement.capo
    return sorted(open_strings[s] + offsets[s] + (capo if f == 0 and capo > 0 else f)
                  for s, f in enumerate(frets) if 0 <= s < len(open_strings) and f >= 0)


def chords_of(key: Key, arrangement: Arrangement, bass: bool = False) -> list[Chorded]:
    """An arrangement's chords as numbers in the key: one after another the same, one (its name's, else its notes'
    root and kind)."""
    out: list[Chorded] = []
    for chord in arrangement.chords:
        pitches = chord_pitches(arrangement, chord.frets, bass)
        heard = chord_name(pitches)  # (its notes in the tuning: charts can name a shape as if in E standard)
        name = chord.name.strip()
        named, played = parse_chord(name), parse_chord(heard)
        if played is not None and (named is None or named[0] != played[0]):
            name, named = heard, played
        name = name or heard
        parsed = named
        notes = frozenset(p % 12 for p in pitches)
        if parsed is None:
            if out and out[-1].name == name:
                continue
            out.append(Chorded(chord.start, chord.end, name or "?", None, notes, "?", "", ""))
            continue
        if out and out[-1].name == name and chord.start - out[-1].end < HELD:
            out[-1] = Chorded(out[-1].start, chord.end, name, out[-1].root, out[-1].notes | notes, out[-1].numeral,
                              out[-1].function, out[-1].tag)
            continue
        root, kind = parsed
        if len(notes) < 2:  # (no shape: its name's notes, root and 5th at least)
            notes = frozenset({root, (root + 7) % 12} | ({(root + 3) % 12} if kind == "minor" else
                                                         {(root + 4) % 12} if kind == "major" else set()))
        text, function, tag = numeral(key, root, kind)
        out.append(Chorded(chord.start, chord.end, name, root, notes, text, function, tag))
    return out


LONGEST_LOOP = 8  # chords a phrase that repeats has at most


def phrases(chords: Sequence[Chorded], start: float, end: float) -> list[dict[str, Any]]:
    """The chords a section goes through, as the phrases that repeat in it: each one's numerals once round, how many
    times round, its roots (for pattern_of) and when it starts and ends. The phrase covering the most of what's
    next, each time (the shortest of those): "(i – ♭III – ii) ×12", then "(v – ♭VI) ×3"."""
    seq: list[Chorded] = []
    for c in chords:
        if start <= c.start < end and c.numeral != "?" and (not seq or seq[-1].numeral != c.numeral):
            seq.append(c)
    names = [c.numeral for c in seq]
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(names):
        best_p, best_r = 1, 1
        for p in range(1, min(LONGEST_LOOP, len(names) - i) + 1):
            r = 1
            while names[i + r * p:i + (r + 1) * p] == names[i:i + p]:
                r += 1
            if r >= 2 and p * r > best_p * best_r:
                best_p, best_r = p, r
        if best_r == 1 and best_p == 1 and out and out[-1]["times"] > 1 and out[-1]["loop"][0] == names[i]:
            out[-1]["end"] = round(seq[i].end, 3)  # (a loop landing home again after it: part of it)
        elif best_r == 1 and out and out[-1]["times"] == 1 and len(out[-1]["loop"]) < LONGEST_LOOP:
            last = out[-1]  # (chords that don't repeat: one run of them)
            last["loop"].append(names[i])
            last["roots"].append(seq[i].root if seq[i].root is not None else -1)
            last["end"] = round(seq[i].end, 3)
        else:
            out.append({"loop": names[i:i + best_p], "times": best_r,
                        "roots": [c.root if c.root is not None else -1 for c in seq[i:i + best_p]],
                        "start": round(seq[i].start, 3), "end": round(seq[i + best_p * best_r - 1].end, 3)})
        i += best_p * best_r
    return out


def pattern_of(key: Key, roots: Sequence[int]) -> str | None:
    """The well-known progression a loop is (any chord first in it), or has in it; None if none."""
    rel = tuple((r - key.tonic) % 12 for r in roots if r >= 0)
    if len(rel) < 2:
        return None
    mode = "minor" if key.mode in MINORISH else "major"
    loop = rel * 2
    for pattern_mode, notes, said in PATTERNS:
        if pattern_mode != mode:
            continue
        if len(notes) == len(rel) and any(loop[i:i + len(notes)] == notes for i in range(len(rel))):
            return said
    for pattern_mode, notes, said in PATTERNS:
        if pattern_mode == mode and len(notes) < len(rel) and len(notes) >= 3 and \
                any(loop[i:i + len(notes)] == notes for i in range(len(rel))):
            return f"has {said}"
    return None


def riff_scale(key: Key, arrangements: Sequence[Arrangement], start: float, end: float) -> dict[str, Any] | None:
    """The scale a section's notes use (all the parts'): its root, its name, and the notes it plays outside it;
    None without notes. Just a root and its 5th: power chords on it."""
    weights = [0.0] * 12
    for arrangement in arrangements:
        for note in arrangement.notes:
            if start <= note.time < end:
                for pitch in note.pitches:
                    weights[pitch % 12] += 1.0
    total = sum(weights)
    if total < 4:
        return None
    used = [pc for pc in range(12) if weights[pc] / total >= OUTSIDE]
    heaviest = max(range(12), key=lambda pc: weights[pc])
    if len(used) <= 2 and all((pc - heaviest) % 12 in (0, 7) for pc in used):
        return {"root": key.spell(heaviest), "scale": "power chords", "outside": [],
                "said": f"{key.spell(heaviest)}5: just root and 5th"}
    roots = [key.tonic, heaviest] + [pc for pc in range(12) if pc not in (key.tonic, heaviest)]
    best: tuple[float, int, str, tuple[int, ...]] | None = None
    for root in roots:
        for name, steps in RIFF_SCALES:
            inside = {(root + s) % 12 for s in steps}
            covered = sum(weights[pc] for pc in inside) / total
            score = covered - SMALLER * len(steps) + (OWN_ROOT if root == key.tonic else 0.0)
            if covered >= COVERS and (best is None or score > best[0]):
                best = (score, root, name, steps)
    if best is None:  # (nothing covers it: the key's own scale, and what's outside it)
        best = (0.0, key.tonic, {"minor": "natural minor"}.get(key.mode, key.mode), SCALES[key.mode])
    _score, root, name, steps = best
    inside = {(root + s) % 12 for s in steps}
    outside = [INTERVALS[(pc - root) % 12] for pc in sorted(range(12), key=lambda pc: (pc - root) % 12)
               if pc not in inside and weights[pc] / total >= OUTSIDE]
    said = f"{key.spell(root)} {name}" + (" + " + " ".join(outside) if outside else "")
    return {"root": key.spell(root), "scale": name, "outside": outside, "said": said}


def notes_why(key: Key, melody: Arrangement, chords: Sequence[Chorded],
              bass: Arrangement | None = None) -> list[list[Any]]:
    """Why each of a part's single notes works: [start, end, its name, what it is (against the chord under it: "R",
    "♭3", "5"...; else against the key: "2", "♭5"...), and how ("chord": a note of the chord under it; "scale": the
    key's; "outside": neither)]. The chord under it: the harmony's (held on after its shape a while), else the bass
    note sounding."""
    starts = [c.start for c in chords]
    bass_notes = [n for n in bass.notes if n.pitches] if bass is not None else []
    bass_starts = [n.time for n in bass_notes]
    scale = set(key.scale)
    out: list[list[Any]] = []
    for note in melody.notes:
        if len(note.pitches) != 1:
            continue
        pc = note.pitches[0] % 12
        root: int | None = None
        tones: frozenset[int] = frozenset()
        i = bisect_right(starts, note.time) - 1
        if i >= 0:
            c = chords[i]
            following = chords[i + 1].start if i + 1 < len(chords) else c.end + HELD
            if note.time < min(following, c.end + HELD) and c.root is not None:
                root, tones = c.root, c.notes
        if root is None and bass_notes:
            j = bisect_right(bass_starts, note.time) - 1
            if j >= 0 and note.time <= bass_notes[j].time + max(bass_notes[j].sustain, 0.3):
                root = bass_notes[j].pitches[0] % 12
                tones = frozenset({root, (root + 7) % 12})
        if root is not None and pc in tones:
            label, how = CHORD_TONES[(pc - root) % 12], "chord"
        elif pc in scale:
            label, how = INTERVALS[(pc - key.tonic) % 12], "scale"
        else:
            label, how = INTERVALS[(pc - key.tonic) % 12], "outside"
        out.append([round(note.time, 3), round(note.time + max(note.sustain, 0.12), 3), key.spell(pc), label, how])
    return out


def sections_of(key: Key, sections: Sequence[Section], named: Sequence[dict[str, Any]], chords: Sequence[Chorded],
                arrangements: Sequence[Arrangement]) -> list[dict[str, Any]]:
    """Each section's progression (its phrases, each with the progression it is, if it's a well-known one) and its
    riffs' scale."""
    out = []
    for section, shown in zip(sections, named):
        found = phrases(chords, section.start, section.end)
        for phrase in found:
            phrase["pattern"] = pattern_of(key, phrase.pop("roots"))
        out.append({"name": shown["name"], "start": round(section.start, 3), "end": round(section.end, 3),
                    "phrases": found, "scale": riff_scale(key, arrangements, section.start, section.end)})
    return out


def key_named(name: str) -> Key | None:
    """A key from its name ("E minor", "F# Dorian")."""
    tonic, _, mode = (name or "").partition(" ")
    parsed = parse_chord(tonic)
    return Key(parsed[0], mode) if parsed is not None and mode in SCALES else None


def chord_key(keys: Sequence[Key], arrangement: Arrangement, bass: bool = False) -> Key:
    """Of keys that fit a song's notes about as well (the key found, and the one nearly as likely), the one its
    chords say: home's the chord it's on longest, and the one it starts or ends on."""
    if len(keys) < 2 or not arrangement.chords:
        return keys[0]
    time = [0.0] * 12
    rooted: list[int] = []
    for chord in arrangement.chords:
        parsed = parse_chord(chord_name(chord_pitches(arrangement, chord.frets, bass))) or parse_chord(chord.name)
        if parsed is not None:
            time[parsed[0]] += max(chord.end - chord.start, 0.1)
            rooted.append(parsed[0])
    if not rooted:
        return keys[0]
    total = sum(time)

    def score(key: Key) -> float:
        return time[key.tonic] / total + 0.15 * (rooted[0] == key.tonic) + 0.15 * (rooted[-1] == key.tonic)
    return max(keys, key=lambda k: (score(k), k is keys[0]))
