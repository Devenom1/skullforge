import logging
import signal
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .. import __version__
from ..config import Config
from .tray import Tray
from .window import SkullForgeWindow
from .worker_client import WorkerClient

log = logging.getLogger(__name__)

APP_ID = "io.skullforge.App"

_AUTOSTART_DESKTOP = """[Desktop Entry]
Type=Application
Name=SkullForge
Comment=Live system stats on your USB TFT panel
Exec={exec_path}
Icon=io.skullforge.App
X-GNOME-Autostart-enabled=true
"""


def _autostart_path() -> Path:
    base = GLib.get_user_config_dir()
    return Path(base) / "autostart" / f"{APP_ID}.desktop"


def _set_autostart(enabled: bool) -> None:
    path = _autostart_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        exec_path = GLib.find_program_in_path("skullforge") or "skullforge"
        path.write_text(_AUTOSTART_DESKTOP.format(exec_path=exec_path))
    elif path.exists():
        path.unlink()


class SkullForgeApplication(Adw.Application):
    def __init__(self, config: Config):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.config = config
        self.worker = WorkerClient(config)
        self.window: SkullForgeWindow | None = None
        self.tray: Tray | None = None

        self._add_actions()
        # Without this, a bare `kill`/SIGTERM (e.g. a session logout, or a
        # supervisor stopping the process) skips do_shutdown() entirely,
        # orphaning the tray helper subprocess instead of it exiting with us.
        # GLib.unix_signal_add integrates directly with the main loop's own
        # signal handling instead of Python's raw signal.signal() + idle_add
        # - the latter was observed to occasionally miss/delay delivery.
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_quit_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_quit_signal)

    def _on_quit_signal(self) -> bool:
        self.quit()
        return GLib.SOURCE_REMOVE

    def _add_actions(self) -> None:
        prefs_action = Gio.SimpleAction.new("preferences", None)
        prefs_action.connect("activate", self._on_preferences)
        self.add_action(prefs_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_a: self.quit())
        self.add_action(quit_action)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = SkullForgeWindow(self, self.worker, self.config)
            self.worker.connect("panel-status-changed", self._on_status_changed)

            self.hold()  # keep running even while the window is hidden

            self.tray = Tray(
                on_show=self._show_window,
                on_pause=lambda: self.window.set_paused(True),
                on_resume=lambda: self.window.set_paused(False),
                on_recover=self.window.recover,
                on_quit=self.quit,
            )
            self.tray.start()
            self.window.pause_button.connect(
                "toggled", lambda btn: self.tray.send_paused(btn.get_active())
            )

            self.worker.start()

        if not self.config.start_minimized:
            self._show_window()

    def _show_window(self) -> None:
        self.window.present()

    def _on_status_changed(self, _worker: WorkerClient, connected: bool, detail: str) -> None:
        if self.tray:
            self.tray.send_status(connected, detail)

    def _on_preferences(self, _action: Gio.SimpleAction, _param) -> None:
        dialog = Adw.PreferencesDialog()
        page = Adw.PreferencesPage(title="General")
        dialog.add(page)

        general_group = Adw.PreferencesGroup(title="Updates")
        interval_row = Adw.SpinRow.new_with_range(0.5, 10.0, 0.5)
        interval_row.set_title("Refresh interval")
        interval_row.set_subtitle("Seconds between panel updates")
        interval_row.set_value(self.config.refresh_interval_s)
        interval_row.connect("notify::value", self._on_interval_changed)
        general_group.add(interval_row)
        page.add(general_group)

        startup_group = Adw.PreferencesGroup(title="Startup")
        autostart_row = Adw.SwitchRow()
        autostart_row.set_title("Start on login")
        autostart_row.set_active(self.config.start_on_login)
        autostart_row.connect("notify::active", self._on_autostart_changed)
        startup_group.add(autostart_row)

        minimized_row = Adw.SwitchRow()
        minimized_row.set_title("Start minimized to tray")
        minimized_row.set_active(self.config.start_minimized)
        minimized_row.connect("notify::active", self._on_start_minimized_changed)
        startup_group.add(minimized_row)
        page.add(startup_group)

        dialog.present(self.window)

    def _on_interval_changed(self, row: "Adw.SpinRow", _pspec) -> None:
        self.worker.set_refresh_interval(row.get_value())
        self.config.save()

    def _on_autostart_changed(self, row: "Adw.SwitchRow", _pspec) -> None:
        self.config.start_on_login = row.get_active()
        _set_autostart(row.get_active())
        self.config.save()

    def _on_start_minimized_changed(self, row: "Adw.SwitchRow", _pspec) -> None:
        self.config.start_minimized = row.get_active()
        self.config.save()

    def _on_about(self, _action: Gio.SimpleAction, _param) -> None:
        about = Adw.AboutDialog(
            application_name="SkullForge",
            application_icon=APP_ID,
            version=__version__,
            developer_name="SkullForge contributors",
            license_type=Gtk.License.MIT_X11,
            comments="Live system stats on your USB TFT panel.",
            website="https://github.com/skullforge/skullforge",
        )
        about.present(self.window)

    def do_shutdown(self) -> None:
        if self.tray:
            self.tray.stop()
        self.worker.stop()
        Adw.Application.do_shutdown(self)
