from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

try:
    from cryptography.hazmat.decrepit.ciphers.modes import CFB
except ImportError:  # cryptography before 47
    from cryptography.hazmat.primitives.ciphers.modes import CFB

# Rocksmith 2014 archives (.psarc): a header, an AES-256-CFB encrypted table of contents (the
# well-known Rocksmith key), then the files in zlib blocks. Entry 0 lists the other entries' names.

TOC_KEY = bytes.fromhex("C53DB23870A1A2F71CAE64061FDD0E1157309DC85204D4C5BFDF25090DF2572C")
TOC_IV = bytes.fromhex("E915AA018FEF71FC508132E4BB4CEB42")
HEADER = struct.Struct(">4sI4sIIIII")
ENCRYPTED_TOC = 4
VERSION = 0x00010004
ENTRY_SIZE = 30


class ArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class Stored:
    """An entry as an archive stores it: its length, and its blocks as they are (each one's size as the block table
    has it, 0 a whole block stored as it is, and its bytes), to be written into another archive unchanged."""
    length: int
    blocks: tuple[tuple[int, bytes], ...]


class Archive:
    def __init__(self, path: Path):
        self.path = path
        self._file: BinaryIO = open(path, "rb")
        try:
            self._read_toc()
        except Exception:
            self._file.close()
            raise

    def __enter__(self) -> Archive:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._file.close()

    def _read_toc(self) -> None:
        header = self._file.read(HEADER.size)
        if len(header) < HEADER.size:
            raise ArchiveError(f"{self.path.name}: too short for a .psarc")
        magic, _version, _compression, toc_length, entry_size, entry_count, block_size, flags = HEADER.unpack(header)
        if magic != b"PSAR" or entry_size < 30 or entry_count < 1 or toc_length < HEADER.size + entry_count * entry_size:
            raise ArchiveError(f"{self.path.name}: not a .psarc")  # (its table too short for its entries: broken)
        toc = self._file.read(toc_length - HEADER.size)
        if len(toc) < entry_count * entry_size:
            raise ArchiveError(f"{self.path.name}: its table of contents is cut short")
        if flags & ENCRYPTED_TOC:
            decryptor = Cipher(algorithms.AES(TOC_KEY), CFB(TOC_IV)).decryptor()
            toc = decryptor.update(toc) + decryptor.finalize()

        # Entry: 16-byte MD5 of the name, first block index (4), length (5), offset (5)
        self._entries = [
            (
                int.from_bytes(toc[i * entry_size + 16:i * entry_size + 20], "big"),
                int.from_bytes(toc[i * entry_size + 20:i * entry_size + 25], "big"),
                int.from_bytes(toc[i * entry_size + 25:i * entry_size + 30], "big"),
            )
            for i in range(entry_count)
        ]
        size_bytes = 2 if block_size <= 0x10000 else 3 if block_size <= 0x1000000 else 4
        sizes = toc[entry_count * entry_size:]
        self._block_size = block_size
        self.block_size = block_size
        self._block_sizes = [
            int.from_bytes(sizes[i:i + size_bytes], "big") for i in range(0, len(sizes) - size_bytes + 1, size_bytes)
        ]
        self.names: list[str] = self._read_entry(0).decode("utf-8", "replace").split("\n")[: entry_count - 1]

    def read(self, name: str) -> bytes:
        try:
            index = self.names.index(name)
        except ValueError:
            raise ArchiveError(f"{self.path.name} has no {name}") from None
        return self._read_entry(index + 1)

    def stored(self, name: str) -> Stored:
        """An entry's blocks as they're stored (not uncompressed), to copy it into another archive."""
        try:
            block, length, offset = self._entries[self.names.index(name) + 1]
        except ValueError:
            raise ArchiveError(f"{self.path.name} has no {name}") from None
        self._file.seek(offset)
        blocks = []
        for _ in range(max(1, -(-length // self._block_size))):
            if block >= len(self._block_sizes):
                raise ArchiveError(f"{self.path.name}: {name} runs past the block table")
            size = self._block_sizes[block]
            blocks.append((size, self._file.read(size or self._block_size)))
            block += 1
        return Stored(length, tuple(blocks))

    def _read_entry(self, index: int) -> bytes:
        block, length, offset = self._entries[index]
        self._file.seek(offset)
        out = bytearray()
        while len(out) < length:
            if block >= len(self._block_sizes):
                raise ArchiveError(f"{self.path.name}: entry {index} runs past the block table")
            stored = self._block_sizes[block] or self._block_size
            block += 1
            chunk = self._file.read(stored)
            out += _unpacked(chunk, min(self._block_size, length - len(out)))
        return bytes(out[:length])


def _unpacked(chunk: bytes, expected: int) -> bytes:
    """A block as its entry has it: uncompressed, if it's zlib (a block its entry's size is stored as it is, unless
    it's zlib that comes out that size: its compressed size can be the same); some packers leave its checksum out, and
    its data's there, whole. Not zlib: as it is (some archives keep blocks so)."""
    if len(chunk) == expected and not (len(chunk) > 2 and chunk[0] & 0x0F == 8 and (chunk[0] << 8 | chunk[1]) % 31 == 0):
        return chunk
    try:
        whole = zlib.decompressobj().decompress(chunk)
    except zlib.error:
        return chunk
    return whole if len(whole) == expected or (len(chunk) != expected and whole) else chunk


def _blocks(content: bytes, block_size: int) -> list[tuple[int, bytes]]:
    """Content in blocks for the block table: each compressed, unless that's no smaller (stored as it is; a whole
    block's size is 0 in the table)."""
    out = []
    for start in range(0, max(len(content), 1), block_size):
        raw = content[start:start + block_size]
        packed = zlib.compress(raw, 9)
        data = packed if len(packed) < len(raw) else raw
        out.append((0 if len(data) == block_size else len(data), data))
    return out


def write_archive(path: Path, entries: Sequence[tuple[str, bytes | Stored]], block_size: int = 0x10000) -> None:
    """An archive (.psarc) of these entries (names, and their content: bytes, or blocks stored as another archive of
    the same block size had them), its table of contents encrypted, as the game's are: each entry's name's MD5, and
    the first (the list of names) none. Written next to it first, then put in its place."""
    if block_size != 0x10000:
        raise ArchiveError("only 64 KiB blocks")  # (2-byte block sizes in the table)
    names = [name for name, _content in entries]
    stored: list[tuple[bytes, int, list[tuple[int, bytes]]]] = [(bytes(16), len("\n".join(names).encode()),
                                                                  _blocks("\n".join(names).encode(), block_size))]
    for name, content in entries:
        if isinstance(content, Stored):
            stored.append((hashlib.md5(name.encode()).digest(), content.length, list(content.blocks)))
        else:
            stored.append((hashlib.md5(name.encode()).digest(), len(content), _blocks(content, block_size)))
    block_count = sum(len(blocks) for _md5, _length, blocks in stored)
    toc_length = HEADER.size + len(stored) * ENTRY_SIZE + 2 * block_count
    toc, sizes, offset, first = bytearray(), bytearray(), toc_length, 0
    for md5, length, blocks in stored:
        toc += md5 + first.to_bytes(4, "big") + length.to_bytes(5, "big") + offset.to_bytes(5, "big")
        for size, data in blocks:
            sizes += size.to_bytes(2, "big")
            offset += len(data)
        first += len(blocks)
    encryptor = Cipher(algorithms.AES(TOC_KEY), CFB(TOC_IV)).encryptor()
    table = encryptor.update(bytes(toc + sizes)) + encryptor.finalize()
    temp = path.with_name(f".{path.name}.part")
    with open(temp, "wb") as f:
        f.write(HEADER.pack(b"PSAR", VERSION, b"zlib", toc_length, ENTRY_SIZE, len(stored), block_size, ENCRYPTED_TOC))
        f.write(table)
        for _md5, _length, blocks in stored:
            for _size, data in blocks:
                f.write(data)
    temp.replace(path)
