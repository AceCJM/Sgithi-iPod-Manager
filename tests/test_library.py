"""End-to-end test of Library against a fake "device" directory on disk
(no real hardware needed): import a FLAC (exercising the transcode path),
verify it shows up and the file lands on "disk", save/reload, then delete
it and verify the file is gone and the reference is scrubbed.
"""

import io
import shutil
import subprocess
from pathlib import Path

import pytest

from ipodmanager.db import itunesdb as idb
from ipodmanager.library import Library, discover_audio_files, parse_m3u
from ipodmanager.settings import Settings
from ipodmanager.transport.base import Device

from .helpers import build_empty_mhbd

ffmpeg_available = shutil.which("ffmpeg") is not None


@pytest.fixture
def fake_device(tmp_path) -> Device:
    control = tmp_path / "iPod_Control"
    (control / "iTunes").mkdir(parents=True)
    (control / "Music").mkdir(parents=True)
    (control / "iTunes" / "iTunesDB").write_bytes(build_empty_mhbd())
    return Device(
        kind="classic_ipod",
        display_name="Fake iPod",
        mount_root=tmp_path,
        itunesdb_path=control / "iTunes" / "iTunesDB",
        music_dir=control / "Music",
    )


@pytest.fixture
def sample_flac(tmp_path) -> Path:
    out = tmp_path / "sample.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=1",
         "-ar", "44100", str(out)],
        check=True, capture_output=True,
    )
    from mutagen.flac import FLAC
    f = FLAC(str(out))
    f["title"] = "Fixture Song"
    f["artist"] = "Fixture Artist"
    f["album"] = "Fixture Album"
    f.save()
    return out


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
def test_import_and_delete_roundtrip(fake_device, sample_flac):
    lib = Library(fake_device)
    lib.load()
    assert lib.list_tracks() == []

    row = lib.import_file(sample_flac)
    assert row.title == "Fixture Song"
    assert row.artist == "Fixture Artist"

    on_disk = fake_device.mount_root / row.ipod_location.lstrip(":").replace(":", "/")
    assert on_disk.is_file()
    assert on_disk.suffix == ".m4a"  # transcoded from FLAC

    tracks = lib.list_tracks()
    assert len(tracks) == 1
    assert tracks[0].track_id == row.track_id

    lib.save()

    lib2 = Library(fake_device)
    lib2.load()
    assert len(lib2.list_tracks()) == 1
    assert lib2.list_tracks()[0].title == "Fixture Song"

    lib2.delete_track(row.track_id)
    assert lib2.list_tracks() == []
    assert not on_disk.exists()

    lib2.save()
    lib3 = Library(fake_device)
    lib3.load()
    assert lib3.list_tracks() == []

    backups = list((fake_device.mount_root / "iPod_Control" / "iTunes" / ".ipodmanager_backups").glob("*.bak"))
    assert len(backups) == 2  # one backup taken per save() call


def test_discover_audio_files_recurses_and_filters(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.flac").write_bytes(b"")
    (tmp_path / "sub" / "b.m4a").write_bytes(b"")
    (tmp_path / "sub" / "notes.txt").write_bytes(b"")
    (tmp_path / "c.MP4").write_bytes(b"")  # case-insensitive match

    found = discover_audio_files(tmp_path)
    assert found == sorted(found)  # stable order
    names = {p.name for p in found}
    assert names == {"a.flac", "b.m4a", "c.MP4"}


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
def test_import_paths_batch_reports_progress_and_saves_once(fake_device, tmp_path):
    import subprocess as sp
    from mutagen.flac import FLAC

    paths = []
    for i in range(3):
        out = tmp_path / f"track{i}.flac"
        sp.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={330+i}:duration=1", "-ar", "44100", str(out)],
            check=True, capture_output=True,
        )
        f = FLAC(str(out))
        f["title"] = f"Batch Track {i}"
        f.save()
        paths.append(out)

    lib = Library(fake_device)
    lib.load()

    progress_events = []
    result = lib.import_paths(paths, on_progress=lambda i, total, msg: progress_events.append((i, total)))

    assert len(result.imported) == 3
    assert result.errors == []
    assert progress_events[-1] == (3, 3)  # final event reports full completion
    assert any(i == 3 and total == 3 for i, total in progress_events)

    # saved exactly once for the whole batch, not once per file
    backups = list((fake_device.mount_root / "iPod_Control" / "iTunes" / ".ipodmanager_backups").glob("*.bak"))
    assert len(backups) == 1

    lib2 = Library(fake_device)
    lib2.load()
    assert len(lib2.list_tracks()) == 3


def test_import_paths_continues_after_one_bad_file(fake_device, tmp_path):
    bad = tmp_path / "not_audio.flac"
    bad.write_bytes(b"this is not a real flac file")

    lib = Library(fake_device)
    lib.load()
    result = lib.import_paths([bad])
    assert result.imported == []
    assert len(result.errors) == 1
    assert result.errors[0][0] == bad


def _make_flac(path: Path, freq: int, title: str, with_art: bool = False) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1", "-ar", "44100", str(path)],
        check=True, capture_output=True,
    )
    from mutagen.flac import FLAC, Picture

    f = FLAC(str(path))
    f["title"] = title
    if with_art:
        from PIL import Image

        img = Image.new("RGB", (50, 50), (10, 200, 10))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        pic = Picture()
        pic.type = 3
        pic.mime = "image/png"
        pic.data = buf.getvalue()
        f.add_picture(pic)
    f.save()
    return path


def test_parse_m3u_resolves_relative_entries_and_skips_comments(tmp_path):
    (tmp_path / "songs").mkdir()
    m3u = tmp_path / "playlist.m3u"
    m3u.write_text("#EXTM3U\n#EXTINF:123,Some Song\nsongs/one.flac\n\n/abs/two.flac\n")
    entries = parse_m3u(m3u)
    assert entries == [tmp_path / "songs" / "one.flac", Path("/abs/two.flac")]


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
def test_import_playlist_creates_playlist_with_members(fake_device, tmp_path):
    a = _make_flac(tmp_path / "a.flac", 300, "Song A")
    b = _make_flac(tmp_path / "b.flac", 400, "Song B")
    m3u = tmp_path / "Road Trip.m3u"
    m3u.write_text("a.flac\nb.flac\n")

    lib = Library(fake_device)
    lib.load()
    result = lib.import_playlist(m3u)
    assert len(result.imported) == 2
    assert result.errors == []

    playlists = lib.list_playlists()
    assert len(playlists) == 1
    assert playlists[0].name == "Road Trip"
    assert playlists[0].track_count == 2

    filtered = lib.list_tracks(track_ids=playlists[0].track_ids)
    assert [t.title for t in filtered] == ["Song A", "Song B"]

    # reload from disk -- playlist membership must have been persisted
    lib2 = Library(fake_device)
    lib2.load()
    playlists2 = lib2.list_playlists()
    assert len(playlists2) == 1
    assert playlists2[0].track_count == 2


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
def test_import_playlist_reports_missing_file(fake_device, tmp_path):
    a = _make_flac(tmp_path / "a.flac", 300, "Song A")
    m3u = tmp_path / "list.m3u"
    m3u.write_text("a.flac\nmissing.flac\n")

    lib = Library(fake_device)
    lib.load()
    result = lib.import_playlist(m3u)
    assert len(result.imported) == 1
    assert len(result.errors) == 1
    assert result.errors[0][0] == tmp_path / "missing.flac"


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
def test_import_playlist_reusing_same_name_does_not_duplicate_playlist(fake_device, tmp_path):
    a = _make_flac(tmp_path / "a.flac", 300, "Song A")
    b = _make_flac(tmp_path / "b.flac", 400, "Song B")
    m3u1 = tmp_path / "list.m3u"
    m3u1.write_text("a.flac\n")
    m3u2 = tmp_path / "list2.m3u"
    m3u2.write_text("b.flac\n")

    lib = Library(fake_device)
    lib.load()
    lib.import_playlist(m3u1, playlist_name="Mix")
    lib.import_playlist(m3u2, playlist_name="Mix")

    playlists = lib.list_playlists()
    assert len(playlists) == 1
    assert playlists[0].track_count == 2


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
def test_rebuild_artwork_db_writes_files_and_links_track(fake_device, tmp_path):
    song = _make_flac(tmp_path / "art.flac", 300, "Arty Song", with_art=True)

    lib = Library(fake_device)
    lib.load()
    row = lib.import_file(song)
    lib.rebuild_artwork_db()
    lib.save()

    artwork_dir = fake_device.mount_root / "iPod_Control" / "Artwork"
    assert (artwork_dir / "ArtworkDB").is_file()
    assert (artwork_dir / "F1028_0.ithmb").stat().st_size > 0
    assert (artwork_dir / "F1029_0.ithmb").stat().st_size > 0

    track = next(t for t in lib.db.tracks if t.track_id == row.track_id)
    assert track.raw[0xA4] == 2  # has_artwork


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
def test_convert_to_aac_320_setting_shrinks_flac_instead_of_alac(fake_device, tmp_path):
    song = _make_flac(tmp_path / "lossless.flac", 300, "Lossless Song")

    lib = Library(fake_device, settings=Settings(convert_to_aac_320=True))
    lib.load()
    row = lib.import_file(song)

    on_disk = fake_device.mount_root / row.ipod_location.lstrip(":").replace(":", "/")
    from mutagen.mp4 import MP4

    codec = MP4(str(on_disk)).info.codec.lower()
    assert "alac" not in codec  # shrunk to lossy AAC, not preserved as lossless ALAC


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
def test_convert_to_aac_320_setting_off_by_default_keeps_lossless(fake_device, tmp_path):
    song = _make_flac(tmp_path / "lossless2.flac", 300, "Lossless Song 2")

    lib = Library(fake_device)  # default settings: convert_to_aac_320 = False
    lib.load()
    row = lib.import_file(song)

    on_disk = fake_device.mount_root / row.ipod_location.lstrip(":").replace(":", "/")
    from mutagen.mp4 import MP4

    codec = MP4(str(on_disk)).info.codec.lower()
    assert "alac" in codec


def _add_bare_track(lib: Library, title: str) -> int:
    """Add a track directly via the db layer (no ffmpeg/real audio file
    needed) -- for tests that only care about playlist membership."""
    meta = idb.TrackMeta(track_id=0, dbid=0, title=title, ipod_location=idb.relpath_to_ipod_path(f"iPod_Control/Music/F00/{title}.m4a"))
    return lib.db.add_track(meta).track_id


def test_rename_playlist(fake_device):
    lib = Library(fake_device)
    lib.load()
    tid = _add_bare_track(lib, "Song")
    lib.add_tracks_to_playlist("Old Name", [tid])

    lib.rename_playlist("Old Name", "New Name")

    names = {p.name for p in lib.list_playlists()}
    assert names == {"New Name"}
    assert lib.list_playlists()[0].track_ids == [tid]

    lib2 = Library(fake_device)
    lib2.load()
    assert {p.name for p in lib2.list_playlists()} == {"New Name"}


def test_rename_playlist_raises_if_missing(fake_device):
    lib = Library(fake_device)
    lib.load()
    with pytest.raises(Exception):
        lib.rename_playlist("Nope", "Whatever")


def test_delete_playlist_leaves_tracks_intact(fake_device):
    lib = Library(fake_device)
    lib.load()
    tid = _add_bare_track(lib, "Song")
    lib.add_tracks_to_playlist("Temp", [tid])
    assert len(lib.list_playlists()) == 1

    lib.delete_playlist("Temp")
    assert lib.list_playlists() == []
    assert len(lib.list_tracks()) == 1  # the track itself wasn't touched

    lib2 = Library(fake_device)
    lib2.load()
    assert lib2.list_playlists() == []
    assert len(lib2.list_tracks()) == 1


def test_add_and_remove_tracks_from_playlist(fake_device):
    lib = Library(fake_device)
    lib.load()
    t1 = _add_bare_track(lib, "A")
    t2 = _add_bare_track(lib, "B")

    lib.add_tracks_to_playlist("Mix", [t1, t2])
    playlist = lib.list_playlists()[0]
    assert playlist.track_ids == [t1, t2]

    # adding an already-present track again doesn't duplicate it
    lib.add_tracks_to_playlist("Mix", [t1])
    assert lib.list_playlists()[0].track_ids == [t1, t2]

    lib.remove_tracks_from_playlist("Mix", [t1])
    assert lib.list_playlists()[0].track_ids == [t2]

    lib2 = Library(fake_device)
    lib2.load()
    assert lib2.list_playlists()[0].track_ids == [t2]
