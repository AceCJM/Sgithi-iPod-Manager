from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..library import Library, discover_audio_files
from ..transport import classic, iphone
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
        self._mounted_iphone: iphone.MountedIPhone | None = None
        self.store = Gio.ListStore(item_type=TrackObject)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(root)

        header = Adw.HeaderBar()
        root.append(header)

        self.add_button = Gtk.MenuButton(label="Add Music")
        self.add_button.add_css_class("suggested-action")
        self.add_button.set_sensitive(False)
        add_popover = Gtk.Popover()
        add_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        add_files_row = Gtk.Button(label="Add Files…", has_frame=False)
        add_files_row.connect("clicked", self._on_add_files_row_clicked, add_popover)
        add_folder_row = Gtk.Button(label="Add Folder…", has_frame=False)
        add_folder_row.connect("clicked", self._on_add_folder_row_clicked, add_popover)
        add_box.append(add_files_row)
        add_box.append(add_folder_row)
        add_popover.set_child(add_box)
        self.add_button.set_popover(add_popover)
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

        self.progress_page = self._build_progress_page()
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

        self.connect("close-request", self._on_close_request)
        self.refresh_device()

    def _on_close_request(self, *_args) -> bool:
        self._unmount_iphone()
        return False  # allow the close to proceed

    def _build_progress_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_size_request(420, -1)

        self.progress_title = Gtk.Label(label="Importing…")
        self.progress_title.add_css_class("title-2")
        box.append(self.progress_title)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_hexpand(True)
        box.append(self.progress_bar)

        self.progress_detail = Gtk.Label(label="")
        self.progress_detail.add_css_class("dim-label")
        self.progress_detail.set_ellipsize(3)  # Pango.EllipsizeMode.END
        box.append(self.progress_detail)

        return box

    def _set_progress(self, completed: int, total: int, detail: str) -> None:
        fraction = (completed / total) if total else 0.0
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{round(fraction * 100)}%  ({completed} of {total})")
        self.progress_detail.set_text(detail)

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
        self._unmount_iphone()

        devices = classic.find_classic_ipods()
        if not devices:
            try:
                udids = iphone.list_udids()
            except iphone.IPhoneError:
                udids = []
            if udids:
                try:
                    device, mounted = iphone.open_device(udids[0])
                    self._mounted_iphone = mounted
                    devices = [device]
                except iphone.IPhoneError as e:
                    self._show_error(f"Found an iPhone but couldn't mount it:\n{e}")

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

    def _unmount_iphone(self) -> None:
        if self._mounted_iphone is not None:
            self._mounted_iphone.unmount()
            self._mounted_iphone = None

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

    def _on_add_files_row_clicked(self, _button, popover: Gtk.Popover) -> None:
        popover.popdown()
        dialog = Gtk.FileDialog()
        filt = Gtk.FileFilter()
        filt.set_name("Audio files (FLAC, ALAC/AAC)")
        for ext in SUPPORTED_EXTENSIONS:
            filt.add_pattern(f"*{ext}")
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.open_multiple(self, None, self._on_files_chosen)

    def _on_add_folder_row_clicked(self, _button, popover: Gtk.Popover) -> None:
        popover.popdown()
        dialog = Gtk.FileDialog()
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_files_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return
        paths = [Path(f.get_path()) for f in files if f.get_path()]
        if paths:
            self._import_paths(paths)

    def _on_folder_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = folder.get_path() if folder else None
        if not path:
            return
        paths = discover_audio_files(Path(path))
        if not paths:
            self._show_error(f"No supported audio files (FLAC/M4A/MP4/M4B) found under:\n{path}")
            return
        self._import_paths(paths)

    def _import_paths(self, paths: list[Path]) -> None:
        assert self.library is not None
        library = self.library  # snapshot: don't chase self.library if it changes mid-import
        self.stack.set_visible_child_name("progress")
        self.add_button.set_sensitive(False)
        self.progress_title.set_text(f"Importing {len(paths)} file{'s' if len(paths) != 1 else ''}…")
        self._set_progress(0, len(paths), "")

        def on_progress(completed: int, total: int, detail: str) -> None:
            GLib.idle_add(self._set_progress, completed, total, detail)

        def worker() -> None:
            try:
                result = library.import_paths(paths, on_progress=on_progress)
            except Exception as e:  # noqa: BLE001 - e.g. save() failing after all files imported
                GLib.idle_add(self._import_finished, 0, [f"Saving the iPod database failed: {e}"])
                return
            errors = [f"{p.name}: {msg}" for p, msg in result.errors]
            GLib.idle_add(self._import_finished, len(result.imported), errors)

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
