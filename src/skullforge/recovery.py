"""Runs the panel power-cycle recovery script via passwordless sudo.

The script itself needs root (it toggles a USB port's power/control sysfs
attribute), so this is set up once via packaging/install-system-files.sh,
which installs a narrowly-scoped /etc/sudoers.d rule for exactly this
script.
"""
import subprocess
from dataclasses import dataclass

from .config import Config


@dataclass
class RecoveryResult:
    ok: bool
    message: str


def recover_panel(config: Config, timeout_s: float = 60.0) -> RecoveryResult:
    try:
        result = subprocess.run(
            ["sudo", "-n", config.recover_script_path, config.usb_port_path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        output = (result.stdout + result.stderr).strip()
        return RecoveryResult(ok=result.returncode == 0, message=output or "Recovery script produced no output.")
    except subprocess.TimeoutExpired:
        return RecoveryResult(ok=False, message="Recovery script timed out.")
    except Exception as e:
        return RecoveryResult(ok=False, message=str(e))
