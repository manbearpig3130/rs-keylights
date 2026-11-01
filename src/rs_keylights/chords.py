from __future__ import annotations

from typing import Iterable

# Chord names from their notes, for chords a chart leaves unnamed (a chart's own names come first).

NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
# Each kind of chord: its notes above the root (pitch classes) and what follows the root's name; the simpler first
QUALITIES = (
    ({0, 7}, "5"), ({0, 4, 7}, ""), ({0, 3, 7}, "m"), ({0, 4, 7, 10}, "7"), ({0, 4, 7, 11}, "maj7"),
    ({0, 3, 7, 10}, "m7"), ({0, 2, 7}, "sus2"), ({0, 5, 7}, "sus4"), ({0, 5, 7, 10}, "7sus4"), ({0, 3, 6}, "dim"),
    ({0, 3, 6, 9}, "dim7"), ({0, 3, 6, 10}, "m7b5"), ({0, 4, 8}, "aug"), ({0, 4, 7, 9}, "6"), ({0, 3, 7, 9}, "m6"),
    ({0, 2, 4, 7}, "add9"), ({0, 2, 4, 7, 10}, "9"), ({0, 2, 3, 7, 10}, "m9"), ({0, 4, 10}, "7"), ({0, 3, 10}, "m7"),
    ({0, 4, 11}, "maj7"),
)


def chord_name(pitches: Iterable[int]) -> str:
    """A chord's name: its root, what kind of chord, and its lowest note after a slash when that isn't the root;
    "" when its notes make none (one note, or two that aren't a fifth apart)."""
    notes = list(pitches)
    classes = {pitch % 12 for pitch in notes}
    if len(classes) < 2:
        return ""
    bass = min(notes) % 12
    found = [(root != bass, i, root, suffix) for root in classes for i, (shape, suffix) in enumerate(QUALITIES)
             if {(c - root) % 12 for c in classes} == shape]  # (on its lowest note first, then the simplest)
    if not found:
        return ""
    _slash, _simplest, root, suffix = min(found)
    return NAMES[root] + suffix + ("" if root == bass else f"/{NAMES[bass]}")
