"""Abstract interface every panel driver implements.

This is the seam that lets SkullForge support more than one physical panel
without a rewrite: the engine only ever talks to this interface, never to
a specific vendor protocol directly.
"""
from abc import ABC, abstractmethod


class PanelDriver(ABC):
    #: Human-readable name shown in the UI, e.g. "SkullSaints Agni".
    name: str = "Unknown Panel"

    #: USB vendor/product ID this driver targets.
    vid: int = 0
    pid: int = 0

    #: Logical (mounted-orientation) canvas size in pixels.
    width: int = 0
    height: int = 0

    #: Whether send_refresh() is implemented. Drivers that support cheaper
    #: partial-region updates (instead of always sending a full frame) set
    #: this True and max_refresh_pixels to their per-call pixel budget.
    supports_partial_refresh: bool = False
    max_refresh_pixels: int = 0

    @classmethod
    @abstractmethod
    def probe(cls) -> bool:
        """Return True if a matching device is currently present."""
        raise NotImplementedError

    @abstractmethod
    def open(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_frame(self, rgb565_bytes: bytes) -> None:
        raise NotImplementedError

    def heartbeat(self) -> None:
        """Optional keepalive; default no-op for drivers that don't need one."""

    def set_orientation(self, portrait: bool = True) -> None:
        """Optional orientation command; default no-op for drivers that don't need one."""

    def send_refresh(self, x: int, y: int, width: int, height: int, rgb565_bytes: bytes) -> None:
        """Partial-region update. x/y/width/height are in this driver's own
        native coordinate space (not necessarily the logical portrait
        width/height above). Only called when supports_partial_refresh is True."""
        raise NotImplementedError
