from rs_keylights.bitmap import (SONG_BAR, HEIGHT, WIDTH, Bitmap, keys_picture, song_bar, song_picture, text_width,
                                 with_note, wrap)


def test_its_data_is_7_pixels_a_byte_the_highest_bit_leftmost():
    picture = Bitmap()
    picture.fill(0, 0, 1, 1)  # the top left
    picture.fill(7, 1, 2, 1)  # the second byte's first two, on the second row
    picture.fill(WIDTH - 1, HEIGHT - 1, 5, 5)  # the bottom right (the rest off the screen)
    data = picture.data()
    assert len(data) == 19 * 64 and data[0] == 0x40 and data[19 + 1] == 0x60
    assert data[-1] == 0x20  # pixel 127: the last byte's second bit (5 unused after it)
    assert sum(1 for byte in data if byte) == 3


def test_text_and_the_songs_progress_line():
    assert text_width("1:23") == 19 and text_width("1:23", scale=2) == 38  # (a 1-pixel gap between characters)
    picture = Bitmap()
    assert picture.text(0, 0, "1:23") == 20
    assert picture.pixels[0][:5] == [False, False, True, False, False]  # the 1's top
    assert song_bar(0.5, None) == WIDTH // 2 and song_bar(1.5, "playing") == WIDTH - SONG_BAR  # (after a sign, playing)
    half = song_picture("Song", "Band", "E minor", "E F# G", done=0.5)
    assert sum(half.pixels[32]) == WIDTH // 2 and sum(half.pixels[33]) == WIDTH  # thick as far as it's got
    seeking = song_picture("Song", "Band", "E minor", "E F# G", done=0.5, handle=True, beside="1:23 / 2:46")
    assert sum(seeking.pixels[30]) == 3  # a handle on the line, where it's moved to
    assert seeking.pixels[30][WIDTH // 2: WIDTH // 2 + 3] == [True] * 3


def test_the_keys_picture_fits_the_longest_tone_name():
    picture = keys_picture(14, "Piano", "HARPSICHORD & STRINGS")
    assert not any(row[-1] for row in picture.pixels[20:31])  # the name's lines: nothing at the right edge
    assert text_width("HARPSICHORD & STRINGS") < WIDTH - 1


def test_long_names_wrap_and_what_the_font_lacks_is_made_plain():
    assert wrap("Twilight of the Thunder God", WIDTH - 2, 2) == ["Twilight of the Thunder", "God"]
    lines = wrap("I Can't Quit You Baby (Remastered Live at the Royal Albert Hall 1970)", WIDTH - 2, 2)
    assert len(lines) == 2 and lines[1].endswith("...") and text_width(lines[1]) <= WIDTH - 2
    assert wrap("Supercalifragilisticexpialidocious", 60, 3)[0] != "Supercalifragilisticexpialidocious"  # broken
    assert text_width("Beyoncé") == text_width("Beyonce") and text_width("Don’t") == text_width("Don't")


def test_the_progress_line_turns_into_the_speed():
    picture = song_picture("Song", "Band", "E minor", "Speed 63%", done=0.9, sign="playing", speed=0.625)
    middle = SONG_BAR + (WIDTH - SONG_BAR) // 2
    assert picture.pixels[29][middle] and sum(picture.pixels[29]) == 1  # 100%, through the line
    assert sum(picture.pixels[32][SONG_BAR:]) == (middle - SONG_BAR) // 2 + 1  # half way down from 100%, to 25%


def test_a_note_goes_over_the_bottom_left_and_leaves_the_muted_speaker():
    def lit(picture, x0, y0, x1, y1):
        return sum(picture.pixels[y][x] for y in range(y0, y1) for x in range(x0, x1))

    plain = song_picture("Song", "Band", "C major", "C D E F G A B", muted=True, corner="12/300")
    assert lit(plain, WIDTH - 50, 51, WIDTH - 13, 58)  # the number in the corner, left of the speaker
    said = with_note(song_picture("Song", "Band", "C major", "C D E F G A B", muted=True, corner="12/300"), ("On",),
                     muted=True)
    assert lit(said, 0, 51, WIDTH - 13, 64) == lit(with_note(Bitmap(), ("On",)), 0, 51, WIDTH, 64)  # just the note
    assert lit(said, WIDTH - 11, 51, WIDTH, 58) == lit(plain, WIDTH - 11, 51, WIDTH, 58) > 0  # the speaker stays
    assert lit(said, 0, 40, WIDTH, 47) == lit(plain, 0, 40, WIDTH, 47)  # the key line too
    two = with_note(song_picture("Song", "Band", "C major", "C D E F G A B"), ("Pick a song", "on the page"))
    assert lit(two, 0, 40, WIDTH, 47) == lit(with_note(Bitmap(), ("Pick a song", "")), 0, 40, WIDTH, 47)
