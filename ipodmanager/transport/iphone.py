"""iPhone/iPod Touch-generation transport, via libimobiledevice.

Unlike a classic iPod, these devices don't mount as a USB mass-storage
drive. They speak usbmuxd (a multiplexed protocol over USB) with a
lockdown handshake and an AFC (Apple File Conduit) file service on top.
`ifuse` (from libimobiledevice) mounts that AFC service as a normal
FUSE filesystem, which is the simplest way to reuse the rest of this
app's filesystem-based approach unmodified.

This module is read-only reconnaissance for now (phase 2 is still being
scoped against real hardware) -- see README for what full write support
still needs (hash72 signing, and confirming whether a SQLite shadow
database is actually required on this device's iOS version).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import Device


class IPhoneError(RuntimeError):
    pass


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise IPhoneError(f"required tool '{name}' not found (install libimobiledevice-utils / ifuse)")


def list_udids() -> list[str]:
    _require_tool("idevice_id")
    proc = subprocess.run(["idevice_id", "-l"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise IPhoneError(f"idevice_id failed: {proc.stderr.strip()}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def get_info(udid: str) -> dict[str, str]:
    _require_tool("ideviceinfo")
    proc = subprocess.run(["ideviceinfo", "-u", udid], capture_output=True, text=True)
    if proc.returncode != 0:
        raise IPhoneError(f"ideviceinfo failed for {udid}: {proc.stderr.strip()}")
    info = {}
    for line in proc.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            info[key] = value
    return info


@dataclass
class MountedIPhone:
    udid: str
    mount_point: Path
    _proc: Optional[subprocess.Popen] = None

    def unmount(self) -> None:
        subprocess.run(["fusermount", "-u", str(self.mount_point)], capture_output=True)
        try:
            self.mount_point.rmdir()
        except OSError:
            pass


def mount(udid: str) -> MountedIPhone:
    """Mount the device's AFC (Media partition) jail via ifuse.

    This is the same directory tree real iTunes/Finder syncs into --
    iTunes_Control/iTunes/iTunesDB, iTunes_Control/Music/F00/..., DCIM,
    etc. -- accessible without a jailbreak, since this AFC service has
    always been available for iTunes' own photo/media sync.
    """
    _require_tool("ifuse")
    mount_point = Path(tempfile.mkdtemp(prefix="ipodmanager-iphone-"))
    proc = subprocess.run(["ifuse", "-u", udid, str(mount_point)], capture_output=True, text=True)
    if proc.returncode != 0:
        mount_point.rmdir()
        raise IPhoneError(f"ifuse failed to mount {udid}: {proc.stderr.strip()}")
    return MountedIPhone(udid=udid, mount_point=mount_point)


def open_device(udid: str) -> tuple[Device, MountedIPhone]:
    """Mount the device and build a Device pointing at its music database.

    Note the database file is "iTunesCDB", not "iTunesDB" -- confirmed
    against a real iPhone 3G (iOS 4.2.1): the plain iTunesDB there is a
    vestigial 0-byte file, and iTunesCDB (same mhbd/mhsd/mhit chunk format,
    optionally zlib-compressed after the header -- see db/itunesdb.py) is
    what's actually live.
    """
    info = get_info(udid)
    mounted = mount(udid)
    control = mounted.mount_point / "iTunes_Control"
    device = Device(
        kind="iphone",
        display_name=info.get("DeviceName") or info.get("ProductType") or "iPhone",
        mount_root=mounted.mount_point,
        itunesdb_path=control / "iTunes" / "iTunesCDB",
        music_dir=control / "Music",
        hash_info_path=control / "Device" / "HashInfo",
        udid_bytes=bytes.fromhex(udid),
    )
    return device, mounted
