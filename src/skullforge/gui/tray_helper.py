"""Standalone GTK3 + AyatanaAppIndicator3 tray icon process.

AppIndicator3 links against GTK3, which can't be loaded in the same
process as the main app's GTK4 window (PyGObject only allows one version
of a given namespace per process). So the tray icon runs here, in its own
process, spawned by gui/tray.py and talking to the main app over its
stdin/stdout pipes with a trivial line-based protocol:

  main -> tray (stdin):  "STATUS <0|1> <detail text...>"   panel connection state
                          "PAUSED <0|1>"                    updates queue running state
  tray -> main (stdout): "SHOW" | "PAUSE" | "RESUME" | "RECOVER" | "QUIT"

Run as: python3 -m skullforge.gui.tray_helper <icon_dir> <icon_stem>
"""
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator3
from gi.repository import GLib, GObject, Gtk


def _send(command: str) -> None:
    print(command, flush=True)


class TrayHelper:
    def __init__(self, icon_dir: str, icon_stem: str):
        self.indicator = AppIndicator3.Indicator.new(
            "skullforge", icon_stem, AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_icon_theme_path(icon_dir)
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("SkullForge")

        self.menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label="Panel: looking…")
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)
        self.menu.append(Gtk.SeparatorMenuItem())

        show_item = Gtk.MenuItem(label="Show Window")
        show_item.connect("activate", lambda *_a: _send("SHOW"))
        self.menu.append(show_item)

        self.pause_item = Gtk.CheckMenuItem(label="Pause Updates")
        self._pause_handler_id = self.pause_item.connect("toggled", self._on_pause_toggled)
        self.menu.append(self.pause_item)

        recover_item = Gtk.MenuItem(label="Recover Panel")
        recover_item.connect("activate", lambda *_a: _send("RECOVER"))
        self.menu.append(recover_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda *_a: _send("QUIT"))
        self.menu.append(quit_item)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

    def _on_pause_toggled(self, item: Gtk.CheckMenuItem) -> None:
        _send("RESUME" if not item.get_active() else "PAUSE")

    def apply_status(self, connected: bool, detail: str) -> None:
        self.status_item.set_label(f"Panel: {detail}")

    def apply_paused(self, paused: bool) -> None:
        self.pause_item.handler_block(self._pause_handler_id)
        self.pause_item.set_active(paused)
        self.pause_item.handler_unblock(self._pause_handler_id)


def _handle_line(helper: TrayHelper, line: str) -> None:
    parts = line.strip().split(maxsplit=1)
    if not parts:
        return
    cmd = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if cmd == "STATUS":
        bits = rest.split(maxsplit=1)
        connected = bool(int(bits[0])) if bits else False
        detail = bits[1] if len(bits) > 1 else ""
        helper.apply_status(connected, detail)
    elif cmd == "PAUSED":
        helper.apply_paused(rest.strip() == "1")


def _on_parent_gone() -> bool:
    # stdin hit EOF, meaning the main app's pipe closed (it exited or
    # crashed) - don't leave an orphaned tray icon behind.
    _send("QUIT")
    Gtk.main_quit()
    return GLib.SOURCE_REMOVE


def _read_stdin(helper: TrayHelper) -> None:
    for line in sys.stdin:
        if not line:
            break
        GLib.idle_add(_handle_line, helper, line)
    GLib.idle_add(_on_parent_gone)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: tray_helper.py <icon_dir> <icon_stem>", file=sys.stderr)
        return 2
    icon_dir, icon_stem = sys.argv[1], sys.argv[2]

    helper = TrayHelper(icon_dir, icon_stem)

    import threading

    reader = threading.Thread(target=_read_stdin, args=(helper,), daemon=True)
    reader.start()

    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
