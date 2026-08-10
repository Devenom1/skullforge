"""Loads/saves ~/.config/skullforge/config.toml (respecting $XDG_CONFIG_HOME)."""
import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import tomli_w


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "skullforge"


def _config_path() -> Path:
    return _config_dir() / "config.toml"


@dataclass
class Config:
    refresh_interval_s: float = 1.0
    time_format: str = "24h"  # "24h" or "12h"
    display_mode: str = "stats"  # "stats" or "color"
    color_hex: str = "#ff3b5c"
    start_on_login: bool = False
    start_minimized: bool = False
    recover_script_path: str = "/usr/local/sbin/skullforge-panel-recover.sh"
    usb_port_path: str = "/sys/bus/usb/devices/1-8"

    @classmethod
    def load(cls) -> "Config":
        path = _config_path()
        if not path.exists():
            return cls()
        try:
            with path.open("rb") as f:
                raw = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        _config_dir().mkdir(parents=True, exist_ok=True)
        with _config_path().open("wb") as f:
            tomli_w.dump(asdict(self), f)
