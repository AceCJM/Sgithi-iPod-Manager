import sqlite3
import struct
import zlib


def build_empty_mhbd() -> bytes:
    """A minimal, format-correct mhbd with one empty tracks + one empty
    playlists section, laid out exactly like mk_mhbd()/mk_mhsd() in
    libgpod (244-byte mhbd header, 96-byte mhsd headers).
    """
    header = bytearray()
    header += struct.pack("<4sIIII", b"mhbd", 244, 0, 1, 0x13)
    header += struct.pack("<I", 2)  # num_children
    header += struct.pack("<Q", 0)  # db_id
    header += struct.pack("<HH", 1, 0)  # platform, unk_0x22
    header += struct.pack("<Q", 0)  # id_0x24
    header += struct.pack("<I", 0)  # unk_0x2c
    header += struct.pack("<H", 0)  # hashing_scheme
    header += b"\x00" * 20  # unk_0x32
    header += b"\x00" * 2  # language_id
    header += struct.pack("<Q", 0)  # db_persistent_id
    header += struct.pack("<II", 0, 0)  # unk_0x50, unk_0x54
    header += b"\x00" * 20  # hash58
    header += struct.pack("<i", 0)  # timezone_offset
    header += struct.pack("<H", 0)  # unk_0x70
    header += b"\x00" * 46  # hash72
    header += struct.pack("<HHHHH", 0, 0, 0, 0, 0)
    header += b"\x00"  # align
    header += b"\x00" * 57  # hashAB
    header += b"\x00" * 16  # dummy space
    assert len(header) == 244

    mhsd_tracks = struct.pack("<4siii", b"mhsd", 96, 96 + 92, 1) + b"\x00" * 80
    mhsd_tracks += struct.pack("<4sii", b"mhlt", 92, 0) + b"\x00" * 80

    mhsd_playlists = struct.pack("<4siii", b"mhsd", 96, 96 + 92, 2) + b"\x00" * 80
    mhsd_playlists += struct.pack("<4sii", b"mhlp", 92, 0) + b"\x00" * 80

    body = bytes(mhsd_tracks) + bytes(mhsd_playlists)
    struct.pack_into("<I", header, 8, len(header) + len(body))
    return bytes(header) + body


def build_empty_mhbd_compressed() -> bytes:
    """Same as build_empty_mhbd, but with the body zlib-compressed and
    unknown1=2, matching what iPhone-generation devices actually use
    (confirmed against a real iPhone 3G's iTunesCDB).
    """
    plain = bytearray(build_empty_mhbd())
    header_len = struct.unpack_from("<I", plain, 4)[0]
    header = plain[:header_len]
    body = zlib.compress(bytes(plain[header_len:]), level=9)
    struct.pack_into("<I", header, 0x0C, 2)  # unknown1 = compressed
    struct.pack_into("<I", header, 8, len(header) + len(body))
    return bytes(header) + body


MASTER_CONTAINER_PID = -5861827358609610638  # arbitrary, matches the shape of a real device's value


def build_minimal_library_itdb(path) -> None:
    """A minimal (but real, valid) SQLite db with just the tables/columns
    sqlite_library.py touches -- enough to exercise it without needing the
    full real schema (~25 tables) or any real user data.
    """
    con = sqlite3.connect(str(path))
    try:
        cur = con.cursor()
        cur.execute("CREATE TABLE db_info (pid INTEGER, primary_container_pid INTEGER)")
        cur.execute("INSERT INTO db_info (pid, primary_container_pid) VALUES (1, ?)", (MASTER_CONTAINER_PID,))
        cur.execute("CREATE TABLE genre_map (id INTEGER PRIMARY KEY, genre TEXT UNIQUE, genre_order INTEGER, genre_order_section INTEGER, genre_blank INTEGER)")
        cur.execute("CREATE TABLE item_artist (pid INTEGER PRIMARY KEY, kind INTEGER, artwork_status INTEGER, artwork_album_pid INTEGER, name TEXT, name_order INTEGER, sort_name TEXT, name_order_section INTEGER, name_blank INTEGER)")
        cur.execute("CREATE TABLE album_artist (pid INTEGER PRIMARY KEY, kind INTEGER, artwork_status INTEGER, artwork_album_pid INTEGER, name TEXT, name_order INTEGER, sort_name TEXT, name_order_section INTEGER, name_blank INTEGER)")
        cur.execute("CREATE TABLE album (pid INTEGER PRIMARY KEY, kind INTEGER, artwork_status INTEGER, artwork_item_pid INTEGER, artist_pid INTEGER, user_rating INTEGER, name TEXT, name_order INTEGER, all_compilations INTEGER, feed_url TEXT, season_number INTEGER, name_order_section INTEGER, sort_name TEXT, artist TEXT, sort_artist TEXT, artist_order_section INTEGER, name_blank INTEGER)")
        cur.execute("""CREATE TABLE item (
            pid INTEGER PRIMARY KEY, revision_level INTEGER, media_kind INTEGER, is_song INTEGER,
            date_modified INTEGER, year INTEGER, artwork_status INTEGER, total_time_ms REAL,
            track_number INTEGER, track_count INTEGER, disc_number INTEGER, disc_count INTEGER,
            genre_id INTEGER, album_pid INTEGER, artist_pid INTEGER, composer_pid INTEGER,
            title TEXT, artist TEXT, album TEXT, album_artist TEXT, composer TEXT,
            sort_title TEXT, sort_artist TEXT, sort_album TEXT, sort_album_artist TEXT,
            title_blank INTEGER, artist_blank INTEGER, album_blank INTEGER, album_artist_blank INTEGER,
            composer_blank INTEGER, grouping_blank INTEGER, album_artist_pid INTEGER, in_songs_collection INTEGER)""")
        cur.execute("CREATE TABLE avformat_info (item_pid INTEGER, sub_id INTEGER, audio_format INTEGER, bit_rate INTEGER, sample_rate REAL, duration INTEGER, PRIMARY KEY (item_pid, sub_id))")
        cur.execute("CREATE TABLE item_to_container (item_pid INTEGER, container_pid INTEGER, physical_order INTEGER, shuffle_order INTEGER)")
        cur.execute("CREATE TABLE container_seed (container_pid INTEGER, item_pid INTEGER, seed_order INTEGER)")
        cur.execute("CREATE TABLE ext_item_view_membership (item_pid INTEGER PRIMARY KEY, movie_mbr INTEGER, movie_rental_mbr INTEGER)")
        con.commit()
    finally:
        con.close()


def build_minimal_locations_itdb(path) -> None:
    con = sqlite3.connect(str(path))
    try:
        cur = con.cursor()
        cur.execute("""CREATE TABLE location (
            item_pid INTEGER, sub_id INTEGER, base_location_id INTEGER, location_type INTEGER,
            location TEXT, extension INTEGER, kind_id INTEGER, date_created INTEGER, file_size INTEGER,
            PRIMARY KEY (item_pid, sub_id))""")
        cur.execute("CREATE TABLE base_location (id INTEGER PRIMARY KEY, path TEXT)")
        cur.execute("INSERT INTO base_location (id, path) VALUES (1, 'iTunes_Control/Music')")
        con.commit()
    finally:
        con.close()
