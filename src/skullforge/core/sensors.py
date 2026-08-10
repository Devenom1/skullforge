"""System sensor readings for the stats panel.

Replaces the Windows app's bundled Intel Power Gadget (EnergyLib64.dll) with
direct reads from the kernel's intel-rapl powercap interface, and everything
else with psutil.
"""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil

_RAPL_PACKAGE_ENERGY = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
_RAPL_MAX_ENERGY = Path("/sys/class/powercap/intel-rapl:0/max_energy_range_uj")


@dataclass
class Stats:
    cpu_load_pct: float
    mem_load_pct: float
    cpu_temp_c: Optional[float]
    fan_rpm: Optional[int]
    cpu_power_w: Optional[float]


class RaplPowerMeter:
    """Tracks intel-rapl energy counter deltas to derive average watts."""

    def __init__(self):
        self._available = _RAPL_PACKAGE_ENERGY.exists()
        self._max_energy = None
        if self._available:
            try:
                self._max_energy = int(_RAPL_MAX_ENERGY.read_text())
            except OSError:
                self._available = False
        self._last_energy = None
        self._last_time = None

    def _read_energy_uj(self) -> int:
        return int(_RAPL_PACKAGE_ENERGY.read_text())

    def read_watts(self) -> Optional[float]:
        if not self._available:
            return None

        now = time.monotonic()
        try:
            energy = self._read_energy_uj()
        except PermissionError:
            self._available = False  # needs root; stop trying
            return None

        if self._last_energy is None:
            self._last_energy, self._last_time = energy, now
            return None

        delta_energy = energy - self._last_energy
        if delta_energy < 0 and self._max_energy:
            delta_energy += self._max_energy  # counter wrapped
        delta_time = now - self._last_time

        self._last_energy, self._last_time = energy, now
        if delta_time <= 0:
            return None
        return delta_energy / 1_000_000 / delta_time


def read_cpu_temp() -> Optional[float]:
    temps = psutil.sensors_temperatures()
    for key in ("coretemp", "k10temp", "zenpower"):
        entries = temps.get(key)
        if entries:
            for e in entries:
                if e.label in ("Package id 0", "Tdie", ""):
                    return e.current
            return entries[0].current
    if "acpitz" in temps and temps["acpitz"]:
        return temps["acpitz"][0].current
    return None


def read_fan_rpm() -> Optional[int]:
    fans = psutil.sensors_fans()
    for entries in fans.values():
        for e in entries:
            if e.current:
                return int(e.current)
    return None  # not exposed via hwmon on this board; needs EC access to fix


class SensorReader:
    def __init__(self):
        self._power_meter = RaplPowerMeter()
        psutil.cpu_percent(interval=None)  # prime the non-blocking counter

    def read(self) -> Stats:
        return Stats(
            cpu_load_pct=psutil.cpu_percent(interval=None),
            mem_load_pct=psutil.virtual_memory().percent,
            cpu_temp_c=read_cpu_temp(),
            fan_rpm=read_fan_rpm(),
            cpu_power_w=self._power_meter.read_watts(),
        )
