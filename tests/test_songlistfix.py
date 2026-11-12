import lzma
import struct
import zlib

import pytest

from rs_keylights.cli import main
from rs_keylights.psarc import Archive, write_archive
from rs_keylights.sevenzip import LZMA1, SevenZip, SevenZipError, _write_number
from rs_keylights.songlistfix import MENU, OVERLAY, TOP, FixError, fix, fixed_menu, is_fixed

PROPS = b"\x5d" + (1 << 16).to_bytes(4, "little")


def seven_zip(files):
    """A 7z archive as the game's: a folder (LZMA) for each file, the header as it is (not packed)."""
    packed = [lzma.compress(data, lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16}])
              for data in files.values()]
    n = len(files)
    h = bytearray(b"\x01\x04\x06" + _write_number(0) + _write_number(n) + b"\x09")
    h += b"".join(_write_number(len(p)) for p in packed) + b"\x00\x07\x0b" + _write_number(n) + b"\x00"
    h += b"".join(b"\x01\x23" + LZMA1 + b"\x05" + PROPS for _ in packed)
    h += b"\x0c" + b"".join(_write_number(len(d)) for d in files.values())
    h += b"\x00\x08\x0a\x01" + b"".join(struct.pack("<I", zlib.crc32(d)) for d in files.values()) + b"\x00\x00"
    names = b"\x00" + b"".join(name.encode("utf-16-le") + b"\x00\x00" for name in files)
    h += b"\x05" + _write_number(n) + b"\x11" + _write_number(len(names)) + names
    h += b"\x14" + _write_number(2 + 8 * n) + b"\x01\x00" + bytes(8 * n) + b"\x00\x00"
    body = b"".join(packed)
    start = struct.pack("<QQI", len(body), len(h), zlib.crc32(bytes(h)))
    return b"7z\xbc\xaf\x27\x1c\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start + body + bytes(h)


def push(*values):
    """An AVM1 push of (kind, value) pairs."""
    out = b""
    for kind, value in values:
        out += bytes([kind]) + (struct.pack("<i", value) if kind == 7 else bytes([value]))
    return b"\x96" + struct.pack("<H", len(out)) + out


POOL = ["ListScene", "ListSceneThumb", "push", "depth", "xMin", "yMin", "xMax", "yMax", "mScrollBarThumb",
        "mScrollBarTrack", "id", "getDepth"]
THUMB, TRACK, DEPTH = POOL.index("mScrollBarThumb"), POOL.index("mScrollBarTrack"), POOL.index("depth")


def bounds(part):
    """The thumb's bounds as the list reads them (from part: the thumb's own, or the track's)."""
    x, y, x2, y2 = (POOL.index(name) for name in ("xMin", "yMin", "xMax", "yMax"))
    return (push((8, x), (4, 1), (8, part)) + push((8, y), (4, 1), (8, part)) + push((8, x2), (4, 1), (8, part))
            + push((4, 1), (8, part)) + push((8, y2), (4, 1), (8, part)) + push((4, 1), (8, part)))


# The thumb's depth as the list gives it: push r4, 'depth', 0.0, r1, 'mScrollBarThumb'; getMember; push 'getDepth';
# callMethod; setMember
DEPTH_READ = (b"\x96\x11\x00\x04\x04\x08" + bytes([DEPTH]) + b"\x06" + bytes(8) + b"\x04\x01\x08" + bytes([THUMB])
              + b"\x4e" + push((8, POOL.index("getDepth"))) + b"\x52\x4f")


def menu(reads=None, depths=1):
    """A list's movie: its script (ListScene) reads the thumb's bounds, its id and its depth, after a jump."""
    strings = b"".join(s.encode() + b"\x00" for s in POOL)
    code = b"\x88" + struct.pack("<HH", 2 + len(strings), len(POOL)) + strings
    code += b"\x99\x02\x00" + struct.pack("<h", 2) + b"\x17\x17"  # (a jump: relative, so it stays right)
    code += reads if reads is not None else bounds(THUMB)
    code += push((4, 4), (8, POOL.index("id")), (8, POOL.index("ListSceneThumb")))
    code += DEPTH_READ * depths + push((4, 4), (7, 1), (4, 3), (8, 2)) + b"\x52\x00"
    tag = struct.pack("<H", 7) + code
    tags = struct.pack("<HI", (59 << 6) | 0x3F, len(tag)) + tag + b"\x00\x00"
    movie = bytearray(b"GFX\x0a\x00\x00\x00\x00" + b"\x00" + b"\x00\x18\x01\x00" + tags)
    struct.pack_into("<I", movie, 4, len(movie))
    return bytes(movie)


def test_seven_zip_rewritten_keeps_other_files_as_stored():
    data = seven_zip({"a.txt": b"one" * 100, MENU: b"menu", "c.bin": bytes(range(256))})
    archive = SevenZip(data)
    assert archive.files() == ["a.txt", MENU, "c.bin"]
    assert archive.read("c.bin") == bytes(range(256))
    again = SevenZip(archive.rewritten({MENU: b"changed"}))
    assert [again.read(n) for n in again.files()] == [b"one" * 100, b"changed", bytes(range(256))]
    assert again.folders[0].packed == archive.folders[0].packed and again.folders[2].packed == archive.folders[2].packed
    assert again.properties == archive.properties
    with pytest.raises(SevenZipError):
        archive.rewritten({"missing": b""})
    with pytest.raises(SevenZipError):
        SevenZip(b"PK\x03\x04" + bytes(40))


def test_fixed_menu_gives_the_thumb_the_whole_track_on_top():
    original = menu()
    assert not is_fixed(original)
    fixed = fixed_menu(original)
    assert is_fixed(fixed) and fixed_menu(fixed) == fixed
    assert struct.unpack_from("<I", fixed, 4)[0] == len(fixed) == len(original)  # (nothing moves)
    code = fixed[fixed.index(b"\x88"):]
    assert bounds(TRACK) in code and bounds(THUMB) not in code  # (the track's bounds: it doesn't move)
    on_top = push((4, 4), (8, DEPTH), (7, TOP)) + b"\x99\x02\x00" + struct.pack("<h", 10) + b"\x17" * 10 + b"\x4f"
    assert on_top in code and DEPTH_READ not in code and len(on_top) == len(DEPTH_READ)
    assert b"\x99\x02\x00" + struct.pack("<h", 2) + b"\x17\x17" in code  # (the jump as it was)
    assert fixed_menu(menu(reads=bounds(TRACK))) == fixed  # (half done: the depth as it was)
    with pytest.raises(FixError):
        fixed_menu(menu(depths=2))  # (a script this doesn't know: left as it is)
    with pytest.raises(FixError):
        fixed_menu(menu(reads=bounds(THUMB)[:-6]))


def game(tmp_path, overlay=None):
    folder = tmp_path / "game"
    (folder / "dlc").mkdir(parents=True)
    write_archive(folder / "cache.psarc", [("cache4.7z", seven_zip({"x.json": b"{}"})),
                                           ("cache8.7z", seven_zip({"a.dds": b"dds" * 50, MENU: menu()}))])
    if overlay is not None:
        (folder / OVERLAY).parent.mkdir()
        write_archive(folder / OVERLAY, overlay)
    return folder


def contents(path):
    with Archive(path) as archive:
        return {name: archive.read(name) for name in archive.names}


def test_fix_adds_the_menu_to_the_overlay_and_keeps_rearmeds_archives(tmp_path):
    theirs = seven_zip({"x.json": b'{"direct": 1}'})
    folder = game(tmp_path, [("cache4.7z", theirs)])
    report = fix(folder)
    assert (report.archive, report.changed, report.fixed) == ("cache8.7z", True, True)
    overlay = contents(folder / OVERLAY)
    assert overlay["cache4.7z"] == theirs
    fixed = SevenZip(overlay["cache8.7z"])
    assert is_fixed(fixed.read(MENU)) and fixed.read("a.dds") == b"dds" * 50
    assert not fix(folder).changed  # (done already)
    report = fix(folder, undo=True)
    assert report.changed and not report.fixed
    assert contents(folder / OVERLAY) == {"cache4.7z": theirs}
    assert not fix(folder, undo=True).changed


def test_fix_makes_the_overlay_and_undo_removes_it(tmp_path):
    folder = game(tmp_path)
    assert fix(folder).changed and list(contents(folder / OVERLAY)) == ["cache8.7z"]
    assert fix(folder, undo=True).changed and not (folder / OVERLAY).exists()


def test_fix_fixes_the_overlays_own_copy_of_the_archive(tmp_path):
    theirs = seven_zip({"a.dds": b"theirs", MENU: menu()})  # (RS Re-ARMed changing the same archive)
    folder = game(tmp_path, [("cache8.7z", theirs)])
    fix(folder)
    fixed = SevenZip(contents(folder / OVERLAY)["cache8.7z"])
    assert fixed.read("a.dds") == b"theirs" and is_fixed(fixed.read(MENU))
    fix(folder, undo=True)
    undone = SevenZip(contents(folder / OVERLAY)["cache8.7z"])
    assert undone.read("a.dds") == b"theirs" and undone.read(MENU) == menu()


def test_song_list_fix_command(tmp_path, capsys):
    folder = game(tmp_path)
    assert main(["--game-dir", str(folder), "song-list-fix"]) == 0
    assert "fixed: Update/patcharchives.psarc (cache8.7z)" in capsys.readouterr().out
    assert main(["--game-dir", str(folder), "song-list-fix"]) == 0
    assert "fixed already" in capsys.readouterr().out
    assert main(["--game-dir", str(folder), "song-list-fix", "--undo"]) == 0
    assert "the game's own again" in capsys.readouterr().out
    (folder / "cache.psarc").write_bytes(b"nothing")
    assert main(["--game-dir", str(folder), "song-list-fix"]) == 1


def test_fix_makes_an_earlier_fix_again_from_the_games_own(tmp_path):
    earlier = seven_zip({"a.dds": b"dds" * 50, MENU: menu(reads=bounds(TRACK))})  # (this one's, half done)
    folder = game(tmp_path, [("cache8.7z", earlier)])
    assert fix(folder).changed
    assert is_fixed(SevenZip(contents(folder / OVERLAY)["cache8.7z"]).read(MENU))
    assert not fix(folder).changed
    assert fix(folder, undo=True).changed and not (folder / OVERLAY).exists()
