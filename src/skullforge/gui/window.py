import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk, GObject

from ..config import Config
from ..core.sensors import Stats
from .preview import draw_color_preview, draw_stats_preview
from .worker_client import WorkerClient

# Only one panel driver exists today (170x320) and the worker process now
# owns the driver instance, not the GUI - the preview just targets this
# fixed size rather than querying a driver object across the process
# boundary. Revisit if/when a second, differently-sized panel exists.
_PANEL_WIDTH = 170
_PANEL_HEIGHT = 320

_BEZEL_CSS = b"""
.sf-bezel {
  background-color: #050507;
  border-radius: 26px;
  padding: 14px 10px;
}
"""

_PREVIEW_SCALE = 1.5

# Ordered (key -> title) so both the Gtk.StringList model and the lookup
# used to map a selected index back to a config value stay in sync.
_DATE_FORMAT_TITLES = {
    "short": "Short (Mon 2026-08-10)",
    "iso": "ISO (2026-08-10)",
    "long": "Long, two lines",
}


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
    def __init__(self, application: Adw.Application, worker: WorkerClient, config: Config):
        super().__init__(application=application, title="SkullForge")
        self.worker = worker
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

        self.worker.connect("stats-updated", self._on_stats_updated)
        self.worker.connect("panel-status-changed", self._on_status_changed)
        self.worker.connect("recover-done", self._on_recover_done)
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
        menu.append("Quit", "app.quit")
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
        width = int(_PANEL_WIDTH * _PREVIEW_SCALE)
        height = int(_PANEL_HEIGHT * _PREVIEW_SCALE)
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

        _rounded_rect_path(cr, 0, 0, width, height, 14)
        cr.clip()
        cr.scale(width / _PANEL_WIDTH, height / _PANEL_HEIGHT)

        if self.config.display_mode == "color":
            draw_color_preview(cr, _PANEL_WIDTH, _PANEL_HEIGHT, self.config.color_hex)
        else:
            draw_stats_preview(cr, _PANEL_WIDTH, _PANEL_HEIGHT, self._preview_stats, self.config.time_format,
                                self.config.date_format, self.config.visible_stats)

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

        self.date_row = Adw.ComboRow(title="Date")
        self.date_row.set_model(Gtk.StringList.new(list(_DATE_FORMAT_TITLES.values())))
        date_keys = list(_DATE_FORMAT_TITLES.keys())
        self.date_row.set_selected(date_keys.index(self.config.date_format))
        self.date_row.connect("notify::selected", self._on_date_format_changed)
        display_group.add(self.date_row)

        box.append(display_group)

        # Which stats show on the panel + their live values
        stats_group = Adw.PreferencesGroup(
            title="Panel Stats", description="Choose what shows on the panel"
        )
        self.stat_rows: dict[str, Gtk.Label] = {}
        self.stat_switches: dict[str, Adw.SwitchRow] = {}
        for key, title in [("temp", "CPU Temperature"), ("load", "CPU Load"),
                            ("mem", "Memory Load"), ("power", "CPU Power")]:
            row = Adw.SwitchRow(title=title)
            row.set_active(key in self.config.visible_stats)
            row.connect("notify::active", self._on_stat_toggled, key)
            value_label = Gtk.Label(label="—")
            value_label.add_css_class("dim-label")
            row.add_suffix(value_label)
            self.stat_rows[key] = value_label
            self.stat_switches[key] = row
            stats_group.add(row)

        fan_row = Adw.SwitchRow(
            title="Fan Speed", subtitle="Not yet supported on this hardware", active=False
        )
        fan_row.set_sensitive(False)
        stats_group.add(fan_row)

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

    # --- worker signal handlers -------------------------------------------------

    def _on_stats_updated(self, _worker: WorkerClient, stats: Stats) -> None:
        self.stat_rows["temp"].set_label(_fmt(stats.cpu_temp_c, "°C", 1))
        self.stat_rows["load"].set_label(_fmt(stats.cpu_load_pct, "%", 0))
        self.stat_rows["mem"].set_label(_fmt(stats.mem_load_pct, "%", 0))
        self.stat_rows["power"].set_label(_fmt(stats.cpu_power_w, "W", 1))
        self._refresh_preview(stats)

    def _on_status_changed(self, _worker: WorkerClient, connected: bool, detail: str) -> None:
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
        mode = toggle_group.get_active_name()
        self.time_row.set_sensitive(mode == "stats")
        self.worker.set_display_mode(mode)
        self.config.save()

    def _on_time_format_changed(self, toggle_group: Adw.ToggleGroup, _pspec: GObject.ParamSpec) -> None:
        self.worker.set_time_format(toggle_group.get_active_name())
        self.config.save()

    def _on_date_format_changed(self, combo_row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        date_format = list(_DATE_FORMAT_TITLES.keys())[combo_row.get_selected()]
        self.worker.set_date_format(date_format)
        self.config.save()

    def _on_stat_toggled(self, switch_row: Adw.SwitchRow, _pspec: GObject.ParamSpec, key: str) -> None:
        visible = [k for k, row in self.stat_switches.items() if row.get_active()]
        self.worker.set_visible_stats(visible)
        self.config.save()

    def _on_pause_toggled(self, button: Gtk.ToggleButton) -> None:
        self.set_paused(button.get_active())

    def set_paused(self, paused: bool) -> None:
        self.worker.set_paused(paused)
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
        self.worker.recover()

    def _on_recover_done(self, _worker: WorkerClient, ok: bool, message: str) -> None:
        self.banner.set_title(message or ("Panel recovered" if ok else "Recovery failed"))

    def _on_close_request(self, _window: Gtk.Widget) -> bool:
        self.hide()
        return True  # don't destroy - the worker process keeps running in the background
