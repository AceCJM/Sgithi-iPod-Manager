"""Ties a mounted device, its iTunesDB, and the local filesystem together:
scanning what's on the device, importing new files (transcoding FLAC to
ALAC on the way), and deleting tracks.
"""

from __future__ import annotations

import os
import random
import shutil
import string
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .audio import inspect as audio_inspect
from .audio import transcode
from .db import artworkdb
from .db import hash72
from .db import itunesdb as idb
from .settings import Settings
from .transport.base import Device

FILETYPE_LABELS = {
    "alac": "Apple Lossless audio file",
    "aac": "AAC audio file",
    "flac": "Apple Lossless audio file",  # FLAC is always transcoded before this is used
}

MUSIC_FOLDER_COUNT = 20

SUPPORTED_AUDIO_EXTS = {".flac", ".m4a", ".mp4", ".m4b"}
M3U_EXTS = {".m3u", ".m3u8"}

AAC_320_BITRATE = 320


def discover_audio_files(folder: Path) -> list[Path]:
    """Recursively find every supported audio file under folder, sorted for
    a stable, predictable import order."""
    folder = Path(folder)
    found = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS]
    return sorted(found)


def parse_m3u(path: Path) -> list[Path]:
    """Read an .m3u/.m3u8 playlist and return the (resolved, but not
    necessarily existing) paths it references, in order. Blank lines and
    '#EXT...' directive/comment lines are skipped; relative entries are
    resolved against the playlist file's own directory, matching how every
    player that writes these treats them.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    base = path.parent
    entries: list[Path] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entry = Path(line)
        if not entry.is_absolute():
            entry = base / entry
        entries.append(entry)
    return entries


@dataclass
class ImportResult:
    imported: list[TrackRow]
    errors: list[tuple[Path, str]]


@dataclass
class PlaylistRow:
    """Flattened view of a device playlist, for display in the UI.

    `artwork`/`artwork_mime` are only ever the embedded cover art of the
    first member track that has any -- this app doesn't write a native
    on-device "playlist thumbnail" (classic click-wheel firmware, even the
    5.5G's Cover Flow, has no such concept), it's purely a convenience for
    this app's own playlist list.
    """

    name: str
    track_ids: list[int]
    artwork: Optional[bytes] = None
    artwork_mime: Optional[str] = None

    @property
    def track_count(self) -> int:
        return len(self.track_ids)


class LibraryError(RuntimeError):
    pass


@dataclass
class TrackRow:
    """Flattened view of a device track, for display in the UI."""

    track_id: int
    title: str
    artist: str
    album: str
    genre: str
    length_ms: int
    size: int
    bitrate: int
    year: int
    track_nr: int
    tracks_total: int
    ipod_location: str

    @classmethod
    def from_raw(cls, raw: idb.RawTrack) -> "TrackRow":
        d = raw.display
        return cls(
            track_id=raw.track_id,
            title=d.get("title", ""),
            artist=d.get("artist", ""),
            album=d.get("album", ""),
            genre=d.get("genre", ""),
            length_ms=d.get("length_ms", 0),
            size=d.get("size", 0),
            bitrate=d.get("bitrate", 0),
            year=d.get("year", 0),
            track_nr=d.get("track_nr", 0),
            tracks_total=d.get("tracks_total", 0),
            ipod_location=d.get("ipod_location", ""),
        )


def _pick_music_folder(music_dir: Path) -> Path:
    existing = sorted(p for p in music_dir.glob("F*") if p.is_dir()) if music_dir.exists() else []
    if len(existing) < MUSIC_FOLDER_COUNT:
        existing = []
        for i in range(MUSIC_FOLDER_COUNT):
            f = music_dir / f"F{i:02d}"
            f.mkdir(parents=True, exist_ok=True)
            existing.append(f)
    return random.choice(existing)


def _random_ipod_filename(ext: str) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=4)) + ext


class Library:
    def __init__(self, device: Device, settings: Optional[Settings] = None):
        self.device = device
        self.settings = settings if settings is not None else Settings.load()
        self.db: Optional[idb.ITunesDB] = None

    def load(self) -> None:
        data = self.device.itunesdb_path.read_bytes()
        self.db = idb.ITunesDB.parse(data)

    def list_tracks(self, track_ids: Optional[list[int]] = None) -> list[TrackRow]:
        """All tracks, or (if track_ids is given) just those, in that order
        -- used to show a single playlist's contents."""
        assert self.db is not None
        tracks = self.db.tracks
        if track_ids is not None:
            order = {tid: i for i, tid in enumerate(track_ids)}
            tracks = sorted((t for t in tracks if t.track_id in order), key=lambda t: order[t.track_id])
        return [TrackRow.from_raw(t) for t in tracks]

    def list_playlists(self) -> list[PlaylistRow]:
        """Every regular (non-master) playlist, with a best-effort cover
        thumbnail read from the first member track's embedded tag art."""
        assert self.db is not None
        rows = []
        for pl in self.db.playlists:
            if pl.is_master:
                continue
            artwork = None
            artwork_mime = None
            for tid in pl.mhip_track_ids:
                track = next((t for t in self.db.tracks if t.track_id == tid), None)
                if track is None:
                    continue
                info = self._inspect_device_track(track)
                if info is not None and info.artwork:
                    artwork, artwork_mime = info.artwork, info.artwork_mime
                    break
            rows.append(PlaylistRow(name=pl.title() or "(untitled)", track_ids=list(pl.mhip_track_ids), artwork=artwork, artwork_mime=artwork_mime))
        return rows

    def _inspect_device_track(self, track: idb.RawTrack) -> Optional[audio_inspect.AudioInfo]:
        rel = idb.ipod_path_to_relpath(track.display.get("ipod_location", ""))
        if not rel:
            return None
        path = self.device.mount_root / rel
        if not path.is_file():
            return None
        try:
            return audio_inspect.inspect(path)
        except Exception:  # noqa: BLE001 - a corrupt/unreadable file just has no art
            return None

    def backup(self) -> Path:
        ts = time.strftime("%Y%m%d-%H%M%S")
        micros = f"{time.time() % 1:.6f}"[2:]
        dest = self.device.backups_dir() / f"{self.device.itunesdb_path.name}.{ts}-{micros}.bak"
        # copyfile (not copy2): AFC-backed filesystems (iPhone, via ifuse)
        # don't support chmod, which copy2 tries after copying data.
        shutil.copyfile(self.device.itunesdb_path, dest)
        return dest

    def save(self) -> None:
        assert self.db is not None
        self.backup()
        data = self.db.serialize()
        if self.device.kind == "iphone":
            data = self._sign_for_iphone(data)
        tmp = self.device.itunesdb_path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(self.device.itunesdb_path)
        try:
            os.sync()
        except OSError:
            pass

    def _sign_for_iphone(self, data: bytes) -> bytes:
        device = self.device
        assert device.udid_bytes is not None and device.hash_info_path is not None
        try:
            secret = hash72.get_or_bootstrap_secret(
                device.hash_info_path, device.udid_bytes, existing_itdb_data=device.itunesdb_path.read_bytes()
            )
        except (ValueError, OSError) as e:
            raise LibraryError(
                "Couldn't get a hash72 signing secret for this iPhone. This device needs to have been "
                "synced with real iTunes (or a libgpod-based tool) at least once already -- there's no "
                f"way to derive a fresh signature otherwise. ({e})"
            ) from e
        return hash72.sign(data, secret)

    def _quality_transcode_target(self, info: audio_inspect.AudioInfo) -> Optional[str]:
        """If the "convert to AAC 320" setting is on and this source is
        better than that (lossless, or lossy above 320kbps), returns "aac"
        to shrink it. Otherwise None -- leave format/quality as-is (still
        subject to the separate FLAC-can't-play-natively transcode below).
        """
        if not self.settings.convert_to_aac_320:
            return None
        if info.format in ("flac", "alac"):
            return "aac"
        if info.bitrate and info.bitrate > AAC_320_BITRATE:
            return "aac"
        return None

    def import_file(self, src_path: Path, on_progress: Optional[Callable[[str], None]] = None) -> TrackRow:
        assert self.db is not None
        src_path = Path(src_path)

        def report(msg: str) -> None:
            if on_progress:
                on_progress(msg)

        report(f"Reading {src_path.name}")
        info = audio_inspect.inspect(src_path)
        shrink_to_aac = self._quality_transcode_target(info) == "aac"

        work_path = src_path
        tmpdir = None
        if info.format == "flac" and shrink_to_aac:
            report(f"Transcoding {src_path.name} (FLAC → AAC 320, quality setting enabled)")
            tmpdir = tempfile.mkdtemp(prefix="ipodmanager-")
            work_path = Path(tmpdir) / (src_path.stem + ".m4a")
            try:
                transcode.to_aac(src_path, work_path, AAC_320_BITRATE)
                info = audio_inspect.inspect(work_path)
            except Exception:
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise
        elif info.format == "flac":
            report(f"Transcoding {src_path.name} (FLAC → ALAC, iPod can't play FLAC)")
            tmpdir = tempfile.mkdtemp(prefix="ipodmanager-")
            work_path = Path(tmpdir) / (src_path.stem + ".m4a")
            try:
                transcode.flac_to_alac(src_path, work_path)
                info = audio_inspect.inspect(work_path)
            except Exception:
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise
        elif shrink_to_aac:
            report(f"Transcoding {src_path.name} ({info.format.upper()} → AAC 320, quality setting enabled)")
            tmpdir = tempfile.mkdtemp(prefix="ipodmanager-")
            work_path = Path(tmpdir) / (src_path.stem + ".m4a")
            try:
                transcode.to_aac(src_path, work_path, AAC_320_BITRATE)
                info = audio_inspect.inspect(work_path)
            except Exception:
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise

        try:
            report(f"Copying {src_path.name} to device")
            folder = _pick_music_folder(self.device.music_dir)
            dest = None
            for _ in range(50):
                candidate = folder / _random_ipod_filename(work_path.suffix)
                if not candidate.exists():
                    dest = candidate
                    break
            if dest is None:
                raise LibraryError("could not allocate a unique on-device filename")

            # copyfile (not copy2): see backup()'s comment -- AFC/ifuse
            # doesn't support chmod.
            shutil.copyfile(work_path, dest)
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

        rel = dest.relative_to(self.device.mount_root)
        meta = idb.TrackMeta(
            track_id=0,
            dbid=0,
            title=info.title or src_path.stem,
            artist=info.artist,
            album=info.album,
            genre=info.genre,
            composer=info.composer,
            comment=info.comment,
            filetype=FILETYPE_LABELS.get(info.format, "AAC audio file"),
            ipod_location=idb.relpath_to_ipod_path(str(rel)),
            size=dest.stat().st_size,
            length_ms=info.length_ms,
            track_nr=info.track_nr,
            tracks_total=info.tracks_total,
            disc_nr=info.disc_nr,
            discs_total=info.discs_total,
            year=info.year,
            bitrate=info.bitrate,
            samplerate=info.samplerate,
            date_added=time.time(),
        )
        report(f"Adding {meta.title} to library")
        raw = self.db.add_track(meta)
        return TrackRow.from_raw(raw)

    def import_paths(
        self,
        paths: list[Path],
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        save: bool = True,
    ) -> ImportResult:
        """Import multiple files (e.g. everything found under a folder),
        reporting (completed_count, total_count, message) as it goes, and
        saving once at the end rather than once per file.
        """
        total = len(paths)
        imported: list[TrackRow] = []
        errors: list[tuple[Path, str]] = []

        def report(i: int) -> Callable[[str], None]:
            def _cb(stage_msg: str) -> None:
                if on_progress:
                    on_progress(i, total, stage_msg)

            return _cb

        for i, path in enumerate(paths):
            try:
                row = self.import_file(path, on_progress=report(i))
                imported.append(row)
            except Exception as e:  # noqa: BLE001 - one bad file shouldn't abort the whole batch
                errors.append((path, str(e)))
            if on_progress:
                on_progress(i + 1, total, path.name)

        if save and imported:
            if self.device.kind == "classic_ipod":
                self.rebuild_artwork_db(on_progress=on_progress)
            if on_progress:
                on_progress(total, total, "Saving library…")
            self.save()

        return ImportResult(imported=imported, errors=errors)

    def import_playlist(
        self,
        m3u_path: Path,
        playlist_name: Optional[str] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> ImportResult:
        """Parse an .m3u/.m3u8, import every audio file it references (there's
        no reliable way to detect "already on the device" once a file's
        original path is gone, so this always imports each referenced file,
        same as "Add Files…" would for the same paths), and add the results
        to a playlist with this name -- creating it if needed, or reusing it
        if a playlist with that name already exists.
        """
        assert self.db is not None
        m3u_path = Path(m3u_path)
        entries = parse_m3u(m3u_path)

        paths: list[Path] = []
        errors: list[tuple[Path, str]] = []
        for entry in entries:
            if not entry.is_file():
                errors.append((entry, "referenced file not found"))
            elif entry.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
                errors.append((entry, f"unsupported file type: {entry.suffix}"))
            else:
                paths.append(entry)

        result = self.import_paths(paths, on_progress=on_progress, save=False)
        result.errors = errors + result.errors

        name = playlist_name or m3u_path.stem
        playlist = self.db.get_or_create_playlist(name)
        existing_ids = set(playlist.mhip_track_ids)
        for row in result.imported:
            if row.track_id not in existing_ids:
                playlist.add_track(row.track_id)
                existing_ids.add(row.track_id)

        if result.imported:
            if self.device.kind == "classic_ipod":
                self.rebuild_artwork_db(on_progress=on_progress)
            self.save()

        return result

    def rebuild_artwork_db(self, on_progress: Optional[Callable[[int, int, str], None]] = None) -> None:
        """Regenerate ArtworkDB + its .ithmb thumbnail files from scratch,
        based on whatever embedded cover art is currently in each track's
        on-device file, and re-point every track's mhit header at its new
        artwork entry (or clear the link if it has none).

        Always a full rebuild rather than an incremental merge -- see
        db/artworkdb.py's module docstring for why. This means it re-reads
        every track's tags on every call, which is the main cost of
        importing music with this feature on.
        """
        assert self.db is not None
        if self.device.kind != "classic_ipod":
            return

        tracks = self.db.tracks
        total = len(tracks)
        tracks_with_art: list[tuple[int, int, bytes]] = []
        for i, t in enumerate(tracks):
            if on_progress:
                on_progress(total, total, f"Scanning artwork ({i + 1}/{total})")
            info = self._inspect_device_track(t)
            if info is not None and info.artwork:
                tracks_with_art.append((t.track_id, t.display.get("dbid", 0), info.artwork))

        if on_progress:
            on_progress(total, total, "Building ArtworkDB…")
        result = artworkdb.build_artwork_db(tracks_with_art)

        thumb_count = len(artworkdb.COVER_ART_FORMATS)
        for t in tracks:
            artwork_id = result.artwork_ids_by_track.get(t.track_id, 0)
            t.set_artwork_link(artwork_id, thumb_count if artwork_id else 0)

        artwork_dir = self.device.artwork_dir()
        artwork_dir.mkdir(parents=True, exist_ok=True)
        (artwork_dir / "ArtworkDB").write_bytes(result.artworkdb_bytes)
        for name, data in result.ithmb_files.items():
            (artwork_dir / name).write_bytes(data)

    def delete_track(self, track_id: int) -> None:
        assert self.db is not None
        track = next((t for t in self.db.tracks if t.track_id == track_id), None)
        if track is None:
            return
        rel = idb.ipod_path_to_relpath(track.display.get("ipod_location", ""))
        if rel:
            path = self.device.mount_root / rel
            if path.exists():
                path.unlink()
        self.db.remove_track(track_id)
