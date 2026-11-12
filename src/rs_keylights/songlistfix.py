from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .psarc import Archive, ArchiveError, Stored, write_archive
from .sevenzip import SevenZip, SevenZipError

# The song list's scroll bar, made to work with a long list (Learn a Song). The list's menu (a Scaleform movie,
# gfxassets/views/las_songslist.gfx, in one of cache.psarc's cacheN.7z archives) tells the game where its parts can be
# clicked: its buttons, each row of songs, and the scroll bar's thumb, where it's drawn, each with its depth. The game
# takes the deepest (the one drawn on top) of those the mouse is in. Two things keep the thumb from being grabbed:
# - The rows reach over the scroll bar, and those in the list's lower panel are drawn above the thumb (it's put just
#   under the song in focus), so the thumb gets the click only in the upper half of the screen.
# - The game asks where the parts are only now and then, not as the list scrolls (the rows stay where they are: only
#   what's in them changes), so the thumb's part stays where the thumb was then.
# Here the part is the scroll bar's whole track, which doesn't move (a click anywhere on it grabs the thumb, and the
# list follows the mouse from there, as the game does it), at a depth above everything else's.
#
# cache.psarc isn't changed (RS Re-ARMed's Metal renderer takes a changed one for a fast-load cache and loads more
# slowly): the archive with the fixed menu goes into RS Re-ARMed's overlay, Update/patcharchives.psarc, which the game
# reads its cacheN.7z archives from first (with the ones RS Re-ARMed puts there itself, kept as they are). RS Re-ARMed
# makes its overlay again at each copy or refresh of the game files: then this is wanted again (quick when it's there).

MENU = "gfxassets/views/las_songslist.gfx"
OVERLAY = Path("Update") / "patcharchives.psarc"
THUMB = "ListSceneThumb"
DO_INIT_ACTION, CONSTANT_POOL, PUSH, CALL_METHOD = 59, 0x88, 0x96, 0x52
GET_MEMBER, SET_MEMBER, JUMP, POP = 0x4E, 0x4F, 0x99, 0x17
TOP = 1_000_000  # (the scroll bar's depth: the list's own are small numbers)
PUSH_STRING, PUSH_REGISTER, PUSH_INT, PUSH_POOL = 0, 4, 7, 8


class FixError(ValueError):
    pass


@dataclass
class Report:
    archive: str  # (the cacheN.7z the menu is in)
    changed: bool  # the overlay written
    fixed: bool  # the menu in the overlay fixed now


def _tags(movie: bytes) -> tuple[int, list[tuple[int, int, int]]]:
    """Where a movie's tags start, and each tag: (its code, where its header starts, where its body starts)."""
    if movie[:3] not in (b"GFX", b"FWS"):
        raise FixError("not an uncompressed Scaleform movie")
    bits = movie[8] >> 3
    at = 8 + (5 + 4 * bits + 7) // 8 + 4
    first, tags = at, []
    while at < len(movie):
        code_length = struct.unpack_from("<H", movie, at)[0]
        code, length, body = code_length >> 6, code_length & 0x3F, at + 2
        if length == 0x3F:
            length, body = struct.unpack_from("<I", movie, at + 2)[0], at + 6
        tags.append((code, at, body))
        at = body + length
        if code == 0:
            break
    return first, tags


def _pool(code: bytes) -> list[str]:
    """A script's constant pool (its first action)."""
    if not code or code[0] != CONSTANT_POOL:
        raise FixError("the list's script has no constant pool first")
    size, count = struct.unpack_from("<HH", code, 1)
    return [s.decode("latin-1") for s in code[5:3 + size].split(b"\x00")[:count]]


def _pushes(code: bytes) -> list[list[tuple[int, int | None, int]]]:
    """Each push of a script, in order: its values (kind, the pool index or register of a pool string or register,
    where that byte is). The actions one after another: a function's body follows its header as more actions."""
    out, at = [], 0
    while at < len(code):
        op = code[at]
        length = struct.unpack_from("<H", code, at + 1)[0] if op >= 0x80 else 0
        body = at + (3 if op >= 0x80 else 1)
        if op == PUSH:
            values, i = [], body
            while i < body + length:
                kind = code[i]
                if kind == PUSH_STRING:
                    values.append((kind, None, i))
                    i = code.index(b"\x00", i + 1) + 1
                    continue
                size = {1: 4, 2: 0, 3: 0, 4: 1, 5: 1, 6: 8, 7: 4, 8: 1, 9: 2}.get(kind)
                if size is None:
                    raise FixError(f"the list's script: a push of kind {kind}")
                values.append((kind, code[i + 1] if size == 1 else None, i + 1))
                i += 1 + size
            out.append(values)
        at = body + length
    return out


def _thumb_bounds(code: bytes, pool: list[str], part: str) -> list[int]:
    """Where the thumb's bounds are read (from the part, mScrollBarThumb or mScrollBarTrack: the bytes of its name),
    from its xMin to the push of its id."""
    if part not in pool or "xMin" not in pool or pool.index(part) > 0xFF:
        return []
    named = [[(pool[value], at) for kind, value, at in push if kind == PUSH_POOL and value is not None]
             for push in _pushes(code)]
    ids = [k for k, push in enumerate(named) if any(name == THUMB for name, _at in push)]
    if len(ids) != 1:
        return []
    starts = [k for k, push in enumerate(named[:ids[0]])
              if [name for name, _at in push][:2] in (["xMin", "mScrollBarThumb"], ["xMin", "mScrollBarTrack"])]
    if not starts:
        return []
    return [at for push in named[starts[-1]:ids[0]] for name, at in push if name == part]


def _list_script(movie: bytes) -> tuple[int, int, int, bytes]:
    """The list's script (ListScene, which has the thumb): its tag's header, its body, its end, its code."""
    _first, tags = _tags(movie)
    for code, header, body in tags:
        if code != DO_INIT_ACTION:
            continue
        length = (struct.unpack_from("<I", movie, header + 2)[0] if body - header == 6
                  else struct.unpack_from("<H", movie, header)[0] & 0x3F)
        script = movie[body + 2:body + length]
        try:
            pool = _pool(script)
        except (FixError, struct.error):
            continue
        if THUMB in pool and "ListScene" in pool:
            return header, body, body + length, script
    raise FixError(f"no list with a scroll bar in {MENU}")


def _depth_site(pool: list[str], fixed: bool) -> bytes:
    """The thumb's depth as the list gives it (push r4, 'depth', 0, r1, 'mScrollBarThumb'; getMember;
    push 'getDepth'; callMethod) or, fixed, the same number of bytes: push r4, 'depth', TOP, and a jump over the rest
    (pops, never run). Then setMember, either way."""
    if any(name not in pool or pool.index(name) > 0xFF for name in ("depth", "mScrollBarThumb", "getDepth")):
        return b"\x00"  # (in no script)
    depth = pool.index("depth")
    if not fixed:
        return (bytes([PUSH]) + struct.pack("<H", 17) + bytes([PUSH_REGISTER, 4, PUSH_POOL, depth, 6]) + bytes(8)
                + bytes([PUSH_REGISTER, 1, PUSH_POOL, pool.index("mScrollBarThumb"), GET_MEMBER, PUSH])
                + struct.pack("<H", 2) + bytes([PUSH_POOL, pool.index("getDepth"), CALL_METHOD, SET_MEMBER]))
    head = (bytes([PUSH]) + struct.pack("<H", 9) + bytes([PUSH_REGISTER, 4, PUSH_POOL, depth, PUSH_INT])
            + struct.pack("<i", TOP))
    rest = 27 - len(head) - 5
    return head + bytes([JUMP]) + struct.pack("<Hh", 2, rest) + bytes([POP]) * rest + bytes([SET_MEMBER])


def is_fixed(movie: bytes) -> bool:
    _header, _body, _end, script = _list_script(movie)
    pool = _pool(script)
    return (len(_thumb_bounds(script, pool, "mScrollBarTrack")) == BOUNDS_READS
            and not _thumb_bounds(script, pool, "mScrollBarThumb")
            and script.count(_depth_site(pool, True)) == 1 and _depth_site(pool, False) not in script)


BOUNDS_READS = 6  # (the thumb's _x, _y, _x and _width, _y and _height)


def fixed_menu(movie: bytes) -> bytes:
    """The song list's movie with its scroll bar's whole track, above everything, where its thumb is grabbed."""
    if is_fixed(movie):
        return movie
    header, body, end, script = _list_script(movie)
    pool = _pool(script)
    code = bytearray(script)
    reads = _thumb_bounds(script, pool, "mScrollBarThumb")
    if reads:
        if len(reads) != BOUNDS_READS or "mScrollBarTrack" not in pool or pool.index("mScrollBarTrack") > 0xFF:
            raise FixError(f"the list's script isn't the one this knows ({len(reads)} reads of the thumb's bounds)")
        for at in reads:  # (one byte each, the name's index: nothing moves)
            code[at] = pool.index("mScrollBarTrack")
    site, fixed = _depth_site(pool, False), _depth_site(pool, True)
    if fixed not in script:
        if script.count(site) != 1:
            raise FixError(f"the list's script isn't the one this knows ({script.count(site)} places for the depth)")
        at = script.index(site)
        code[at:at + len(site)] = fixed  # (as long: nothing moves)
    tag = movie[body:body + 2] + bytes(code)  # (the sprite's id, then the script)
    out = bytearray(movie[:header]) + struct.pack("<HI", (DO_INIT_ACTION << 6) | 0x3F, len(tag)) + tag + movie[end:]
    struct.pack_into("<I", out, 4, len(out))
    if not is_fixed(bytes(out)):
        raise FixError("the list's script didn't change as it should")
    return bytes(out)


def _menu_archive(cache: Archive) -> str:
    for name in cache.names:
        if name.lower().endswith(".7z") and MENU in SevenZip(cache.read(name)).files():
            return name
    raise FixError(f"no {MENU} in cache.psarc")


def _only_menu_differs(archive: SevenZip, original: SevenZip) -> bool:
    """Whether an archive is the game's own but for the menu (put there here, not by RS Re-ARMed)."""
    if archive.names != original.names or len(archive.folders) != len(original.folders):
        return False
    menu_folder = archive.streams()[MENU][0]
    return all(f == menu_folder or (a.packed, a.coder, a.props) == (o.packed, o.coder, o.props)
               for f, (a, o) in enumerate(zip(archive.folders, original.folders)))


def fix(game_dir: Path, undo: bool = False) -> Report:
    """The song list's scroll bar fixed in RS Re-ARMed's overlay (undo: as the game has it again)."""
    overlay = game_dir / OVERLAY
    try:
        with Archive(game_dir / "cache.psarc") as cache:
            name = _menu_archive(cache)
            original = cache.read(name)
        entries: list[tuple[str, bytes | Stored]] = []
        current = original
        if overlay.is_file():
            with Archive(overlay) as patch:
                for entry in patch.names:
                    if entry == name:
                        current = patch.read(entry)
                    else:
                        entries.append((entry, patch.stored(entry)))
        game, archive = SevenZip(original), SevenZip(current)
        ours = current is not original and _only_menu_differs(archive, game)  # (the overlay's copy: made here)
        menu = archive.read(MENU)
        if undo:
            if ours:
                new = original  # (left out of the overlay)
            elif menu == game.read(MENU):
                return Report(name, False, False)
            else:
                new = archive.rewritten({MENU: game.read(MENU)})
        else:
            if is_fixed(menu):
                return Report(name, False, True)
            base = game if ours else archive  # (an earlier fix of this one's: made again from the game's own)
            fixed = fixed_menu(base.read(MENU))
            new = base.rewritten({MENU: fixed})
            if SevenZip(new).read(MENU) != fixed:
                raise FixError("the archive made doesn't read back")
    except (ArchiveError, SevenZipError, OSError, struct.error) as exc:
        raise FixError(str(exc)) from exc
    if new != original:
        entries.append((name, new))
    if not entries:
        overlay.unlink()
        return Report(name, True, False)
    overlay.parent.mkdir(parents=True, exist_ok=True)
    write_archive(overlay, sorted(entries, key=lambda entry: entry[0]))
    return Report(name, True, not undo)
