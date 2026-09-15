"""Structural tests for the ArtworkDB/.ithmb writer.

These check the chunk layout is self-consistent (sizes, nesting, and total
lengths line up byte-for-byte the way libgpod's writer produces them) --
they can't confirm a real classic iPod actually accepts and displays this
data, since nobody working on this project has one to test against. See
db/artworkdb.py's module docstring.
"""

from __future__ import annotations

import io
import struct

import pytest
from PIL import Image

from ipodmanager.db import artworkdb as adb


def _make_image_bytes(size=(300, 200), color=(200, 40, 40)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_render_thumbnail_fits_without_cropping_and_centers():
    fmt = adb.ArtworkFormat(1028, 100, 100)
    # wide source (300x200) fit into a 100x100 square -> scale limited by
    # height, so width padding should appear on both sides
    pixels, h_pad, v_pad = adb.render_thumbnail(_make_image_bytes((300, 200)), fmt)
    assert len(pixels) == fmt.width * fmt.height * 2  # RGB565 = 2 bytes/pixel
    # 300x200 fit into 100x100: width (300 -> 100, x1/3) is the limiting
    # dimension, so it fills the full width with no horizontal bars, and
    # gets letterboxed top/bottom instead.
    assert h_pad == 0
    assert v_pad > 0


def test_render_thumbnail_rgb565_packing_is_correct():
    fmt = adb.ArtworkFormat(11, 4, 4)  # tiny non-square-source-free case
    pixels, h_pad, v_pad = adb.render_thumbnail(_make_image_bytes((4, 4), (255, 0, 0)), fmt)
    assert h_pad == 0 and v_pad == 0
    # pure red -> R=31(11111), G=0, B=0 -> 0b11111_000000_00000 = 0xF800
    (value,) = struct.unpack_from("<H", pixels, 0)
    assert value == 0xF800


def test_build_artwork_db_empty_when_no_art():
    result = adb.build_artwork_db([])
    assert result.artwork_ids_by_track == {}
    magic, header_len, total_len = struct.unpack_from("<4sii", result.artworkdb_bytes, 0)
    assert magic == b"mhfd"
    assert total_len == len(result.artworkdb_bytes)
    for fmt in adb.COVER_ART_FORMATS:
        assert result.ithmb_files[f"F{fmt.format_id}_0.ithmb"] == b""


def test_build_artwork_db_structure_and_nesting():
    image = _make_image_bytes()
    result = adb.build_artwork_db([(5, 12345, image), (7, 67890, image)])

    assert result.artwork_ids_by_track == {5: 0x64, 7: 0x65}

    mhfd = result.artworkdb_bytes
    magic, header_len, total_len = struct.unpack_from("<4sii", mhfd, 0)
    assert magic == b"mhfd" and header_len == adb.MHFD_PADDED_LEN and total_len == len(mhfd)

    # mhfd -> 3 mhsd sections (image list, empty album list, file list),
    # each self-consistently sized and contiguous.
    pos = header_len
    seen_indexes = []
    for _ in range(3):
        m, hl, tl, index, _unk = struct.unpack_from("<4siihh", mhfd, pos)
        assert m == b"mhsd" and hl == adb.MHSD_PADDED_LEN
        seen_indexes.append(index)
        pos += tl
    assert pos == len(mhfd)
    assert seen_indexes == [1, 2, 3]

    # drill into the image list (index 1): mhli -> 2x mhii -> mhod -> mhni -> mhod(filename)
    mhsd1_start = header_len
    _m, hl1, _tl1, _idx1, _u1 = struct.unpack_from("<4siihh", mhfd, mhsd1_start)
    mhli_start = mhsd1_start + hl1
    m2, hl2, numch2 = struct.unpack_from("<4sii", mhfd, mhli_start)
    assert m2 == b"mhli" and hl2 == adb.MHLI_PADDED_LEN and numch2 == 2

    mhii_start = mhli_start + hl2
    for expected_song_id, expected_image_id in ((12345, 0x64), (67890, 0x65)):
        m3, hl3, tl3, numch3, image_id, song_id = struct.unpack_from("<4siiiiq", mhfd, mhii_start)
        assert m3 == b"mhii" and hl3 == adb.MHII_PADDED_LEN
        assert image_id == expected_image_id and song_id == expected_song_id
        assert numch3 == len(adb.COVER_ART_FORMATS)

        child_pos = mhii_start + hl3
        for fmt in adb.COVER_ART_FORMATS:
            m4, hl4, tl4, mtype4 = struct.unpack_from("<4siih", mhfd, child_pos)
            assert m4 == b"mhod" and hl4 == 24 and mtype4 == 2
            mhni_pos = child_pos + 24
            m5, hl5, tl5, numch5, fmtid, ithmb_off, imgsize, vpad, hpad, imgh, imgw = struct.unpack_from(
                "<4siiiiiihhhh", mhfd, mhni_pos
            )
            assert m5 == b"mhni" and hl5 == adb.MHNI_PADDED_LEN
            assert fmtid == fmt.format_id and imgw == fmt.width and imgh == fmt.height
            assert imgsize == fmt.width * fmt.height * 2

            mhod2_pos = mhni_pos + hl5
            m6, hl6, tl6, mtype6 = struct.unpack_from("<4siih", mhfd, mhod2_pos)
            assert m6 == b"mhod" and mtype6 == 3
            str_len = struct.unpack_from("<i", mhfd, mhod2_pos + 24)[0]
            filename = mhfd[mhod2_pos + 36 : mhod2_pos + 36 + str_len].decode("utf-16-le")
            assert filename == f":F{fmt.format_id}_0.ithmb"

            child_pos += tl4
        assert child_pos == mhii_start + tl3
        mhii_start += tl3

    # .ithmb files: each format's file holds exactly 2 concatenated thumbnails
    for fmt in adb.COVER_ART_FORMATS:
        data = result.ithmb_files[f"F{fmt.format_id}_0.ithmb"]
        assert len(data) == 2 * fmt.width * fmt.height * 2


def test_build_artwork_db_skips_corrupt_artwork_without_failing():
    image = _make_image_bytes()
    result = adb.build_artwork_db([(1, 111, image), (2, 222, b"not an image")])
    assert result.artwork_ids_by_track == {1: 0x64}  # track 2's bad art was skipped, not fatal
