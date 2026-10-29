from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator

from .psarc import Archive, ArchiveError
from .sng import Arrangement, decrypt, parse

# Finds the .sng of an arrangement from its manifest PersistentID (what RS Companion's overlay API
# reports), with an index of the game's archives that is cached between runs and updated for new
# or changed archives.

INDEX_VERSION = 1
SNG_PREFIX = "songs/bin/macos/"
MANIFEST_PREFIX = "manifests/"
NO_NOTES = ("vocals", "jvocals", "showlights")  # parts without notes to play


@dataclass(frozen=True)
class ArrangementRef:
    persistent_id: str
    archive: str
    sng: str  # entry in the archive, e.g. songs/bin/macos/amonpurs_lead.sng
    song_key: str
    name: str  # Lead, Rhythm, Bass, ...
    song: str = ""
    artist: str = ""

    @property
    def title(self) -> str:
        return f"{self.artist} - {self.song}" if self.artist else self.song or self.song_key

    @property
    def part(self) -> str:
        # amonpurs_lead.sng -> lead
        return Path(self.sng).stem.rsplit("_", 1)[-1].lower()

    @property
    def bass(self) -> bool:
        return self.part == "bass" or self.name.lower() == "bass"

    def load(self) -> Arrangement:
        with Archive(Path(self.archive)) as archive:
            return parse(decrypt(archive.read(self.sng)), bass=self.bass)


def default_cache() -> Path:
    return Path.home() / "Library" / "Caches" / "rs-keylights" / "index.json"


def song_archives(game_dir: Path) -> Iterator[Path]:
    # On-disc songs (songs.psarc), DLC and custom songs; PC archives (_p) have no Mac notes
    if (game_dir / "songs.psarc").is_file():
        yield game_dir / "songs.psarc"
    dlc = game_dir / "dlc"
    if dlc.is_dir():
        for root, _dirs, files in os.walk(dlc):
            for name in sorted(files):
                lower = name.lower()
                if lower.endswith(".psarc") and not lower.endswith("_p.psarc") and not name.startswith("._"):
                    yield Path(root) / name


def read_arrangements(path: Path) -> list[ArrangementRef]:
    with Archive(path) as archive:
        sngs = {
            name[len(SNG_PREFIX):-4].lower(): name
            for name in archive.names
            if name.startswith(SNG_PREFIX) and name.endswith(".sng")
        }
        refs = []
        for name in archive.names:
            if not (name.startswith(MANIFEST_PREFIX) and name.endswith(".json")):
                continue
            try:
                manifest = json.loads(archive.read(name))
            except (ValueError, ArchiveError):
                continue
            entries = manifest.get("Entries") if isinstance(manifest, dict) else None
            for entry in (entries or {}).values():
                attributes = entry.get("Attributes") if isinstance(entry, dict) else None
                if not isinstance(attributes, dict):
                    continue
                # SongAsset is urn:application:musicgame-song:<sng name>
                asset = str(attributes.get("SongAsset", "")).rsplit(":", 1)[-1].lower()
                sng = sngs.get(asset) or sngs.get(Path(name).stem.lower())
                persistent_id = str(attributes.get("PersistentID", "")).upper()
                if sng and persistent_id:
                    refs.append(ArrangementRef(
                        persistent_id, str(path), sng,
                        song_key=str(attributes.get("SongKey", "")),
                        name=str(attributes.get("ArrangementName", "")),
                        song=str(attributes.get("SongName", "")),
                        artist=str(attributes.get("ArtistName", "")),
                    ))
        return refs


class SongIndex:
    def __init__(self, game_dir: Path, cache: Path | None = None):
        self.game_dir = game_dir
        self.cache = cache or default_cache()
        self._archives: dict[str, dict] = {}
        self._by_id: dict[str, ArrangementRef] = {}
        self._load_cache()

    def __len__(self) -> int:
        return len(self._by_id)

    def arrangements(self) -> list[ArrangementRef]:
        return list(self._by_id.values())

    def find(self, persistent_id: str) -> ArrangementRef | None:
        return self._by_id.get(persistent_id.upper())

    def search(self, text: str) -> list[ArrangementRef]:
        """Arrangements whose artist, song name, song key or archive name contains the text."""
        wanted = text.casefold()
        return sorted(
            (
                ref for ref in self._by_id.values()
                if any(wanted in field.casefold() for field in (ref.title, ref.song_key, Path(ref.archive).name))
            ),
            key=lambda ref: (ref.title.casefold(), ref.archive, ref.part),
        )

    def refresh(self, progress: Callable[[int, int], None] | None = None) -> int:
        """Reads new and changed archives; returns how many it read."""
        paths = list(song_archives(self.game_dir))
        seen: dict[str, dict] = {}
        read = 0
        for done, path in enumerate(paths, 1):
            try:
                stat = path.stat()
            except OSError:
                continue
            key = str(path)
            cached = self._archives.get(key)
            if cached and cached["size"] == stat.st_size and cached["mtime"] == stat.st_mtime:
                seen[key] = cached
            else:
                try:
                    refs = [asdict(ref) for ref in read_arrangements(path)]
                except (OSError, ArchiveError):
                    refs = []  # remembered, so a broken archive isn't read again until it changes
                seen[key] = {"size": stat.st_size, "mtime": stat.st_mtime, "arrangements": refs}
                read += 1
            if progress is not None:
                progress(done, len(paths))
        changed = read > 0 or seen.keys() != self._archives.keys()
        self._archives = seen
        self._build()
        if changed:
            self._save_cache()
        return read

    def _build(self) -> None:
        self._by_id = {}
        for entry in self._archives.values():
            for fields in entry["arrangements"]:
                ref = ArrangementRef(**fields)
                self._by_id[ref.persistent_id] = ref

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("version") != INDEX_VERSION or data.get("game_dir") != str(self.game_dir):
            return
        archives = data.get("archives")
        if isinstance(archives, dict):
            self._archives = archives
            try:
                self._build()
            except (TypeError, KeyError):
                self._archives, self._by_id = {}, {}

    def _save_cache(self) -> None:
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        temp = self.cache.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": INDEX_VERSION, "game_dir": str(self.game_dir), "archives": self._archives}))
        temp.replace(self.cache)
