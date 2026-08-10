import math
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from .. import recovery
from ..config import Config
from ..core.engine import Engine
from ..core.sensors import Stats
from .preview import draw_color_preview, draw_stats_preview

_BEZEL_CSS = b"""
.sf-bezel {
  background-color: #050507;
  border-radius: 26px;
  padding: 14px 10px;
}
"""

_PREVIEW_SCALE = 1.5


def _fmt(value, unit, precision=0):
    if value is None:
        return "N/A"
    return f"{value:.{precision}f}{unit}"


def _rounded_rect_path(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


class SkullForgeWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, engine: Engine, config: Config):
        super().__init__(application=application, title="SkullForge")
        self.engine = engine
        self.config = config
        self.set_default_size(760, 720)

        self._install_css()

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self._build_header())

        self.banner = Adw.Banner(title="Looking for panel…", button_label="Recover")
        self.banner.connect("button-clicked", self._on_recover_clicked)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self.banner)

        clamp = Adw.Clamp(maximum_size=760)
        clamp.set_margin_top(24)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(18)
        clamp.set_margin_end(18)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=28, halign=Gtk.Align.CENTER)
        columns.append(self._build_preview_column())
        columns.append(self._build_controls_column())
        clamp.set_child(columns)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(clamp)
        outer.append(scroller)

        toolbar_view.set_content(outer)
        self.set_content(toolbar_view)

        self.engine.connect("stats-updated", self._on_stats_updated)
        self.engine.connect("panel-status-changed", self._on_status_changed)
        self.connect("close-request", self._on_close_request)

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(_BEZEL_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()

        self.pause_button = Gtk.ToggleButton()
        self.pause_button.set_icon_name("media-playback-pause-symbolic")
        self.pause_button.set_tooltip_text("Pause panel updates")
        self.pause_button.connect("toggled", self._on_pause_toggled)
        header.pack_start(self.pause_button)

        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("About SkullForge", "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(menu_button)

        return header

    def _build_preview_column(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)

        bezel = Gtk.Box(css_classes=["sf-bezel"])
        # Drawn via a DrawingArea + native Cairo/Pango drawing rather than
        # blitting any pre-rendered bitmap (Gtk.Picture/Gdk.Texture,
        # Gdk.cairo_set_source_pixbuf, or even cr.set_source_surface() on a
        # hand-built cairo.ImageSurface) - see preview.py's docstring: all
        # of those were found to corrupt non-uniform content once
        # composited through this DrawingArea's cairo.RecordingSurface:
        # only pure vector drawing (paths, Pango text) replays correctly
        # here. Rounded corners are also clipped manually in Cairo rather
        # than via CSS overflow:hidden, for the same reason.
        self._preview_stats: Stats | None = None
        self.preview_area = Gtk.DrawingArea()
        width = int(170 * _PREVIEW_SCALE)
        height = int(320 * _PREVIEW_SCALE)
        self.preview_area.set_content_width(width)
        self.preview_area.set_content_height(height)
        self.preview_area.set_draw_func(self._draw_preview)
        bezel.append(self.preview_area)
        box.append(bezel)

        self.status_label = Gtk.Label(label="170 × 320 · live preview")
        self.status_label.add_css_class("dim-label")
        self.status_label.add_css_class("caption")
        box.append(self.status_label)

        return box

    def _draw_preview(self, _area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        if self._preview_stats is None:
            return
        driver_width = self.engine.driver.width if self.engine.driver else 170
        driver_height = self.engine.driver.height if self.engine.driver else 320

        _rounded_rect_path(cr, 0, 0, width, height, 14)
        cr.clip()
        cr.scale(width / driver_width, height / driver_height)

        if self.config.display_mode == "color":
            draw_color_preview(cr, driver_width, driver_height, self.config.color_hex)
        else:
            draw_stats_preview(cr, driver_width, driver_height, self._preview_stats, self.config.time_format)

    def _build_controls_column(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, width_request=320)

        # Display mode + time format
        display_group = Adw.PreferencesGroup(title="Display")

        self.mode_toggle = Adw.ToggleGroup()
        self.mode_toggle.add(Adw.Toggle(name="stats", label="Live Stats"))
        self.mode_toggle.add(Adw.Toggle(name="color", label="Solid Color"))
        self.mode_toggle.set_active_name(self.config.display_mode)
        self.mode_toggle.connect("notify::active-name", self._on_mode_changed)
        mode_row = Adw.ActionRow(title="Mode")
        mode_row.add_suffix(self.mode_toggle)
        display_group.add(mode_row)

        self.time_toggle = Adw.ToggleGroup()
        self.time_toggle.add(Adw.Toggle(name="24h", label="24-Hour"))
        self.time_toggle.add(Adw.Toggle(name="12h", label="12-Hour"))
        self.time_toggle.set_active_name(self.config.time_format)
        self.time_toggle.connect("notify::active-name", self._on_time_format_changed)
        self.time_row = Adw.ActionRow(title="Clock")
        self.time_row.add_suffix(self.time_toggle)
        display_group.add(self.time_row)

        box.append(display_group)

        # Live stat readouts
        stats_group = Adw.PreferencesGroup(title="Live Readings")
        self.stat_rows: dict[str, Adw.ActionRow] = {}
        for key, title in [("temp", "CPU Temperature"), ("load", "CPU Load"),
                            ("mem", "Memory Load"), ("power", "CPU Power")]:
            row = Adw.ActionRow(title=title)
            value_label = Gtk.Label(label="—")
            value_label.add_css_class("dim-label")
            row.add_suffix(value_label)
            self.stat_rows[key] = value_label
            stats_group.add(row)
        box.append(stats_group)

        # Actions
        actions_group = Adw.PreferencesGroup(title="Actions")
        recover_row = Adw.ActionRow(title="Recover Panel", subtitle="Power-cycles the panel's USB port")
        recover_button = Gtk.Button(label="Recover")
        recover_button.add_css_class("flat")
        recover_button.connect("clicked", self._on_recover_clicked)
        recover_row.add_suffix(recover_button)
        actions_group.add(recover_row)
        box.append(actions_group)

        return box

    # --- engine signal handlers -------------------------------------------------

    def _on_stats_updated(self, _engine: Engine, stats: Stats) -> None:
        self.stat_rows["temp"].set_label(_fmt(stats.cpu_temp_c, "°C", 1))
        self.stat_rows["load"].set_label(_fmt(stats.cpu_load_pct, "%", 0))
        self.stat_rows["mem"].set_label(_fmt(stats.mem_load_pct, "%", 0))
        self.stat_rows["power"].set_label(_fmt(stats.cpu_power_w, "W", 1))
        self._refresh_preview(stats)

    def _on_status_changed(self, _engine: Engine, connected: bool, detail: str) -> None:
        if connected:
            self.banner.set_revealed(False)
        else:
            self.banner.set_title(detail or "Panel not detected")
            self.banner.set_revealed(True)

    def _refresh_preview(self, stats: Stats) -> None:
        self._preview_stats = stats
        self.preview_area.queue_draw()

    # --- UI event handlers -------------------------------------------------

    def _on_mode_changed(self, toggle_group: Adw.ToggleGroup, _pspec: GObject.ParamSpec) -> None:
        self.config.display_mode = toggle_group.get_active_name()
        self.time_row.set_sensitive(self.config.display_mode == "stats")
        self.config.save()

    def _on_time_format_changed(self, toggle_group: Adw.ToggleGroup, _pspec: GObject.ParamSpec) -> None:
        self.config.time_format = toggle_group.get_active_name()
        self.config.save()

    def _on_pause_toggled(self, button: Gtk.ToggleButton) -> None:
        self.set_paused(button.get_active())

    def set_paused(self, paused: bool) -> None:
        self.engine.set_paused(paused)
        self.pause_button.set_icon_name(
            "media-playback-start-symbolic" if paused else "media-playback-pause-symbolic"
        )
        if self.pause_button.get_active() != paused:
            self.pause_button.set_active(paused)

    def _on_recover_clicked(self, _widget: Gtk.Widget) -> None:
        self.recover()

    def recover(self) -> None:
        self.banner.set_title("Recovering panel…")
        self.banner.set_revealed(True)

        # Stop the engine's own reconnect polling first - otherwise it keeps
        # touching the USB device in the background, which resets the port's
        # idle timer and prevents the recovery script's suspend step from
        # ever actually triggering (confirmed: it reported "port never
        # suspended" while the engine kept reconnecting concurrently).
        self.engine.suspend_for_recovery()

        def worker():
            result = recovery.recover_panel(self.config)
            GLib.idle_add(self._on_recover_done, result)

        # recover_panel() blocks on a subprocess for up to ~60s (polling the
        # USB port through a real power cycle) - run it off the main loop so
        # the window stays responsive.
        threading.Thread(target=worker, daemon=True).start()

    def _on_recover_done(self, result: recovery.RecoveryResult) -> bool:
        self.engine.resume_after_recovery()
        self.banner.set_title(result.message or ("Panel recovered" if result.ok else "Recovery failed"))
        return GLib.SOURCE_REMOVE

    def _on_close_request(self, _window: Gtk.Widget) -> bool:
        self.hide()
        return True  # don't destroy - the engine keeps running in the background
