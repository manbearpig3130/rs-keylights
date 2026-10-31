from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Sequence

from .lights import KeyRange
from .sng import FRET_HAND_MUTE, Arrangement

# The key of a song from its notes: how long each pitch class sounds across its arrangements (the bass's
# counting more), and how long each is the lowest note of what plays, the riff's or chord's root. Every
# tonic and mode is scored on how much of the song its scale holds, how well the notes fit the major or
# minor key profile (Krumhansl and Kessler), and how much the song sits on its tonic: rock riffs say where
# home is more than the notes around them do. On well-known songs it gets the usual tonic 14 times in 16. A
# song that changes key gets the one it spends most time in.

MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "Dorian": (0, 2, 3, 5, 7, 9, 10),
    "Phrygian": (0, 1, 3, 5, 7, 8, 10),
    "Lydian": (0, 2, 4, 6, 7, 9, 11),
    "Mixolydian": (0, 2, 4, 5, 7, 9, 10),
}
# Each mode's profile, and a head start for plain major and minor: a mode has to fit clearly better
MODE_PROFILES = {"major": (MAJOR_PROFILE, 0.05), "minor": (MINOR_PROFILE, 0.05), "Dorian": (MINOR_PROFILE, 0.0),
                 "Phrygian": (MINOR_PROFILE, -0.01), "Lydian": (MAJOR_PROFILE, -0.01),
                 "Mixolydian": (MAJOR_PROFILE, 0.0)}
INSIDE, FIT, ROOTS = 1.0, 0.5, 0.7  # how much each counts in a key's score
OTHER = 0.35  # a note outside the scale sounding this long (of an average scale note) is one the song uses
CLOSE = 0.03  # a key with another tonic scoring this nearly as well is named too
BASS_WEIGHT = 1.5  # the bass's notes count more: they say where the tonic is
LONGEST = 2.0  # seconds a note counts for at most
SHARPS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
FLATS = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
FLAT_MAJORS = (5, 10, 3, 8, 1, 6)  # F, Bb, Eb, Ab, Db, Gb major: keys spelled with flats
TUNINGS = {0: "E", -1: "Eb", -2: "D", -3: "C#", -4: "C", -5: "B", -6: "Bb", -7: "A", 1: "F", 2: "F#"}


@dataclass(frozen=True)
class Key:
    tonic: int  # pitch class, C = 0
    mode: str  # one of SCALES
    other: tuple[int, ...] = ()  # pitch classes outside the scale that the song uses a lot
    alternative: str = ""  # another key that fits nearly as well

    @property
    def scale(self) -> tuple[int, ...]:
        """Its pitch classes, from the tonic up."""
        return tuple((self.tonic + step) % 12 for step in SCALES[self.mode])

    @property
    def name(self) -> str:
        return f"{self.spell(self.tonic)} {self.mode}"

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(self.spell(pc) for pc in self.scale)

    def spell(self, pitch_class: int) -> str:
        """A note's name, with flats in keys written with flats."""
        return (FLATS if _parent_major(self) in FLAT_MAJORS else SHARPS)[pitch_class % 12]

    def moved(self, semitones: int) -> Key:
        return Key((self.tonic + semitones) % 12, self.mode)


def find_key(arrangements: Sequence[Arrangement], bass: Sequence[bool] = ()) -> Key | None:
    """The key the notes of these arrangements suggest (bass: which are bass parts), or None without notes."""
    totals, roots = [0.0] * 12, [0.0] * 12  # seconds each pitch class sounds, and is the lowest sounding
    for i, arrangement in enumerate(arrangements):
        weight = BASS_WEIGHT if i < len(bass) and bass[i] else 1.0
        notes = [note for note in arrangement.notes if not note.mask & FRET_HAND_MUTE]  # dead notes: no pitch
        for n, note in enumerate(notes):
            gap = notes[n + 1].time - note.time if n + 1 < len(notes) else note.sustain
            length = min(max(note.sustain, min(gap, 1.0), 0.05), LONGEST) * weight
            for pitch in note.pitches:
                totals[pitch % 12] += length
            roots[min(note.pitches) % 12] += length
    if not any(totals):
        return None
    total, most = sum(totals), max(roots)
    scores = sorted(
        (
            INSIDE * sum(totals[(tonic + step) % 12] for step in SCALES[mode]) / total
            + FIT * _correlation(totals, [profile[(pc - tonic) % 12] for pc in range(12)])
            + ROOTS * roots[tonic] / most
            + head_start,
            tonic,
            mode,
        )
        for tonic in range(12)
        for mode, (profile, head_start) in MODE_PROFILES.items()
    )[::-1]
    best, tonic, mode = scores[0]
    scale = SCALES[mode]
    average = sum(totals[(tonic + step) % 12] for step in scale) / len(scale)
    other = tuple(pc for pc in range(12) if (pc - tonic) % 12 not in scale and totals[pc] >= OTHER * average)
    alternative = next((Key(t, m).name for score, t, m in scores[1:] if t != tonic and best - score <= CLOSE), "")
    return Key(tonic, mode, other, alternative)


def tuning_name(tuning: Sequence[int], capo: int = 0) -> str:
    """E standard, Eb standard, Drop D, Drop C...; else the strings' offsets (as Rocksmith lists them)."""
    strings = list(tuning)
    if not strings:
        name = ""
    elif len(set(strings)) == 1:
        name = f"{TUNINGS.get(strings[0], f'{strings[0]:+d}')} standard"
    elif strings[0] == strings[1] - 2 and len(set(strings[1:])) == 1:
        name = f"Drop {FLATS[(4 + strings[0]) % 12]}"
    else:
        name = " ".join(f"{s:+d}" if s else "0" for s in strings)
    return name + (f", capo {capo}" if capo > 0 else "")


def shapes(key: Key, arrangement: Arrangement) -> tuple[Key, str] | None:
    """For a guitar tuned with every string moved the same (or with a capo): the key its fingers play, as in
    standard tuning, and the tuning ("B standard, capo 2"); None in standard tuning or a drop tuning."""
    moves = set(arrangement.tuning)
    capo = max(arrangement.capo, 0)
    if len(moves) != 1 or (moves == {0} and not capo):
        return None
    move = moves.pop()
    tuning = f"{TUNINGS.get(move, f'{move:+d}')} standard" + (f", capo {capo}" if capo else "")
    return key.moved(-move - capo), tuning


def scale_show(key: Key, keys: KeyRange = KeyRange(), step: float = 0.1, hold: float = 1.0) -> list[tuple[float, tuple[int, ...]]]:
    """The scale on the keys, as frames (seconds into it, keys lit; the last one's time is its length, to repeat
    it): it climbs the whole keyboard, three keys lit at a time with each tonic lit a little longer, holds the
    tonic chord (tonic, third, fifth, octave) at the top, comes back down, and holds it at the bottom."""
    steps = SCALES[key.mode]
    pitch_classes = {(key.tonic + s) % 12 for s in steps}
    notes = [p for p in range(keys.lowest, keys.highest + 1) if p % 12 in pitch_classes]
    tonics = [p for p in notes if p % 12 == key.tonic]
    frames: list[tuple[float, tuple[int, ...]]] = []
    at = 0.0

    def run(pitches: list[int]) -> None:
        nonlocal at
        for i in range(len(pitches)):
            trail = pitches[max(0, i - 2): i + 1]
            tonic = [p for p in pitches[max(0, i - 4): i] if p % 12 == key.tonic and p not in trail][-1:]
            frames.append((at, tuple(sorted(trail + tonic))))
            at += step

    def chord(tonic: int) -> None:
        nonlocal at
        frames.append((at, (tonic, tonic + steps[2], tonic + steps[4], tonic + 12)))
        at += hold

    run(notes)
    chord(max(t for t in tonics if t + 12 <= keys.highest))
    run(notes[::-1])
    chord(tonics[0])
    frames.append((at, ()))
    return frames


def _parent_major(key: Key) -> int:
    """The major key with the same notes."""
    steps = set(SCALES[key.mode])
    return next((key.tonic + root) % 12 for root in range(12)
                if {(step - root) % 12 for step in steps} == set(SCALES["major"]))


def _correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    dx, dy = [x - mx for x in xs], [y - my for y in ys]
    spread = sqrt(sum(d * d for d in dx) * sum(d * d for d in dy))
    return sum(a * b for a, b in zip(dx, dy)) / spread if spread else 0.0
