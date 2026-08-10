# SkullForge

A native Linux desktop app for USB "stats panel" mini-displays — live CPU
temp/load, memory, and power on a little TFT screen, driven from Linux
instead of the vendor's Windows-only tool.

Ships today with support for the **SkullSaints Agni** (and the closely
related AceMagic S1 family — same Holtek HT32 control board). Built so
support for other panels can be added later without a rewrite; see
[Architecture](#architecture).

![status](https://img.shields.io/badge/status-early--days-orange)

## Features

- Live CPU temperature, load, memory, and power on the panel, refreshed
  continuously
- GTK4 / libadwaita desktop app with a tray icon — closing the window
  doesn't stop updates, it just goes to the tray; a "Quit" entry in the
  header menu (and the tray) exits fully
- Choose which stats show on the panel (temperature/load/memory/power,
  independently toggleable) — Fan speed is listed but disabled, since
  there's no known way to read it on this hardware yet
- 12/24-hour clock toggle, selectable date format (short, ISO, or a
  two-line long format), solid-color test mode
- One-click panel recovery (power-cycles the panel's USB port to clear a
  stuck "Disconnection" state) without a password prompt each time
- `skullforge --headless` for running just the update loop with no GUI —
  handy for servers or a systemd unit, see below

## Install

Requires Python 3.11+, GTK4, and libadwaita 1.5+ (already present on most
current desktop distros).

```bash
git clone https://github.com/skullforge/skullforge
cd skullforge
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -e .
```

`--system-site-packages` matters: PyGObject (the `gi` module, i.e. GTK/
libadwaita bindings) links against system libraries and generally can't be
installed cleanly into an isolated venv — this flag lets the venv inherit
your system's already-installed GTK4/Adwaita bindings while still keeping
the rest of SkullForge's dependencies isolated. On Debian/Ubuntu, the
system packages you need are `gir1.2-gtk-4.0`, `gir1.2-adw-1`,
`gir1.2-ayatanaappindicator3-0.1` (for the tray icon), and `python3-cairo`
(for the live preview's Cairo-based rendering).

Then, once (as root, one time only — see the script for exactly what it
does before running it):

```bash
sudo ./packaging/install-system-files.sh
```

This installs a udev rule (so the panel works without root for normal use)
and a narrowly-scoped sudoers rule (so the "Recover Panel" action, which
needs root to power-cycle a USB port, doesn't prompt for a password every
time), plus the desktop entry and icon.

## Optional: headless + systemd

If you don't want a GUI window/tray icon at all — e.g. running on a
headless box, or just preferring a background service — run the update
loop as a systemd user service instead:

```bash
mkdir -p ~/.config/systemd/user
cp data/systemd/skullforge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now skullforge.service
```

This starts `skullforge --headless` now, on every future login, and
restarts it automatically if it ever exits unexpectedly. Check on it with
`systemctl --user status skullforge.service` or `journalctl --user -u
skullforge.service -f`. `data/systemd/skullforge.service` uses systemd's
`%h` specifier for your home directory, so it doesn't need editing.

If you'd rather run it by hand instead of as a service:

```bash
skullforge --headless
```

## Running the GUI

```bash
skullforge
```

Opens the main window and a tray icon. Closing the window keeps updates
running in the tray; use Quit (header menu or tray) to exit fully.

## Architecture

- `skullforge.panels` — the `PanelDriver` abstract interface plus one
  concrete implementation (`skullsaints_agni.py`). Adding support for a
  different panel is: write one more `PanelDriver` subclass implementing
  `probe()`/`open()`/`close()`/`send_frame()`, add it to
  `panels/registry.py`'s `DRIVERS` list. Nothing else needs to change —
  the engine and GUI only ever talk to the abstract interface.
- `skullforge.core.engine` — the sensor-read → render → panel-write loop,
  as a `GObject.Object` with signals (`stats-updated`,
  `panel-status-changed`). Runs identically whether driven by the GTK app
  or `skullforge --headless`. All USB I/O happens on a dedicated
  background thread, not the GLib main thread — a full-frame send can
  block for ~1.7s, and running that on the main thread froze the window
  entirely (confirmed: made it unresponsive to input, including dragging).
- `skullforge.gui` — the GTK4/libadwaita window and application. The
  engine/USB connection is owned by a **separate worker process**
  (`gui/worker_process.py`), talked to over a line-based protocol on its
  stdin/stdout pipes (`gui/worker_client.py` is the GUI-side handle).
  This isn't just IPC hygiene: having GTK4/Adwaita/Cairo/Pango loaded in
  the same process as the USB engine was found to destabilize the
  physical panel during sustained use (corrupted content after a few
  minutes), reproduced with a real window, with no window, without the
  tray subprocess, and even with those libraries merely imported and
  never used — `--headless` mode (no GTK anywhere) never showed it. The
  worker process imports none of GTK/Adwaita/Cairo/Pango, so the GUI and
  the USB-owning code never share a process. The tray icon (`gui/tray.py`
  + `gui/tray_helper.py`) follows the same pattern for a different
  reason: the tray library (`AyatanaAppIndicator3`) links against GTK3,
  which can't be loaded in the same process as a GTK4 app, so it's a
  small standalone GTK3 process talking to the main app over its own
  stdin/stdout line protocol. The live preview (`gui/preview.py`) paints
  directly onto a `cairo.ImageSurface` and displays it via
  `Gtk.DrawingArea` rather than `Gtk.Picture`/`Gdk.Texture` — this
  environment's graphics stack was found to corrupt non-uniform image
  content (text came out unreadable) anywhere a `GdkPixbuf` got bridged
  into GDK's texture/cairo integration, so that bridge is bypassed
  entirely.

## Roadmap / not yet built

- A second panel driver (the seam exists; nothing else to plug into it
  yet)
- Packaged releases (`.deb`/Flatpak) — for now it's run-from-source

## License

MIT — see [LICENSE](LICENSE).
