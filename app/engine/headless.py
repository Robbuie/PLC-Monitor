"""
headless.py
Runs PLC polling and socket monitoring with no window.

Used by the Windows service and by `PLCMonitor.exe headless`. It reads the same
config the window saves and builds its monitors with the same functions
(app/engine/monitors.py), so whatever was tested in the window is what runs
here.

A PLC that is not answering at boot is retried rather than given up on: a
service usually starts before the network is fully up, and a monitor that
quietly stopped trying at 06:00 is found out at 14:00 when somebody wants the
data.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("plc_monitor")

#: Seconds between connection attempts while the PLC is not answering.
RETRY_SECONDS = 15


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s.%(msecs)03d  %(levelname)-8s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def run(config_path: str | None = None, stop_event: threading.Event | None = None) -> int:
    """Run until `stop_event` is set (or Ctrl-C). Returns a process exit code."""
    _configure_logging()
    from app import paths
    from app.engine.config_manager import ConfigManager
    from app.engine.event_log import EV_INFO, EV_PLC_ERROR, EventLogger
    from app.engine.monitors import ConfigError, build_driver, build_socket_monitor, tags_for
    from app.engine.plc_poller import PLCPoller

    config_path = config_path or paths.config_path()
    if not os.path.exists(config_path):
        paths.migrate_legacy_config()
    if not os.path.exists(config_path):
        log.error("Config file not found: %s", config_path)
        return 2

    stop = stop_event or threading.Event()
    cfg = ConfigManager(config_path)
    lg = cfg.logging
    logger = EventLogger(
        lg["log_path"], gui_queue=None,
        max_bytes=int(lg.get("max_mb", 50)) * 1024 * 1024,
        max_rotated=int(lg.get("max_rotated", 30)),
        rotate_daily=bool(lg.get("rotate_daily", True)),
    )
    logger.log(EV_INFO, "Runner", notes=f"Headless runner started (config {config_path})")
    log.info("Logging to %s", lg["log_path"])

    def status(msg: str) -> None:
        log.info(msg)

    # --- Socket monitor first: it does not depend on anything answering. ---
    sock_mon = None
    if cfg.socket.get("enabled"):
        try:
            sock_mon = build_socket_monitor(cfg.socket, logger, status)
            started = sock_mon.start()
            if started is False:
                sock_mon = None
        except ConfigError as exc:
            log.error("Socket monitor not started: %s", exc)
            logger.log(EV_INFO, "Runner", notes=f"Socket monitor not started: {exc}")
    else:
        log.info("Socket monitor is not enabled for unattended runs.")

    # --- PLC, retried until it answers or we are told to stop. ---
    plc_poller: PLCPoller | None = None
    tags = tags_for(cfg.plc)
    want_plc = bool(cfg.plc.get("enabled")) and bool(tags)
    if cfg.plc.get("enabled") and not tags:
        log.warning("PLC polling is enabled but there are no tags for %s.", cfg.plc.get("type"))
    elif not cfg.plc.get("enabled"):
        log.info("PLC polling is not enabled for unattended runs.")

    last_failure = ""
    next_try = 0.0
    try:
        while not stop.is_set():
            if want_plc and plc_poller is None and time.monotonic() >= next_try:
                try:
                    driver = build_driver(cfg.plc)
                    ok, err = driver.connect()
                except ConfigError as exc:
                    log.error("PLC not started: %s", exc)
                    want_plc = False
                    ok, err = False, ""
                if ok:
                    log.info("PLC connected: %s", cfg.plc.get("ip"))
                    plc_poller = PLCPoller(driver=driver, tags=tags,
                                           poll_ms=int(cfg.plc.get("poll_ms", 100)),
                                           logger=logger, status_cb=status)
                    plc_poller.start()
                    last_failure = ""
                elif want_plc:
                    if err != last_failure:
                        log.error("PLC connect failed: %s", err)
                        logger.log(EV_PLC_ERROR, "Runner", notes=f"Connect failed, retrying every "
                                   f"{RETRY_SECONDS}s: {err}")
                        last_failure = err
                    next_try = time.monotonic() + RETRY_SECONDS
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass

    log.info("Shutting down...")
    if plc_poller:
        plc_poller.stop()
        plc_poller.driver.disconnect()
    if sock_mon:
        sock_mon.stop()
    logger.log(EV_INFO, "Runner", notes="Headless runner stopped")
    log.info("Done.")
    return 0
