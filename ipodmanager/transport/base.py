"""Common interface for a mounted device this app can manage.

v1 only implements the classic click-wheel iPod transport (plain USB mass
storage, mounted like any other drive). An iPhone/iPod Touch-generation
transport (usbmuxd/AFC via ifuse, plus hash72 signing and an SQLite shadow
database) is a planned second phase -- see the README for what that needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Device:
    kind: str  # "classic_ipod" (more kinds land here in phase 2)
    display_name: str
    mount_root: Path
    itunesdb_path: Path
    music_dir: Path
    volume_serial: str = ""

    def backups_dir(self) -> Path:
        d = self.mount_root / "iPod_Control" / "iTunes" / ".ipodmanager_backups"
        d.mkdir(parents=True, exist_ok=True)
        return d
