from __future__ import annotations

import unicodedata

# Pictures for a Launchkey MK4's screen, sent as a bitmap (Programmer's Reference Guide: 128 by 64 pixels, 19
# bytes a row, 7 pixels a byte, the highest bit leftmost). It has no font of its own for them, so text is drawn
# here, in a 5 by 7 one (narrower for some characters; lower-case letters reaching a row below). It only holds
# one picture at a time.

WIDTH, HEIGHT = 128, 64
ROW_BYTES = 19

# Each character's rows, top down, "#" lit
_GLYPHS = {
    " ": "...|...|...|...|...|...|...",
    "0": ".###.|#...#|#...#|#...#|#...#|#...#|.###.",
    "1": "..#..|.##..|..#..|..#..|..#..|..#..|.###.",
    "2": ".###.|#...#|....#|...#.|..#..|.#...|#####",
    "3": "#####|...#.|..#..|...#.|....#|#...#|.###.",
    "4": "...#.|..##.|.#.#.|#..#.|#####|...#.|...#.",
    "5": "#####|#....|####.|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|####.|#...#|#...#|.###.",
    "7": "#####|....#|...#.|..#..|.#...|.#...|.#...",
    "8": ".###.|#...#|#...#|.###.|#...#|#...#|.###.",
    "9": ".###.|#...#|#...#|.####|....#|...#.|.##..",
    "A": ".###.|#...#|#...#|#####|#...#|#...#|#...#",
    "B": "####.|#...#|#...#|####.|#...#|#...#|####.",
    "C": ".###.|#...#|#....|#....|#....|#...#|.###.",
    "D": "####.|#...#|#...#|#...#|#...#|#...#|####.",
    "E": "#####|#....|#....|####.|#....|#....|#####",
    "F": "#####|#....|#....|####.|#....|#....|#....",
    "G": ".###.|#...#|#....|#.###|#...#|#...#|.####",
    "H": "#...#|#...#|#...#|#####|#...#|#...#|#...#",
    "I": "###|.#.|.#.|.#.|.#.|.#.|###",
    "J": "..###|...#.|...#.|...#.|...#.|#..#.|.##..",
    "K": "#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#",
    "L": "#....|#....|#....|#....|#....|#....|#####",
    "M": "#...#|##.##|#.#.#|#.#.#|#...#|#...#|#...#",
    "N": "#...#|#...#|##..#|#.#.#|#..##|#...#|#...#",
    "O": ".###.|#...#|#...#|#...#|#...#|#...#|.###.",
    "P": "####.|#...#|#...#|####.|#....|#....|#....",
    "Q": ".###.|#...#|#...#|#...#|#.#.#|#..#.|.##.#",
    "R": "####.|#...#|#...#|####.|#.#..|#..#.|#...#",
    "S": ".####|#....|#....|.###.|....#|....#|####.",
    "T": "#####|..#..|..#..|..#..|..#..|..#..|..#..",
    "U": "#...#|#...#|#...#|#...#|#...#|#...#|.###.",
    "V": "#...#|#...#|#...#|#...#|#...#|.#.#.|..#..",
    "W": "#...#|#...#|#...#|#.#.#|#.#.#|#.#.#|.#.#.",
    "X": "#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#",
    "Y": "#...#|#...#|.#.#.|..#..|..#..|..#..|..#..",
    "Z": "#####|....#|...#.|..#..|.#...|#....|#####",
    "a": ".....|.....|.###.|....#|.####|#...#|.####",
    "b": "#....|#....|#.##.|##..#|#...#|#...#|####.",
    "c": ".....|.....|.###.|#....|#....|#...#|.###.",
    "d": "....#|....#|.##.#|#..##|#...#|#...#|.####",
    "e": ".....|.....|.###.|#...#|#####|#....|.###.",
    "f": "..##|.#..|.#..|###.|.#..|.#..|.#..",
    "g": ".....|.....|.####|#...#|#...#|.####|....#|.###.",
    "h": "#....|#....|#.##.|##..#|#...#|#...#|#...#",
    "i": "#|.|#|#|#|#|#",
    "j": "..#|...|.##|..#|..#|..#|..#|##.",
    "k": "#...|#...|#..#|#.#.|##..|#.#.|#..#",
    "l": "#|#|#|#|#|#|#",
    "m": ".....|.....|##.#.|#.#.#|#.#.#|#...#|#...#",
    "n": ".....|.....|#.##.|##..#|#...#|#...#|#...#",
    "o": ".....|.....|.###.|#...#|#...#|#...#|.###.",
    "p": ".....|.....|####.|#...#|#...#|####.|#....|#....",
    "q": ".....|.....|.####|#...#|#...#|.####|....#|....#",
    "r": ".....|.....|#.##.|##..#|#....|#....|#....",
    "s": ".....|.....|.####|#....|.###.|....#|####.",
    "t": ".#..|.#..|####|.#..|.#..|.#..|..##",
    "u": ".....|.....|#...#|#...#|#...#|#..##|.##.#",
    "v": ".....|.....|#...#|#...#|#...#|.#.#.|..#..",
    "w": ".....|.....|#...#|#...#|#.#.#|#.#.#|.#.#.",
    "x": ".....|.....|#...#|.#.#.|..#..|.#.#.|#...#",
    "y": ".....|.....|#...#|#...#|#...#|.####|....#|.###.",
    "z": ".....|.....|#####|...#.|..#..|.#...|#####",
    ":": ".|#|#|.|#|#|.",
    "/": "....#|....#|...#.|..#..|.#...|#....|#....",
    ".": ".|.|.|.|.|.|#",
    ",": "..|..|..|..|..|.#|.#|#.",
    "-": ".....|.....|.....|#####|.....|.....|.....",
    "+": ".....|..#..|..#..|#####|..#..|..#..|.....",
    "%": ".#...|#.#.#|.#.#.|..#..|.#.#.|#.#.#|...#.",
    "'": "#|#|.|.|.|.|.",
    "(": ".#|#.|#.|#.|#.|#.|.#",
    ")": "#.|.#|.#|.#|.#|.#|#.",
    "!": "#|#|#|#|#|.|#",
    "&": ".##..|#..#.|#.#..|.#...|#.#.#|#..#.|.##.#",
    "?": ".###.|#...#|....#|...#.|..#..|.....|..#..",
    "#": ".#.#.|.#.#.|#####|.#.#.|#####|.#.#.|.#.#.",
    "♭": "#...|#...|#...|###.|#..#|#.#.|##..",
    '"': "#.#|#.#|...|...|...|...|...",
    ";": "..|.#|.#|..|.#|.#|#.",
    "*": ".....|..#..|#.#.#|.###.|#.#.#|..#..|.....",
    "_": ".....|.....|.....|.....|.....|.....|#####",
    "=": ".....|.....|#####|.....|#####|.....|.....",
    "[": "##|#.|#.|#.|#.|#.|##",
    "]": "##|.#|.#|.#|.#|.#|##",
    "●": ".....|.###.|#####|#####|#####|.###.|.....",  # (Record)
    "▲": ".......|.......|...#...|..###..|.#####.|#######|.......",  # (the up arrow buttons)
    "▼": ".......|#######|.#####.|..###..|...#...|.......|.......",  # (and the down ones)
}
_FONT = {c: glyph.split("|") for c, glyph in _GLYPHS.items()}
_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "..."})
_KNOB = "...#####...|..#.....#..|.#.......#.|#.........#|#.........#|#.........#|#.........#|#.........#|" \
        ".#.......#.|..#.....#..|...#####..."  # a knob's outline, a character in it
_UP = "...#...|..###..|.#####.|#######"  # the up arrow button
SIGNS = {"playing": "#.....|###...|#####.|######|#####.|###...|#.....", "paused": "##.##|##.##|##.##|##.##|##.##|##.##|##.##",
         "stopped": "......|######|######|######|######|######|######"}  # a song's, as a player shows them
SONG_BAR = 10  # where the song's progress line starts, after its sign
MUTED = "...#......|..##.#...#|####..#.#.|####...#..|####..#.#.|..##.#...#|...#......"  # a speaker, crossed out
SLOWEST, FASTEST = 0.25, 1.5  # the speeds at the ends of the pitch strip


class Bitmap:
    def __init__(self) -> None:
        self.pixels = [[False] * WIDTH for _ in range(HEIGHT)]

    def fill(self, x: int, y: int, width: int, height: int, on: bool = True) -> None:
        """A rectangle, lit (or not); what's off the screen left out."""
        for row in range(max(y, 0), min(y + height, HEIGHT)):
            for column in range(max(x, 0), min(x + width, WIDTH)):
                self.pixels[row][column] = on

    def frame(self, x: int, y: int, width: int, height: int) -> None:
        """A rectangle's outline."""
        self.fill(x, y, width, 1)
        self.fill(x, y + height - 1, width, 1)
        self.fill(x, y, 1, height)
        self.fill(x + width - 1, y, 1, height)

    def text(self, x: int, y: int, text: str, scale: int = 1) -> int:
        """Text from its top left (y: the top of capitals), each pixel scale by scale; where it ends."""
        for c in _plain(text):
            glyph = _FONT.get(c, _FONT["?"])
            for row, line in enumerate(glyph):
                for column, pixel in enumerate(line):
                    if pixel == "#":
                        self.fill(x + column * scale, y + row * scale, scale, scale)
            x += (len(glyph[0]) + 1) * scale
        return x

    def shape(self, x: int, y: int, rows: str) -> None:
        """A shape given as rows (as the font's)."""
        for row, line in enumerate(rows.split("|")):
            for column, pixel in enumerate(line):
                if pixel == "#":
                    self.fill(x + column, y + row, 1, 1)

    def hatch(self, x: int, y: int, width: int, height: int) -> None:
        """A rectangle half lit, in a checkerboard: greyed."""
        for row in range(max(y, 0), min(y + height, HEIGHT)):
            for column in range(max(x, 0), min(x + width, WIDTH)):
                self.pixels[row][column] = (row + column) % 2 == 0

    def knob(self, x: int, y: int, label: str) -> None:
        """A knob, 11 pixels across, with a character in it."""
        self.shape(x, y, _KNOB)
        self.text(x + 3, y + 2, label)

    def data(self) -> list[int]:
        """As the Launchkey takes it: 19 bytes a row, 7 pixels a byte, the highest bit leftmost."""
        data = []
        for row in self.pixels:
            for byte in range(ROW_BYTES):
                value = 0
                for bit in range(7):
                    x = byte * 7 + bit
                    if x < WIDTH and row[x]:
                        value |= 1 << (6 - bit)
                data.append(value)
        return data


def text_width(text: str, scale: int = 1) -> int:
    return sum((len(_FONT.get(c, _FONT["?"])[0]) + 1) * scale for c in _plain(text)) - scale if text else 0


def wrap(text: str, width: int, lines: int) -> list[str]:
    """Text in lines width pixels wide at most, broken between words (or in a word too long for a line), lines of
    them at most: the last ending in "..." if it doesn't all fit."""
    found: list[str] = []
    line = ""
    for word in text.split():
        if text_width(f"{line} {word}" if line else word) <= width:
            line = f"{line} {word}" if line else word
            continue
        if line:
            found.append(line)
        while text_width(word) > width:
            cut = max(n for n in range(1, len(word) + 1) if n == 1 or text_width(word[:n]) <= width)
            found.append(word[:cut])
            word = word[cut:]
        line = word
    if line:
        found.append(line)
    if len(found) > lines:
        last = " ".join(found[lines - 1:])
        while last and text_width(last + "...") > width:
            last = last[:-1]
        found = found[:lines - 1] + [last.rstrip() + "..."]
    return found


def _plain(text: str) -> str:
    """Text as the font has it: accents left off, quotes and dashes plain."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.translate(_LOOKALIKES)) if not unicodedata.combining(c))


def _tone_picture(title: str, number: int | None, group: str, name: str, muted: bool) -> Bitmap:
    """A tone the knobs are on: what it's for, its number (None: none, as for "Same tone") and group, and its
    name; what they do goes below (the corner left for the crossed-out speaker, muted)."""
    picture = Bitmap()
    if muted:
        picture.shape(WIDTH - 11, 51, MUTED)
    picture.text(1, 1, title)
    picture.text(1, 12, group if number is None else f"{number:03d}  {group}")
    picture.text(1, 22, name)
    picture.fill(0, 32, WIDTH, 1)
    return picture


def keys_picture(number: int, group: str, name: str, muted: bool = False) -> Bitmap:
    """Knob 7 on the tone the Launchkey's keys play: that tone, and what knobs 7 and 8 do."""
    picture = _tone_picture("Launchkey keys", number, group, name, muted)
    picture.knob(0, 37, "7")
    after = picture.text(14, 39, "Tone")
    picture.knob(after + 8, 37, "8")
    picture.text(after + 22, 39, "Group")
    return picture


def parts_picture(title: str, number: int | None, group: str, name: str, knobs: int, field: int,
                  muted: bool = False) -> Bitmap:
    """The knobs on the song's arrangements' tones: the one turned's (title: whose and which, e.g. "Lead tone"),
    what the knobs (as many as there are arrangements, and 8) do, and which of its tones they're on (field: the
    tone, palm mutes or low notes, a square each at the top right, Func going through them)."""
    picture = _tone_picture(wrap(title, WIDTH - 24, 1)[0], number, group, name, muted)
    picture.knob(0, 37, "1")
    after = 11
    if knobs > 1:
        after = picture.text(13, 39, "-") + 1
        picture.knob(after, 37, str(min(knobs, 9)))
        after += 11
    after = picture.text(after + 3, 39, "Tones")
    picture.knob(after + 7, 37, "8")
    picture.text(after + 21, 39, "Group")
    for i in range(3):
        if i == field:
            picture.fill(WIDTH - 19 + 7 * i, 2, 5, 5)
        else:
            picture.frame(WIDTH - 19 + 7 * i, 2, 5, 5)
    return picture


def arrangements_picture(muted: bool = False) -> Bitmap:
    """The knobs and pads on the song's arrangements: what they do, and the down arrow."""
    picture = Bitmap()
    picture.text(1, 1, "Arrangements")
    picture.knob(0, 11, "1")
    after = picture.text(13, 13, "-") + 1
    picture.knob(after, 11, "6")
    picture.text(after + 14, 13, "Tones")
    picture.knob(0, 24, "7")
    picture.text(14, 26, "Keys tone")
    picture.knob(0, 37, "8")
    picture.text(14, 39, "Volume")
    picture.shape(67, 40, _GLYPHS["▼"])
    picture.text(78, 39, "Playback")
    picture.text(1, 51, "Func: palm, low")
    if muted:
        picture.shape(WIDTH - 11, 51, MUTED)
    return picture


def playback_picture(muted: bool = False) -> Bitmap:
    """The knobs on playing the song: what knobs 1 to 5 do, and the up arrow."""
    picture = Bitmap()
    picture.text(1, 1, "Playback")
    for i, (said, x, y) in enumerate((("Position", 0, 11), ("Speed", 0, 24), ("Section", 0, 37), ("Loop A", 66, 11),
                                       ("Loop B", 66, 24))):
        picture.knob(x, y, str(i + 1))
        picture.text(x + 14, y + 2, said)
    picture.shape(67, 41, _UP)
    picture.text(78, 39, "Parts")
    if muted:
        picture.shape(WIDTH - 11, 51, MUTED)
    return picture


def song_picture(title: str, artist: str, key: str, notes: str, done: float | None = None, sign: str | None = None,
                 handle: bool = False, speed: float | None = None, beside: str | None = None,
                 muted: bool = False, corner: str | None = None,
                 loop: tuple[float | None, float | None, bool] | None = None,
                 knob: tuple[str, str] | None = None) -> Bitmap:
    """A song (its title over two lines if it needs them), its artist, and under them its key and its scale's notes
    (or what's shown without a song). The line between them shows how far it's got (done: 0 to 1), if that's
    known, after a sign of whether it's playing, paused or stopped (one of SIGNS), if that's known; with a
    handle while it's moved, and an A-B loop's brackets (its start and end, 0 to 1, each if it has one; joined
    under the line while it's on). Or it's the speed the pitch strip sets: 25% at the left, 100% in the middle, 150% at the right.
    Something can be beside the key, at the right (the time: where it is and its length), or in the corner under
    it (corner); muted (the sound off), a crossed-out speaker is in that corner (on every screen). A knob and
    what it does can be in the notes' place."""
    picture = Bitmap()
    lines = wrap(title, WIDTH - 2, 2) + (wrap(artist, WIDTH - 2, 1) if artist else [])
    for i, line in enumerate(lines):
        picture.text(1, 1 + 10 * i, line)
    left = 0 if sign is None else SONG_BAR
    if sign is not None:
        picture.shape(1, 30, SIGNS[sign])
    picture.fill(left, 33, WIDTH - left, 1)
    if speed is not None:
        middle, speed = left + (WIDTH - left) // 2, min(max(speed, SLOWEST), FASTEST)
        if speed <= 1.0:
            x = round(left + (speed - SLOWEST) / (1.0 - SLOWEST) * (middle - left))
        else:
            x = round(middle + (speed - 1.0) / (FASTEST - 1.0) * (WIDTH - 1 - middle))
        picture.fill(min(x, middle), 32, abs(x - middle) + 1, 3)
        picture.fill(middle, 29, 1, 9)  # 100%, through it
    elif done is not None:
        picture.fill(left, 32, song_bar(done, sign), 3)
        if handle:
            picture.fill(min(left + song_bar(done, sign), WIDTH - 3), 29, 3, 9)
    if loop is not None and speed is None:
        a, b = (None if end is None else min(left + song_bar(end, sign), WIDTH - 1) for end in loop[:2])
        for x, foot in ((a, a), (b, None if b is None else b - 1)):  # [ and ]
            if x is not None and foot is not None:
                picture.fill(x, 29, 1, 9)
                picture.fill(foot, 29, 2, 1)
                picture.fill(foot, 37, 2, 1)
        if loop[2] and a is not None and b is not None:
            picture.fill(a, 37, b - a + 1, 1)
    room = WIDTH - 2
    if beside:
        room -= text_width(beside) + 6
        picture.text(WIDTH - 1 - text_width(beside), 40, beside)
    picture.text(1, 40, wrap(key, room, 1)[0] if key else "")
    picture.text(1, 51, wrap(notes, WIDTH - (16 if muted else 2), 1)[0] if notes else "")
    if knob is not None:
        picture.knob(0, 49, knob[0])
        picture.text(14, 51, knob[1])
    if corner:
        picture.text(WIDTH - (14 if muted else 1) - text_width(corner), 51, corner)
    if muted:
        picture.shape(WIDTH - 11, 51, MUTED)
    return picture


def with_note(picture: Bitmap, lines: tuple[str, ...], muted: bool = False, bottom: int = 51) -> Bitmap:
    """A note over a picture for a while, at its bottom left: a line where its bottom line is (bottom: its top;
    a knob there, from 2 above), or two, the line above too (the corner left for the crossed-out speaker,
    muted)."""
    right = WIDTH - 13 if muted else WIDTH
    picture.fill(0, bottom - 2, right, HEIGHT - bottom + 2, on=False)
    picture.text(1, bottom, wrap(lines[-1], WIDTH - (16 if muted else 2), 1)[0] if lines[-1] else "")
    if len(lines) > 1:
        picture.fill(0, bottom - 11, WIDTH, 11, on=False)
        picture.text(1, bottom - 11, wrap(lines[0], WIDTH - 2, 1)[0] if lines[0] else "")
    return picture


def song_bar(done: float, sign: str | None) -> int:
    """How much of the song's progress line is filled, in pixels."""
    return round(min(max(done, 0.0), 1.0) * (WIDTH - (0 if sign is None else SONG_BAR)))


def mixer_picture(names: list[str], volumes: list[int], turned: int | None, muted: bool = False,
                  quiet: list[bool] | None = None, lights: list[float] | None = None) -> Bitmap:
    """The mixer: a slider for each arrangement's volume (0 to 127, 100 as it starts, marked), its volume above it
    (none: a crossed-out speaker) and its name under it, the one last turned underlined (None: all of them were).
    Each one quiet (muted by itself) is greyed, with the speaker over it; muted (the sound off), the speaker is in
    the corner, as on every screen. Its lit keys (lights: 0 to 1, of the 4) are rails either side of it, joined
    under it, as high as it's lit (dotted, quiet)."""
    picture = Bitmap()
    column = WIDTH // max(len(names), 3)
    first = (WIDTH - column * len(names)) // 2
    top, height = 11, 40
    room = height - 4
    percent = text_width("127%") <= column - 1  # (narrow: the numbers alone)
    for i, (name, volume) in enumerate(zip(names, volumes)):
        middle = first + i * column + column // 2
        x = middle - 4
        greyed = bool(quiet and i < len(quiet) and quiet[i])
        if greyed or volume <= 0:
            picture.shape(middle - 5, 1, MUTED)
        else:
            said = f"{volume}%" if percent else str(volume)
            picture.text(middle - text_width(said) // 2, 1, said)
        picture.frame(x, top, 9, height)
        filled = round(min(max(volume, 0), 127) / 127 * room)
        if greyed:
            picture.hatch(x + 2, top + 2 + room - filled, 5, filled)
        else:
            picture.fill(x + 2, top + 2 + room - filled, 5, filled)
        lit = min(max(lights[i], 0.0), 1.0) if lights and i < len(lights) else 0.0
        if lit:
            rail = round(lit * (height + 1))
            for y in range(top + height + 1 - rail, top + height + 2):
                if not greyed or y % 2 == 0:
                    picture.fill(x - 2, y, 1, 1)
                    picture.fill(x + 10, y, 1, 1)
            for across in range(x - 2, x + 11):
                if not greyed or across % 2 == 0:
                    picture.fill(across, top + height + 1, 1, 1)
        mark = top + 2 + room - round(100 / 127 * room)
        picture.fill(x - 3, mark, 2, 1)  # 100%, either side
        picture.fill(x + 10, mark, 2, 1)
        low, high = first + i * column, first + (i + 1) * column
        if muted and i == len(names) - 1:
            high = min(high, WIDTH - 13)  # (the corner's speaker)
        label = name
        while len(label) > 1 and text_width(label) > high - low - 2:
            label = label[:-1]
        left = (low + high) // 2 - text_width(label) // 2
        picture.text(left, 54, label)
        if i == turned:
            picture.fill(left, 63, text_width(label), 1)
    if muted:
        picture.shape(WIDTH - 11, 54, MUTED)
    return picture
