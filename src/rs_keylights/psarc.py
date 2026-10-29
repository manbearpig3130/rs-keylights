from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import BinaryIO

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


class ArchiveError(ValueError):
    pass


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
        if magic != b"PSAR" or entry_size < 30 or entry_count < 1:
            raise ArchiveError(f"{self.path.name}: not a .psarc")
        toc = self._file.read(toc_length - HEADER.size)
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
            if stored != min(self._block_size, length - len(out)):
                try:
                    chunk = zlib.decompress(chunk)
                except zlib.error:
                    pass  # some archives have blocks that aren't valid zlib; others keep them as stored
            out += chunk
        return bytes(out[:length])
