"""Draws the live preview directly with Cairo/Pango (not by blitting a
PIL-rendered bitmap via Gdk.Texture, Gdk.cairo_set_source_pixbuf, or even a
hand-built cairo.ImageSurface passed to cr.set_source_surface()).

All of those bitmap-blit paths were found to corrupt non-uniform content
once composited through GTK4's DrawingArea (which records draw operations
onto a cairo.RecordingSurface for GSK to replay/composite later - pure
vector drawing, like this module's Cairo paths and Pango text layouts,
replays correctly; blitting an externally-built raster image does not, on
this system's graphics stack). This mirrors core/render.py's visual
design (same fonts/colors/layout) but is a fully separate implementation:
core/render.py's PIL-based rendering is unaffected by any of this - it
only ever produces raw RGB565 bytes sent straight over USB, no GTK/Cairo
involved - and stays exactly as-is for the physical panel.

Font descriptions are built once at import time (not per draw call) and
each text element only creates one Pango layout (measure + draw together)
rather than two - rebuilding these from scratch on every ~1s redraw was
CPU-bound work happening on the GTK main thread, which doesn't release
the GIL during Pango/HarfBuzz text shaping. That was found to occasionally
stall the engine's background USB-send thread mid-frame for the better
part of a second (measured via instrumented chunk-write timing), long
enough for the panel firmware to time out and abandon an in-progress
frame - the actual cause of intermittent garbled content on the physical
panel while the app was running (not present in --headless mode, which
never does any GTK/Cairo/Pango work at all).
"""
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from ..core.render import DEFAULT_VISIBLE_STATS, STAT_DEFS, _date_lines
from ..core.sensors import Stats

_BG = (12 / 255, 14 / 255, 24 / 255)
_FG = (235 / 255, 235 / 255, 245 / 255)
_MUTED = (140 / 255, 145 / 255, 165 / 255)
_ACCENT = (90 / 255, 180 / 255, 255 / 255)


def _font_description(family: str, px: int) -> Pango.FontDescription:
    # Absolute size in Pango units, not a point-size string: a plain size
    # string is in points (DPI-scaled), which rendered noticeably wider
    # than PIL's pixel-size fonts and overlapped the 170px-wide layout.
    desc = Pango.FontDescription()
    desc.set_family(family)
    desc.set_absolute_size(px * Pango.SCALE)
    return desc


_FONT_TIME = _font_description("DejaVu Sans Bold", 26)
_FONT_DATE = _font_description("DejaVu Sans", 16)
_FONT_LABEL = _font_description("DejaVu Sans", 14)
_FONT_VALUE = _font_description("DejaVu Sans Bold", 20)


def _make_layout(cr, text: str, font_desc: Pango.FontDescription) -> Pango.Layout:
    layout = PangoCairo.create_layout(cr)
    layout.set_text(text, -1)
    layout.set_font_description(font_desc)
    return layout


def _show_centered(cr, width: int, y: float, text: str, font_desc: Pango.FontDescription, rgb: tuple) -> None:
    layout = _make_layout(cr, text, font_desc)
    w, _h = layout.get_pixel_size()
    cr.set_source_rgb(*rgb)
    cr.move_to((width - w) / 2, y)
    PangoCairo.show_layout(cr, layout)


def _show_left(cr, x: float, y: float, text: str, font_desc: Pango.FontDescription, rgb: tuple) -> None:
    layout = _make_layout(cr, text, font_desc)
    cr.set_source_rgb(*rgb)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


def _show_right(cr, right_edge: float, y: float, text: str, font_desc: Pango.FontDescription, rgb: tuple) -> None:
    layout = _make_layout(cr, text, font_desc)
    w, _h = layout.get_pixel_size()
    cr.set_source_rgb(*rgb)
    cr.move_to(right_edge - w, y)
    PangoCairo.show_layout(cr, layout)


def _draw_background(cr, width: int, height: int) -> None:
    cr.set_source_rgb(*_BG)
    cr.rectangle(0, 0, width, height)
    cr.fill()


def draw_stats_preview(cr, width: int, height: int, stats: Stats, time_format: str,
                        date_format: str = "short", visible_stats: list | None = None) -> None:
    from datetime import datetime

    if visible_stats is None:
        visible_stats = DEFAULT_VISIBLE_STATS

    _draw_background(cr, width, height)
    now = datetime.now()

    time_str = now.strftime("%I:%M %p").lstrip("0") if time_format == "12h" else now.strftime("%H:%M")
    _show_centered(cr, width, 16, time_str, _FONT_TIME, _FG)

    date_lines = _date_lines(now, date_format)
    if len(date_lines) == 1:
        _show_centered(cr, width, 64, date_lines[0], _FONT_DATE, _MUTED)
        line_y, rows_start_y = 98, 120
    else:
        _show_centered(cr, width, 60, date_lines[0], _FONT_DATE, _MUTED)
        _show_centered(cr, width, 78, date_lines[1], _FONT_DATE, _MUTED)
        line_y, rows_start_y = 108, 128

    cr.set_source_rgb(*_ACCENT)
    cr.set_line_width(2)
    cr.move_to(14, line_y)
    cr.line_to(width - 14, line_y)
    cr.stroke()

    y = rows_start_y
    for key in visible_stats:
        stat_def = STAT_DEFS.get(key)
        if stat_def is None:
            continue
        label, formatter = stat_def
        _show_left(cr, 14, y, label, _FONT_LABEL, _MUTED)
        _show_right(cr, width - 14, y - 3, formatter(stats), _FONT_VALUE, _FG)
        y += 40


def draw_color_preview(cr, width: int, height: int, hexcolor: str) -> None:
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    cr.set_source_rgb(r, g, b)
    cr.rectangle(0, 0, width, height)
    cr.fill()
