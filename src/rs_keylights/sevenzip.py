from __future__ import annotations

import lzma
import struct
import zlib
from dataclasses import dataclass, field

# The 7z archives inside the game's cache.psarc (cache0.7z to cache8.7z): read, and written again with some of their
# files changed. Only what those archives use: folders of one coder (stored, LZMA, LZMA2), the header packed or not.
# A rewrite keeps every folder it doesn't change byte for byte, and the files' order, names, times and attributes;
# a folder with a changed file is packed again with its own coder.

SIGNATURE = b"7z\xbc\xaf\x27\x1c"
COPY, LZMA1, LZMA2 = b"\x00", b"\x03\x01\x01", b"\x21"
END, HEADER, ENCODED_HEADER = 0x00, 0x01, 0x17
MAIN_STREAMS, FILES_INFO, PACK_INFO, UNPACK_INFO, SUBSTREAMS = 0x04, 0x05, 0x06, 0x07, 0x08
SIZE, CRC, FOLDER, CODERS_UNPACK_SIZE, UNPACK_STREAMS = 0x09, 0x0A, 0x0B, 0x0C, 0x0D
EMPTY_STREAM, NAMES, DUMMY = 0x0E, 0x11, 0x19


class SevenZipError(ValueError):
    pass


def _number(data: bytes, at: int) -> tuple[int, int]:
    """7z's number at data[at]: (it, where the next thing is)."""
    first, at = data[at], at + 1
    mask = 0x80
    for extra in range(8):
        if not first & mask:
            high = first & (mask - 1)
            return int.from_bytes(data[at:at + extra], "little") | (high << (8 * extra)), at + extra
        mask >>= 1
    return int.from_bytes(data[at:at + 8], "little"), at + 8


def _write_number(value: int) -> bytes:
    for extra in range(8):
        if value < 1 << (7 * (extra + 1)):
            return bytes([((0xFF00 >> extra) & 0xFF) | (value >> (8 * extra))]) + value.to_bytes(8, "little")[:extra]
    return b"\xff" + value.to_bytes(8, "little")


def _bits(data: bytes, at: int, count: int) -> tuple[list[bool], int]:
    """A bit vector (all set: its first byte nonzero, then none)."""
    if data[at]:
        return [True] * count, at + 1
    at += 1
    size = (count + 7) // 8
    return [bool(data[at + k // 8] & (0x80 >> (k % 8))) for k in range(count)], at + size


@dataclass
class Folder:
    coder: bytes
    props: bytes
    unpack_size: int
    packed: bytes = b""
    sizes: list[int] = field(default_factory=list)  # (its files' sizes, in order)
    crc: bool = False  # (the folder's own CRC is stored)

    def unpacked(self) -> bytes:
        return _decode(self.coder, self.props, self.packed, self.unpack_size)


def _filters(coder: bytes, props: bytes) -> list[dict]:
    if coder == LZMA1:
        lclppb, dict_size = props[0], int.from_bytes(props[1:5], "little")
        return [{"id": lzma.FILTER_LZMA1, "lc": lclppb % 9, "lp": lclppb // 9 % 5, "pb": lclppb // 45,
                 "dict_size": dict_size}]
    if coder == LZMA2:
        bits = props[0]
        dict_size = 0xFFFFFFFF if bits == 40 else (2 | (bits & 1)) << (bits // 2 + 11)
        return [{"id": lzma.FILTER_LZMA2, "dict_size": dict_size}]
    raise SevenZipError(f"7z coder {coder.hex()}")


def _decode(coder: bytes, props: bytes, packed: bytes, size: int) -> bytes:
    if coder == COPY:
        return packed[:size]
    try:
        out = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=_filters(coder, props)).decompress(packed, size)
    except lzma.LZMAError as exc:
        raise SevenZipError(f"7z data: {exc}") from exc
    if len(out) != size:
        raise SevenZipError("7z data: short")
    return out


def _encode(coder: bytes, props: bytes, data: bytes) -> bytes:
    if coder == COPY:
        return data
    return lzma.compress(data, lzma.FORMAT_RAW, filters=_filters(coder, props))


class SevenZip:
    """A 7z archive's files: names (as stored, in order), and each one's content."""

    def __init__(self, data: bytes):
        if data[:6] != SIGNATURE:
            raise SevenZipError("not a 7z archive")
        offset, size = struct.unpack_from("<QQ", data, 12)
        self._data = data
        self.folders: list[Folder] = []
        self.names: list[str] = []
        self.empty: list[bool] = []
        self.properties: dict[int, bytes] = {}  # (the files' other properties as stored: times, attributes)
        try:
            self._header(data[32 + offset:32 + offset + size])
        except (IndexError, struct.error) as exc:
            raise SevenZipError(f"7z header: {exc}") from exc
        self._cache: dict[int, bytes] = {}

    def _header(self, header: bytes) -> None:
        while header[0] == ENCODED_HEADER:
            folders, _at = self._streams(header, 1)
            header = folders[0].unpacked()
        if header[0] != HEADER:
            raise SevenZipError(f"7z header kind {header[0]}")
        at = 1
        if header[at] == MAIN_STREAMS:
            self.folders, at = self._streams(header, at + 1)
        if header[at] == FILES_INFO:
            count, at = _number(header, at + 1)
            self.empty = [False] * count
            while header[at] != END:
                kind, (size, at) = header[at], _number(header, at + 1)
                body = header[at:at + size]
                at += size
                if kind == EMPTY_STREAM:
                    self.empty = [bool(body[k // 8] & (0x80 >> (k % 8))) for k in range(count)]
                elif kind == NAMES:
                    if body[0]:
                        raise SevenZipError("7z names elsewhere")
                    self.names = body[1:].decode("utf-16-le").split("\0")[:count]
                elif kind != DUMMY:
                    self.properties[kind] = bytes(body)

    def _streams(self, data: bytes, at: int) -> tuple[list[Folder], int]:
        pack_at, pack_sizes, folders = 0, [], []
        counts: list[int] | None = None
        while True:
            kind, at = data[at], at + 1
            if kind == END:
                break
            if kind == PACK_INFO:
                pack_at, at = _number(data, at)
                count, at = _number(data, at)
                while data[at] != END:
                    if data[at] == SIZE:
                        at += 1
                        for _ in range(count):
                            size, at = _number(data, at)
                            pack_sizes.append(size)
                    elif data[at] == CRC:
                        defined, at = _bits(data, at + 1, count)
                        at += 4 * sum(defined)
                    else:
                        raise SevenZipError(f"7z pack info {data[at]}")
                at += 1
            elif kind == UNPACK_INFO:
                if data[at] != FOLDER:
                    raise SevenZipError("7z folders")
                count, at = _number(data, at + 1)
                if data[at] != 0:
                    raise SevenZipError("7z folders elsewhere")
                at += 1
                for _ in range(count):
                    coders, at = _number(data, at)
                    flags = data[at]
                    if coders != 1 or flags & 0x10:
                        raise SevenZipError("7z folder of several coders")
                    coder = data[at + 1:at + 1 + (flags & 0x0F)]
                    at += 1 + (flags & 0x0F)
                    props = b""
                    if flags & 0x20:
                        size, at = _number(data, at)
                        props, at = data[at:at + size], at + size
                    folders.append(Folder(bytes(coder), bytes(props), 0))
                if data[at] != CODERS_UNPACK_SIZE:
                    raise SevenZipError("7z folder sizes")
                at += 1
                for folder in folders:
                    folder.unpack_size, at = _number(data, at)
                if data[at] == CRC:
                    defined, at = _bits(data, at + 1, len(folders))
                    at += 4 * sum(defined)
                    for folder, has in zip(folders, defined):
                        folder.crc = has
                if data[at] != END:
                    raise SevenZipError(f"7z unpack info {data[at]}")
                at += 1
            elif kind == SUBSTREAMS:
                counts = [1] * len(folders)
                while True:
                    part, at = data[at], at + 1
                    if part == END:
                        break
                    if part == UNPACK_STREAMS:
                        counts = []
                        for _ in folders:
                            n, at = _number(data, at)
                            counts.append(n)
                    elif part == SIZE:
                        for folder, n in zip(folders, counts):
                            if n:
                                these = []
                                for _ in range(n - 1):
                                    size, at = _number(data, at)
                                    these.append(size)
                                folder.sizes = these + [folder.unpack_size - sum(these)]
                    elif part == CRC:
                        n = sum(c for c, folder in zip(counts, folders) if not (c == 1 and folder.crc))
                        defined, at = _bits(data, at, n)
                        at += 4 * sum(defined)
                    else:
                        raise SevenZipError(f"7z substreams {part}")
            else:
                raise SevenZipError(f"7z streams {kind}")
        start = 32 + pack_at
        for folder, size in zip(folders, pack_sizes):
            folder.packed = self._data[start:start + size]
            start += size
        for folder, n in zip(folders, counts or [1] * len(folders)):
            if not folder.sizes:
                folder.sizes = [folder.unpack_size] if n == 1 else []
            if len(folder.sizes) != n:
                raise SevenZipError("7z substream sizes")
        return folders, at

    def streams(self) -> dict[str, tuple[int, int, int]]:
        """Each file with content: (its folder, where in the folder, its size)."""
        out, stored = {}, [name for name, empty in zip(self.names, self.empty or [False] * len(self.names))
                           if not empty]
        streams = [(f, sum(folder.sizes[:k]), size) for f, folder in enumerate(self.folders)
                   for k, size in enumerate(folder.sizes)]
        if len(streams) != len(stored):
            raise SevenZipError("7z files and streams differ")
        for name, stream in zip(stored, streams):
            out[name.replace("\\", "/")] = stream
        return out

    def files(self) -> list[str]:
        return list(self.streams())

    def read(self, name: str) -> bytes:
        folder, start, size = self.streams()[name]
        if folder not in self._cache:
            self._cache = {folder: self.folders[folder].unpacked()}
        return self._cache[folder][start:start + size]

    def rewritten(self, changed: dict[str, bytes]) -> bytes:
        """This archive with these files' contents changed (each folder with one of them packed again)."""
        where = self.streams()
        missing = set(changed) - set(where)
        if missing:
            raise SevenZipError(f"not in the archive: {', '.join(sorted(missing))}")
        folders = []
        for f, folder in enumerate(self.folders):
            names = [name for name, (g, _start, _size) in where.items() if g == f]
            if not any(name in changed for name in names):
                folders.append(folder)
                continue
            content = folder.unpacked()
            parts = [changed.get(name, content[where[name][1]:where[name][1] + where[name][2]]) for name in names]
            whole = b"".join(parts)
            folders.append(Folder(folder.coder, folder.props, len(whole), _encode(folder.coder, folder.props, whole),
                                  [len(part) for part in parts]))
        crcs = []
        for folder in folders:
            content = folder.unpacked()
            at = 0
            for size in folder.sizes:
                crcs.append(zlib.crc32(content[at:at + size]))
                at += size
        header = bytearray([HEADER, MAIN_STREAMS, PACK_INFO]) + _write_number(0) + _write_number(len(folders))
        header += bytes([SIZE]) + b"".join(_write_number(len(folder.packed)) for folder in folders) + bytes([END])
        header += bytes([UNPACK_INFO, FOLDER]) + _write_number(len(folders)) + b"\x00"
        for folder in folders:
            header += b"\x01" + bytes([len(folder.coder) | (0x20 if folder.props else 0)]) + folder.coder
            if folder.props:
                header += _write_number(len(folder.props)) + folder.props
        header += bytes([CODERS_UNPACK_SIZE]) + b"".join(_write_number(folder.unpack_size) for folder in folders)
        header += bytes([END, SUBSTREAMS])
        if any(len(folder.sizes) != 1 for folder in folders):
            header += bytes([UNPACK_STREAMS]) + b"".join(_write_number(len(folder.sizes)) for folder in folders)
            header += bytes([SIZE]) + b"".join(_write_number(size) for folder in folders for size in folder.sizes[:-1])
        header += bytes([CRC, 1]) + b"".join(struct.pack("<I", crc) for crc in crcs) + bytes([END, END])
        count = len(self.names)
        header += bytes([FILES_INFO]) + _write_number(count)
        if any(self.empty):
            vector = bytearray((count + 7) // 8)
            for k, empty in enumerate(self.empty):
                if empty:
                    vector[k // 8] |= 0x80 >> (k % 8)
            header += bytes([EMPTY_STREAM]) + _write_number(len(vector)) + vector
        names = b"\x00" + b"".join(name.encode("utf-16-le") + b"\x00\x00" for name in self.names)
        header += bytes([NAMES]) + _write_number(len(names)) + names
        for kind in sorted(self.properties):
            header += bytes([kind]) + _write_number(len(self.properties[kind])) + self.properties[kind]
        header += bytes([END, END])
        body = b"".join(folder.packed for folder in folders)
        props = b"\x5d" + (1 << 20).to_bytes(4, "little")  # (the header packed as the game's are: LZMA, 1 MiB)
        packed = _encode(LZMA1, props, bytes(header))
        encoded = (bytes([ENCODED_HEADER, PACK_INFO]) + _write_number(len(body)) + _write_number(1) + bytes([SIZE])
                   + _write_number(len(packed)) + bytes([END, UNPACK_INFO, FOLDER]) + _write_number(1) + b"\x00"
                   + b"\x01\x23" + LZMA1 + _write_number(len(props)) + props + bytes([CODERS_UNPACK_SIZE])
                   + _write_number(len(header)) + bytes([CRC, 1]) + struct.pack("<I", zlib.crc32(bytes(header)))
                   + bytes([END, END]))
        start = struct.pack("<QQI", len(body) + len(packed), len(encoded), zlib.crc32(encoded))
        return SIGNATURE + b"\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start + body + packed + encoded
