"""Drives sensor reads -> rendering -> panel writes.

A GObject.Object so both the GTK app and the headless CLI can react via
normal signals instead of polling. GLib/GObject need no display, so this
same class runs identically in --headless mode.

All USB I/O (open/send_frame/heartbeat/close) happens on a dedicated
background thread, never on the GLib main thread: a full-frame send can
block for ~1.7s, and running that inside a GLib timeout callback froze the
whole window (confirmed - it made the app unresponsive to input, including
dragging the window, for most of every refresh cycle). The main thread only
does fast sensor reads and wakes the worker; GObject signals raised from
the worker are marshaled back onto the main loop via GLib.idle_add.
"""
import logging
import threading
import time
from typing import Optional

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib, GObject
from PIL import Image

from ..config import Config
from ..panels import registry
from ..panels.base import PanelDriver
from .render import (
    crop_and_pack_region,
    image_to_rgb565_bytes,
    render_frame_bytes,
    render_stats_image,
    split_portrait_band,
)
from .sensors import SensorReader, Stats

log = logging.getLogger(__name__)

_RECONNECT_INTERVAL_S = 3.0
_HEARTBEAT_INTERVAL_S = 60.0
# Sending full frames back-to-back with no rest between them (the panel's
# own ~1.8-2.2s USB bandwidth ceiling was already the only throttle)
# triggers escalating per-chunk latency (confirmed via instrumented timing:
# a "first chunk of frame" write climbing from ~80ms to 900-1200ms after a
# few consecutive frames) that eventually disconnects the device entirely.
# Reproduced identically in the older skullsaints-tft prototype's plain
# send loop too, so this is a genuine hardware/firmware characteristic
# under sustained continuous updates, not anything specific to this
# engine's threading.
#
# A 1.0s cooldown was enough to prevent outright disconnects in testing,
# but the protocol has no acknowledgment/readback mechanism, so "no USB
# error was thrown" does NOT guarantee the firmware actually rendered that
# frame cleanly - and the user still saw occasional single-frame visual
# corruption on the physical panel even with a cooldown (screen unreadable
# from software; this can only really be confirmed by eye), on drivers with
# no partial-refresh support. Raised to a more conservative value to give
# the firmware more headroom on those, trading refresh speed for
# reliability.
_POST_SEND_COOLDOWN_S = 2.5

# For drivers that support partial refresh (see PanelDriver.supports_partial_refresh):
# send a full frame only occasionally, and a small partial LCD_REFRESH of
# just the time band on the ticks in between. A full LCD_REDRAW is a
# ~108KB/~1.8-2.2s transfer touching the panel's whole framebuffer every
# single cycle, forever - the likely reason continuous operation degrades
# (see the cooldown note above and skullsaints_project_structure memory for
# the full investigation). The reference protocol's own LCD_REFRESH command
# exists for exactly this - small, cheap, targeted updates - which is very
# plausibly how the original Windows tool avoids this ("Windows can do it
# well" per user report), rather than redrawing the whole screen every tick.
_FULL_REDRAW_EVERY_N_TICKS = 6
_TIME_BAND_HEIGHT = 48  # portrait-space rows 0..48: covers the 26px time font with margin


def _hex_to_rgb(hexcolor: str) -> tuple[int, int, int]:
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class Engine(GObject.Object):
    __gsignals__ = {
        # (Stats)
        "stats-updated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # (connected: bool, detail: str)
        "panel-status-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool, str)),
    }

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.sensor_reader = SensorReader()
        self.driver: Optional[PanelDriver] = None
        self.connected = False
        self.paused = False

        self._last_stats: Optional[Stats] = None
        self._last_reconnect_attempt = 0.0
        self._last_heartbeat = 0.0
        self._source_id: Optional[int] = None

        self._needs_full_redraw = True
        self._ticks_since_full_redraw = 0
        self._last_display_mode: Optional[str] = None
        self._last_time_format: Optional[str] = None

        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._recovery_in_progress = threading.Event()
        self._worker: Optional[threading.Thread] = None

    @property
    def last_stats(self) -> Optional[Stats]:
        return self._last_stats

    def start(self) -> None:
        if self._source_id is not None:
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        self._tick()
        interval_ms = max(200, int(self.config.refresh_interval_s * 1000))
        self._source_id = GLib.timeout_add(interval_ms, self._on_timeout)

    def stop(self) -> None:
        if self._source_id is not None:
            GLib.source_remove(self._source_id)
            self._source_id = None
        self._stop_event.set()
        self._wake_event.set()
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None
        self._close_driver()

    def set_refresh_interval(self, seconds: float) -> None:
        self.config.refresh_interval_s = seconds
        if self._source_id is not None:
            GLib.source_remove(self._source_id)
            interval_ms = max(200, int(seconds * 1000))
            self._source_id = GLib.timeout_add(interval_ms, self._on_timeout)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def suspend_for_recovery(self) -> None:
        """Stop trying to connect/send while an external power-cycle
        recovery script runs. Without this, the engine's own reconnect
        polling (every _RECONNECT_INTERVAL_S) keeps touching the USB
        device, which resets its idle timer and prevents the recovery
        script's port-suspend step from ever actually triggering -
        confirmed via the recovery script reporting "port never suspended"
        while the engine kept polling in the background."""
        self._recovery_in_progress.set()
        self._wake_event.set()

    def resume_after_recovery(self) -> None:
        self._recovery_in_progress.clear()
        self._last_reconnect_attempt = 0.0
        self._wake_event.set()

    def tick_now(self) -> None:
        """Force an immediate sensor read + wake the worker, e.g. for a manual 'Send Now' action."""
        self._tick()

    def _on_timeout(self) -> bool:
        self._tick()
        return GLib.SOURCE_CONTINUE

    def _tick(self) -> None:
        """Main-thread only: fast sensor read, then wake the worker. Never touches self.driver."""
        stats = self.sensor_reader.read()
        self._last_stats = stats
        self.emit("stats-updated", stats)
        self._wake_event.set()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=1.0)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            self._worker_tick()

    def _worker_tick(self) -> None:
        """Background thread only: all USB I/O happens here."""
        if self._recovery_in_progress.is_set():
            if self.driver is not None:
                self._close_driver()
                self._emit_status(False, "Paused for recovery")
            return

        if self.driver is None:
            self._try_connect()

        if self.driver is None or self.paused:
            return

        stats = self._last_stats
        if stats is None:
            return

        if (self.config.display_mode != self._last_display_mode
                or self.config.time_format != self._last_time_format):
            self._needs_full_redraw = True
            self._last_display_mode = self.config.display_mode
            self._last_time_format = self.config.time_format

        try:
            if self.config.display_mode == "color":
                color = _hex_to_rgb(self.config.color_hex)
                img = Image.new("RGB", (self.driver.width, self.driver.height), color)
                frame = image_to_rgb565_bytes(img)
                self.driver.send_frame(frame)
                # a solid fill has no "time band" to partially refresh -
                # make sure switching back to stats mode redraws fully first
                self._needs_full_redraw = True
            elif not self.driver.supports_partial_refresh:
                frame = render_frame_bytes(stats, self.driver.width, self.driver.height, self.config.time_format,
                                            self.config.date_format, self.config.visible_stats)
                self.driver.send_frame(frame)
            elif self._needs_full_redraw or self._ticks_since_full_redraw >= _FULL_REDRAW_EVERY_N_TICKS:
                frame = render_frame_bytes(stats, self.driver.width, self.driver.height, self.config.time_format,
                                            self.config.date_format, self.config.visible_stats)
                self.driver.send_frame(frame)
                self._needs_full_redraw = False
                self._ticks_since_full_redraw = 0
            else:
                self._send_time_refresh(stats)
                self._ticks_since_full_redraw += 1

            time.sleep(_POST_SEND_COOLDOWN_S)

            now = GLib.get_monotonic_time() / 1_000_000
            if now - self._last_heartbeat > _HEARTBEAT_INTERVAL_S:
                self.driver.heartbeat()
                self._last_heartbeat = now
        except Exception:
            log.exception("Failed to send frame; treating panel as disconnected")
            self._close_driver()
            self._needs_full_redraw = True
            self._emit_status(False, "Lost connection to panel")

    def _send_time_refresh(self, stats: Stats) -> None:
        img = render_stats_image(stats, self.driver.width, self.driver.height, self.config.time_format)
        for sub_y, sub_h in split_portrait_band(0, _TIME_BAND_HEIGHT, self.driver.width, self.driver.max_refresh_pixels):
            lx, ly, lw, lh, packed = crop_and_pack_region(img, 0, sub_y, self.driver.width, sub_h)
            self.driver.send_refresh(lx, ly, lw, lh, packed)

    def _try_connect(self) -> None:
        """Background thread only."""
        now = GLib.get_monotonic_time() / 1_000_000
        if now - self._last_reconnect_attempt < _RECONNECT_INTERVAL_S:
            return
        self._last_reconnect_attempt = now

        driver = registry.autodetect()
        if driver is None:
            if self.connected:
                self._emit_status(False, "Panel not detected")
            return

        try:
            driver.open()
            driver.set_orientation(portrait=True)
        except Exception as e:
            log.warning("Found panel but failed to open it: %s", e)
            self._emit_status(False, f"Panel found but couldn't open it: {e}")
            return

        self.driver = driver
        self._last_heartbeat = 0.0
        self._needs_full_redraw = True
        self._emit_status(True, f"{driver.name} connected")

    def _close_driver(self) -> None:
        if self.driver is None:
            return
        try:
            self.driver.close()
        except Exception:
            pass
        self.driver = None

    def _emit_status(self, connected: bool, detail: str) -> None:
        """Callable from any thread: marshals the actual signal emission onto the main loop."""

        def do_emit() -> bool:
            self.connected = connected
            self.emit("panel-status-changed", connected, detail)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(do_emit)
