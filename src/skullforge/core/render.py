"""Composes the stats overlay into an RGB565 frame for a panel of any size."""
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .sensors import Stats

_FONT_DIR = "/usr/share/fonts/truetype/dejavu"
_font_time = ImageFont.truetype(f"{_FONT_DIR}/DejaVuSans-Bold.ttf", 26)
_font_date = ImageFont.truetype(f"{_FONT_DIR}/DejaVuSans.ttf", 16)
_font_label = ImageFont.truetype(f"{_FONT_DIR}/DejaVuSans.ttf", 14)
_font_value = ImageFont.truetype(f"{_FONT_DIR}/DejaVuSans-Bold.ttf", 20)

_BG = (12, 14, 24)
_FG = (235, 235, 245)
_MUTED = (140, 145, 165)
_ACCENT = (90, 180, 255)


#: key -> (label, formatter(Stats) -> str). Shared with gui/preview.py so
#: the live preview and the actual panel render identical row sets.
#: "fan" is deliberately not offered as a toggleable choice in the UI (see
#: config.py) since there's no known Linux source for it on this hardware
#: - it stays defined here so nothing breaks if a user's saved config
#: still lists it from before that restriction existed.
STAT_DEFS = {
    "temp": ("TEMP", lambda s: _fmt(s.cpu_temp_c, "°C", 1)),
    "load": ("LOAD", lambda s: _fmt(s.cpu_load_pct, "%", 0)),
    "mem": ("MEM", lambda s: _fmt(s.mem_load_pct, "%", 0)),
    "fan": ("FAN", lambda s: _fmt(s.fan_rpm, " RPM", 0)),
    "power": ("PWR", lambda s: _fmt(s.cpu_power_w, "W", 1)),
}

DEFAULT_VISIBLE_STATS = ["temp", "load", "mem", "power"]

#: key -> human label, for preference UI display order/labels.
DATE_FORMAT_LABELS = {
    "short": "Short (Mon 2026-08-10)",
    "iso": "ISO (2026-08-10)",
    "long": "Long, two lines (Monday / August 10, 2026)",
}


def _fmt(value, unit, precision=0):
    if value is None:
        return "N/A"
    return f"{value:.{precision}f}{unit}"


def _date_lines(now: datetime, date_format: str) -> list[str]:
    if date_format == "iso":
        return [now.strftime("%Y-%m-%d")]
    if date_format == "long":
        return [now.strftime("%A"), now.strftime("%B %d, %Y")]
    return [now.strftime("%a %Y-%m-%d")]  # "short" (default)


def _center_text(draw, width, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((width - w) / 2, y), text, font=font, fill=fill)


def _stat_row(draw, width, y, label, value_text):
    draw.text((14, y), label, font=_font_label, fill=_MUTED)
    bbox = draw.textbbox((0, 0), value_text, font=_font_value)
    w = bbox[2] - bbox[0]
    draw.text((width - 14 - w, y - 3), value_text, font=_font_value, fill=_FG)


def render_stats_image(stats: Stats, width: int, height: int, time_format: str = "24h",
                        date_format: str = "short", visible_stats: list[str] | None = None) -> Image.Image:
    if visible_stats is None:
        visible_stats = DEFAULT_VISIBLE_STATS

    now = datetime.now()
    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    time_str = now.strftime("%I:%M %p").lstrip("0") if time_format == "12h" else now.strftime("%H:%M")
    _center_text(draw, width, 16, time_str, _font_time, _FG)

    date_lines = _date_lines(now, date_format)
    if len(date_lines) == 1:
        _center_text(draw, width, 64, date_lines[0], _font_date, _MUTED)
        line_y, rows_start_y = 98, 120
    else:
        _center_text(draw, width, 60, date_lines[0], _font_date, _MUTED)
        _center_text(draw, width, 78, date_lines[1], _font_date, _MUTED)
        line_y, rows_start_y = 108, 128

    draw.line([(14, line_y), (width - 14, line_y)], fill=_ACCENT, width=2)

    y = rows_start_y
    for key in visible_stats:
        stat_def = STAT_DEFS.get(key)
        if stat_def is None:
            continue
        label, formatter = stat_def
        _stat_row(draw, width, y, label, formatter(stats))
        y += 40

    return img


def image_to_rgb565_bytes(img: Image.Image, rotate: bool = True) -> bytes:
    # Some panels (e.g. the SkullSaints Agni) have a native scanline order
    # that's 90 degrees off from how they're physically mounted - confirmed
    # empirically: solid colors looked fine regardless of row width (color
    # is invariant to stride), but real structured content (text/bars) came
    # out as diagonally-torn vertical streaks when sent in the "obvious"
    # row order. Panel drivers that need this pass rotate=True (the
    # default); a driver whose native orientation already matches its
    # mounting can pass rotate=False.
    if rotate:
        img = img.transpose(Image.Transpose.ROTATE_90)
    arr = np.asarray(img.convert("RGB"), dtype=np.uint16)
    r = (arr[:, :, 0] & 0xF8) << 8
    g = (arr[:, :, 1] & 0xFC) << 3
    b = arr[:, :, 2] >> 3
    # Big-endian: confirmed against ht32-panel's Rust source, which writes
    # (pixel >> 8) then (pixel & 0xFF) into successive bytes. White text
    # looked fine under the old little-endian packing (0xFFFF either way)
    # but dark/subtle colors like our near-black background came out wrong.
    packed = (r | g | b).astype(">u2")
    return packed.tobytes()


def render_frame_bytes(stats: Stats, width: int, height: int, time_format: str = "24h",
                        date_format: str = "short", visible_stats: list[str] | None = None,
                        rotate: bool = True) -> bytes:
    img = render_stats_image(stats, width, height, time_format, date_format, visible_stats)
    return image_to_rgb565_bytes(img, rotate=rotate)


# --- Partial-region (LCD_REFRESH) support -----------------------------------
#
# The panel's native scanline order is rotated 90 degrees from how content
# is authored here (see image_to_rgb565_bytes above), so a rectangle in our
# portrait coordinate space doesn't map to a simple contiguous byte range -
# it needs its own coordinate transform. This mapping was derived
# empirically (not hand-derived from the rotation math, to avoid an
# off-by-one/direction mistake that would be very hard to catch without
# eyes on the physical panel): mark a single pixel, rotate exactly like
# image_to_rgb565_bytes does, see where it lands. Confirmed round-trip
# byte-identical against slicing the same region out of a full,
# already-verified frame before trusting it against real hardware.

def portrait_rect_to_landscape(x: int, y: int, w: int, h: int, portrait_width: int) -> tuple[int, int, int, int]:
    """Maps a portrait-space rectangle (x, y, w, h) to the panel's native
    (rotated) coordinate space: (landscape_x, landscape_y, landscape_w, landscape_h)."""
    return y, portrait_width - x - w, h, w


def crop_and_pack_region(img: Image.Image, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int, bytes]:
    """Rotates + RGB565-packs just a portrait-space sub-rectangle of img,
    for a partial LCD_REFRESH update. Returns (landscape_x, landscape_y,
    landscape_w, landscape_h, rgb565_bytes)."""
    region = img.crop((x, y, x + w, y + h))
    rotated = region.transpose(Image.Transpose.ROTATE_90)
    packed = image_to_rgb565_bytes(rotated, rotate=False)  # already rotated
    lx, ly, lw, lh = portrait_rect_to_landscape(x, y, w, h, img.width)
    return lx, ly, lw, lh, packed


def split_portrait_band(y: int, height: int, width: int, max_pixels: int) -> list[tuple[int, int]]:
    """Splits a full-width portrait horizontal band [y, y+height) into a
    list of (sub_y, sub_height) pieces, each small enough that its
    landscape mapping (landscape_w=sub_height, landscape_h=width) stays
    within max_pixels - a single LCD_REFRESH report can't hold more than
    that (see panels/skullsaints_agni.py's MAX_REFRESH_PIXELS)."""
    max_slice_h = max(1, max_pixels // width)
    pieces = []
    cur = y
    end = y + height
    while cur < end:
        h = min(max_slice_h, end - cur)
        pieces.append((cur, h))
        cur += h
    return pieces
