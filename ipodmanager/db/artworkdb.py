"""Binary writer for the classic iPod `ArtworkDB` + `.ithmb` thumbnail files.

Like itunesdb.py, this is a from-scratch reimplementation. The chunk layout
(mhfd/mhsd/mhli/mhii/mhod/mhni/mhla/mhlf/mhif) and the RGB565 thumbnail
packing algorithm were transliterated field-by-field from libgpod's actual
source (src/db-artwork-writer.c, src/db-itunes-parser.h, src/ithumb-writer.c,
src/itdb_device.c; GNU LGPL-2.1+, https://github.com/fadingred/libgpod)
while writing this module.

UNVERIFIED AGAINST REAL HARDWARE: unlike itunesdb.py's core track read/write
path, nobody has confirmed a real classic iPod actually displays artwork
written by this module (the author doesn't own one). Back up your iPod's
iPod_Control directory before relying on this. If cover art doesn't show up
on a real device, the on-disk chunk layout here is the first place to
re-check against libgpod's source.

Only the iPod Video (5th/5.5th generation) cover-art format is implemented:
two RGB565, little-endian, non-cropped ("fit" scaling, black letterbox
padding) thumbnails at 100x100 (format 1028) and 200x200 (format 1029).
Every album needs to be re-rendered from a source image every time the
ArtworkDB is rebuilt -- this module always does a full rebuild (see
rebuild_artwork_db in library.py) rather than an incremental merge, which
keeps this code far simpler at the cost of a bit of extra I/O per sync.
"""

from __future__ import annotations

import io
import struct
from array import array
from dataclasses import dataclass
from typing import Optional

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is a hard dependency in practice
    Image = None


@dataclass(frozen=True)
class ArtworkFormat:
    format_id: int
    width: int
    height: int


# Itdb_ArtworkFormat entries for ITDB_IPOD_GENERATION_VIDEO_1/2 in libgpod's
# itdb_device.c (ipod_video_cover_art_info[]). Both are THUMB_FORMAT_RGB565_LE
# with row_bytes_alignment=0, back_color=black, crop=false (unset fields in
# that table's 4-field C initializer all default to zero/false).
COVER_ART_FORMATS: tuple[ArtworkFormat, ...] = (
    ArtworkFormat(format_id=1028, width=100, height=100),
    ArtworkFormat(format_id=1029, width=200, height=200),
)

ARTWORK_MIN_ID = 0x64  # matches ipod_artwork_db_set_ids()'s min_id


# --------------------------------------------------------------------------
# Thumbnail rendering (fit-not-crop scale, RGB565 LE pack)
# --------------------------------------------------------------------------


def render_thumbnail(image_bytes: bytes, fmt: ArtworkFormat) -> tuple[bytes, int, int]:
    """Scale `image_bytes` to fit within fmt.width x fmt.height (preserving
    aspect ratio, no cropping -- matches ithumb_writer_scale_and_crop with
    crop=false), center it on a black canvas of exactly that size, and pack
    the result as raw RGB565 little-endian pixel data.

    Returns (pixel_bytes, horizontal_padding, vertical_padding) -- the
    padding values are the black letterbox border baked into pixel_bytes,
    reported separately because mhni also records them as metadata
    (matching ithumb_writer_write_thumbnail's horizontal_padding/
    vertical_padding fields).
    """
    if Image is None:
        raise RuntimeError("Pillow is required to render iPod artwork thumbnails")

    img = Image.open(io.BytesIO(image_bytes))
    img.load()
    img = img.convert("RGB")
    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        raise ValueError("empty source image")

    scale = min(fmt.width / src_w, fmt.height / src_h)
    scaled_w = max(1, round(src_w * scale))
    scaled_h = max(1, round(src_h * scale))
    if (scaled_w, scaled_h) != (src_w, src_h):
        img = img.resize((scaled_w, scaled_h), Image.BILINEAR)

    canvas = Image.new("RGB", (fmt.width, fmt.height), (0, 0, 0))
    h_pad = (fmt.width - scaled_w) // 2
    v_pad = (fmt.height - scaled_h) // 2
    canvas.paste(img, (h_pad, v_pad))

    raw = canvas.tobytes()  # 3 bytes/pixel, row-major RGB
    out = array(
        "H",
        (((raw[i] >> 3) << 11) | ((raw[i + 1] >> 2) << 5) | (raw[i + 2] >> 3) for i in range(0, len(raw), 3)),
    )
    import sys

    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes(), h_pad, v_pad


# --------------------------------------------------------------------------
# Chunk builders -- struct formats and "padded" (real on-disk) sizes are
# transliterated from db-itunes-parser.h and db-artwork-writer.c's
# get_padded_header_size(). All multi-byte fields are little-endian, since
# that's what every classic click-wheel iPod (including the iPod Video this
# module targets) uses.
# --------------------------------------------------------------------------

# ArtworkDB_MhodHeaderString (36 fixed bytes + string + zero padding to a
# 4-byte boundary). encoding=2 means UTF-16LE, matching a little-endian device.
_MHOD_STRING = struct.Struct("<4siihbbiiibbhi")
assert _MHOD_STRING.size == 36

# ArtworkDB_MhodHeader (container mhod, e.g. wrapping an mhni)
_MHOD_CONTAINER = struct.Struct("<4siihhii")
assert _MHOD_CONTAINER.size == 24

# MhniHeader fixed fields (36 bytes of 76 padded)
_MHNI_FIXED = struct.Struct("<4siiiiiihhhh")
assert _MHNI_FIXED.size == 36
MHNI_PADDED_LEN = 0x4C

# MhiiHeader fixed fields (52 bytes of 152 padded, originally __packed__ in C
# -- '<' already disables Python struct alignment padding, so this matches)
_MHII_FIXED = struct.Struct("<4siiiiqiiiiii")
assert _MHII_FIXED.size == 52
MHII_PADDED_LEN = 0x98

# MhliHeader (12 bytes of 92 padded) -- header_id, header_len, num_children
_MHLI_FIXED = struct.Struct("<4sii")
MHLI_PADDED_LEN = 0x5C
MHLA_PADDED_LEN = 0x5C  # same shape as mhli, always empty (photo albums only)

# MhifHeader (24 bytes of 124 padded)
_MHIF_FIXED = struct.Struct("<4siiiii")
assert _MHIF_FIXED.size == 24
MHIF_PADDED_LEN = 0x7C

MHLF_PADDED_LEN = 0x5C  # header_id, header_len, num_files

# ArtworkDB_MhsdHeader (16 bytes of 96 padded)
_MHSD_FIXED = struct.Struct("<4siihh")
assert _MHSD_FIXED.size == 16
MHSD_PADDED_LEN = 0x60

# MhfdHeader (68 bytes of 132 padded)
_MHFD_FIXED = struct.Struct("<4siiiiiiiqqbbbbiiii")
assert _MHFD_FIXED.size == 68
MHFD_PADDED_LEN = 0x84


def _pad(fixed: bytes, padded_len: int) -> bytearray:
    b = bytearray(fixed)
    b += b"\x00" * (padded_len - len(b))
    return b


def _build_mhod_filename(filename: str) -> bytes:
    """Type-3 (FILE_NAME) string mhod, as written inside an mhni."""
    encoded = filename.encode("utf-16-le")
    str_len = len(encoded)
    before_pad = _MHOD_STRING.size + str_len
    padding = (4 - (before_pad % 4)) % 4
    total_len = before_pad + padding
    header = _MHOD_STRING.pack(
        b"mhod", 24, total_len, 3, 0, padding, 0, 0, str_len, 2, 0, 0, 0
    )
    return header + encoded + b"\x00" * padding


def _build_mhni(fmt: ArtworkFormat, ithmb_offset: int, image_size: int, h_pad: int, v_pad: int, filename: str) -> bytes:
    fixed = _MHNI_FIXED.pack(
        b"mhni", MHNI_PADDED_LEN, 0, 1, fmt.format_id, ithmb_offset, image_size, v_pad, h_pad, fmt.height, fmt.width
    )
    fixed = _pad(fixed, MHNI_PADDED_LEN)
    mhod_fn = _build_mhod_filename(filename)
    struct.pack_into("<i", fixed, 8, MHNI_PADDED_LEN + len(mhod_fn))
    return bytes(fixed) + mhod_fn


def _build_mhod_location(mhni_bytes: bytes) -> bytes:
    total_len = _MHOD_CONTAINER.size + len(mhni_bytes)
    header = _MHOD_CONTAINER.pack(b"mhod", 24, total_len, 2, 0, 0, 0)  # type=MHOD_ARTWORK_TYPE_THUMBNAIL(2)
    return header + mhni_bytes


def _build_mhii(song_id: int, image_id: int, mhod_children: list[bytes]) -> bytes:
    fixed = _MHII_FIXED.pack(b"mhii", MHII_PADDED_LEN, 0, len(mhod_children), image_id, song_id, 0, 0, 0, 0, 0, 0)
    fixed = _pad(fixed, MHII_PADDED_LEN)
    body = b"".join(mhod_children)
    struct.pack_into("<i", fixed, 8, MHII_PADDED_LEN + len(body))
    return bytes(fixed) + body


def _build_mhli(mhii_children: list[bytes]) -> bytes:
    fixed = _MHLI_FIXED.pack(b"mhli", MHLI_PADDED_LEN, len(mhii_children))
    fixed = _pad(fixed, MHLI_PADDED_LEN)
    return bytes(fixed) + b"".join(mhii_children)


def _build_mhla_empty() -> bytes:
    fixed = struct.pack("<4sii", b"mhla", MHLA_PADDED_LEN, 0)
    return bytes(_pad(fixed, MHLA_PADDED_LEN))


def _build_mhif(fmt: ArtworkFormat) -> bytes:
    fixed = _MHIF_FIXED.pack(b"mhif", MHIF_PADDED_LEN, MHIF_PADDED_LEN, 0, fmt.format_id, fmt.width * fmt.height * 2)
    return bytes(_pad(fixed, MHIF_PADDED_LEN))


def _build_mhlf(formats: tuple[ArtworkFormat, ...]) -> bytes:
    children = b"".join(_build_mhif(f) for f in formats)
    fixed = struct.pack("<4sii", b"mhlf", MHLF_PADDED_LEN, len(formats))
    fixed = _pad(fixed, MHLF_PADDED_LEN)
    return bytes(fixed) + children


def _build_mhsd(index: int, body: bytes) -> bytes:
    fixed = _MHSD_FIXED.pack(b"mhsd", MHSD_PADDED_LEN, 0, index, 0)
    fixed = _pad(fixed, MHSD_PADDED_LEN)
    struct.pack_into("<i", fixed, 8, MHSD_PADDED_LEN + len(body))
    return bytes(fixed) + body


def _build_mhfd(next_id: int, sections: list[bytes]) -> bytes:
    fixed = _MHFD_FIXED.pack(
        b"mhfd", MHFD_PADDED_LEN, 0,
        0, 2,  # unknown1, unknown2 (version flag -- 2 means "iTunes 4.9+" era, matching write_mhfd())
        len(sections), 0, next_id,
        0, 0,  # unknown5, unknown6
        2, 0, 0, 0,  # unknown_flag1=2 (matches write_mhfd()), flag2-4=0
        0, 0, 0, 0,
    )
    fixed = _pad(fixed, MHFD_PADDED_LEN)
    body = b"".join(sections)
    struct.pack_into("<i", fixed, 8, MHFD_PADDED_LEN + len(body))
    return bytes(fixed) + body


# --------------------------------------------------------------------------
# Top-level build
# --------------------------------------------------------------------------


@dataclass
class ArtworkDBResult:
    artworkdb_bytes: bytes
    ithmb_files: dict[str, bytes]  # e.g. {"F1028_0.ithmb": b"...", "F1029_0.ithmb": b"..."}
    artwork_ids_by_track: dict[int, int]  # track_id -> mhii image_id (for mhit's mhii_link)


def build_artwork_db(tracks_with_art: list[tuple[int, int, bytes]]) -> ArtworkDBResult:
    """tracks_with_art: list of (track_id, song_dbid, cover_image_bytes) for
    every track that has embedded artwork. Tracks with no artwork should
    simply be omitted from this list.

    Always does a full rebuild from scratch (no merge with a pre-existing
    ArtworkDB) -- see the module docstring.
    """
    ithmb_files: dict[int, bytearray] = {fmt.format_id: bytearray() for fmt in COVER_ART_FORMATS}
    mhii_children: list[bytes] = []
    artwork_id = ARTWORK_MIN_ID
    artwork_ids_by_track: dict[int, int] = {}

    for track_id, song_dbid, image_bytes in tracks_with_art:
        try:
            thumbs = []
            for fmt in COVER_ART_FORMATS:
                pixels, h_pad, v_pad = render_thumbnail(image_bytes, fmt)
                buf = ithmb_files[fmt.format_id]
                offset = len(buf)
                buf.extend(pixels)
                fname = f":F{fmt.format_id}_0.ithmb"
                thumbs.append((fmt, offset, len(pixels), h_pad, v_pad, fname))
        except Exception:
            # Corrupt/unsupported embedded art shouldn't block the whole
            # sync -- just skip artwork for this one track.
            continue

        mhod_children = [_build_mhod_location(_build_mhni(*t)) for t in thumbs]
        mhii_children.append(_build_mhii(song_dbid, artwork_id, mhod_children))
        artwork_ids_by_track[track_id] = artwork_id
        artwork_id += 1

    sections = [
        _build_mhsd(1, _build_mhli(mhii_children)),
        _build_mhsd(2, _build_mhla_empty()),
        _build_mhsd(3, _build_mhlf(COVER_ART_FORMATS)),
    ]
    mhfd = _build_mhfd(artwork_id, sections)

    ithmb_out = {f"F{fmt.format_id}_0.ithmb": bytes(ithmb_files[fmt.format_id]) for fmt in COVER_ART_FORMATS}
    return ArtworkDBResult(artworkdb_bytes=mhfd, ithmb_files=ithmb_out, artwork_ids_by_track=artwork_ids_by_track)
