"""GUI-side handle to the worker_process.py subprocess that actually owns
the Engine/USB connection - see worker_process.py's docstring for why this
split exists. Exposes an Engine-like GObject interface (stats-updated,
panel-status-changed signals; set_paused/set_refresh_interval/recover)
so window.py/application.py barely need to know the engine now lives in a
different process.
"""
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib, GObject

from ..config import Config
from ..core.sensors import Stats

log = logging.getLogger(__name__)


class WorkerClient(GObject.Object):
    __gsignals__ = {
        # (Stats)
        "stats-updated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # (connected: bool, detail: str)
        "panel-status-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool, str)),
        # (ok: bool, message: str)
        "recover-done": (GObject.SignalFlags.RUN_FIRST, None, (bool, str)),
    }

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.connected = False
        self.paused = False
        self._last_stats: Optional[Stats] = None
        self._process: Optional[Gio.Subprocess] = None
        self._stdin: Optional[Gio.OutputStream] = None
        self._line_reader: Optional[Gio.DataInputStream] = None

    @property
    def last_stats(self) -> Optional[Stats]:
        return self._last_stats

    def start(self) -> None:
        if self._process is not None:
            return
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
        )
        try:
            self._process = launcher.spawnv([sys.executable, "-m", "skullforge.gui.worker_process"])
        except GLib.Error as e:
            log.error("Could not start the panel worker process: %s", e)
            self.emit("panel-status-changed", False, f"Couldn't start worker process: {e}")
            return

        self._stdin = self._process.get_stdin_pipe()
        stdout = self._process.get_stdout_pipe()
        self._line_reader = Gio.DataInputStream.new(stdout)
        self._read_next_line()

        # Push current config to the freshly-started worker, in case it
        # differs from what's on disk (e.g. changed earlier this session).
        self._send("SET_DISPLAY_MODE", self.config.display_mode)
        self._send("SET_TIME_FORMAT", self.config.time_format)
        self._send("SET_DATE_FORMAT", self.config.date_format)
        self._send("SET_VISIBLE_STATS", ",".join(self.config.visible_stats))
        self._send("SET_COLOR", self.config.color_hex)

    def stop(self) -> None:
        if self._process is None:
            return
        self._send("QUIT")
        try:
            self._process.send_signal(15)
        except GLib.Error:
            pass
        self._process = None

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._send("SET_PAUSED", "1" if paused else "0")

    def set_display_mode(self, mode: str) -> None:
        self.config.display_mode = mode
        self._send("SET_DISPLAY_MODE", mode)

    def set_time_format(self, time_format: str) -> None:
        self.config.time_format = time_format
        self._send("SET_TIME_FORMAT", time_format)

    def set_date_format(self, date_format: str) -> None:
        self.config.date_format = date_format
        self._send("SET_DATE_FORMAT", date_format)

    def set_visible_stats(self, visible_stats: list) -> None:
        self.config.visible_stats = visible_stats
        self._send("SET_VISIBLE_STATS", ",".join(visible_stats))

    def set_color(self, hexcolor: str) -> None:
        self.config.color_hex = hexcolor
        self._send("SET_COLOR", hexcolor)

    def set_refresh_interval(self, seconds: float) -> None:
        self.config.refresh_interval_s = seconds
        self._send("SET_REFRESH_INTERVAL", str(seconds))

    def recover(self) -> None:
        self._send("RECOVER")

    def _send(self, command: str, arg: Optional[str] = None) -> None:
        if self._stdin is None:
            return
        line = command if arg is None else f"{command} {arg}"
        try:
            self._stdin.write_bytes(GLib.Bytes.new((line + "\n").encode()), None)
        except GLib.Error as e:
            log.debug("failed writing to worker process: %s", e)

    def _read_next_line(self) -> None:
        if self._line_reader is None:
            return
        self._line_reader.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line_read)

    def _on_line_read(self, source: Gio.DataInputStream, result: Gio.AsyncResult) -> None:
        try:
            line, _length = source.read_line_finish_utf8(result)
        except GLib.Error as e:
            log.debug("worker process stdout closed: %s", e)
            return
        if line is None:
            return  # EOF - worker process exited
        self._dispatch(line.strip())
        self._read_next_line()

    def _dispatch(self, line: str) -> None:
        parts = line.split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0]
        payload = parts[1] if len(parts) > 1 else ""

        if cmd == "STATS":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return
            stats = Stats(**data)
            self._last_stats = stats
            self.emit("stats-updated", stats)
        elif cmd == "STATUS":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return
            self.connected = data["connected"]
            self.emit("panel-status-changed", data["connected"], data["detail"])
        elif cmd == "RECOVER_RESULT":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return
            self.emit("recover-done", data["ok"], data["message"])
        else:
            log.debug("unrecognized line from worker: %r", line)
