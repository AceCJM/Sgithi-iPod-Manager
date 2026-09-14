"""GObject wrapper so a TrackRow can live in a Gio.ListStore."""

import gi

gi.require_version("GObject", "2.0")
from gi.repository import GObject  # noqa: E402

from ..library import TrackRow


def _fmt_duration(ms: int) -> str:
    if not ms:
        return ""
    total_seconds = ms // 1000
    m, s = divmod(total_seconds, 60)
    return f"{m}:{s:02d}"


def _fmt_size(n: int) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class TrackObject(GObject.Object):
    def __init__(self, row: TrackRow):
        super().__init__()
        self.row = row

    @property
    def title(self) -> str:
        return self.row.title or "(untitled)"

    @property
    def artist(self) -> str:
        return self.row.artist

    @property
    def album(self) -> str:
        return self.row.album

    @property
    def genre(self) -> str:
        return self.row.genre

    @property
    def duration(self) -> str:
        return _fmt_duration(self.row.length_ms)

    @property
    def bitrate(self) -> str:
        return f"{self.row.bitrate} kbps" if self.row.bitrate else ""

    @property
    def size(self) -> str:
        return _fmt_size(self.row.size)

    @property
    def track_id(self) -> int:
        return self.row.track_id
