from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..library import Library, PlaylistRow, discover_audio_files
from ..settings import Settings
from ..transport import classic, iphone
from ..transport.base import Device
from .track_object import TrackObject

SUPPORTED_EXTENSIONS = [".flac", ".m4a", ".mp4", ".m4b"]
M3U_EXTENSIONS = [".m3u", ".m3u8"]


class IPodWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("iPod Manager")
        self.set_default_size(900, 600)

        self.device: Device | None = None
        self.library: Library | None = None
        self._mounted_iphone: iphone.MountedIPhone | None = None
        self.settings = Settings.load()
        self._active_playlist: PlaylistRow | None = None
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
        add_playlist_row = Gtk.Button(label="Import Playlist (M3U)…", has_frame=False)
        add_playlist_row.connect("clicked", self._on_add_playlist_row_clicked, add_popover)
        add_box.append(add_files_row)
        add_box.append(add_folder_row)
        add_box.append(add_playlist_row)
        add_popover.set_child(add_box)
        self.add_button.set_popover(add_popover)
        header.pack_start(self.add_button)

        self.delete_button = Gtk.Button(label="Delete")
        self.delete_button.add_css_class("destructive-action")
        self.delete_button.connect("clicked", self.on_delete_clicked)
        self.delete_button.set_sensitive(False)
        header.pack_start(self.delete_button)

        self.playlists_toggle = Gtk.ToggleButton(label="Playlists")
        self.playlists_toggle.set_sensitive(False)
        self.playlists_toggle.connect("toggled", self._on_playlists_toggled)
        header.pack_start(self.playlists_toggle)

        refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_button.set_tooltip_text("Look for a connected iPod")
        refresh_button.connect("clicked", lambda *_: self.refresh_device())
        header.pack_end(refresh_button)

        prefs_button = Gtk.Button(icon_name="preferences-system-symbolic")
        prefs_button.set_tooltip_text("Preferences")
        prefs_button.connect("clicked", lambda *_: self._show_preferences())
        header.pack_end(prefs_button)

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
        scroller.set_vexpand(True)

        self.playlist_filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.playlist_filter_bar.set_margin_top(6)
        self.playlist_filter_bar.set_margin_bottom(6)
        self.playlist_filter_bar.set_margin_start(12)
        self.playlist_filter_bar.set_margin_end(12)
        self.playlist_filter_label = Gtk.Label(xalign=0)
        self.playlist_filter_label.add_css_class("title-4")
        self.playlist_filter_label.set_hexpand(True)
        show_all_button = Gtk.Button(label="Show All Tracks")
        show_all_button.connect("clicked", self._on_show_all_tracks_clicked)
        self.playlist_filter_bar.append(self.playlist_filter_label)
        self.playlist_filter_bar.append(show_all_button)
        self.playlist_filter_bar.set_visible(False)

        tracks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tracks_box.append(self.playlist_filter_bar)
        tracks_box.append(scroller)
        self.stack.add_named(tracks_box, "tracks")

        self.playlists_listbox = Gtk.ListBox()
        self.playlists_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.playlists_listbox.add_css_class("boxed-list")
        self.playlists_listbox.set_margin_top(12)
        self.playlists_listbox.set_margin_bottom(12)
        self.playlists_listbox.set_margin_start(12)
        self.playlists_listbox.set_margin_end(12)
        self.playlists_listbox.connect("row-activated", self._on_playlist_row_activated)
        playlists_scroller = Gtk.ScrolledWindow()
        playlists_scroller.set_child(self.playlists_listbox)
        self.stack.add_named(playlists_scroller, "playlists")

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
            self._active_playlist = None
            self.store.remove_all()
            self.status_label.set_text("No iPod detected")
            self.add_button.set_sensitive(False)
            self.playlists_toggle.set_sensitive(False)
            self.stack.set_visible_child_name("empty")
            return

        self.device = devices[0]
        self.status_label.set_text(self.device.display_name)
        try:
            self.library = Library(self.device, settings=self.settings)
            self.library.load()
        except Exception as e:  # noqa: BLE001 - surface any parse failure to the user
            self.library = None
            self._show_error(f"Couldn't read this iPod's music library:\n{e}")
            self.stack.set_visible_child_name("empty")
            return

        self.add_button.set_sensitive(True)
        self.playlists_toggle.set_sensitive(True)
        self._active_playlist = None
        self._reload_track_list()
        self.stack.set_visible_child_name("tracks")

    def _unmount_iphone(self) -> None:
        if self._mounted_iphone is not None:
            self._mounted_iphone.unmount()
            self._mounted_iphone = None

    def _reload_track_list(self) -> None:
        self.store.remove_all()
        assert self.library is not None
        if self._active_playlist is not None:
            # re-fetch in case membership changed since it was selected
            self._active_playlist = next(
                (p for p in self.library.list_playlists() if p.name == self._active_playlist.name), None
            )
        track_ids = self._active_playlist.track_ids if self._active_playlist else None
        for row in self.library.list_tracks(track_ids=track_ids):
            self.store.append(TrackObject(row))
        if self._active_playlist is not None:
            self.playlist_filter_label.set_text(f"Playlist: {self._active_playlist.name}")
            self.playlist_filter_bar.set_visible(True)
        else:
            self.playlist_filter_bar.set_visible(False)

    def _reload_playlists_list(self) -> None:
        row = self.playlists_listbox.get_row_at_index(0)
        while row is not None:
            self.playlists_listbox.remove(row)
            row = self.playlists_listbox.get_row_at_index(0)
        assert self.library is not None
        for pl in self.library.list_playlists():
            self.playlists_listbox.append(self._build_playlist_row(pl))

    def _build_playlist_row(self, pl: PlaylistRow) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.playlist = pl
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(self._make_playlist_thumbnail(pl))
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.set_valign(Gtk.Align.CENTER)
        title_label = Gtk.Label(label=pl.name, xalign=0)
        count_label = Gtk.Label(label=f"{pl.track_count} track{'s' if pl.track_count != 1 else ''}", xalign=0)
        count_label.add_css_class("dim-label")
        labels.append(title_label)
        labels.append(count_label)
        box.append(labels)
        row.set_child(box)
        return row

    def _make_playlist_thumbnail(self, pl: PlaylistRow) -> Gtk.Widget:
        size = 48
        if pl.artwork:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(pl.artwork))
                picture = Gtk.Picture.new_for_paintable(texture)
                picture.set_content_fit(Gtk.ContentFit.COVER)
                picture.set_size_request(size, size)
                return picture
            except GLib.Error:
                pass
        icon = Gtk.Image.new_from_icon_name("emblem-music-symbolic")
        icon.set_pixel_size(size)
        icon.set_size_request(size, size)
        return icon

    def _on_playlists_toggled(self, button: Gtk.ToggleButton) -> None:
        if button.get_active():
            self._reload_playlists_list()
            self.stack.set_visible_child_name("playlists")
        elif self.library is not None:
            self.stack.set_visible_child_name("tracks")

    def _on_playlist_row_activated(self, _listbox, row: Gtk.ListBoxRow) -> None:
        self._active_playlist = row.playlist
        self._reload_track_list()
        self.playlists_toggle.set_active(False)
        self.stack.set_visible_child_name("tracks")

    def _on_show_all_tracks_clicked(self, _button) -> None:
        self._active_playlist = None
        self._reload_track_list()

    def on_selection_changed(self, *_args) -> None:
        bitset = self.selection_model.get_selection()
        self.delete_button.set_sensitive(bitset.get_size() > 0)

    def _show_preferences(self) -> None:
        switch = Gtk.Switch()
        switch.set_active(self.settings.convert_to_aac_320)
        switch.set_valign(Gtk.Align.CENTER)
        label = Gtk.Label(label="Convert higher-quality imports to AAC 320", xalign=0)
        label.set_wrap(True)
        label.set_hexpand(True)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.append(label)
        row.append(switch)

        dialog = Adw.AlertDialog(
            heading="Preferences",
            body="When enabled, lossless (FLAC/ALAC) imports and lossy files above 320kbps are "
            "re-encoded down to AAC 320kbps on import to save space. Files already at or below "
            "that quality are always left unchanged.",
        )
        dialog.set_extra_child(row)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.connect("response", lambda *_: self._save_preferences(switch.get_active()))
        dialog.present(self)

    def _save_preferences(self, convert_to_aac_320: bool) -> None:
        self.settings.convert_to_aac_320 = convert_to_aac_320
        self.settings.save()

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

    def _on_add_playlist_row_clicked(self, _button, popover: Gtk.Popover) -> None:
        popover.popdown()
        dialog = Gtk.FileDialog()
        filt = Gtk.FileFilter()
        filt.set_name("M3U playlists")
        for ext in M3U_EXTENSIONS:
            filt.add_pattern(f"*{ext}")
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.open(self, None, self._on_m3u_chosen)

    def _on_m3u_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        path = file.get_path() if file else None
        if not path:
            return
        m3u_path = Path(path)

        entry = Gtk.Entry()
        entry.set_text(m3u_path.stem)
        name_dialog = Adw.AlertDialog(
            heading="Playlist Name", body="Name for the new (or existing) playlist on the iPod:"
        )
        name_dialog.set_extra_child(entry)
        name_dialog.add_response("cancel", "Cancel")
        name_dialog.add_response("import", "Import")
        name_dialog.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
        name_dialog.set_default_response("import")
        name_dialog.connect("response", self._on_playlist_name_response, m3u_path, entry)
        name_dialog.present(self)

    def _on_playlist_name_response(self, _dialog, response: str, m3u_path: Path, entry: Gtk.Entry) -> None:
        if response != "import":
            return
        name = entry.get_text().strip() or m3u_path.stem
        self._import_m3u(m3u_path, name)

    def _import_m3u(self, m3u_path: Path, playlist_name: str) -> None:
        assert self.library is not None
        library = self.library  # snapshot: don't chase self.library if it changes mid-import
        self.stack.set_visible_child_name("progress")
        self.add_button.set_sensitive(False)
        self.playlists_toggle.set_sensitive(False)
        self.progress_title.set_text(f"Importing playlist “{playlist_name}”…")
        self._set_progress(0, 1, "")

        def on_progress(completed: int, total: int, detail: str) -> None:
            GLib.idle_add(self._set_progress, completed, total, detail)

        def worker() -> None:
            try:
                result = library.import_playlist(m3u_path, playlist_name=playlist_name, on_progress=on_progress)
            except Exception as e:  # noqa: BLE001 - e.g. save() failing after all files imported
                GLib.idle_add(self._import_finished, 0, [f"Importing the playlist failed: {e}"])
                return
            errors = [f"{p.name}: {msg}" for p, msg in result.errors]
            GLib.idle_add(self._import_finished, len(result.imported), errors)

        threading.Thread(target=worker, daemon=True).start()

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
        self.playlists_toggle.set_sensitive(False)
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
        self.playlists_toggle.set_sensitive(True)
        self._reload_track_list()
        self._reload_playlists_list()
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
        self._reload_playlists_list()
        self.toast(f"Deleted {len(track_ids)} track{'s' if len(track_ids) != 1 else ''}")
