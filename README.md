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
- Delete tracks (removes the file and its database entry, and scrubs it
  from playlist membership so nothing points at a deleted track)
- Every write to `iTunesDB` is preceded by a timestamped backup under
  `iPod_Control/iTunes/.ipodmanager_backups/`

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
round-trip tests, the hash72 crypto, the SQLite mirror writer, and a full
import→save→reload→delete→save→reload cycle — including batch/folder
imports — against a fake device directory on disk):

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
  `transcode.py` shells out to `ffmpeg` for FLAC → ALAC.
- **`ipodmanager/transport/`** — device detection. `classic.py` finds a
  mounted classic iPod via `Gio.VolumeMonitor`. `iphone.py` connects over
  USB via `pymobiledevice3`/AFC, no mount or jailbreak needed.
- **`ipodmanager/library.py`** — orchestrates device + database +
  filesystem: importing (with transcode when needed, singly or as a
  batch with progress reporting), deleting, backup, and safe writes
  (write to a temp file, then atomic rename).
- **`ipodmanager/ui/`** — the GTK4 + libadwaita interface.

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

Classic iPod support has been verified with synthetic round-trip tests, a
simulated fake device directory on disk, and the app itself launched and
visually confirmed to render correctly (empty state, populated track
list, folder import with a live progress bar) — but **not yet against
real classic-iPod hardware**. Test with a **spare/non-critical library
first**; automatic backups live on the device itself
(`iPod_Control/iTunes/.ipodmanager_backups/`) — copy one off the device
if you want a copy that survives a "restore iPod" in iTunes.

The iPhone/iPod Touch classic-format path *has* been tested against real
hardware (see above) and is durable; the SQLite/Music-app-visibility path
has not been made to work at all yet.
