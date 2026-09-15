# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A GTK4/libadwaita desktop app (`ipodmanager`) that browses, imports to, and
deletes music on classic click-wheel iPods and (partially) iPhone/iPod
Touch-generation devices, without iTunes. It reimplements Apple's
`iTunesDB` binary format from scratch in pure Python — no `libgpod`
dependency — by transliterating libgpod's actual C source
(`db-itunes-parser.h`, `itdb_itunesdb.c`, `db-artwork-writer.c`,
`itdb_hash72.c`) rather than reconstructing the format from memory.

Read `README.md` for user-facing status/setup, and
`docs/HARDWARE_FINDINGS.md` for the detailed real-hardware investigation
behind the iPhone-generation and classic-iPod-artwork code paths — both
have context that matters before touching those areas.

## Commands

```bash
# Setup
sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 ffmpeg
pip install -e .

# Run the app
python -m ipodmanager.ui.app

# Run the full test suite
pip install pytest
pytest

# Run a single test file / test
pytest tests/test_itunesdb.py
pytest tests/test_itunesdb.py::test_playlists_at_nonstandard_index_are_found_by_magic

# Read-only diagnostic dump of a real device's iTunesDB/iTunesCDB structure
# (auto-detects a connected iPhone/iPod Touch; also accepts --udid or a path)
python -m ipodmanager.diagnose
```

There is no lint/format/typecheck command configured in this repo
(no ruff/black/mypy config in `pyproject.toml`).

iPhone-generation device support additionally needs `pymobiledevice3`,
which the README has installed into a project-local `.venv`
(`--system-site-packages` so it still sees system PyGObject/GTK) rather
than globally — but see "Documentation vs. implementation drift" below,
since the actually-shipped transport code does not use
`pymobiledevice3` at all.

## Architecture

**Layering, bottom to top:**

1. **`ipodmanager/db/`** — binary/SQLite format layer, no filesystem or
   device concept.
   - `itunesdb.py`: parses/builds the `iTunesDB`/`iTunesCDB` chunk format
     (`mhbd` → `mhsd` sections → `mhit`/`mhyp`/`mhip` records). Central
     design rule: **existing tracks and playlists are kept as opaque byte
     blobs and copied through unmodified** unless directly touched — only
     new tracks are freshly synthesized (`build_new_mhit`), and only
     playlists referencing a deleted track are patched. This is what
     makes a save byte-for-byte identical to the original except for
     what actually changed, which is the safety property the whole app
     leans on when writing to someone's real, irreplaceable music
     library. `ITunesDB` also tracks any `mhsd` section it doesn't
     understand (podcast groupings, Genius data, etc.) as raw bytes and
     round-trips it unchanged; `has_stale_raw_reference()` can detect
     (read-only, best-effort) when a deleted track is still referenced
     inside one of those unparsed sections.
   - `artworkdb.py`: writes classic-iPod `ArtworkDB` + `.ithmb` thumbnail
     files. Always rebuilt from scratch on import (re-scans every
     on-device track's tags), not merged incrementally.
   - `hash72.py`: the AES-128-CBC signature iPhone/Touch-generation
     devices require over the written database. It *extracts* a secret
     from a hash already present on the device (left by a prior real
     iTunes/libgpod sync) rather than deriving one fresh — a device that
     has never synced with real iTunes can't be signed for.
   - `sqlite_library.py`: mirrors track add/remove into the
     iPhone-generation `Library.itdb`/`Locations.itdb` SQLite bundle.
     Known **not sufficient** to make new tracks show up in the device's
     own Music app — see `docs/HARDWARE_FINDINGS.md`.
2. **`ipodmanager/transport/`** — device discovery/mounting, no format
   knowledge.
   - `classic.py`: finds a mounted classic iPod via `Gio.VolumeMonitor`
     (anything with `iPod_Control/iTunes/iTunesDB` on it).
   - `iphone.py`: finds/mounts an iPhone/iPod Touch via the
     `idevice_id`/`ideviceinfo`/`ifuse` CLI tools (libimobiledevice),
     mounting its AFC media jail as a normal FUSE path. Its own docstring
     calls this "read-only reconnaissance for now."
   - `base.py`: the `Device` dataclass both transports produce — the
     common handle (`mount_root`, `itunesdb_path`, `music_dir`, plus
     iPhone-only `hash_info_path`/`udid_bytes`) everything above this
     layer works from.
3. **`ipodmanager/audio/`** — file-level concerns, no device concept.
   `inspect.py` reads tags/artwork via `mutagen`; `transcode.py` shells
   out to `ffmpeg` (FLAC→ALAC, and anything→AAC 320 when the quality
   setting is on).
4. **`ipodmanager/library.py`** — the orchestration layer everything else
   is a detail of. `Library` ties a `Device` + its parsed `ITunesDB` +
   the real filesystem together: `import_file`/`import_paths`/
   `import_playlist` (transcode → copy into a rotated `iPod_Control/
   Music/F##/` folder → synthesize a `TrackMeta` → `db.add_track`),
   `delete_track`, playlist CRUD, and `save()` (which backs up first,
   serializes, hash72-signs if `device.kind == "iphone"`, then does a
   write-to-temp-then-atomic-rename). This is the layer to change for
   any new import/delete/playlist behavior — the UI layer just calls
   into it and re-renders.
5. **`ipodmanager/ui/`** — GTK4 + libadwaita. `window.py` (~800 lines) is
   a single `IPodWindow` holding essentially all UI logic: device
   lifecycle (`refresh_device`), the track `Gtk.ColumnView` + its
   `Gtk.MultiSelection`/`Gio.ListStore` of `TrackObject` (a thin GObject
   wrapper in `track_object.py` so `TrackRow` dataclasses can live in a
   list model), the Playlists view, right-click context menus (built via
   a shared `_popover_menu` helper), and long-running imports run on a
   background `threading.Thread` with progress reported back via
   `GLib.idle_add`-style callbacks into the progress page.

**Settings** (`settings.py`) is a single JSON file under the XDG config
dir with one persisted boolean (convert-to-AAC-320) — deliberately not
GSettings, since that needs a compiled/installed schema for one flag.

**Backups**: every `Library.save()` copies the current on-device
database to `<control_dir>/iTunes/.ipodmanager_backups/` before writing,
timestamped to the microsecond. Writes always go to a `.tmp` path and
`replace()` atomically — never write the live `iTunesDB`/`iTunesCDB`
path directly.

## Testing approach

Tests build synthetic fixtures (`tests/helpers.py`: a minimal but
format-correct empty `mhbd`, and minimal real SQLite DBs matching just
the columns `sqlite_library.py` touches) rather than depending on real
device images or real hardware. `tests/test_library.py` exercises full
import → save → reload → delete → save → reload cycles against a fake
device directory on disk. There is no hardware-in-the-loop test — real
hardware validation is manual and is what `docs/HARDWARE_FINDINGS.md`
records.

## Documentation vs. implementation drift

`docs/HARDWARE_FINDINGS.md` (and the README section it's linked from)
describes iPhone-generation sync as tested via `pymobiledevice3`,
including a specific lockdown/notification-proxy bracketing sequence
(`syncWillStart` → lock `/com.apple.itunes.lock_sync` → `syncLockRequest`
→ `syncDidStart` → write → `syncDidFinish`) confirmed necessary for a
correct sync. **None of that bracketing is implemented in
`ipodmanager/transport/iphone.py` or in `Library.save()`** — the shipped
code mounts via `ifuse` and writes the signed database straight to the
mounted path with no lockdown handshake at all. Do not assume the
documented protocol is live in this codebase; if you're working on
iPhone-generation writes, that bracketing is the most likely next thing
to actually implement (in `transport/iphone.py`, invoked from
`Library.save()`), not something to treat as already handled.
