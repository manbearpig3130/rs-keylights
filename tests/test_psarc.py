import os

import pytest
from builders import psarc

from rs_keylights.psarc import Archive, ArchiveError


@pytest.mark.parametrize("encrypt", [True, False])
def test_reads_names_and_files(tmp_path, encrypt):
    path = tmp_path / "song_m.psarc"
    path.write_bytes(psarc({"a/one.txt": b"hello" * 100, "b/two.bin": b"\x01\x02"}, encrypt=encrypt))
    with Archive(path) as archive:
        assert archive.names == ["a/one.txt", "b/two.bin"]
        assert archive.read("a/one.txt") == b"hello" * 100
        assert archive.read("b/two.bin") == b"\x01\x02"


def test_reads_files_over_several_blocks_including_stored_ones(tmp_path):
    noise = os.urandom(70000)  # doesn't compress: stored as is
    text = b"abc" * 50000  # compresses
    path = tmp_path / "big.psarc"
    path.write_bytes(psarc({"noise": noise, "text": text}))
    with Archive(path) as archive:
        assert archive.read("noise") == noise
        assert archive.read("text") == text


def test_missing_file(tmp_path):
    path = tmp_path / "song.psarc"
    path.write_bytes(psarc({"x": b"y"}))
    with Archive(path) as archive, pytest.raises(ArchiveError, match="has no nope"):
        archive.read("nope")


def test_not_an_archive(tmp_path):
    path = tmp_path / "junk.psarc"
    path.write_bytes(b"\x00" * 64)
    with pytest.raises(ArchiveError, match="not a .psarc"):
        Archive(path)
