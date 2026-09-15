"""Read-only diagnostic dump of an iTunesDB/iTunesCDB file's top-level
structure -- used to debug cases like "playlists don't show up" without
having to guess blindly at the binary format. Never writes anything.

Usage:
    python -m ipodmanager.diagnose                        # auto-detect and
                                                            # mount a connected
                                                            # iPhone/iPod Touch
    python -m ipodmanager.diagnose --udid <udid>           # a specific one
    python -m ipodmanager.diagnose /path/to/iTunesCDB      # an already-
                                                            # mounted/copied file
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

from .db import itunesdb as idb


def dump(path: Path) -> None:
    buf = path.read_bytes()
    print(f"file: {path}  ({len(buf)} bytes)")

    magic, header_len, total_len, unknown1, version, num_children = idb.MHBD_STRUCT.unpack_from(buf, 0)
    print(f"mhbd: magic={magic!r} header_len={header_len} total_len={total_len} "
          f"unknown1={unknown1} version={version} num_children(header)={num_children}")
    if magic != b"mhbd":
        print("  -> not a valid iTunesDB/iTunesCDB (bad magic); stopping.")
        return
    if total_len != len(buf):
        print(f"  ! header total_len ({total_len}) != actual file size ({len(buf)})")

    raw_body = buf[header_len:]
    if unknown1 == 2:
        try:
            body = zlib.decompress(raw_body)
            print(f"  body is zlib-compressed: {len(raw_body)} bytes -> {len(body)} bytes decompressed")
        except zlib.error as e:
            print(f"  ! zlib decompression FAILED: {e}")
            return
    else:
        body = raw_body
        print(f"  body is plain (not compressed): {len(body)} bytes")

    # Walk every mhsd chunk actually present in the body, ignoring what the
    # mhbd header claims num_children is -- so a header/reality mismatch
    # shows up as a finding instead of silently truncating the scan.
    pos = 0
    found = 0
    while pos < len(body):
        if body[pos : pos + 4] != b"mhsd":
            print(f"  ! expected mhsd at body offset {pos}, found {body[pos:pos+4]!r} -- stopping scan")
            break
        mhsd_header_len, mhsd_total_len, index = struct.unpack_from("<iii", body, pos + 4)
        found += 1
        print(f"  mhsd #{found} @ {pos}: header_len={mhsd_header_len} total_len={mhsd_total_len} index={index}")

        body_start = pos + mhsd_header_len

        # Identify the section by the magic of its inner container (mhlt =
        # track list, mhlp = playlist list) rather than trusting `index` --
        # this app's parser currently trusts index (1 = tracks, 2 =
        # playlists), which is what libgpod's classic-iPod source documents,
        # but a newer/different firmware's index numbering isn't guaranteed
        # to match that, and the magic bytes are a ground-truth signal either
        # way.
        inner_magic = body[body_start : body_start + 4]
        if index != 1 and inner_magic == b"mhlt":
            print(f"    ! index={index} but this section's inner container is 'mhlt' (track list) -- "
                  f"index alone would have been misleading here")
        if index != 2 and inner_magic == b"mhlp":
            print(f"    ! index={index} but this section's inner container is 'mhlp' (PLAYLIST LIST) -- "
                  f"this is very likely where your playlists actually are")

        if inner_magic == b"mhlt":
            _magic, _mhlt_header_len, num_songs = struct.unpack_from("<4sii", body, body_start)
            print(f"    -> track list (by magic): num_songs={num_songs}")
        elif inner_magic == b"mhlp":
            _magic, mhlp_header_len, num_pl = struct.unpack_from("<4sii", body, body_start)
            print(f"    -> playlist list (by magic): num_pl={num_pl}")
            ppos = body_start + mhlp_header_len
            for i in range(num_pl):
                pl = idb.parse_mhyp(body, ppos)
                plen = struct.unpack_from("<I", body, ppos + 8)[0]
                title = pl.title()
                print(f"       [{i}] title={title!r} is_master={pl.is_master} tracks={len(pl.mhip_track_ids)}")
                ppos += plen
        else:
            counts = {
                m.decode(): body.count(m, pos, pos + mhsd_total_len)
                for m in (b"mhlt", b"mhlp", b"mhyp", b"mhip", b"mhod")
            }
            counts = {k: v for k, v in counts.items() if v}
            print(f"    -> unrecognized section (index={index}), {mhsd_total_len} bytes, preserved raw"
                  + (f"; magic bytes found inside: {counts}" if counts else ""))

        pos += mhsd_total_len

    print(f"\nTotal mhsd sections found by walking the body: {found} "
          f"(mhbd header claimed num_children={num_children})")
    if found != num_children:
        print("  ! MISMATCH -- this is very likely the cause of missing data: the app's parser "
              "trusts the header's num_children and stops after that many sections, so any "
              "sections beyond that point (playlists included) are silently dropped.")


def main() -> None:
    args = sys.argv[1:]

    if len(args) == 1 and args[0] not in ("-h", "--help") and Path(args[0]).exists():
        dump(Path(args[0]))
        return

    if not args:
        udid = None
    elif len(args) == 2 and args[0] == "--udid":
        udid = args[1]
    else:
        print(__doc__)
        sys.exit(0 if args and args[0] in ("-h", "--help") else 1)
        return

    from .transport import iphone

    if udid is None:
        try:
            udids = iphone.list_udids()
        except iphone.IPhoneError as e:
            print(f"Couldn't list connected devices: {e}")
            sys.exit(1)
        if not udids:
            print("No connected iPhone/iPod Touch found, and no file path was given.")
            print(__doc__)
            sys.exit(1)
        udid = udids[0]

    print(f"Mounting {udid}...")
    device, mounted = iphone.open_device(udid)
    try:
        dump(device.itunesdb_path)
    finally:
        mounted.unmount()


if __name__ == "__main__":
    main()
