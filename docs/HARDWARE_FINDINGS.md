# Hardware findings: what's proven and what's not

This document is the detailed, real-hardware-tested record behind two parts
of iPod Manager that the main [README](../README.md) only summarizes:
classic-iPod cover art (`ArtworkDB`), and iPhone/iPod Touch-generation
device support. It exists so a future attempt at either doesn't start from
zero — each section lists exactly what's been verified against real
hardware or real source, and what's still an open question.

## Classic iPod ArtworkDB: what's proven and what's not

Unlike the rest of the classic-iPod code (verified against real hardware),
nobody working on this project owns a physical classic iPod, so this piece
has only been checked for internal consistency, not for what a real device
actually does with it.

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

Tested against a real, jailbroken iPhone 3G on iOS 4.2.1 whose library had
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
