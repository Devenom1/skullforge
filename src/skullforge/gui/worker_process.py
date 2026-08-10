"""Standalone process that owns the Engine/USB connection - no GTK/Adwaita/
Cairo/Pango imported here at all, deliberately.

Confirmed via extensive isolated testing (see project memory) that simply
having gi's Gtk/Adw/Pango/PangoCairo + pycairo loaded in the same process
as the USB engine causes the physical panel to degrade within a few
minutes of continuous operation - reproduced with a real window, without
a window, without the tray subprocess, and even with those libraries only
imported and never used. --headless mode (no GTK anywhere) never showed
this. So the GUI process and the USB-owning process are kept fully
separate, communicating over this process's stdin/stdout - the same
pattern already used for the tray icon (see tray.py/tray_helper.py).

Protocol (line-based):
  gui -> worker (stdin):  SET_DISPLAY_MODE stats|color
                          SET_TIME_FORMAT 24h|12h
                          SET_COLOR <#rrggbb>
                          SET_REFRESH_INTERVAL <seconds>
                          SET_PAUSED 0|1
                          RECOVER
                          QUIT
  worker -> gui (stdout): STATUS <json: {"connected": bool, "detail": str}>
                          STATS <json: dataclasses.asdict(Stats)>
                          RECOVER_RESULT <json: {"ok": bool, "message": str}>

Run as: python3 -m skullforge.gui.worker_process
"""
import json
import logging
import signal
import sys
import threading

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from .. import recovery
from ..config import Config
from ..core.engine import Engine

log = logging.getLogger("skullforge.worker")


def _send(command: str, payload=None) -> None:
    if payload is None:
        print(command, flush=True)
    else:
        print(f"{command} {json.dumps(payload)}", flush=True)


class Worker:
    def __init__(self, config: Config):
        self.config = config
        self.engine = Engine(config)
        self.engine.connect("stats-updated", self._on_stats_updated)
        self.engine.connect("panel-status-changed", self._on_status_changed)

    def start(self) -> None:
        self.engine.start()

    def stop(self) -> None:
        self.engine.stop()

    def _on_stats_updated(self, _engine, stats) -> None:
        _send("STATS", {
            "cpu_load_pct": stats.cpu_load_pct,
            "mem_load_pct": stats.mem_load_pct,
            "cpu_temp_c": stats.cpu_temp_c,
            "fan_rpm": stats.fan_rpm,
            "cpu_power_w": stats.cpu_power_w,
        })

    def _on_status_changed(self, _engine, connected: bool, detail: str) -> None:
        _send("STATUS", {"connected": connected, "detail": detail})

    def handle_command(self, line: str) -> None:
        parts = line.strip().split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "SET_DISPLAY_MODE":
            self.config.display_mode = arg
        elif cmd == "SET_TIME_FORMAT":
            self.config.time_format = arg
        elif cmd == "SET_DATE_FORMAT":
            self.config.date_format = arg
        elif cmd == "SET_VISIBLE_STATS":
            self.config.visible_stats = [k for k in arg.split(",") if k]
        elif cmd == "SET_COLOR":
            self.config.color_hex = arg
        elif cmd == "SET_REFRESH_INTERVAL":
            try:
                self.engine.set_refresh_interval(float(arg))
            except ValueError:
                log.warning("bad SET_REFRESH_INTERVAL value: %r", arg)
        elif cmd == "SET_PAUSED":
            self.engine.set_paused(arg == "1")
        elif cmd == "RECOVER":
            threading.Thread(target=self._do_recover, daemon=True).start()
        elif cmd == "QUIT":
            GLib.idle_add(_quit_main_loop)
        else:
            log.warning("unknown command: %r", line)

    def _do_recover(self) -> None:
        self.engine.suspend_for_recovery()
        result = recovery.recover_panel(self.config)
        GLib.idle_add(self._recover_done, result)

    def _recover_done(self, result: recovery.RecoveryResult) -> bool:
        self.engine.resume_after_recovery()
        _send("RECOVER_RESULT", {"ok": result.ok, "message": result.message})
        return GLib.SOURCE_REMOVE


_loop: GLib.MainLoop | None = None


def _quit_main_loop() -> bool:
    if _loop is not None:
        _loop.quit()
    return GLib.SOURCE_REMOVE


def _read_stdin(worker: Worker) -> None:
    for line in sys.stdin:
        if not line:
            break
        GLib.idle_add(worker.handle_command, line)
    # parent's pipe closed (it exited) - don't linger as an orphan
    GLib.idle_add(_quit_main_loop)


def main() -> int:
    global _loop
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = Config.load()
    worker = Worker(config)

    reader = threading.Thread(target=_read_stdin, args=(worker,), daemon=True)
    reader.start()

    _loop = GLib.MainLoop()

    def _stop(*_a) -> bool:
        worker.stop()
        _loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, _stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, _stop)

    worker.start()
    _loop.run()
    worker.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
