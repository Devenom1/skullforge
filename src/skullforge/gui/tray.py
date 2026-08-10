"""Spawns and talks to the GTK3 tray-icon subprocess (see tray_helper.py).

AppIndicator3 needs GTK3, which can't share a process with our GTK4
window, so the tray icon lives in a separate process, controlled here over
its stdin/stdout pipes.
"""
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_ICON_STEM = "io.skullforge.App"


class Tray:
    def __init__(self, on_show: Callable[[], None], on_pause: Callable[[], None],
                 on_resume: Callable[[], None], on_recover: Callable[[], None],
                 on_quit: Callable[[], None]):
        self._handlers = {
            "SHOW": on_show,
            "PAUSE": on_pause,
            "RESUME": on_resume,
            "RECOVER": on_recover,
            "QUIT": on_quit,
        }
        self._process: Optional[Gio.Subprocess] = None
        self._stdin: Optional[Gio.OutputStream] = None
        self._line_reader: Optional[Gio.DataInputStream] = None

    def start(self) -> None:
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
        )
        try:
            self._process = launcher.spawnv([
                sys.executable, "-m", "skullforge.gui.tray_helper", str(_DATA_DIR), _ICON_STEM,
            ])
        except GLib.Error as e:
            log.warning("Could not start tray icon helper (continuing without a tray icon): %s", e)
            self._process = None
            return

        self._stdin = self._process.get_stdin_pipe()
        stdout = self._process.get_stdout_pipe()
        self._line_reader = Gio.DataInputStream.new(stdout)
        self._read_next_line()

    def _read_next_line(self) -> None:
        if self._line_reader is None:
            return
        self._line_reader.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line_read)

    def _on_line_read(self, source: Gio.DataInputStream, result: Gio.AsyncResult) -> None:
        try:
            line, _length = source.read_line_finish_utf8(result)
        except GLib.Error as e:
            log.debug("tray helper stdout closed: %s", e)
            return
        if line is None:
            return  # EOF - helper process exited
        handler = self._handlers.get(line.strip())
        if handler:
            handler()
        self._read_next_line()

    def send_status(self, connected: bool, detail: str) -> None:
        self._write(f"STATUS {1 if connected else 0} {detail}\n")

    def send_paused(self, paused: bool) -> None:
        self._write(f"PAUSED {1 if paused else 0}\n")

    def _write(self, text: str) -> None:
        if self._stdin is None:
            return
        try:
            self._stdin.write_bytes(GLib.Bytes.new(text.encode()), None)
        except GLib.Error as e:
            log.debug("failed writing to tray helper: %s", e)

    def stop(self) -> None:
        if self._process is not None:
            try:
                self._process.send_signal(15)
            except GLib.Error:
                pass
            self._process = None
