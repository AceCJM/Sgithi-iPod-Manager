"""Detect mounted classic click-wheel iPods (plain USB mass storage)."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

from .base import Device


def find_classic_ipods() -> list[Device]:
    devices = []
    monitor = Gio.VolumeMonitor.get()
    for mount in monitor.get_mounts():
        root = mount.get_root()
        if root is None:
            continue
        path = root.get_path()
        if not path:
            continue
        p = Path(path)
        itunesdb = p / "iPod_Control" / "iTunes" / "iTunesDB"
        if itunesdb.is_file():
            devices.append(
                Device(
                    kind="classic_ipod",
                    display_name=mount.get_name() or p.name,
                    mount_root=p,
                    itunesdb_path=itunesdb,
                    music_dir=p / "iPod_Control" / "Music",
                )
            )
    return devices
