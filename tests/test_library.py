"""End-to-end test of Library against a fake "device" directory on disk
(no real hardware needed): import a FLAC (exercising the transcode path),
verify it shows up and the file lands on "disk", save/reload, then delete
it and verify the file is gone and the reference is scrubbed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from ipodmanager.library import Library, discover_audio_files
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
