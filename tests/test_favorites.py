import struct
import zlib

import pytest

from rs_keylights import favorites

PROFILE = (b'{\n"SongListsRoot" : {\n"SongLists" : \n[\n[\n"A"\n]\n]\n},\n"FavoritesListRoot" : {\n'
           b'"FavoritesList" : \n[\n"NiAmonAGodsO",\n"AmonPurs"\n]\n},\n"Stats" : {\n"Score" : 1.5\n}\n}\0')


def _profile(tmp_path, data=PROFILE):
    """A profile as the game writes it (its header's 8 bytes of its own kept as they are)."""
    header = favorites.MAGIC + struct.pack("<I", 1) + bytes.fromhex("9d835c0200001001") + struct.pack("<I", 0)
    path = tmp_path / "steam" / "123" / "221680" / "remote" / "ABC_PRFLDB"
    path.parent.mkdir(parents=True)
    path.write_bytes(favorites.encode(header, data))
    return path


def test_a_profiles_favourites_are_read_and_written_the_rest_of_it_byte_for_byte(tmp_path):
    path = _profile(tmp_path)
    assert favorites.profile_path(tmp_path / "steam") == path
    assert favorites.read(path) == ["NiAmonAGodsO", "AmonPurs"]
    before = favorites.decode(path.read_bytes())
    favorites.write(path, ["NiAmonAGodsO", "AmonPurs", "CusRammsteinRADIO"], backups=tmp_path / "backups")
    header, data = favorites.decode(path.read_bytes())
    assert favorites.read(path) == ["NiAmonAGodsO", "AmonPurs", "CusRammsteinRADIO"]
    assert header[:16] == before[0][:16] and struct.unpack("<I", header[16:20])[0] == len(data)
    start, end = data.index(b'"FavoritesList"'), before[1].index(b'"FavoritesList"')
    assert data[:start] == before[1][:end]  # (what's before the list as it was)
    assert data.endswith(b'\n]\n},\n"Stats" : {\n"Score" : 1.5\n}\n}\0')  # (and after it)
    assert [p.name.split("_", 1)[1] for p in (tmp_path / "backups").iterdir()] == ["ABC_PRFLDB"]  # (saved first)
    favorites.write(path, [], backups=tmp_path / "backups")
    assert favorites.read(path) == [] and b'"FavoritesList" : \n[\n]' in favorites.decode(path.read_bytes())[1]


def test_its_own_bytes_come_back_as_they_were_when_nothing_changes(tmp_path):
    path = _profile(tmp_path)
    raw = path.read_bytes()
    header, data = favorites.decode(raw)
    assert favorites.encode(header, favorites.with_favorites(data, favorites.favorites_in(data))) == raw
    assert zlib.decompress(favorites._cipher().decryptor().update(raw[20:]))[:1] == b"{"


def test_what_isnt_a_profile_says_so(tmp_path):
    with pytest.raises(favorites.ProfileError):
        favorites.decode(b"NOPE" + bytes(40))
    assert favorites.song_key("/x/dlc/amon_m.psarc#NiAmonAGodsO") == "NiAmonAGodsO"
