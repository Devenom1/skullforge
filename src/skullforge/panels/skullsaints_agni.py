"""Driver for the SkullSaints Agni / AceMagic S1 family TFT panel.

Panel is driven over HID (not the CH340 serial port also present on this
board): USB VID 0x04D9 / PID 0xFD01, interface 1 (MI_01), endpoint 0x02 OUT.

Reverse-engineered from SkullSaintsTFTTool.exe via static disassembly and a
live USB capture (GDB attached to a QEMU process running a real Windows VM
with the panel passed through). Independently confirmed against
github.com/tjaworski/AceMagic-S1-LED-TFT-Linux (GPL-3, same Holtek HT32
control board family, used on the sibling AceMagic S1 mini PC) and
github.com/ananthb/ht32-panel (a Linux driver for this exact hardware),
which use identical constants and match byte-for-byte.

A full RGB565 frame is sent as a sequence of fixed 4104-byte HID output
reports: an 8-byte header followed by up to 4096 bytes of pixel payload.
There is no leading report-ID byte on the wire (hidapi's Windows backend
strips it from the app's own buffer before transmission).
"""
from datetime import datetime

import usb.core
import usb.util

from .base import PanelDriver

_INTERFACE = 1
_EP_OUT = 0x02

_CHUNK_PAYLOAD = 4096
_REPORT_LEN = 4104

# Command bytes (header[1]) and sub-command bytes (header[2])
_LCD_CONFIG = 0xA1
_LCD_REFRESH = 0xA2
_LCD_REDRAW = 0xA3

# LCD_REFRESH sends its whole region in a single, un-chunked report (unlike
# LCD_REDRAW's 27-chunk sequence) - confirmed against the reference
# tjaworski/AceMagic-S1-LED-TFT-Linux lcd_device.js's refresh(), which
# allocates the same fixed-size report buffer and never chunks. So a
# region's pixel count is capped by what fits in one report's payload.
MAX_REFRESH_PIXELS = _CHUNK_PAYLOAD // 2

_LCD_ORIENTATION = 0xF1
_LCD_SET_TIME = 0xF2

_LCD_LANDSCAPE = 0x01
_LCD_PORTRAIT = 0x02

_TYPE_FIRST = 0xF0
_TYPE_CONTINUE = 0xF1
_TYPE_FINAL = 0xF2


class SkullSaintsAgniDriver(PanelDriver):
    name = "SkullSaints Agni"
    vid = 0x04D9
    pid = 0xFD01
    width = 170
    height = 320
    # Disabled: the user saw the panel's error/lockout overlay start
    # cycling every few seconds once partial refreshes were in use - a
    # regression, actively worse than the always-full-redraw baseline.
    # send_refresh() itself is unit-tested at the byte level (coordinate
    # mapping verified against a known-good full frame) but not confirmed
    # correct against the real firmware - leaving the implementation in
    # place but switched off rather than ripping it out, in case it's
    # revisited later. Don't re-enable without a way to verify against
    # real hardware first.
    supports_partial_refresh = False
    max_refresh_pixels = MAX_REFRESH_PIXELS

    def __init__(self):
        self._dev: usb.core.Device | None = None

    @classmethod
    def probe(cls) -> bool:
        return usb.core.find(idVendor=cls.vid, idProduct=cls.pid) is not None

    def open(self) -> None:
        dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)
        if dev is None:
            raise RuntimeError(f"USB device {self.vid:04x}:{self.pid:04x} not found")

        if dev.is_kernel_driver_active(_INTERFACE):
            dev.detach_kernel_driver(_INTERFACE)

        # Only set the configuration if the device doesn't already have one
        # active. Re-issuing SET_CONFIGURATION on an already-configured
        # device forces the whole device (all interfaces, including ones the
        # kernel's usbhid driver still owns) to reset, which can wedge the
        # panel's firmware.
        try:
            dev.get_active_configuration()
        except usb.core.USBError:
            dev.set_configuration()

        usb.util.claim_interface(dev, _INTERFACE)
        self._dev = dev

    def close(self) -> None:
        if self._dev is None:
            return
        usb.util.release_interface(self._dev, _INTERFACE)
        usb.util.dispose_resources(self._dev)
        self._dev = None

    def _write(self, buf: bytes, timeout_ms: int) -> None:
        if self._dev is None:
            raise RuntimeError("panel not open")
        self._dev.write(_EP_OUT, buf, timeout=timeout_ms)

    def send_frame(self, rgb565_bytes: bytes, timeout_ms: int = 3000) -> None:
        expected = self.width * self.height * 2
        if len(rgb565_bytes) != expected:
            raise ValueError(f"frame must be exactly {expected} bytes, got {len(rgb565_bytes)}")

        offset = 0
        seq = 1
        while offset < expected:
            payload = rgb565_bytes[offset:offset + _CHUNK_PAYLOAD]
            is_last = (offset + len(payload)) == expected
            ptype = _TYPE_FIRST if offset == 0 else (_TYPE_FINAL if is_last else _TYPE_CONTINUE)

            buf = bytearray(_REPORT_LEN)
            buf[0] = 0x55
            buf[1] = _LCD_REDRAW
            buf[2] = ptype
            buf[3] = seq & 0xFF
            buf[5] = (offset >> 8) & 0xFF
            buf[7] = len(payload) // 256
            buf[8:8 + len(payload)] = payload
            self._write(bytes(buf), timeout_ms)

            offset += len(payload)
            seq += 1

    def send_refresh(self, x: int, y: int, width: int, height: int, rgb565_bytes: bytes,
                      timeout_ms: int = 1000) -> None:
        """Partial-region update (LCD_REFRESH). x/y/width/height are in the
        panel's native (rotated/landscape) coordinate space - the same byte
        order send_frame() expects, not the logical portrait layout. See
        panels/geometry.py for mapping a portrait-space rectangle to this.
        width*height must be <= MAX_REFRESH_PIXELS (the payload has to fit
        in a single un-chunked report)."""
        expected = width * height * 2
        if len(rgb565_bytes) != expected:
            raise ValueError(f"refresh region must be exactly {expected} bytes, got {len(rgb565_bytes)}")
        if width * height > MAX_REFRESH_PIXELS:
            raise ValueError(
                f"refresh region {width}x{height}={width * height} pixels exceeds the "
                f"{MAX_REFRESH_PIXELS}-pixel single-report limit"
            )

        buf = bytearray(_REPORT_LEN)
        buf[0] = 0x55
        buf[1] = _LCD_REFRESH
        buf[2] = x & 0xFF
        buf[3] = (x >> 8) & 0xFF
        buf[4] = y & 0xFF
        buf[5] = (y >> 8) & 0xFF
        buf[6] = width & 0xFF
        buf[7] = height & 0xFF
        buf[8:8 + expected] = rgb565_bytes
        self._write(bytes(buf), timeout_ms)

    def set_orientation(self, portrait: bool = True, timeout_ms: int = 1000) -> None:
        buf = bytearray(_REPORT_LEN)
        buf[0] = 0x55
        buf[1] = _LCD_CONFIG
        buf[2] = _LCD_ORIENTATION
        buf[3] = _LCD_PORTRAIT if portrait else _LCD_LANDSCAPE
        self._write(bytes(buf), timeout_ms)

    def heartbeat(self, timeout_ms: int = 1000) -> None:
        """Sends the current wall-clock time as a keepalive. The real app
        sends these roughly once a second to keep the panel's connection
        alive; without them it reverts to its own idle/demo screen."""
        now = datetime.now()
        buf = bytearray(_REPORT_LEN)
        buf[0] = 0x55
        buf[1] = _LCD_CONFIG
        buf[2] = _LCD_SET_TIME
        buf[3] = now.hour
        buf[4] = now.minute
        buf[5] = now.second
        self._write(bytes(buf), timeout_ms)
