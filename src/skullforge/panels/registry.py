"""Known panel drivers and autodetection.

Adding support for another panel is: write one more PanelDriver subclass,
add it to DRIVERS below.
"""
from typing import Optional

from .base import PanelDriver
from .skullsaints_agni import SkullSaintsAgniDriver

DRIVERS: list[type[PanelDriver]] = [
    SkullSaintsAgniDriver,
]


def autodetect() -> Optional[PanelDriver]:
    """Return an unopened driver instance for the first connected panel found."""
    for driver_cls in DRIVERS:
        try:
            if driver_cls.probe():
                return driver_cls()
        except Exception:
            continue
    return None
