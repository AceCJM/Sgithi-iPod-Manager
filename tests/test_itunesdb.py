"""Round-trip tests for the iTunesDB binary parser/writer.

These use a synthetic (but format-correct) mhbd, built the same way real
iTunes would, since we don't have a real device dump available in CI. They
verify the parser/writer is self-consistent; final confidence still comes
from testing against a real device (see README).
"""

import struct
import zlib

from ipodmanager.db import itunesdb as idb

from .helpers import build_empty_mhbd, build_empty_mhbd_compressed


def test_parse_empty_db():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    assert db.tracks == []
    assert db.playlists == []


def test_add_track_roundtrip():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    meta = idb.TrackMeta(
        track_id=0,
        dbid=0,
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        genre="Rock",
        ipod_location=idb.relpath_to_ipod_path("iPod_Control/Music/F00/TEST.m4a"),
        size=123456,
        length_ms=180000,
        track_nr=3,
        tracks_total=12,
        year=2024,
        bitrate=256,
        samplerate=44100,
    )
    db.add_track(meta)
    assert len(db.tracks) == 1
    assert len(db.master_playlist().mhip_track_ids) == 1

    serialized = db.serialize()
    db2 = idb.ITunesDB.parse(serialized)
    assert len(db2.tracks) == 1
    t = db2.tracks[0]
    assert t.display["title"] == "Test Song"
    assert t.display["artist"] == "Test Artist"
    assert t.display["album"] == "Test Album"
    assert t.display["genre"] == "Rock"
    assert t.display["ipod_location"] == ":iPod_Control:Music:F00:TEST.m4a"
    assert t.display["size"] == 123456
    assert t.display["length_ms"] == 180000
    assert t.display["track_nr"] == 3
    assert t.display["tracks_total"] == 12
    assert t.display["year"] == 2024
    assert t.display["bitrate"] == 256
    assert db2.master_playlist().mhip_track_ids == [t.track_id]


def test_delete_track_removes_playlist_reference():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    ids = []
    for i in range(3):
        meta = idb.TrackMeta(
            track_id=0, dbid=0, title=f"Song {i}",
            ipod_location=idb.relpath_to_ipod_path(f"iPod_Control/Music/F00/S{i}.m4a"),
        )
        ids.append(db.add_track(meta).track_id)

    victim = ids[1]
    assert db.remove_track(victim) is True
    assert victim not in [t.track_id for t in db.tracks]
    assert victim not in db.master_playlist().mhip_track_ids
    assert len(db.tracks) == 2

    # round-trip again after the delete
    db2 = idb.ITunesDB.parse(db.serialize())
    assert len(db2.tracks) == 2
    assert victim not in db2.master_playlist().mhip_track_ids


def test_unknown_mhsd_sections_are_preserved_verbatim():
    base = build_empty_mhbd()
    # splice in a fake, unrecognized mhsd section (index 4, "album list")
    # the way real iTunes does -- we should copy it through unmodified.
    fake_index4 = struct.pack("<4siii", b"mhsd", 96, 96 + 16, 4) + b"\x00" * 80 + b"SOME_OPAQUE_DATA"
    header = bytearray(base)
    mhbd_header_len = struct.unpack_from("<I", header, 4)[0]
    new_buf = bytes(header[:mhbd_header_len]) + bytes(header[mhbd_header_len:]) + fake_index4
    struct.pack_into("<I", new_buf := bytearray(new_buf), 0x14, 3)
    struct.pack_into("<I", new_buf, 8, len(new_buf))

    db = idb.ITunesDB.parse(bytes(new_buf))
    assert len(db.sections) == 3
    kind, raw = db.sections[2]
    assert kind == "raw"
    assert raw == fake_index4

    reserialized = db.serialize()
    db2 = idb.ITunesDB.parse(reserialized)
    assert db2.sections[2] == ("raw", fake_index4)


def test_playlists_at_nonstandard_index_are_found_by_magic():
    """A real iPhone 3G (iTunesDB version 110) was found writing its real
    playlist list at mhsd index 3, not the classic index 2 -- with a
    second, always-empty mhlp-shaped section at index 5 holding Apple's
    built-in library category placeholders (Music/Movies/TV Shows/...).
    The parser must find the real one by its inner 'mhlp' magic (not by
    index), must not treat the placeholder list as real playlists, and
    must preserve every section's original index number on write-back --
    silently renumbering to the classic 1/2 convention would desync this
    device's own firmware expectations.
    """
    track_raw = idb.build_new_mhit(idb.TrackMeta(
        track_id=1, dbid=1, title="Song One",
        ipod_location=idb.relpath_to_ipod_path("iTunes_Control/Music/F00/A.m4a"),
    ))

    master = idb.build_new_master_playlist("iPhone")
    master.add_track(1)
    user_pl = idb.build_new_playlist("Road Trip")
    user_pl.add_track(1)

    placeholder_names = ["Music", "Movies", "TV Shows", "Audiobooks", "Tones", "Rentals", "Books"]
    placeholders = [idb.build_new_playlist(n) for n in placeholder_names]

    def mhsd(index: int, inner_body: bytes) -> bytes:
        return struct.pack("<4siii", b"mhsd", 96, 96 + len(inner_body), index) + b"\x00" * 80 + inner_body

    def mhlt(tracks_raw: list) -> bytes:
        return struct.pack("<4sii", b"mhlt", 92, len(tracks_raw)) + b"\x00" * 80 + b"".join(tracks_raw)

    def mhlp(playlists: list) -> bytes:
        return struct.pack("<4sii", b"mhlp", 92, len(playlists)) + b"\x00" * 80 + b"".join(pl.serialize() for pl in playlists)

    body = b"".join([
        mhsd(1, mhlt([track_raw])),
        mhsd(3, mhlp([master, user_pl])),
        mhsd(5, mhlp(placeholders)),
    ])
    header = bytearray(b"\x00" * 244)
    struct.pack_into("<4sIIII", header, 0, b"mhbd", 244, 244 + len(body), 1, 110)
    struct.pack_into("<I", header, 0x14, 3)
    data = bytes(header) + body

    db = idb.ITunesDB.parse(data)
    assert len(db.tracks) == 1
    names = {p.title() for p in db.playlists}
    assert "Road Trip" in names
    assert not any(n in names for n in placeholder_names)
    assert db.master_playlist().title() == "iPhone"

    reserialized = db.serialize()
    header_len = struct.unpack_from("<I", reserialized, 4)[0]
    pos = header_len
    seen_indices = []
    while pos < len(reserialized):
        _magic, mhsd_total_len, index = struct.unpack_from("<iii", reserialized, pos + 4)
        seen_indices.append(index)
        pos += mhsd_total_len
    assert seen_indices == [1, 3, 5]

    db2 = idb.ITunesDB.parse(reserialized)
    names2 = {p.title() for p in db2.playlists}
    assert "Road Trip" in names2
    assert not any(n in names2 for n in placeholder_names)


def test_ipod_path_conversion():
    assert idb.relpath_to_ipod_path("iPod_Control/Music/F00/A.m4a") == ":iPod_Control:Music:F00:A.m4a"
    assert idb.ipod_path_to_relpath(":iPod_Control:Music:F00:A.m4a") == "iPod_Control/Music/F00/A.m4a"


def test_compressed_db_parses_and_roundtrips():
    """iPhone/iPod Touch-generation devices zlib-compress everything after
    the mhbd header (confirmed against a real iPhone 3G's iTunesCDB)."""
    data = build_empty_mhbd_compressed()
    db = idb.ITunesDB.parse(data)
    assert db.tracks == []

    meta = idb.TrackMeta(
        track_id=0, dbid=0, title="Compressed Track",
        ipod_location=idb.relpath_to_ipod_path("iTunes_Control/Music/F00/C.m4a"),
    )
    db.add_track(meta)

    reserialized = db.serialize()
    # still flagged as compressed, and the body must actually decompress
    header_len = struct.unpack_from("<I", reserialized, 4)[0]
    unknown1 = struct.unpack_from("<I", reserialized, 0x0C)[0]
    assert unknown1 == 2
    zlib.decompress(reserialized[header_len:])  # raises if not valid zlib

    db2 = idb.ITunesDB.parse(reserialized)
    assert len(db2.tracks) == 1
    assert db2.tracks[0].display["title"] == "Compressed Track"


def test_get_or_create_playlist_creates_named_non_master_playlist():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    pl = db.get_or_create_playlist("Road Trip")
    assert pl.is_master is False
    assert pl.title() == "Road Trip"
    assert [p for p in db.playlists if not p.is_master] == [pl]

    # calling again with the same name reuses it rather than duplicating
    again = db.get_or_create_playlist("Road Trip")
    assert again is pl
    assert len([p for p in db.playlists if not p.is_master]) == 1

    other = db.get_or_create_playlist("Chill")
    assert other is not pl
    assert len([p for p in db.playlists if not p.is_master]) == 2


def test_named_playlist_roundtrips_with_members():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    t1 = db.add_track(idb.TrackMeta(track_id=0, dbid=0, title="A", ipod_location=idb.relpath_to_ipod_path("iPod_Control/Music/F00/A.m4a")))
    t2 = db.add_track(idb.TrackMeta(track_id=0, dbid=0, title="B", ipod_location=idb.relpath_to_ipod_path("iPod_Control/Music/F00/B.m4a")))

    pl = db.get_or_create_playlist("Faves")
    pl.add_track(t1.track_id)
    pl.add_track(t2.track_id)

    db2 = idb.ITunesDB.parse(db.serialize())
    found = db2.find_playlist_by_name("Faves")
    assert found is not None
    assert found.mhip_track_ids == [t1.track_id, t2.track_id]
    # the master playlist is untouched and still separately findable
    assert db2.master_playlist().is_master is True
    assert db2.find_playlist_by_name("iPod") is None  # master isn't a "named" playlist match


def test_set_artwork_link_roundtrips():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    t = db.add_track(idb.TrackMeta(track_id=0, dbid=0, title="Art", ipod_location=idb.relpath_to_ipod_path("iPod_Control/Music/F00/X.m4a")))
    t.set_artwork_link(100, thumb_count=2)

    db2 = idb.ITunesDB.parse(db.serialize())
    raw = db2.tracks[0].raw
    has_artwork = raw[0xA4]
    artwork_count = struct.unpack_from("<H", raw, 0x7C)[0]
    mhii_link = struct.unpack_from("<I", raw, 0x160)[0]
    assert has_artwork == 2
    assert artwork_count == 2
    assert mhii_link == 100

    # clearing it back to "no artwork" works too
    db2.tracks[0].set_artwork_link(0)
    raw2 = db2.tracks[0].raw
    assert raw2[0xA4] == 1
    assert struct.unpack_from("<H", raw2, 0x7C)[0] == 0
    assert struct.unpack_from("<I", raw2, 0x160)[0] == 0


def test_rename_playlist_preserves_membership_and_roundtrips():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    t = db.add_track(idb.TrackMeta(track_id=0, dbid=0, title="A", ipod_location=idb.relpath_to_ipod_path("iPod_Control/Music/F00/A.m4a")))
    pl = db.get_or_create_playlist("Old Name")
    pl.add_track(t.track_id)
    pl.set_title("New Name")

    assert pl.title() == "New Name"
    assert pl.mhip_track_ids == [t.track_id]

    db2 = idb.ITunesDB.parse(db.serialize())
    assert db2.find_playlist_by_name("Old Name") is None
    found = db2.find_playlist_by_name("New Name")
    assert found is not None
    assert found.mhip_track_ids == [t.track_id]


def test_delete_playlist_removes_it_but_not_its_tracks():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    t = db.add_track(idb.TrackMeta(track_id=0, dbid=0, title="A", ipod_location=idb.relpath_to_ipod_path("iPod_Control/Music/F00/A.m4a")))
    pl = db.get_or_create_playlist("Temp")
    pl.add_track(t.track_id)

    assert db.delete_playlist(pl) is True
    assert db.find_playlist_by_name("Temp") is None
    # the track itself and its master-playlist membership are untouched
    assert t.track_id in [tr.track_id for tr in db.tracks]
    assert t.track_id in db.master_playlist().mhip_track_ids

    db2 = idb.ITunesDB.parse(db.serialize())
    assert db2.find_playlist_by_name("Temp") is None
    assert len(db2.tracks) == 1


def test_delete_playlist_refuses_master():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    master = db.master_playlist()
    assert db.delete_playlist(master) is False
    assert db.master_playlist() is master


def _splice_raw_section_with_mhip(base: bytes, track_id: int, index: int = 3) -> bytes:
    """Same splicing technique as test_unknown_mhsd_sections_are_preserved_verbatim,
    but the payload is a real (fixed-shape) mhip record referencing track_id --
    standing in for real iTunes's mhsd type 3 (separately-formatted podcast
    playlist copy), the one raw section type that can hold a stale reference.
    """
    mhip = idb.build_mhip(track_id)
    fake_section = struct.pack("<4siii", b"mhsd", 96, 96 + len(mhip), index) + b"\x00" * 80 + mhip
    header_len = struct.unpack_from("<I", base, 4)[0]
    new_buf = bytearray(base) + fake_section
    struct.pack_into("<I", new_buf, 0x14, 3)  # num_children: was 2, now 3
    struct.pack_into("<I", new_buf, 8, len(new_buf))
    return bytes(new_buf)


def test_has_stale_raw_reference_detects_mhip_in_unknown_section():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    t = db.add_track(idb.TrackMeta(track_id=0, dbid=0, title="A", ipod_location=idb.relpath_to_ipod_path("iPod_Control/Music/F00/A.m4a")))

    spliced = _splice_raw_section_with_mhip(db.serialize(), t.track_id)
    db2 = idb.ITunesDB.parse(spliced)

    assert db2.has_stale_raw_reference(t.track_id) is True
    assert db2.has_stale_raw_reference(t.track_id + 999) is False


def test_has_stale_raw_reference_false_when_no_raw_sections_reference_it():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    t = db.add_track(idb.TrackMeta(track_id=0, dbid=0, title="A", ipod_location=idb.relpath_to_ipod_path("iPod_Control/Music/F00/A.m4a")))
    assert db.has_stale_raw_reference(t.track_id) is False


def test_delete_track_reports_stale_raw_reference_without_modifying_it():
    db = idb.ITunesDB.parse(build_empty_mhbd())
    t = db.add_track(idb.TrackMeta(track_id=0, dbid=0, title="A", ipod_location=idb.relpath_to_ipod_path("iPod_Control/Music/F00/A.m4a")))

    spliced = _splice_raw_section_with_mhip(db.serialize(), t.track_id)
    db2 = idb.ITunesDB.parse(spliced)
    raw_before = db2.sections[2][1]

    assert db2.has_stale_raw_reference(t.track_id) is True
    db2.remove_track(t.track_id)
    # the real track/playlist references are gone...
    assert t.track_id not in [tr.track_id for tr in db2.tracks]
    # ...but the raw section is deliberately left untouched (not rewritten)
    assert db2.sections[2][1] == raw_before
    assert db2.has_stale_raw_reference(t.track_id) is True
