"""Common interface for a mounted device this app can manage.

v1 only implements the classic click-wheel iPod transport (plain USB mass
storage, mounted like any other drive). An iPhone/iPod Touch-generation
transport (usbmuxd/AFC via ifuse, plus hash72 signing and an SQLite shadow
database) is a planned second phase -- see the README for what that needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Device:
    kind: str  # "classic_ipod" or "iphone"
    display_name: str
    mount_root: Path
    itunesdb_path: Path  # "iTunesDB" on classic iPods, "iTunesCDB" on iPhone-generation devices
    music_dir: Path
    volume_serial: str = ""
    # iphone-only: where to cache the hash72 secret, and this device's UDID
    # (as bytes, for HashInfo's uuid field)
    hash_info_path: Optional[Path] = None
    udid_bytes: Optional[bytes] = None

    def _control_dir_name(self) -> str:
        return "iTunes_Control" if self.kind == "iphone" else "iPod_Control"

    def backups_dir(self) -> Path:
        d = self.mount_root / self._control_dir_name() / "iTunes" / ".ipodmanager_backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def artwork_dir(self) -> Path:
        """iPod_Control/Artwork -- where ArtworkDB and its .ithmb files
        live. Classic-iPod-only; not used for iphone-kind devices."""
        return self.mount_root / self._control_dir_name() / "Artwork"
