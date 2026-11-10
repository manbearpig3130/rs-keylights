from __future__ import annotations

import json
import os
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .library import SongIndex
from .psarc import Archive, ArchiveError, Stored, write_archive
from .sng import SNG_KEY_MAC, SNG_MAGIC

# The PC's song archives (*_p.psarc) as the Mac game's (*_m.psarc), for the songs there's no Mac archive of. The two
# platforms' archives of a song have the same files (the audio byte for byte), but for: their folders (songs/bin/generic
# and audio/windows, against songs/bin/macos and audio/mac), the .nt graph naming them and the platform ("dx9", against
# "macos"), and the charts (.sng), encrypted with a key a platform (the 56 bytes after, a signature or none, the same).
# So a PC archive is made a Mac one here: its folders renamed, its graph's names and platform changed, its charts
# encrypted again with the Mac's key; the rest (the audio) copied block for block. Its AppID kept, if it's the game's
# own or the game (RS Re-ARMed's copy) lists it, else the free one custom songs carry: the game mounts an archive only
# for an AppID it has.
#
# A PC archive is converted only when no other archive has its song (its arrangements' persistent IDs, or its artist
# and title): RS Re-ARMed's copy of the Mac game and rs-keylights list each song once. The archives made go in a folder
# of their own in the game's dlc (from-pc), which RS Re-ARMed's copy leaves alone; one whose PC archive is gone, or
# has a Mac archive now, is removed.

SNG_KEY_PC = bytes.fromhex("CB648DF3D12A16BF71701414E69619EC171CCA5D2A142E3E59DE7ADDA18A3A30")
SNG_TRAILER = 56  # (bytes after a chart's encrypted part: its signature, or none)
FOLDERS = (("songs/bin/generic/", "songs/bin/macos/"), ("audio/windows/", "audio/mac/"))
FREE_APP = "248750"  # (the free DLC custom songs carry: the game lists it)
GAME_APP = "221680"  # (Rocksmith 2014's own: official songs carry it, and the game mounts them)
FOLDER = "from-pc"
STATE = ".from-pc.json"
CONVERT_VERSION = 1  # (of what's made: another, made again)


class PcSongsError(Exception):
    pass


def mac_name(name: str) -> str:
    """An entry's name in the Mac's archive: its folders the Mac's, and / between them (some PC archives, packed on
    Windows, have \\)."""
    name = name.replace("\\", "/")
    for pc, mac in FOLDERS:
        if name.lower().startswith(pc):
            return mac + name[len(pc):]
    return name


def _plain(raw: bytes, key: bytes, end: int | None = None) -> bytes:
    """A chart's encrypted part decrypted (from its IV on, to end)."""
    decryptor = Cipher(algorithms.AES(key), modes.CTR(raw[8:24])).decryptor()
    return decryptor.update(raw[24:end]) + decryptor.finalize()


def _content(plain: bytes) -> tuple[bytes, int]:
    """A decrypted chart's content (its length, then zlib), and where its zlib ends (in plain)."""
    inflate = zlib.decompressobj()
    try:
        content = inflate.decompress(plain[4:], int.from_bytes(plain[:4], "little"))
    except zlib.error as exc:
        raise PcSongsError(f"a chart that doesn't decrypt: {exc}") from exc
    return content, len(plain) - len(inflate.unused_data)


def mac_chart(raw: bytes) -> bytes:
    """A PC chart (.sng) encrypted for the Mac: the same IV; encrypted again to where its zlib ends, or to the 56 bytes
    after (its signature, or none: as they are), whichever is later; checked: its content as the PC's."""
    if len(raw) < 24 + SNG_TRAILER or int.from_bytes(raw[:4], "little") != SNG_MAGIC:
        raise PcSongsError("not a PC chart")
    plain = _plain(raw, SNG_KEY_PC)
    content, used = _content(plain)
    end = max(len(raw) - SNG_TRAILER, 24 + used)
    encryptor = Cipher(algorithms.AES(SNG_KEY_MAC), modes.CTR(raw[8:24])).encryptor()
    made = raw[:24] + encryptor.update(plain[:end - 24]) + encryptor.finalize() + raw[end:]
    if _content(_plain(made, SNG_KEY_MAC))[0] != content:
        raise PcSongsError("a chart that didn't come out the same")
    return made


def mac_graph(text: str) -> str:
    """The .nt graph naming a PC archive's files, as the Mac's names them."""
    for pc, mac in FOLDERS:
        text = re.sub(f'"/{re.escape(pc.rstrip("/"))}(?=[/"])', f'"/{mac.rstrip("/")}', text, flags=re.I)
    return text.replace('/tag> "dx9"', '/tag> "macos"')


def convert(pc_path: Path, out: Path, listed: set[str] | None = None) -> str:
    """A PC archive made a Mac one at out; its AppID (kept if the game lists it, or nothing's listed; else the free
    one). Returns the AppID it has."""
    entries: list[tuple[str, bytes | Stored]] = []
    with Archive(pc_path) as archive:
        if archive.block_size != 0x10000:
            raise PcSongsError(f"{pc_path.name}: blocks of {archive.block_size} bytes")
        if not any(name.replace("\\", "/").lower().startswith(FOLDERS[0][0]) for name in archive.names):
            raise PcSongsError(f"{pc_path.name} has no PC charts")
        app = FREE_APP
        for name in archive.names:
            lower = name.replace("\\", "/").lower()
            if lower.endswith(".sng") and lower.startswith(FOLDERS[0][0]):
                entries.append((mac_name(name), mac_chart(archive.read(name))))
            elif lower.endswith(".nt"):
                entries.append((mac_name(name), mac_graph(archive.read(name).decode("utf-8")).encode("utf-8")))
            elif lower == "appid.appid":
                own = archive.read(name).decode("ascii", "replace").strip()
                app = own if listed is None or own in listed or own == GAME_APP else FREE_APP
                entries.append((mac_name(name), app.encode()))
            else:
                entries.append((mac_name(name), archive.stored(name)))
    write_archive(out, entries)
    return app


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def song_of(path: Path) -> tuple[set[str], set[str]]:
    """An archive's arrangements' persistent IDs, and its songs (artist and title, as compared: lower case, letters
    and digits)."""
    ids, titles = set(), set()
    with Archive(path) as archive:
        for name in archive.names:
            if not (name.replace("\\", "/").lower().startswith("manifests/") and name.lower().endswith(".json")):
                continue
            try:
                manifest = json.loads(archive.read(name))
            except (ValueError, ArchiveError):
                continue
            for entry in ((manifest.get("Entries") if isinstance(manifest, dict) else None) or {}).values():
                attributes = entry.get("Attributes") if isinstance(entry, dict) else None
                if not isinstance(attributes, dict):
                    continue
                if attributes.get("PersistentID"):
                    ids.add(str(attributes["PersistentID"]).upper())
                if attributes.get("SongName"):
                    titles.add(f"{_norm(str(attributes.get('ArtistName') or ''))}|{_norm(str(attributes['SongName']))}")
    return ids, titles


def listed_apps(game_dir: Path) -> set[str] | None:
    """The AppIDs RS Re-ARMed's copy of the game lists (its steam.env's RS_STEAM_DLC); None without it."""
    try:
        found = re.search(r"^RS_STEAM_DLC=([0-9,]*)", (game_dir / ".rearmed" / "steam.env").read_text(), re.M)
    except OSError:
        return None
    return set(found.group(1).split(",")) - {""} if found else None


def steam_dlc(game_dir: Path) -> Path | None:
    """The Steam game's dlc folder RS Re-ARMed's copy is made from."""
    try:
        source = json.loads((game_dir / ".rearmed" / "source.json").read_text()).get("source")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(source) / "dlc" if source else None


def pc_archives(folders: Iterable[Path], skip: Path) -> list[Path]:
    """The PC archives in these folders and theirs (not skip's, nor macOS's ._ files), the first of a name only."""
    out, names = [], set()
    for folder in folders:
        for root, dirs, files in os.walk(folder):
            if Path(root) == skip:
                dirs[:] = []
                continue
            dirs.sort()
            for name in sorted(files):
                if name.lower().endswith("_p.psarc") and not name.startswith("._") and name.lower() not in names:
                    names.add(name.lower())
                    out.append(Path(root) / name)
    return out


@dataclass
class Report:
    made: list[str] = field(default_factory=list)  # (the archives made, by name)
    kept: int = 0  # made before, their PC archive unchanged
    have_mac: list[str] = field(default_factory=list)  # (PC archives whose song has a Mac archive)
    older: list[str] = field(default_factory=list)  # (PC archives of a song a newer PC archive has too)
    removed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # (each "name: why")
    apps_changed: int = 0  # (made with the free AppID, theirs not listed)


def sync(game_dir: Path, index: SongIndex, folders: Iterable[Path] | None = None, dry_run: bool = False,
         say: Callable[[str], None] = lambda _text: None) -> Report:
    """The PC archives of the songs without a Mac archive made Mac ones, in the game's dlc/from-pc (folders: where
    the PC archives are; by default the Steam game's dlc and the game's own); those made before kept while their PC
    archive is unchanged, and removed once it's gone, or its song has a Mac archive."""
    out_dir = game_dir / "dlc" / FOLDER
    state_file = out_dir / STATE
    try:
        state = json.loads(state_file.read_text())
        made = state.get("made", {}) if state.get("version") == CONVERT_VERSION else {}
        known = state.get("songs", {})
    except (OSError, ValueError, AttributeError):
        made, known = {}, {}
    if folders is None:
        folders = [f for f in (steam_dlc(game_dir), game_dir / "dlc") if f is not None and f.is_dir()]
    sources = pc_archives(folders, out_dir)
    index.refresh()
    ids, titles = set(), set()
    for ref in index.arrangements():
        if Path(ref.archive).parent != out_dir:  # (the Mac archives, not those made here)
            ids.add(ref.persistent_id.upper())
            titles.add(f"{_norm(ref.artist)}|{_norm(ref.song)}")
    listed = listed_apps(game_dir)
    report = Report()
    wanted: dict[str, dict] = {}
    songs: dict[str, dict] = {}
    for source in sources:  # (each one's song: read once, then as it was while it's unchanged)
        stat = source.stat()
        stamp = [stat.st_size, stat.st_mtime_ns]
        seen = known.get(str(source))
        if seen and seen.get("stamp") == stamp:
            songs[str(source)] = seen
            continue
        try:
            song_ids, song_titles = song_of(source)
        except (OSError, ArchiveError) as exc:
            report.failed.append(f"{source.name}: {exc}")
            continue
        songs[str(source)] = {"stamp": stamp, "ids": sorted(song_ids), "titles": sorted(song_titles)}
    taken_ids, taken_titles = set(), set()  # (the songs of the PC archives converted: the newest of a song's)
    for source in sorted((s for s in sources if str(s) in songs), key=lambda s: -songs[str(s)]["stamp"][1]):
        song = songs[str(source)]
        stamp, song_ids, song_titles = song["stamp"], set(song["ids"]), set(song["titles"])
        if not song_ids or song_ids & ids or song_titles & titles:
            report.have_mac.append(source.name)
            continue
        if song_ids & taken_ids or song_titles & taken_titles:
            report.older.append(source.name)
            continue
        taken_ids |= song_ids
        taken_titles |= song_titles
        name = re.sub(r"_p\.psarc$", "_m.psarc", source.name, flags=re.I)
        before = made.get(name)
        if before and before.get("source") == str(source) and before.get("stamp") == stamp and (out_dir / name).exists():
            wanted[name] = before
            report.kept += 1
            continue
        if dry_run:
            report.made.append(name)
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            app = convert(source, out_dir / name, listed)
        except (OSError, ArchiveError, PcSongsError, UnicodeDecodeError, ValueError) as exc:
            report.failed.append(f"{source.name}: {exc}")
            continue
        wanted[name] = {"source": str(source), "stamp": stamp, "app": app}
        report.made.append(name)
        report.apps_changed += app == FREE_APP and listed is not None
        say(f"made {name}")
    for name in made:
        if name not in wanted:
            report.removed.append(name)
            if not dry_run:
                (out_dir / name).unlink(missing_ok=True)
    if not dry_run and (wanted or made or songs):
        out_dir.mkdir(parents=True, exist_ok=True)
        temp = state_file.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": CONVERT_VERSION, "made": wanted, "songs": songs}))
        temp.replace(state_file)
    return report
