"""Read tags, format, and embedded artwork from a local audio file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mutagen.flac import FLAC
from mutagen.mp4 import MP4, MP4Cover

FLAC_EXTS = {".flac"}
ALAC_AAC_EXTS = {".m4a", ".mp4", ".m4b"}


@dataclass
class AudioInfo:
    path: Path
    format: str  # "flac", "alac", "aac", "other"
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    composer: str = ""
    comment: str = ""
    track_nr: int = 0
    tracks_total: int = 0
    disc_nr: int = 0
    discs_total: int = 0
    year: int = 0
    length_ms: int = 0
    bitrate: int = 0  # kbps
    samplerate: int = 44100
    channels: int = 2
    artwork: Optional[bytes] = None
    artwork_mime: Optional[str] = None


def _year_from_date(date_str: str) -> int:
    for part in (date_str or "").replace("-", " ").split():
        if len(part) == 4 and part.isdigit():
            return int(part)
    return 0


def _inspect_flac(path: Path) -> AudioInfo:
    f = FLAC(str(path))
    info = AudioInfo(
        path=path,
        format="flac",
        title=(f.get("title") or [""])[0],
        artist=(f.get("artist") or [""])[0],
        album=(f.get("album") or [""])[0],
        genre=(f.get("genre") or [""])[0],
        composer=(f.get("composer") or [""])[0],
        comment=(f.get("comment") or [""])[0],
        year=_year_from_date((f.get("date") or [""])[0]),
        length_ms=int(f.info.length * 1000),
        bitrate=int(f.info.bitrate / 1000) if f.info.bitrate else 0,
        samplerate=f.info.sample_rate,
        channels=f.info.channels,
    )
    tn = (f.get("tracknumber") or [""])[0]
    if "/" in tn:
        a, b = tn.split("/", 1)
        info.track_nr, info.tracks_total = _safe_int(a), _safe_int(b)
    else:
        info.track_nr = _safe_int(tn)
    dn = (f.get("discnumber") or [""])[0]
    if "/" in dn:
        a, b = dn.split("/", 1)
        info.disc_nr, info.discs_total = _safe_int(a), _safe_int(b)
    else:
        info.disc_nr = _safe_int(dn)
    if f.pictures:
        pic = f.pictures[0]
        info.artwork = pic.data
        info.artwork_mime = pic.mime
    return info


def _safe_int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def _inspect_mp4(path: Path) -> AudioInfo:
    f = MP4(str(path))
    codec = getattr(f.info, "codec", "") or ""
    fmt = "alac" if "alac" in codec.lower() else "aac"
    tags = f.tags or {}
    info = AudioInfo(
        path=path,
        format=fmt,
        title=_mp4_text(tags, "\xa9nam"),
        artist=_mp4_text(tags, "\xa9ART"),
        album=_mp4_text(tags, "\xa9alb"),
        genre=_mp4_text(tags, "\xa9gen"),
        composer=_mp4_text(tags, "\xa9wrt"),
        comment=_mp4_text(tags, "\xa9cmt"),
        year=_year_from_date(_mp4_text(tags, "\xa9day")),
        length_ms=int(f.info.length * 1000),
        bitrate=int(f.info.bitrate / 1000) if getattr(f.info, "bitrate", None) else 0,
        samplerate=getattr(f.info, "sample_rate", 44100),
        channels=getattr(f.info, "channels", 2),
    )
    trkn = tags.get("trkn")
    if trkn:
        info.track_nr, info.tracks_total = trkn[0][0], trkn[0][1]
    disk = tags.get("disk")
    if disk:
        info.disc_nr, info.discs_total = disk[0][0], disk[0][1]
    covr = tags.get("covr")
    if covr:
        cover = covr[0]
        info.artwork = bytes(cover)
        info.artwork_mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
    return info


def _mp4_text(tags, key: str) -> str:
    v = tags.get(key)
    return str(v[0]) if v else ""


def inspect(path: Path) -> AudioInfo:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in FLAC_EXTS:
        return _inspect_flac(path)
    if ext in ALAC_AAC_EXTS:
        return _inspect_mp4(path)
    raise ValueError(f"unsupported file type: {ext} (expected .flac, .m4a, .mp4, or .m4b)")
