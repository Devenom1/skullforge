"""Entry point: `skullforge` launches the GUI, `skullforge --headless` runs
just the update loop (no GTK/display needed - for systemd/server use)."""
import argparse
import logging
import signal
import sys

from .config import Config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="skullforge", description="SkullForge panel control")
    parser.add_argument("--headless", action="store_true",
                         help="Run the panel-update loop with no GUI (for systemd/server use)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv[1:] if argv is not None else None)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                         format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = Config.load()

    if args.headless:
        return _run_headless(config)

    from .gui.application import SkullForgeApplication
    app = SkullForgeApplication(config)
    return app.run(sys.argv[:1])


def _run_headless(config: Config) -> int:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    from .core.engine import Engine

    log = logging.getLogger("skullforge.headless")
    engine = Engine(config)
    engine.connect(
        "panel-status-changed",
        lambda _e, connected, detail: log.info(
            "panel %s: %s", "connected" if connected else "disconnected", detail
        ),
    )

    loop = GLib.MainLoop()

    def _stop():
        log.info("shutting down")
        engine.stop()
        loop.quit()
        return GLib.SOURCE_REMOVE

    # GLib.unix_signal_add integrates directly with the main loop's own
    # signal handling instead of Python's raw signal.signal() + idle_add -
    # the latter was observed to occasionally miss/delay delivery.
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, _stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, _stop)

    engine.start()
    log.info("engine started, waiting for panel")
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
