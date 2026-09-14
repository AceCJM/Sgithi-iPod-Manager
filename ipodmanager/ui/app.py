from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from .window import IPodWindow

APP_ID = "ca.millerfamily.ipodmanager"


class IPodManagerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self) -> None:
        win = self.props.active_window
        if not win:
            win = IPodWindow(application=self)
        win.present()


def main() -> int:
    app = IPodManagerApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
