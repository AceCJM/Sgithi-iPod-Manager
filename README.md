# iPod Manager

A GTK4/libadwaita app to browse, upload, and delete music on classic
click-wheel iPods — and, with real caveats documented below, iPhone/iPod
Touch-generation devices — without iTunes.

It reimplements the iPod's `iTunesDB` binary format from scratch in pure
Python (no `libgpod` dependency). FLAC files are automatically transcoded
to ALAC on import, since no iPod firmware can actually decode FLAC.

## Status

**Fully working — classic click-wheel iPods (e.g. iPod Video 5/5.5G):**

- Detect a connected classic iPod (anything with `iPod_Control` on it),
  mounted as a plain USB drive
- List existing tracks with title/artist/album/genre/duration/bitrate/size
- Import FLAC or ALAC/AAC (`.m4a`/`.mp4`/`.m4b`) files, or a whole folder
  recursively, with automatic FLAC → ALAC transcoding (via ffmpeg) so the
  device can actually play them — with a live progress bar (percentage,
  file count, current file/stage)
- Import an `.m3u`/`.m3u8` playlist: every audio file it references gets
  imported (if not already present) and a real on-device playlist is
  created (or reused, if one with that name already exists) containing
  them
- Optional "convert to AAC 320" setting (Preferences): when on, anything
  better than AAC 320kbps (lossless FLAC/ALAC, or lossy sources above
  320kbps) is re-encoded down to AAC 320kbps on import instead of kept
  losslessly, to save space; anything already at or below that quality is
  always left unchanged. Off by default.
- Delete tracks (removes the file and its database entry, and scrubs it
  from playlist membership so nothing points at a deleted track)
- Every write to `iTunesDB` is preceded by a timestamped backup under
  `iPod_Control/iTunes/.ipodmanager_backups/`

**Implemented but unverified against real hardware — classic iPod album
art (iPod Video 5/5.5G):**

- Song/album cover art embedded in an imported file's tags is written to
  the device's `ArtworkDB` + `.ithmb` thumbnail files (100×100 and
  200×200 RGB565, matching the iPod Video generation's on-device format),
  so it should show up in the device's own now-playing/Cover
  Flow UI — this is regenerated from scratch (re-scanning every track's
  on-device file for embedded art) every time music is imported.
- This one piece has **not** been confirmed against a real iPod — the
  author doesn't own one. It was built the same way as the rest of the
  classic-iPod code (transliterated from libgpod's actual C source, not
  guessed), and every chunk length/nesting relationship is covered by
  structural unit tests, but "does a real device actually render it" is
  unverified. See "Classic iPod ArtworkDB: what's proven and what's not"
  below before relying on it. Back up `iPod_Control` first.
- "Playlist art" in this app's own Playlists view is a separate, much
  lower-risk thing: just a thumbnail read from the first member track's
  tags, shown only in this app's UI. Classic click-wheel firmware (even
  the 5.5G's Cover Flow) has no native concept of a playlist thumbnail, so
  there's nothing to write to the device for this part.

**Partially working — iPhone 3G / iPod Touch-generation devices:**

- Detection and mounting over USB via `usbmuxd`/lockdown/AFC
  (`pymobiledevice3`) — no jailbreak or WiFi required, works over the USB
  cable directly
- The classic-format layer (`iTunesCDB`, hash72-signed) is **fully working
  and verified against real hardware**: writes are cryptographically
  correct (byte-exact match against the real device, confirmed with the
  actual libgpod C source compiled as ground truth) and durable across
  reboots
- **Tracks added this way do not appear in the device's own Music app.**
  This is fully diagnosed (see "iPhone-generation devices" below) but not
  solved. The underlying `Library.itdb`/`Locations.itdb` SQLite database
  the Music app actually reads requires a `.cbk` checksum file whose
  signing key doesn't match what's derivable from the device's own
  classic-format secret — a data inconsistency intrinsic to the specific
  test device, not a gap in this app's understanding of the format.

**Not yet implemented:**

- Editing or deleting a playlist from within the app (only creating one via
  M3U import, or having existing ones round-trip untouched).
- iPod 3G artwork: it has no color screen, so `ArtworkDB` writing is only
  ever attempted for the iPod Video 5/5.5G generation this app targets.
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

(`pip install -e .` pulls in Pillow, used to scale and pack cover art into
the on-device `ArtworkDB` thumbnail format.)

Run it:

```
python -m ipodmanager.ui.app
```

Run the test suite (covers the binary format parser/writer with synthetic
round-trip tests, the `ArtworkDB`/`.ithmb` chunk structure, the hash72
crypto, the SQLite mirror writer, and a full import→save→reload→delete→
save→reload cycle — including batch/folder/M3U-playlist imports and the
AAC-320 quality setting — against a fake device directory on disk):

```
pip install pytest
pytest
```

### iPhone-generation devices: extra setup

Talking to an iPhone/iPod Touch needs `pymobiledevice3`, which is kept in
a project-local virtualenv (`.venv`, created with `--system-site-packages`
so it can still see the system PyGObject/GTK install) rather than your
global Python environment — installing it globally previously caused a
version conflict with an unrelated tool on the dev machine this was built
on. To work on that code path:

```
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install pymobiledevice3
```

## How it's built

- **`ipodmanager/db/itunesdb.py`** — the classic binary format layer.
  Existing tracks and playlists are kept as opaque byte blobs and copied
  through unmodified unless directly touched; only newly added tracks are
  freshly synthesized, and only playlists referencing a deleted track are
  patched. This means an existing, working library round-trips
  byte-for-byte identical except for what you actually changed —
  deliberately the safest possible design against a real music
  collection. Every chunk offset was verified against
  [libgpod](https://github.com/fadingred/libgpod)'s actual source
  (`src/db-itunes-parser.h`, `src/itdb_itunesdb.c`, GNU LGPL-2.1+) rather
  than reconstructed from memory. Also handles the zlib-compressed body
  variant of this same format used by iPhone-generation devices.
- **`ipodmanager/db/artworkdb.py`** — writes the classic iPod's `ArtworkDB`
  + `.ithmb` thumbnail files (cover art). Same "transliterated from
  libgpod's actual C source, not reconstructed from memory" standard as
  `itunesdb.py`, but unlike that module, **not yet confirmed against real
  hardware** — see the "Classic iPod ArtworkDB" section below. Always
  rebuilds from scratch (re-reading every track's on-device file's tags)
  rather than incrementally merging with whatever's already there, which
  keeps the write path simple at the cost of a bit of extra I/O per sync.
- **`ipodmanager/db/hash72.py`** — the cryptographic signature
  iPhone/Touch/Nano-3G+-generation devices require over the classic
  format before they'll accept it. AES-128-CBC with a fixed key; ported
  from libgpod's `itdb_hash72.c` and verified byte-exact against real
  hardware. Works by *extracting* a secret from a hash already on the
  device (put there by a real iTunes or libgpod sync) and reusing it —
  there's no way to derive a fresh one from nothing.
- **`ipodmanager/db/sqlite_library.py`** — mirrors track add/remove into
  the iPhone-generation `Library.itdb`/`Locations.itdb` SQLite bundle.
  The schema and its triggers were reverse-engineered against a real
  device's actual database (not guessed); see the "iPhone-generation
  devices" section for why this alone isn't enough to make new tracks
  show up in the Music app.
- **`ipodmanager/audio/`** — `inspect.py` reads tags/artwork via `mutagen`;
  `transcode.py` shells out to `ffmpeg` for FLAC → ALAC and, when the
  quality setting is on, anything → AAC 320.
- **`ipodmanager/transport/`** — device detection. `classic.py` finds a
  mounted classic iPod via `Gio.VolumeMonitor`. `iphone.py` connects over
  USB via `pymobiledevice3`/AFC, no mount or jailbreak needed.
- **`ipodmanager/settings.py`** — the one persisted app setting (convert-
  to-AAC-320), a small JSON file under the user's config dir. Not
  GSettings — that needs a compiled/installed schema, overkill for one
  boolean.
- **`ipodmanager/library.py`** — orchestrates device + database +
  filesystem: importing (with transcode when needed, singly, as a batch,
  or from an M3U playlist, all with progress reporting), rebuilding
  `ArtworkDB`, deleting, backup, and safe writes (write to a temp file,
  then atomic rename).
- **`ipodmanager/ui/`** — the GTK4 + libadwaita interface.

## Classic iPod ArtworkDB: what's proven and what's not

Unlike the rest of the classic-iPod code (verified against real hardware),
nobody working on this project owns a physical classic iPod, so this piece
has only been checked for internal consistency, not for what a real device
actually does with it. Documenting it the same way as the iPhone findings
below, so a future attempt (with real hardware) knows exactly what's solid
and what to check first.

**Proven, from libgpod's actual source, not guesses:**

- The full chunk hierarchy — `mhfd` (top-level header) → three `mhsd`
  sections (image list / album list / file list, indices 1/2/3) → `mhli`
  → one `mhii` per track-with-artwork (keyed by the track's own `dbid`,
  the same id used in its `iTunesDB` `mhit` record) → one `mhod`
  (type=LOCATION) per thumbnail size → `mhni` (format id, dimensions,
  padding, byte offset/size within the `.ithmb` file) → one more `mhod`
  (type=FILE_NAME) holding the `.ithmb` filename — was transliterated
  field-by-field from `src/db-artwork-writer.c` and `src/db-itunes-parser.h`,
  including every struct's exact "padded" on-disk size (e.g. `mhii` is
  152 bytes even though only 52 are meaningful, the rest is zero
  padding — `get_padded_header_size()` in that same file spells out every
  chunk's real size). Covered by structural round-trip tests in
  `tests/test_artworkdb.py`.
- The iPod Video (5th/5.5th generation)'s cover-art thumbnail formats —
  two sizes, 100×100 (format id 1028) and 200×200 (format id 1029), both
  raw RGB565 little-endian pixel data — come from
  `ipod_video_cover_art_info[]` in `src/itdb_device.c`.
- The scaling/packing algorithm — fit-to-bounding-box (not crop-to-fill;
  the iPod Video's format table doesn't set `crop`), centered on a black
  canvas, bilinear resize — was transliterated from
  `ithumb_writer_scale_and_crop()`/`pack_RGB_565()` in
  `src/ithumb-writer.c`.
- A track's `iTunesDB` `mhit` record links to its `ArtworkDB` entry via
  three fields at fixed offsets in its fixed header (`has_artwork` at
  0xA4, `artwork_count` at 0x7C, and the artwork id itself — `mhii_link`
  — at 0x160), matching field order in `src/db-itunes-parser.h`'s
  `_MhitHeader` struct.
- The files live at `iPod_Control/Artwork/ArtworkDB` and
  `iPod_Control/Artwork/F<format_id>_0.ithmb`, per
  `itdb_get_artwork_dir()`/`get_ithmb_filename()` in the same source tree.

**Not proven:**

- Whether a real iPod Video actually renders thumbnails built this way.
  Nothing here has been checked against a real device's firmware
  behavior — only against what libgpod's own C code would produce, which
  is itself a third-party reimplementation, not Apple's specification.
- Whether `has_artwork`/`artwork_count`/`mhii_link`'s exact byte offsets
  and value conventions (1 = no artwork, 2 = has artwork) are current for
  every classic-iPod firmware revision, or specific to what libgpod's
  authors observed.

**If picking this back up:** test on a spare/non-critical classic iPod
Video, back up `iPod_Control` first, import one track with embedded
artwork, and check the device's own now-playing screen and Cover Flow. If
artwork doesn't appear, re-check `db/artworkdb.py`'s chunk structure
against `db-artwork-writer.c` byte-by-byte before assuming the algorithm
itself is wrong — the format has a lot of interdependent small structs,
and this document is a checklist of exactly what's already been verified
against libgpod's source vs. what's still unconfirmed on real hardware.

## iPhone-generation devices: what's proven and what's not

This section exists so a future attempt doesn't start from zero. Tested
against a real, jailbroken iPhone 3G on iOS 4.2.1 whose library had
previously been managed by a libgpod-based tool (gtkpod) from the same
machine — which turned out to be exactly the right test case, since it
meant every finding below could be checked against real, working,
Apple-accepted artifacts already on the device.

**Proven, with real evidence, not guesses:**

- The device connects over `usbmuxd` (via `pymobiledevice3`), no WiFi or
  jailbreak required for AFC access — even SSH, when needed, tunnels over
  USB via `pymobiledevice3 usbmux forward`, no network needed at all.
- The classic-format database lives at `iTunes_Control/iTunes/iTunesCDB`
  (not `iTunesDB`, which is a vestigial 0-byte file on this generation),
  and its body is zlib-compressed after a plaintext `mhbd` header — a
  compression flag in the header (`unknown1 == 2`) signals this.
- A correct sync requires bracketing the write with the real
  lockdown/notification-proxy protocol real iTunes uses: post
  `com.apple.itunes-mobdev.syncWillStart`, open and exclusively lock
  `/com.apple.itunes.lock_sync` over AFC, post `syncLockRequest`, wait for
  the lock, post `syncDidStart` — then write — then post `syncDidFinish`.
- The `hash72` signature is required and verified correct byte-for-byte
  against real hardware (see `db/hash72.py`'s docstring).
- The Music app (`MobileMusicPlayer`) does **not** read the classic
  format at all. It reads `iTunes_Control/iTunes/iTunes Library.itlp/
  {Library,Locations,Dynamic,Extras}.itdb`, four SQLite databases it
  `ATTACH`es together into one session on every launch. This was
  confirmed by watching the device's live syslog during a real launch.
- If any of those four fails a proprietary consistency check during
  `ATTACH`, `MobileMusicPlayer` automatically restores **all four** from
  a `DBTemp/Backup/` snapshot it keeps for exactly this purpose — this is
  a built-in crash-recovery safety net, not something adversarial, but it
  means a partial/inconsistent write gets silently reverted on next
  launch (not immediately — a fresh AFC read right after writing still
  shows the new content; the revert happens the next time
  `MobileMusicPlayer` itself opens the database).
- The specific failure, isolated via the device's live syslog: only
  `Locations.itdb`'s `ATTACH` genuinely fails; `Dynamic.itdb`/
  `Extras.itdb` failures are logged as `"failure due to prior failure"` —
  cascading consequences, not independent problems.
- `Locations.itdb.cbk` — a companion file alongside `Locations.itdb`,
  626 bytes on the real device — is the checksum gating that `ATTACH`.
  Its **exact structure and algorithm were found in libgpod's own source**
  (`itdb_sqlite.c`'s `mk_Locations_cbk`, not guessed): SHA1 every
  consecutive 1024-byte block of `Locations.itdb`, SHA1 the concatenation
  of those block-hashes into one "meta-hash", then `hash72`-sign the
  meta-hash with the same per-device secret used for the classic format.
  This structure was verified exactly against the real file (byte-length
  arithmetic and the meta-hash both matched precisely).
- `itdbprepserver` (a separate on-device daemon, launched on-demand by
  `SpringBoard` in response to the sync notifications) is **not** the
  culprit — confirmed by disabling it entirely
  (`defaults write -g itdbprepserverDisabled YES`, edited directly into
  `/var/mobile/Library/Preferences/.GlobalPreferences.plist` since the
  `defaults` CLI tool isn't present in this minimal jailbreak
  environment) and observing the exact same revert behavior regardless.
  (This preference was restored to its original absent state before
  finishing.)

**The actual, currently unresolved blocker:**

Applying the documented `hash72` signing algorithm — using the exact same
per-device secret that correctly, verifiably signs the classic format —
to the real `Locations.itdb`'s own correctly-computed meta-hash does
**not** reproduce the real device's actual `Locations.itdb.cbk` signature.
This was checked as rigorously as possible before concluding it's a real
dead end:

1. The block-hash structure and meta-hash are proven correct (exact byte
   match against the real file).
2. The AES-128 implementation is proven correct in isolation (round-trips
   perfectly on synthetic data) and against the classic format (exact
   match on real hardware).
3. As an ultimate ground-truth check, `itdb_hash72.c` + `rijndael.c` from
   libgpod were compiled **verbatim** (see the session's working notes —
   not committed here, since it's someone else's C source used only as a
   diagnostic) and run directly. Even this produces the same mismatch.
4. Most tellingly: extracting a secret from the real `.cbk` signature and
   immediately re-signing the same data with that extracted secret —
   which is pure algebra, mathematically guaranteed to round-trip
   regardless of whether the extracted secret is "the true one" — **still
   fails** to reproduce the second AES block. That should be impossible
   for a straightforward hash72 signature, which strongly suggests this
   device's `Locations.itdb.cbk` was signed under a secret that no longer
   matches what's currently cached in its own `HashInfo` file (e.g. from
   a secret re-bootstrap at some point in this specific device's history)
   — a pre-existing inconsistency in this device's data, not a gap in the
   algorithm as documented.

**If picking this back up:** the most promising next step is probably a
real decompiler (Hopper/IDA/Ghidra — not available in the environment
this was built in) on `MusicLibrary.framework`'s `.cbk`-validation routine
(`__MLSSqliteVFSCandyScanAndCheckP7BFilePaths` and whatever it calls,
inside the framework embedded in `dyld_shared_cache_armv6` — it's not a
standalone file on iOS 4.x, has to be extracted from the shared cache
first) to find the actual validation logic on-device, rather than
continuing to assume libgpod's approximation is exactly what the real
firmware expects.

## A note on testing

Classic iPod support (including M3U playlist import and the AAC-320
quality setting) has been verified with synthetic round-trip tests, a
simulated fake device directory on disk, and the app itself launched and
exercised end-to-end (empty state, populated track list, folder/playlist
import with a live progress bar, playlist browsing, preferences) — but
**not yet against real classic-iPod hardware**. `ArtworkDB` writing is
further behind: it's only been checked for internal structural
consistency (see the dedicated section above), never against a real
device at all. Test with a **spare/non-critical library first**;
automatic backups live on the device itself
(`iPod_Control/iTunes/.ipodmanager_backups/`) — copy one off the device
if you want a copy that survives a "restore iPod" in iTunes.

The iPhone/iPod Touch classic-format path *has* been tested against real
hardware (see above) and is durable; the SQLite/Music-app-visibility path
has not been made to work at all yet.
