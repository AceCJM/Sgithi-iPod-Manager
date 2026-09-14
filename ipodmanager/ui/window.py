from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..library import Library, LibraryError
from ..transport import classic
from ..transport.base import Device
from .track_object import TrackObject

SUPPORTED_EXTENSIONS = [".flac", ".m4a", ".mp4", ".m4b"]


class IPodWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("iPod Manager")
        self.set_default_size(900, 600)

        self.device: Device | None = None
        self.library: Library | None = None
        self.store = Gio.ListStore(item_type=TrackObject)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(root)

        header = Adw.HeaderBar()
        root.append(header)

        self.add_button = Gtk.Button(label="Add Music")
        self.add_button.add_css_class("suggested-action")
        self.add_button.connect("clicked", self.on_add_clicked)
        self.add_button.set_sensitive(False)
        header.pack_start(self.add_button)

        self.delete_button = Gtk.Button(label="Delete")
        self.delete_button.add_css_class("destructive-action")
        self.delete_button.connect("clicked", self.on_delete_clicked)
        self.delete_button.set_sensitive(False)
        header.pack_start(self.delete_button)

        refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_button.set_tooltip_text("Look for a connected iPod")
        refresh_button.connect("clicked", lambda *_: self.refresh_device())
        header.pack_end(refresh_button)

        self.status_label = Gtk.Label(label="No iPod detected")
        self.status_label.add_css_class("dim-label")
        header.pack_end(self.status_label)

        self.stack = Gtk.Stack()
        root.append(self.stack)
        self.stack.set_vexpand(True)

        self.empty_page = Adw.StatusPage(
            icon_name="drive-removable-media-symbolic",
            title="No iPod Connected",
            description="Connect a classic click-wheel iPod (with iPod_Control on it) and click refresh.",
        )
        self.stack.add_named(self.empty_page, "empty")

        self.progress_page = Adw.StatusPage(icon_name="emblem-synchronizing-symbolic", title="Working…")
        self.stack.add_named(self.progress_page, "progress")

        self.selection_model = Gtk.MultiSelection(model=self.store)
        self.selection_model.connect("selection-changed", self.on_selection_changed)
        self.column_view = Gtk.ColumnView(model=self.selection_model)
        self.column_view.set_show_row_separators(True)

        self._add_column("Title", "title", expand=True)
        self._add_column("Artist", "artist", expand=True)
        self._add_column("Album", "album", expand=True)
        self._add_column("Genre", "genre")
        self._add_column("Time", "duration")
        self._add_column("Bitrate", "bitrate")
        self._add_column("Size", "size")

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.column_view)
        self.stack.add_named(scroller, "tracks")

        self.stack.set_visible_child_name("empty")

        self.refresh_device()

    def _add_column(self, title: str, prop: str, expand: bool = False) -> None:
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, list_item):
            label = Gtk.Label(xalign=0)
            label.set_ellipsize(3)  # Pango.EllipsizeMode.END
            list_item.set_child(label)

        def bind(_factory, list_item):
            label = list_item.get_child()
            obj: TrackObject = list_item.get_item()
            label.set_text(getattr(obj, prop))

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        column = Gtk.ColumnViewColumn(title=title, factory=factory)
        column.set_expand(expand)
        column.set_resizable(True)
        self.column_view.append_column(column)

    def toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))

    # -- device lifecycle --------------------------------------------------

    def refresh_device(self) -> None:
        devices = classic.find_classic_ipods()
        if not devices:
            self.device = None
            self.library = None
            self.store.remove_all()
            self.status_label.set_text("No iPod detected")
            self.add_button.set_sensitive(False)
            self.stack.set_visible_child_name("empty")
            return

        self.device = devices[0]
        self.status_label.set_text(self.device.display_name)
        try:
            self.library = Library(self.device)
            self.library.load()
        except Exception as e:  # noqa: BLE001 - surface any parse failure to the user
            self.library = None
            self._show_error(f"Couldn't read this iPod's music library:\n{e}")
            self.stack.set_visible_child_name("empty")
            return

        self.add_button.set_sensitive(True)
        self._reload_track_list()
        self.stack.set_visible_child_name("tracks")

    def _reload_track_list(self) -> None:
        self.store.remove_all()
        assert self.library is not None
        for row in self.library.list_tracks():
            self.store.append(TrackObject(row))

    def on_selection_changed(self, *_args) -> None:
        bitset = self.selection_model.get_selection()
        self.delete_button.set_sensitive(bitset.get_size() > 0)

    def _show_error(self, message: str) -> None:
        dialog = Adw.AlertDialog(heading="Error", body=message)
        dialog.add_response("ok", "OK")
        dialog.present(self)

    # -- add music -----------------------------------------------------

    def on_add_clicked(self, _button) -> None:
        dialog = Gtk.FileDialog()
        filt = Gtk.FileFilter()
        filt.set_name("Audio files (FLAC, ALAC/AAC)")
        for ext in SUPPORTED_EXTENSIONS:
            filt.add_pattern(f"*{ext}")
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.open_multiple(self, None, self._on_files_chosen)

    def _on_files_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return
        paths = [Path(f.get_path()) for f in files if f.get_path()]
        if paths:
            self._import_files(paths)

    def _import_files(self, paths: list[Path]) -> None:
        assert self.library is not None
        library = self.library  # snapshot: don't chase self.library if it changes mid-import
        self.stack.set_visible_child_name("progress")
        self.add_button.set_sensitive(False)

        def set_progress(text: str) -> None:
            GLib.idle_add(self.progress_page.set_description, text)

        def worker() -> None:
            errors = []
            imported = 0
            for path in paths:
                try:
                    library.import_file(path, on_progress=set_progress)
                    imported += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{path.name}: {e}")
            if imported:
                try:
                    library.save()
                except Exception as e:  # noqa: BLE001
                    errors.append(f"Saving the iPod database failed: {e}")
            GLib.idle_add(self._import_finished, imported, errors)

        threading.Thread(target=worker, daemon=True).start()

    def _import_finished(self, imported: int, errors: list[str]) -> bool:
        self.add_button.set_sensitive(True)
        self._reload_track_list()
        self.stack.set_visible_child_name("tracks")
        if imported:
            self.toast(f"Added {imported} track{'s' if imported != 1 else ''}")
        if errors:
            self._show_error("Some files couldn't be imported:\n\n" + "\n".join(errors))
        return False

    # -- delete ----------------------------------------------------------

    def on_delete_clicked(self, _button) -> None:
        bitset = self.selection_model.get_selection()
        track_ids = []
        titles = []
        ok, it, pos = Gtk.BitsetIter.init_first(bitset)
        while ok:
            obj: TrackObject = self.store.get_item(pos)
            track_ids.append(obj.track_id)
            titles.append(obj.title)
            ok, pos = it.next()
        if not track_ids:
            return

        dialog = Adw.AlertDialog(
            heading=f"Delete {len(track_ids)} track{'s' if len(track_ids) != 1 else ''}?",
            body="This removes the file(s) from the iPod and can't be undone.\n\n" + "\n".join(titles[:10]),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_response, track_ids)
        dialog.present(self)

    def _on_delete_response(self, _dialog, response: str, track_ids: list[int]) -> None:
        if response != "delete":
            return
        assert self.library is not None
        try:
            for tid in track_ids:
                self.library.delete_track(tid)
            self.library.save()
        except Exception as e:  # noqa: BLE001
            self._show_error(f"Couldn't delete: {e}")
        self._reload_track_list()
        self.toast(f"Deleted {len(track_ids)} track{'s' if len(track_ids) != 1 else ''}")
