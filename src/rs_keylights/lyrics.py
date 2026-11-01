from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .sng import Syllable

# A song's lyrics in lines, from its vocals part's syllables: one ending in "-" goes on into the next (the same
# word), one ending in "+" ends a line.

LINE_GAP = 4.0  # seconds without a syllable: a new line, where the chart marks none


@dataclass(frozen=True)
class Line:
    text: str
    start: float
    end: float
    syllables: tuple[tuple[float, int, int], ...]  # each one's start, and where it is in the text (from, to)


def lyric_lines(syllables: Iterable[Syllable]) -> tuple[Line, ...]:
    lines: list[Line] = []
    text, spans, end = "", [], 0.0

    def close() -> None:
        nonlocal text, spans
        if spans:
            lines.append(Line(text.rstrip(), spans[0][0], end, tuple(spans)))
        text, spans = "", []

    for syllable in syllables:
        if spans and syllable.start - end > LINE_GAP:
            close()
        word = syllable.text.rstrip("+-")
        spans.append((syllable.start, len(text), len(text) + len(word)))
        text, end = text + word, syllable.end
        if syllable.text.endswith("+"):
            close()
        elif not syllable.text.endswith("-"):
            text += " "
    close()
    return tuple(lines)
