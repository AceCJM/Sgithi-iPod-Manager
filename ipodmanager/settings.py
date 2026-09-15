"""Small persisted app-settings file (JSON) under the user's config dir.

Deliberately not GSettings -- that needs a compiled/installed schema, which
is overkill for the one boolean this app currently needs.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ipodmanager"


def _config_path() -> Path:
    return _config_dir() / "settings.json"


@dataclass
class Settings:
    # When true, any imported file whose quality exceeds AAC 320kbps
    # (lossless FLAC/ALAC, or lossy sources above 320kbps) is transcoded
    # down to AAC 320kbps instead of preserved losslessly, to save space.
    # Files already at or below that quality are left as-is either way.
    convert_to_aac_320: bool = False

    @classmethod
    def load(cls) -> "Settings":
        path = _config_path()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)

    def save(self) -> None:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
