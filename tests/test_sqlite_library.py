"""Tests for the iPhone-generation SQLite library mirror.

Uses a minimal synthetic schema (see helpers.py) -- no real user data.
"""

import sqlite3

from ipodmanager.db import sqlite_library as sqlib
from ipodmanager.db.itunesdb import TrackMeta

from .helpers import MASTER_CONTAINER_PID, build_minimal_library_itdb, build_minimal_locations_itdb


def test_dbid_pid_conversion_is_bit_identical():
    # a real example from an actual device: dbid (unsigned) <-> pid (signed)
    dbid = 17046730565867733648
    pid = -1400013507841817968
    assert sqlib.dbid_to_pid(dbid) == pid
    assert sqlib.pid_to_dbid(pid) == dbid


def test_add_track_creates_expected_rows(tmp_path):
    lib_path = tmp_path / "Library.itdb"
    loc_path = tmp_path / "Locations.itdb"
    build_minimal_library_itdb(lib_path)
    build_minimal_locations_itdb(loc_path)

    meta = TrackMeta(
        track_id=1, dbid=17046730565867733648,
        title="Test Song", artist="Test Artist", album="Test Album", genre="Rock",
        length_ms=200000, track_nr=3, tracks_total=10, bitrate=256, samplerate=44100,
        size=5_000_000,
    )
    sqlib.add_track(lib_path, loc_path, meta, "F08/ABCD.m4a")

    lib = sqlite3.connect(str(lib_path))
    lib.row_factory = sqlite3.Row
    cur = lib.cursor()
    pid = sqlib.dbid_to_pid(meta.dbid)

    cur.execute("SELECT * FROM item WHERE pid=?", (pid,))
    item = dict(cur.fetchone())
    assert item["title"] == "Test Song"
    assert item["artist"] == "Test Artist"
    assert item["album"] == "Test Album"
    assert item["total_time_ms"] == 200000.0
    assert item["track_number"] == 3

    cur.execute("SELECT genre FROM genre_map WHERE id=?", (item["genre_id"],))
    assert cur.fetchone()[0] == "Rock"

    cur.execute("SELECT * FROM avformat_info WHERE item_pid=?", (pid,))
    av = dict(cur.fetchone())
    assert av["bit_rate"] == 256
    assert av["sample_rate"] == 44100.0

    cur.execute("SELECT container_pid FROM item_to_container WHERE item_pid=?", (pid,))
    assert cur.fetchone()[0] == MASTER_CONTAINER_PID

    cur.execute("SELECT * FROM ext_item_view_membership WHERE item_pid=?", (pid,))
    assert cur.fetchone() is not None
    lib.close()

    loc = sqlite3.connect(str(loc_path))
    cur = loc.cursor()
    cur.execute("SELECT location, base_location_id, file_size FROM location WHERE item_pid=?", (pid,))
    row = cur.fetchone()
    assert row == ("F08/ABCD.m4a", 1, 5_000_000)
    loc.close()


def test_add_track_reuses_existing_genre_and_artist(tmp_path):
    lib_path = tmp_path / "Library.itdb"
    loc_path = tmp_path / "Locations.itdb"
    build_minimal_library_itdb(lib_path)
    build_minimal_locations_itdb(loc_path)

    for i in range(2):
        meta = TrackMeta(
            track_id=i, dbid=1000 + i,
            title=f"Song {i}", artist="Same Artist", album="Same Album", genre="Same Genre",
        )
        sqlib.add_track(lib_path, loc_path, meta, f"F00/T{i}.m4a")

    lib = sqlite3.connect(str(lib_path))
    cur = lib.cursor()
    cur.execute("SELECT count(*) FROM genre_map WHERE genre='Same Genre'")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT count(*) FROM item_artist WHERE name='Same Artist'")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT count(*) FROM album WHERE name='Same Album'")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT count(*) FROM item")
    assert cur.fetchone()[0] == 2
    lib.close()


def test_remove_track_deletes_all_rows(tmp_path):
    lib_path = tmp_path / "Library.itdb"
    loc_path = tmp_path / "Locations.itdb"
    build_minimal_library_itdb(lib_path)
    build_minimal_locations_itdb(loc_path)

    meta = TrackMeta(track_id=1, dbid=17046730565867733648, title="X", artist="Y")
    sqlib.add_track(lib_path, loc_path, meta, "F00/X.m4a")
    sqlib.remove_track(lib_path, loc_path, meta.dbid)

    pid = sqlib.dbid_to_pid(meta.dbid)
    lib = sqlite3.connect(str(lib_path))
    cur = lib.cursor()
    for table, col in [("item", "pid"), ("avformat_info", "item_pid"), ("item_to_container", "item_pid"), ("ext_item_view_membership", "item_pid")]:
        cur.execute(f"SELECT count(*) FROM {table} WHERE {col}=?", (pid,))
        assert cur.fetchone()[0] == 0, table
    lib.close()

    loc = sqlite3.connect(str(loc_path))
    cur = loc.cursor()
    cur.execute("SELECT count(*) FROM location WHERE item_pid=?", (pid,))
    assert cur.fetchone()[0] == 0
    loc.close()
