# iPod Manager

A GTK4/libadwaita app to browse, upload, and delete music on a classic
click-wheel iPod, without iTunes.

It reimplements the iPod's `iTunesDB` binary format from scratch in pure
Python (no `libgpod` dependency) and talks to the device as a plain mounted
USB drive. FLAC files are automatically transcoded to ALAC on import, since
no iPod firmware can actually decode FLAC.

## Status

**Working (v1, targets classic click-wheel iPods, e.g. iPod Video 5/5.5G):**

- Detect a connected classic iPod (anything with `iPod_Control` on it)
- List existing tracks with title/artist/album/genre/duration/bitrate/size
- Import FLAC or ALAC/AAC (`.m4a`/`.mp4`/`.m4b`) files, with automatic
  FLAC → ALAC transcoding (via ffmpeg) so the device can actually play them
- Delete tracks (removes the file and its database entry, and scrubs it
  from playlist membership so nothing points at a deleted track)
- Every write to `iTunesDB` is preceded by a timestamped backup under
  `iPod_Control/iTunes/.ipodmanager_backups/`

**Not yet implemented:**

- **iPhone 3G / iPod Touch-generation devices.** These don't mount as a
  plain USB drive — they need the `usbmuxd`/lockdown/AFC protocol stack
  (via `libimobiledevice`/`ifuse`, both already used by this project's
  dependencies) instead of a filesystem mount, a `hash72` cryptographic
  signature over the whole `iTunesDB` for the device to accept it (the
  algorithm is implemented and verified against libgpod's source, but it
  works by *extracting* a secret from a hash already on the device — put
  there by a real iTunes sync — and reusing it, so it only works on a
  device that's been synced with real iTunes at least once), and a
  companion SQLite "shadow" database that the on-device Music app on these
  generations actually reads from. This is a well-scoped second phase, not
  attempted here yet.
- Writing album art onto the device (on-device Cover Flow/now-playing art).
  The app shows artwork it finds in a file's tags everywhere in its own UI
  (browsing, before upload), but doesn't write to the device's `ArtworkDB`
  — that's a separate, poorly-documented, per-device raw-pixel binary
  format with real corruption risk if gotten wrong. iPod 3G has no color
  screen anyway; this only ever applies to Video 5/5.5G.
- Playlist management (creating/editing playlists). New tracks are added
  to the master playlist (so they show up under "Music"/"Songs"); any
  existing custom playlists round-trip untouched.
- Any `mhsd` section this app doesn't understand (album/artist browse
  indices, Genius data, podcast/categorized playlist lists) is preserved
  byte-for-byte on write, not regenerated. If your library uses podcasts
  specifically, deleting a track won't scrub it from a podcast playlist's
  membership list (a stale reference in a section this app doesn't
  interpret, not a source of playback truth).

## Setup

```
sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 ffmpeg
pip install -e .
```

Run it:

```
python -m ipodmanager.ui.app
```

Run the test suite (covers the binary format parser/writer with synthetic
round-trip tests, plus a full import→save→reload→delete→save→reload cycle
against a fake device directory on disk):

```
pip install pytest
pytest
```

## How it's built

- **`ipodmanager/db/itunesdb.py`** — the binary format layer. Existing
  tracks and playlists are kept as opaque byte blobs and copied through
  unmodified unless directly touched; only newly added tracks are freshly
  synthesized, and only playlists referencing a deleted track are patched.
  This means an existing, working library round-trips byte-for-byte
  identical except for what you actually changed — deliberately the
  safest possible design against a real music collection. Every chunk
  offset was verified against
  [libgpod](https://github.com/fadingred/libgpod)'s actual source
  (`src/db-itunes-parser.h`, `src/itdb_itunesdb.c`, GNU LGPL-2.1+) rather
  than reconstructed from memory.
- **`ipodmanager/audio/`** — `inspect.py` reads tags/artwork via `mutagen`;
  `transcode.py` shells out to `ffmpeg` for FLAC → ALAC.
- **`ipodmanager/transport/`** — device detection. `classic.py` finds a
  mounted classic iPod via `Gio.VolumeMonitor`. `iphone.py` (phase 2, not
  yet written) would add the AFC/hash72/SQLite-shadow-db path.
- **`ipodmanager/library.py`** — orchestrates device + database +
  filesystem: importing (with transcode when needed), deleting, backup,
  and safe writes (write to a temp file, then atomic rename).
- **`ipodmanager/ui/`** — the GTK4 + libadwaita interface.

## A note on testing

This has been verified with synthetic round-trip tests and against a
simulated fake device directory on disk, and the app itself has been
launched and visually confirmed to render correctly (empty state, and a
populated track list). It has **not yet been tested against a real
device** — that needs to happen before trusting it with an irreplaceable
music library. Test against a real iPod with a **spare/non-critical
library first**, and keep in mind the automatic backups live on the
device itself (`iPod_Control/iTunes/.ipodmanager_backups/`) — copy one off
the device if you want a copy that survives a "restore iPod" in iTunes.
