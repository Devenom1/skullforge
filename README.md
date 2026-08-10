# SkullForge

A native Linux desktop app for USB "stats panel" mini-displays — live CPU
temp/load, memory, and power on a little TFT screen, driven from Linux
instead of the vendor's Windows-only tool.

Ships today with support for the **SkullSaints Agni** (and the closely
related AceMagic S1 family — same Holtek HT32 control board). Built so
support for other panels can be added later without a rewrite; see
[Architecture](#architecture).

![status](https://img.shields.io/badge/status-early--days-orange)

> **Known issue:** the GTK4 GUI's own live preview renders correctly, but
> running the full GUI app has been observed to cause intermittent visual
> corruption on the *physical panel* during sustained use, not reproduced
> in `--headless` mode (see [Known issues](#known-issues-gui-vs-headless)).
> **`skullforge --headless`, run as a systemd user service, is the
> currently-recommended way to run this** until that's tracked down —
> see [Recommended setup](#recommended-setup-headless--systemd).

## Features

- Live CPU temperature, load, memory, and power on the panel, refreshed
  continuously
- GTK4 / libadwaita desktop app with a tray icon — closing the window
  doesn't stop updates, it just goes to the tray (see the known-issue note
  above though)
- 12/24-hour clock toggle, solid-color test mode
- One-click panel recovery (power-cycles the panel's USB port to clear a
  stuck "Disconnection" state) without a password prompt each time
- `skullforge --headless` for running just the update loop with no GUI —
  currently the reliable way to run this, see below

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

## Recommended setup: headless + systemd

Until the GUI issue above is resolved, run the update loop as a systemd
user service instead of the GUI app:

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

Works, and the in-app preview is correct — see the known-issue note at the
top before relying on it for actual panel updates over long sessions.

## Known issues (GUI vs headless)

Extensive investigation (see the project memory / git history for the
full trail) ruled out the obvious suspects: the exact bytes SkullForge
sends to the panel were verified byte-perfect across multiple long runs
(both headless and GUI), individual USB writes were confirmed to always
transmit their full length (no silent partial writes), and a partial-
region-update (`LCD_REFRESH`) approach - intended to reduce how often a
full ~108KB redraw hits the panel, since continuous full redraws appear to
destabilize it over time - made things measurably *worse* (the panel's
error/lockout overlay started cycling) and was reverted.

What's confirmed: `skullforge --headless` (no GTK loaded at all) has run
reliably for the user over real, extended sessions. The full GUI app
(GTK4/Adwaita/Cairo/Pango all loaded in the same process as the USB
engine) has shown the panel reverting to corrupted content during
sessions where headless mode did not. The exact mechanism isn't
understood yet - it's not explained by anything in the rendered data or
the USB transport layer that's been checked so far.

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
- `skullforge.gui` — the GTK4/libadwaita window and application. The tray
  icon (`gui/tray.py` + `gui/tray_helper.py`) runs as a **separate
  process**: the tray library (`AyatanaAppIndicator3`) links against GTK3,
  which can't be loaded in the same process as a GTK4 app, so the tray
  icon is a small standalone GTK3 process talking to the main app over a
  line-based protocol on its stdin/stdout pipes. The live preview
  (`gui/preview.py`) paints directly onto a `cairo.ImageSurface` and
  displays it via `Gtk.DrawingArea` rather than `Gtk.Picture`/
  `Gdk.Texture` — this environment's graphics stack was found to corrupt
  non-uniform image content (text came out unreadable) anywhere a
  `GdkPixbuf` got bridged into GDK's texture/cairo integration, so that
  bridge is bypassed entirely.

## Roadmap / not yet built

- **Root-cause the GUI-vs-headless panel corruption** (see Known issues)
  — top priority, blocks recommending the GUI for real use
- A second panel driver (the seam exists; nothing else to plug into it
  yet)
- Per-row stat visibility toggles (choose which stats show on the panel)
- Packaged releases (`.deb`/Flatpak) — for now it's run-from-source

## License

MIT — see [LICENSE](LICENSE).
