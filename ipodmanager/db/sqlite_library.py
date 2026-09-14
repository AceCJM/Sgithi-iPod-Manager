"""Mirrors track add/remove into the iPhone-generation "iTunes Library"
SQLite bundle (iTunes_Control/iTunes/iTunes Library.itlp/{Library,Locations}.itdb).

Unlike the classic iTunesDB, this is a genuinely standard SQLite database
-- no binary-format reverse engineering needed, Python's stdlib `sqlite3`
handles the file format entirely. The only research question was *which*
tables/columns matter, answered by inspecting a real iPhone 3G's actual
database (see README/session notes): the on-device Music app on this
generation reads from THIS database, not the classic iTunesDB directly --
confirmed empirically (writes to the classic format alone never appeared
on-device; this database's `item` table is exactly the 70-track library a
prior libgpod-based sync produced).

Track identity is shared across both databases: this database's `item.pid`
is bit-identical to the classic format's `dbid`, just reinterpreted as a
signed 64-bit integer instead of unsigned (confirmed against a real track
present in both). So a track's classic-format dbid is reused directly as
its SQLite pid -- no separate ID scheme to invent.

Only the columns confirmed present on a real populated row are set;
anything else is left at its schema default. Sort-order caching
(`sort_map`) and the finer-grained item_to_album/item_to_artist/
item_to_composer junction tables (present in the schema but empty even on
real populated rows) are intentionally not populated -- same "don't
replicate what isn't load-bearing" approach as the classic format's
skipped sort-key cache.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

from .itunesdb import TrackMeta, unix_to_mac

FOURCC_FILE = struct.unpack(">I", b"FILE")[0]
FOURCC_M4A = struct.unpack(">I", b"M4A ")[0]
LOCATION_KIND_AAC_FAMILY = 1  # "AAC audio file" in location_kind_map -- also used for ALAC-in-M4A
AUDIO_FORMAT_ALAC = 502  # empirically observed on a real ALAC track; used for all our uploads (always ALAC/M4A)
MUSIC_BASE_LOCATION_ID = 1  # base_location "iTunes_Control/Music" on a real device

# 64-bit unsigned <-> signed conversion (SQLite's INTEGER is signed 64-bit;
# the classic format's dbid is stored/compared as unsigned)
_SIGN_BIT = 1 << 63
_MASK64 = (1 << 64) - 1


def dbid_to_pid(dbid: int) -> int:
    return dbid - (1 << 64) if dbid & _SIGN_BIT else dbid


def pid_to_dbid(pid: int) -> int:
    return pid & _MASK64


def _get_or_create_lookup(cur: sqlite3.Cursor, table: str, name_col: str, name: str, extra_cols: dict) -> int:
    cur.execute(f"SELECT pid FROM {table} WHERE {name_col}=?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(f"SELECT COALESCE(MIN(pid), 0) FROM {table}")
    # new pids just need to be unused in this table; walk downward from the
    # current minimum so we never collide with whatever real iTunes/libgpod
    # already assigned.
    new_pid = cur.fetchone()[0] - 1
    cols = [name_col] + list(extra_cols.keys())
    placeholders = ", ".join("?" for _ in cols)
    cur.execute(
        f"INSERT INTO {table} (pid, {', '.join(cols)}) VALUES (?, {placeholders})",
        (new_pid, name, *extra_cols.values()),
    )
    return new_pid


def _iphone_sort_key(text):
    """Stub for Apple's native iPhoneSortKey SQL function.

    The real database's triggers call this (registered by Apple's own
    code, not present in plain SQLite/Python) to compute locale-aware
    collation keys for browse-view sort order -- purely cosmetic, not
    load-bearing for whether a track shows up or plays. Real device
    triggers call this directly (not just via a sort_map lookup) when a
    brand-new genre or composer name is inserted, so it must exist or the
    insert raises "no such function". This stub just produces a reasonable
    ASCII-ish ordering, not Apple's exact collation.
    """
    return (text or "").upper()


def _iphone_sort_section(key):
    """Stub for Apple's native iPhoneSortSection SQL function (see
    _iphone_sort_key) -- buckets a sort key into a section (e.g. for
    A/B/C.. index headers). Cosmetic only.
    """
    if not key:
        return 0
    ch = key[0]
    if ch.isalpha():
        return ord(ch) - ord("A") + 1
    if ch.isdigit():
        return 0
    return -1


def _register_stub_functions(con: sqlite3.Connection) -> None:
    con.create_function("iPhoneSortKey", 1, _iphone_sort_key)
    con.create_function("iPhoneSortSection", 1, _iphone_sort_section)


def _get_or_create_genre(cur: sqlite3.Cursor, genre: str) -> int:
    cur.execute("SELECT id FROM genre_map WHERE genre=?", (genre,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM genre_map")
    new_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO genre_map (id, genre, genre_order, genre_order_section, genre_blank) VALUES (?, ?, 0, 0, 0)",
        (new_id, genre),
    )
    return new_id


def add_track(library_path: Path, locations_path: Path, meta: TrackMeta, music_relpath: str) -> None:
    """music_relpath is relative to the "iTunes_Control/Music" base
    location, e.g. "F08/ABCD.m4a"."""
    pid = dbid_to_pid(meta.dbid)
    mac_date = unix_to_mac(meta.date_added)

    lib = sqlite3.connect(str(library_path))
    try:
        _register_stub_functions(lib)
        cur = lib.cursor()
        cur.execute("SELECT primary_container_pid FROM db_info LIMIT 1")
        row = cur.fetchone()
        container_pid = row[0] if row else 0

        genre_id = _get_or_create_genre(cur, meta.genre) if meta.genre else 0
        artist_pid = (
            _get_or_create_lookup(cur, "item_artist", "name", meta.artist, {"kind": None, "artwork_status": None, "artwork_album_pid": 0, "name_order": 0, "sort_name": meta.artist, "name_order_section": 0, "name_blank": 0})
            if meta.artist
            else 0
        )
        album_artist_pid = (
            _get_or_create_lookup(cur, "album_artist", "name", meta.artist, {"kind": 2, "artwork_status": 0, "artwork_album_pid": 0, "name_order": 0, "sort_name": meta.artist, "name_order_section": 0, "name_blank": 0})
            if meta.artist
            else 0
        )
        album_pid = (
            _get_or_create_lookup(cur, "album", "name", meta.album, {"kind": 2, "artwork_status": 0, "artwork_item_pid": None, "artist_pid": album_artist_pid, "user_rating": 0, "name_order": 0, "all_compilations": 0, "feed_url": None, "season_number": 0, "name_order_section": 0, "sort_name": meta.album, "artist": meta.artist, "sort_artist": meta.artist, "artist_order_section": 0, "name_blank": 0})
            if meta.album
            else 0
        )

        cur.execute(
            """INSERT INTO item (
                pid, revision_level, media_kind, is_song, date_modified, year,
                artwork_status, total_time_ms, track_number, track_count,
                disc_number, disc_count, genre_id, album_pid, artist_pid, composer_pid,
                title, artist, album, album_artist, composer,
                sort_title, sort_artist, sort_album, sort_album_artist,
                title_blank, artist_blank, album_blank, album_artist_blank, composer_blank, grouping_blank,
                album_artist_pid, in_songs_collection
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                # media_kind=1 (song); the "insert_item"/"create_sort_data_..."
                # triggers derive is_song etc. FROM media_kind and will
                # overwrite whatever is_song value we pass here, so it must
                # be a real bit pattern, not 0 (confirmed against a real
                # track: media_kind=1 for a plain song).
                pid, 0, 1, 1, mac_date, meta.year,
                1, float(meta.length_ms), meta.track_nr, meta.tracks_total,
                meta.disc_nr, meta.discs_total, genre_id, album_pid, artist_pid, 0,
                meta.title, meta.artist, meta.album, meta.artist, meta.composer or "",
                meta.title, meta.artist, meta.album, meta.artist,
                0 if meta.title else 1, 0 if meta.artist else 1, 0 if meta.album else 1,
                0 if meta.artist else 1, 0 if meta.composer else 1, 1,
                album_artist_pid, 1,
            ),
        )
        cur.execute(
            """INSERT INTO avformat_info (item_pid, sub_id, audio_format, bit_rate, sample_rate, duration)
               VALUES (?, 0, ?, ?, ?, 0)""",
            (pid, AUDIO_FORMAT_ALAC, meta.bitrate, float(meta.samplerate)),
        )
        cur.execute(
            "INSERT INTO item_to_container (item_pid, container_pid, physical_order, shuffle_order) VALUES (?, ?, 0, NULL)",
            (pid, container_pid),
        )
        # OR REPLACE, not plain INSERT: on a real device, an AFTER INSERT
        # trigger on `item` cascades into an AFTER UPDATE trigger that
        # already does "INSERT OR REPLACE INTO ext_item_view_membership"
        # for this pid -- a plain INSERT here raises a UNIQUE constraint
        # error in that case (found by testing against a real device's
        # actual schema+triggers, not guessed). Our synthetic test schema
        # has no triggers, so this is the one that actually creates the row
        # there.
        cur.execute(
            "INSERT OR REPLACE INTO ext_item_view_membership (item_pid, movie_mbr, movie_rental_mbr) VALUES (?, 0, 0)",
            (pid,),
        )
        lib.commit()
    finally:
        lib.close()

    loc = sqlite3.connect(str(locations_path))
    try:
        cur = loc.cursor()
        cur.execute(
            """INSERT INTO location (item_pid, sub_id, base_location_id, location_type, location, extension, kind_id, date_created, file_size)
               VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, MUSIC_BASE_LOCATION_ID, FOURCC_FILE, music_relpath, FOURCC_M4A, LOCATION_KIND_AAC_FAMILY, mac_date, meta.size),
        )
        loc.commit()
    finally:
        loc.close()


def remove_track(library_path: Path, locations_path: Path, dbid: int) -> None:
    pid = dbid_to_pid(dbid)

    lib = sqlite3.connect(str(library_path))
    try:
        cur = lib.cursor()
        cur.execute("DELETE FROM item WHERE pid=?", (pid,))
        cur.execute("DELETE FROM avformat_info WHERE item_pid=?", (pid,))
        cur.execute("DELETE FROM item_to_container WHERE item_pid=?", (pid,))
        cur.execute("DELETE FROM container_seed WHERE item_pid=?", (pid,))
        cur.execute("DELETE FROM ext_item_view_membership WHERE item_pid=?", (pid,))
        lib.commit()
    finally:
        lib.close()

    loc = sqlite3.connect(str(locations_path))
    try:
        cur = loc.cursor()
        cur.execute("DELETE FROM location WHERE item_pid=?", (pid,))
        loc.commit()
    finally:
        loc.close()
