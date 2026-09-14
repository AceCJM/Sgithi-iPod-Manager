"""Binary reader/writer for the classic iPod `iTunesDB` format.

This is a from-scratch, from-the-wire-format reimplementation (no libgpod
dependency). The chunk layouts and field offsets below were verified against
libgpod's actual source (src/db-itunes-parser.h and src/itdb_itunesdb.c,
GNU LGPL-2.1+, https://github.com/fadingred/libgpod) while writing this
module, rather than reconstructed from memory -- this format has ~80 mostly
undocumented fields per track and getting offsets wrong risks writing a
database a real iPod can't read.

Only covers what classic click-wheel iPods (no hash, no SQLite shadow db)
need. iPhone/iPod Touch-generation support (hash72 signing + SQLite shadow
database) is a separate, not-yet-implemented layer -- see transport/iphone.py.

Design choice: existing tracks and playlists are kept as opaque byte blobs
and copied through unmodified unless directly touched. Only newly added
tracks are freshly synthesized, and only playlists that reference a deleted
track are patched (to drop the dangling reference). This means 99% of an
existing, working library round-trips byte-for-byte identical, which is the
safest possible behavior against someone's real music collection.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

MAC_EPOCH_OFFSET = 2082844800  # seconds between 1904-01-01 and 1970-01-01 (Mac HFS epoch)


def mac_to_unix(t: int) -> Optional[int]:
    if not t:
        return None
    return t - MAC_EPOCH_OFFSET


def unix_to_mac(t: Optional[float]) -> int:
    if not t:
        return 0
    return int(t) + MAC_EPOCH_OFFSET


def ipod_path_to_relpath(ipod_path: str) -> str:
    """':iPod_Control:Music:F00:ABCD.m4a' -> 'iPod_Control/Music/F00/ABCD.m4a'"""
    return ipod_path.lstrip(":").replace(":", "/")


def relpath_to_ipod_path(relpath: str) -> str:
    """'iPod_Control/Music/F00/ABCD.m4a' -> ':iPod_Control:Music:F00:ABCD.m4a'"""
    return ":" + relpath.replace("/", ":")


# --------------------------------------------------------------------------
# MHOD (string data object)
# --------------------------------------------------------------------------

class MhodType:
    TITLE = 1
    LOCATION = 2
    ALBUM = 3
    ARTIST = 4
    GENRE = 5
    FILETYPE = 6
    EQ_SETTING = 7
    COMMENT = 8
    COMPOSER = 12
    GROUPING = 13


_MHOD_STRING_HEADER = struct.Struct("<4sIIIIIIIII")
# header_id, header_len, total_len, type, unknown1, unknown2, position,
# string_len, unknown3, unknown4  (= 40 bytes, matches libgpod's
# _MhodHeaderString struct exactly)


def pack_mhod_string(mhod_type: int, text: str) -> bytes:
    encoded = text.encode("utf-16-le")
    header = _MHOD_STRING_HEADER.pack(
        b"mhod", 40, 40 + len(encoded), mhod_type, 0, 0, 0, len(encoded), 1, 0
    )
    return header + encoded


def parse_mhod(buf: bytes, offset: int) -> tuple[int, Optional[str], int]:
    """Returns (mhod_type, text_or_None, total_len_of_this_chunk)."""
    magic, header_len, total_len, mtype, _u1, _u2, position, str_len, _u3, _u4 = (
        _MHOD_STRING_HEADER.unpack_from(buf, offset)
    )
    if magic != b"mhod":
        raise ValueError(f"expected mhod at offset {offset}, found {magic!r}")
    if mtype >= 50 or str_len == 0 or header_len != 40:
        # Non-string mhod (smart playlist rules, sort-index blobs, etc) --
        # we don't interpret these, just report the span so callers can skip.
        return mtype, None, total_len
    text_bytes = buf[offset + 40 : offset + 40 + str_len]
    try:
        text = text_bytes.decode("utf-16-le")
    except UnicodeDecodeError:
        text = text_bytes.decode("utf-8", errors="replace")
    return mtype, text, total_len


# --------------------------------------------------------------------------
# MHIT (track) fixed header -- 0x248 bytes, transliterated field-by-field
# from libgpod's mk_mhit() so the byte layout is verified, not guessed.
# --------------------------------------------------------------------------

MHIT_HEADER_LEN = 0x248


@dataclass
class TrackMeta:
    """The subset of mhit/mhod fields this app understands and can set.

    Every other byte of an existing track's mhit header is preserved
    opaquely (see RawTrack below) -- this dataclass is only the shape of
    a *newly created* track record, plus what's shown for display.
    """

    track_id: int
    dbid: int
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    composer: str = ""
    comment: str = ""
    filetype: str = "AAC audio file"
    ipod_location: str = ""  # ':iPod_Control:Music:F00:XXXX.m4a'
    size: int = 0
    length_ms: int = 0
    track_nr: int = 0
    tracks_total: int = 0
    disc_nr: int = 0
    discs_total: int = 0
    year: int = 0
    bitrate: int = 0
    samplerate: int = 44100
    date_added: Optional[float] = None
    rating: int = 0
    media_type_video: bool = False


def build_new_mhit(meta: TrackMeta) -> bytes:
    """Build a complete mhit chunk (fixed header + string mhods) for a
    brand-new track. Mirrors libgpod's mk_mhit()/write order exactly.
    """
    b = bytearray()
    p = lambda fmt, *v: b.extend(struct.pack(fmt, *v))
    zeros = lambda n_u32: b.extend(b"\x00" * 4 * n_u32)

    p("<4s", b"mhit")
    p("<I", MHIT_HEADER_LEN)  # header_len
    p("<I", 0)  # total_len, patched below
    p("<I", 0)  # num_mhod, patched below
    assert len(b) == 0x10
    p("<I", meta.track_id)
    p("<I", 1)  # visible
    p("<I", 0)  # filetype_marker (legacy, unused by modern firmware)
    p("<B", 0)  # type1
    p("<B", 0)  # type2
    p("<B", 0)  # compilation
    p("<B", meta.rating)
    assert len(b) == 0x20
    p("<I", unix_to_mac(meta.date_added))  # date_modified
    p("<I", meta.size)
    p("<I", meta.length_ms)
    p("<I", meta.track_nr)
    assert len(b) == 0x30
    p("<I", meta.tracks_total)
    p("<I", meta.year)
    p("<I", meta.bitrate)
    p("<I", (meta.samplerate << 16))  # (samplerate << 16) | samplerate_low(0)
    assert len(b) == 0x40
    p("<I", 0)  # volume
    p("<I", 0)  # starttime
    p("<I", 0)  # stoptime
    p("<I", 0)  # soundcheck
    assert len(b) == 0x50
    p("<I", 0)  # playcount
    p("<I", 0)  # playcount2
    p("<I", 0)  # last_played
    p("<I", meta.disc_nr)
    assert len(b) == 0x60
    p("<I", meta.discs_total)
    p("<I", 0)  # drm_userid
    p("<I", unix_to_mac(meta.date_added))  # date_added
    p("<I", 0)  # bookmark_time
    assert len(b) == 0x70
    p("<Q", meta.dbid)
    p("<B", 0)  # checked
    p("<B", 0)  # app_rating
    p("<H", 0)  # BPM
    p("<H", 0)  # artwork_count
    p("<H", 0)  # unk126
    assert len(b) == 0x80
    p("<I", 0)  # artwork_size
    p("<I", 0)  # unk132
    p("<f", float(meta.samplerate))  # samplerate2
    p("<I", 0)  # time_released
    assert len(b) == 0x90
    p("<H", 0)  # unk144
    p("<H", 0)  # explicit_flag
    p("<I", 0)  # unk148
    p("<I", 0)  # unk152
    p("<I", 0)  # skipcount
    assert len(b) == 0xA0
    p("<I", 0)  # last_skipped
    p("<B", 1)  # has_artwork: 1 = no artwork / not yet processed (matches iTunes default)
    p("<B", 0)  # skip_when_shuffling
    p("<B", 0)  # remember_playback_position
    p("<B", 0)  # flag4
    p("<Q", 0)  # dbid2
    assert len(b) == 0xB0
    p("<B", 0)  # lyrics_flag
    p("<B", 1 if meta.media_type_video else 0)  # movie_flag
    p("<B", 0)  # mark_unplayed
    p("<B", 0)  # unk179
    p("<I", 0)  # unk180
    p("<I", 0)  # pregap
    p("<Q", 0)  # samplecount
    assert len(b) == 0xC4
    p("<I", 0)  # unk196
    p("<I", 0)  # postgap
    p("<I", 0)  # unk204
    assert len(b) == 0xD0
    p("<I", 1 if meta.media_type_video else 0)  # mediatype (1=movie/video, 0=audio)
    p("<I", 0)  # season_nr
    p("<I", 0)  # episode_nr
    p("<I", 0)  # unk220
    assert len(b) == 0xE0
    zeros(4)  # unk224..unk236
    assert len(b) == 0xF0
    zeros(2)  # unk240, unk244
    p("<I", 0)  # gapless_data
    p("<I", 0)  # unk252
    assert len(b) == 0x100
    p("<H", 0)  # gapless_track_flag
    p("<H", 0)  # gapless_album_flag
    zeros(7)
    assert len(b) == 0x120
    p("<I", 0)  # album_id (resolved lazily by iTunes/device, 0 is safe)
    p("<Q", 0)  # id_0x24 (mirrors mhbd+0x24, purpose unknown -- 0 is what a
    #              fresh/never-iTunes-synced db has anyway)
    p("<I", meta.size)  # size again
    assert len(b) == 0x130
    p("<I", 0)
    p("<Q", 0x808080808080)
    p("<I", 0)
    assert len(b) == 0x140
    zeros(2)
    p("<I", 0)  # book flags (not an ebook/pdf)
    zeros(5)
    assert len(b) == 0x160
    p("<I", 0)  # mhii_link (ArtworkDB linkage -- none, we don't write artwork)
    p("<I", 0)
    p("<I", 1)
    p("<I", 0)
    assert len(b) == 0x170
    zeros(28)
    assert len(b) == 0x1E0
    p("<I", 0)  # artist_id
    zeros(4)
    assert len(b) == 0x1F4
    p("<I", 0)  # composer_id
    zeros(20)
    assert len(b) == MHIT_HEADER_LEN

    mhods = bytearray()
    mhods += pack_mhod_string(MhodType.TITLE, meta.title)
    mhods += pack_mhod_string(MhodType.LOCATION, meta.ipod_location)
    n = 2
    if meta.album:
        mhods += pack_mhod_string(MhodType.ALBUM, meta.album)
        n += 1
    if meta.artist:
        mhods += pack_mhod_string(MhodType.ARTIST, meta.artist)
        n += 1
    if meta.genre:
        mhods += pack_mhod_string(MhodType.GENRE, meta.genre)
        n += 1
    if meta.filetype:
        mhods += pack_mhod_string(MhodType.FILETYPE, meta.filetype)
        n += 1
    if meta.comment:
        mhods += pack_mhod_string(MhodType.COMMENT, meta.comment)
        n += 1
    if meta.composer:
        mhods += pack_mhod_string(MhodType.COMPOSER, meta.composer)
        n += 1

    total_len = len(b) + len(mhods)
    struct.pack_into("<I", b, 0x08, total_len)
    struct.pack_into("<I", b, 0x0C, n)
    return bytes(b) + bytes(mhods)


# Byte offsets of a few fields useful for read-only display, within an
# existing mhit's fixed header (see build_new_mhit for how these line up).
_DISPLAY_FIELDS = {
    "size": (0x24, "<I"),
    "length_ms": (0x28, "<I"),
    "track_nr": (0x2C, "<I"),
    "tracks_total": (0x30, "<I"),
    "year": (0x34, "<I"),
    "bitrate": (0x38, "<I"),
    "samplerate_combo": (0x3C, "<I"),
    "disc_nr": (0x5C, "<I"),
    "discs_total": (0x60, "<I"),
    "date_added_mac": (0x68, "<I"),
    "dbid": (0x70, "<Q"),
    "rating": (0x1F, "<B"),
}


@dataclass
class RawTrack:
    """An existing track, kept mostly as an opaque blob.

    `raw` is the complete, untouched mhit chunk bytes (fixed header + all
    mhod children) exactly as read from the device -- writing it back
    unmodified guarantees we never corrupt a field we don't understand.
    """

    track_id: int
    raw: bytes
    display: dict = field(default_factory=dict)


def parse_mhit(buf: bytes, offset: int) -> RawTrack:
    magic, header_len, total_len, num_mhod = struct.unpack_from("<4sIII", buf, offset)
    if magic != b"mhit":
        raise ValueError(f"expected mhit at offset {offset}, found {magic!r}")
    track_id = struct.unpack_from("<I", buf, offset + 0x10)[0]
    raw = bytes(buf[offset : offset + total_len])

    display = {}
    for name, (off, fmt) in _DISPLAY_FIELDS.items():
        display[name] = struct.unpack_from(fmt, raw, off)[0]
    display["date_added"] = mac_to_unix(display.pop("date_added_mac"))

    pos = header_len
    for _ in range(num_mhod):
        mtype, text, mlen = parse_mhod(raw, pos)
        if text is not None:
            key = {
                MhodType.TITLE: "title",
                MhodType.LOCATION: "ipod_location",
                MhodType.ALBUM: "album",
                MhodType.ARTIST: "artist",
                MhodType.GENRE: "genre",
                MhodType.FILETYPE: "filetype",
                MhodType.COMMENT: "comment",
                MhodType.COMPOSER: "composer",
            }.get(mtype)
            if key:
                display[key] = text
        pos += mlen

    return RawTrack(track_id=track_id, raw=raw, display=display)


# --------------------------------------------------------------------------
# MHIP (playlist item) + MHYP (playlist)
# --------------------------------------------------------------------------

MHIP_HEADER_LEN = 76


def build_mhip(track_id: int) -> bytes:
    b = bytearray()
    b += struct.pack("<4sIIIIIII", b"mhip", MHIP_HEADER_LEN, MHIP_HEADER_LEN, 1, 0, 0, track_id, 0)
    b += struct.pack("<I", 0)  # podcastgroupref
    b += b"\x00" * 4 * 10
    assert len(b) == MHIP_HEADER_LEN
    return bytes(b)


@dataclass
class RawPlaylist:
    is_master: bool
    header_prefix: bytes  # mhyp fixed header, with total_len/num_mhips left as placeholders
    body_before_mhips: bytes  # title mhod + any other non-mhip children, verbatim
    mhip_track_ids: list[int]
    mhip_blobs: list[bytes]

    def remove_track(self, track_id: int) -> bool:
        try:
            idx = self.mhip_track_ids.index(track_id)
        except ValueError:
            return False
        del self.mhip_track_ids[idx]
        del self.mhip_blobs[idx]
        return True

    def add_track(self, track_id: int) -> None:
        self.mhip_track_ids.append(track_id)
        self.mhip_blobs.append(build_mhip(track_id))

    def serialize(self) -> bytes:
        body = self.header_prefix + self.body_before_mhips + b"".join(self.mhip_blobs)
        total_len = len(body)
        body = bytearray(body)
        struct.pack_into("<I", body, 0x08, total_len)
        struct.pack_into("<I", body, 0x10, len(self.mhip_blobs))
        return bytes(body)


def parse_mhyp(buf: bytes, offset: int) -> RawPlaylist:
    magic, header_len, total_len, num_mhod = struct.unpack_from("<4sIII", buf, offset)
    if magic != b"mhyp":
        raise ValueError(f"expected mhyp at offset {offset}, found {magic!r}")
    num_mhips = struct.unpack_from("<I", buf, offset + 0x10)[0]
    pl_type = buf[offset + 0x14]
    is_master = pl_type == 1
    header_prefix = bytes(buf[offset : offset + header_len])

    # mhyp's two counters are independent: num_mhod counts the leading
    # mhod children (title, and on the MPL an optional sort-key cache),
    # num_mhips separately counts the mhip track-membership records that
    # follow them. They are NOT one combined child count.
    pos = offset + header_len
    body_before_mhips = bytearray()
    for _ in range(num_mhod):
        if buf[pos : pos + 4] != b"mhod":
            raise ValueError(f"expected mhod inside mhyp at {pos}, found {buf[pos:pos+4]!r}")
        _mtype, _text, mlen = parse_mhod(buf, pos)
        body_before_mhips += buf[pos : pos + mlen]
        pos += mlen

    track_ids: list[int] = []
    blobs: list[bytes] = []
    for _ in range(num_mhips):
        if buf[pos : pos + 4] != b"mhip":
            raise ValueError(f"expected mhip inside mhyp at {pos}, found {buf[pos:pos+4]!r}")
        mhip_len = struct.unpack_from("<I", buf, pos + 8)[0]
        tid = struct.unpack_from("<I", buf, pos + 0x18)[0]
        track_ids.append(tid)
        blobs.append(bytes(buf[pos : pos + mhip_len]))
        pos += mhip_len

    return RawPlaylist(
        is_master=is_master,
        header_prefix=header_prefix,
        body_before_mhips=bytes(body_before_mhips),
        mhip_track_ids=track_ids,
        mhip_blobs=blobs,
    )


def build_new_master_playlist(name: str = "iPod") -> RawPlaylist:
    """A fresh, empty master playlist -- title mhod only.

    Real iTunes also writes a second mhod (MHOD_ID_PLAYLIST, a ~0x288-byte
    blob of column-width/sort-order preferences for its own desktop UI) and,
    only on the MPL, an optional precomputed sort-key cache. Both are
    iTunes-display conveniences, not something the on-device firmware needs
    to list or play tracks, so we omit them -- this is exactly the shape of
    a never-synced-with-iTunes library.
    """
    header = bytearray()
    header += struct.pack("<4sIII", b"mhyp", 108, 0, 1)
    header += struct.pack("<I", 0)  # num_mhips, patched on serialize
    header += struct.pack("<BBBB", 1, 0, 0, 0)  # type=master, flags
    header += struct.pack("<I", unix_to_mac(None))  # timestamp
    header += struct.pack("<Q", 0)  # id
    header += struct.pack("<I", 0)
    header += struct.pack("<H", 1)  # string mhod count
    header += struct.pack("<H", 0)  # podcastflag
    header += struct.pack("<I", 0)  # sortorder
    header += b"\x00" * 4 * 15
    assert len(header) == 108

    body = pack_mhod_string(MhodType.TITLE, name)
    return RawPlaylist(is_master=True, header_prefix=bytes(header), body_before_mhips=body, mhip_track_ids=[], mhip_blobs=[])


# --------------------------------------------------------------------------
# MHBD / MHSD / MHLT / MHLP  (top-level containers)
# --------------------------------------------------------------------------

MHBD_STRUCT = struct.Struct("<4sIIIII")  # up through num_children (offset 0x18)


@dataclass
class TracksSection:
    tracks: list[RawTrack]


@dataclass
class PlaylistsSection:
    playlists: list[RawPlaylist]


# A parsed mhbd child is either a section we understand and can mutate, or
# a raw (index, bytes) blob we don't interpret -- e.g. album/artist browse
# indices, Genius data, categorized (TV/Movie/Ringtone) playlist lists.
# Modern iTunes writes up to ~8 of these per device regardless of how old
# the hardware is; the device firmware just ignores index types it
# predates. We copy anything we don't understand through byte-for-byte
# rather than dropping it, since we can't be sure what a given firmware
# expects to find.
Section = TracksSection | PlaylistsSection | tuple


@dataclass
class ITunesDB:
    version: int
    header_raw: bytes  # full mhbd header, byte-for-byte, patched only at total_len/num_children
    sections: list  # list[Section], in original on-disk order
    next_track_id: int
    next_dbid: int

    @property
    def tracks(self) -> list[RawTrack]:
        for s in self.sections:
            if isinstance(s, TracksSection):
                return s.tracks
        return []

    @property
    def playlists(self) -> list[RawPlaylist]:
        for s in self.sections:
            if isinstance(s, PlaylistsSection):
                return s.playlists
        return []

    @classmethod
    def parse(cls, buf: bytes) -> "ITunesDB":
        magic, header_len, total_len, unknown1, version, num_children = MHBD_STRUCT.unpack_from(buf, 0)
        if magic != b"mhbd":
            raise ValueError("not an iTunesDB (missing mhbd header)")
        header_raw = bytes(buf[0:header_len])

        pos = header_len
        sections: list = []
        max_track_id = 0
        max_dbid = 0
        have_tracks = False
        have_playlists = False

        for _ in range(num_children):
            mhsd_magic, mhsd_header_len, mhsd_total_len, index = struct.unpack_from("<4siii", buf, pos)
            if mhsd_magic != b"mhsd":
                raise ValueError(f"expected mhsd at {pos}, found {mhsd_magic!r}")
            body_start = pos + mhsd_header_len
            body_end = pos + mhsd_total_len

            if index == 1 and not have_tracks:  # track list
                have_tracks = True
                _magic, mhlt_header_len, num_songs = struct.unpack_from("<4sii", buf, body_start)
                tpos = body_start + mhlt_header_len
                tracks: list[RawTrack] = []
                for _ in range(num_songs):
                    t = parse_mhit(buf, tpos)
                    tracks.append(t)
                    max_track_id = max(max_track_id, t.track_id)
                    max_dbid = max(max_dbid, t.display.get("dbid", 0))
                    total_track_len = struct.unpack_from("<I", buf, tpos + 8)[0]
                    tpos += total_track_len
                sections.append(TracksSection(tracks=tracks))
            elif index == 2 and not have_playlists:  # playlists
                have_playlists = True
                _magic, mhlp_header_len, num_pl = struct.unpack_from("<4sii", buf, body_start)
                ppos = body_start + mhlp_header_len
                playlists: list[RawPlaylist] = []
                for _ in range(num_pl):
                    pl = parse_mhyp(buf, ppos)
                    playlists.append(pl)
                    plen = struct.unpack_from("<I", buf, ppos + 8)[0]
                    ppos += plen
                sections.append(PlaylistsSection(playlists=playlists))
            else:
                # Unknown or duplicate-index section (album/artist index,
                # Genius, podcasts, categorized playlists, ...): preserve
                # verbatim.
                sections.append(("raw", bytes(buf[pos:body_end])))
            pos = body_end

        return cls(
            version=version,
            header_raw=header_raw,
            sections=sections,
            next_track_id=max_track_id + 1,
            next_dbid=max(max_dbid, 0x100000000) + 1,
        )

    def master_playlist(self) -> RawPlaylist:
        for pl in self.playlists:
            if pl.is_master:
                return pl
        for s in self.sections:
            if isinstance(s, PlaylistsSection):
                pl = build_new_master_playlist()
                s.playlists.insert(0, pl)
                return pl
        pl = build_new_master_playlist()
        self.sections.append(PlaylistsSection(playlists=[pl]))
        return pl

    def add_track(self, meta: TrackMeta) -> RawTrack:
        meta.track_id = self.next_track_id
        meta.dbid = self.next_dbid
        self.next_track_id += 1
        self.next_dbid += 1
        raw_bytes = build_new_mhit(meta)
        rt = RawTrack(track_id=meta.track_id, raw=raw_bytes, display={
            "title": meta.title, "artist": meta.artist, "album": meta.album,
            "genre": meta.genre, "ipod_location": meta.ipod_location,
            "size": meta.size, "length_ms": meta.length_ms, "bitrate": meta.bitrate,
            "dbid": meta.dbid,
        })
        self.tracks.append(rt)
        self.master_playlist().add_track(meta.track_id)
        return rt

    def remove_track(self, track_id: int) -> bool:
        removed = False
        for s in self.sections:
            if isinstance(s, TracksSection):
                before = len(s.tracks)
                s.tracks[:] = [t for t in s.tracks if t.track_id != track_id]
                removed = removed or len(s.tracks) != before
        # Note: this only scrubs the dangling reference from playlists we
        # actually parse (the regular playlist list, index 2). Any raw/
        # unknown section (e.g. a podcast playlist list) that happens to
        # reference this track ID is left untouched -- see module
        # docstring. That's a stale reference in a supplemental index, not
        # a source of truth, so it isn't corrupting.
        for pl in self.playlists:
            pl.remove_track(track_id)
        return removed

    def serialize(self) -> bytes:
        # mhlt/mhlp/mhsd header sizes (92 / 92 / 96) and the zero-padding
        # after each logical header match libgpod's mk_mhlt/mk_mhlp/mk_mhsd.
        parts = []
        for s in self.sections:
            if isinstance(s, TracksSection):
                mhlt_body = struct.pack("<4sii", b"mhlt", 92, len(s.tracks)) + b"\x00" * 80
                mhlt_body += b"".join(t.raw for t in s.tracks)
                parts.append(struct.pack("<4siii", b"mhsd", 96, 96 + len(mhlt_body), 1) + b"\x00" * 80 + mhlt_body)
            elif isinstance(s, PlaylistsSection):
                mhlp_body = struct.pack("<4sii", b"mhlp", 92, len(s.playlists)) + b"\x00" * 80
                mhlp_body += b"".join(pl.serialize() for pl in s.playlists)
                parts.append(struct.pack("<4siii", b"mhsd", 96, 96 + len(mhlp_body), 2) + b"\x00" * 80 + mhlp_body)
            else:
                _kind, raw = s
                parts.append(raw)

        body = b"".join(parts)
        header = bytearray(self.header_raw)
        total_len = len(header) + len(body)
        struct.pack_into("<I", header, 0x08, total_len)
        struct.pack_into("<I", header, 0x14, len(self.sections))
        return bytes(header) + body
