"""What is running, owned in one place and announced by signal.

Every page reads and drives the monitors through this object rather than
holding them itself. That is what lets the title bar's chips, the rail's status
card and the page that started a monitor all agree on whether it is running,
and what lets "Use as PLC address" on the Network page fill a field on the Tags
page without either page knowing the other exists.

Worker threads never touch a widget. They call `emit` on a signal of this
object, which lives on the UI thread, and Qt queues the delivery.
"""

from __future__ import annotations

import os
import queue
import threading
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from app import paths
from app.engine.config_manager import ConfigManager
from app.engine.event_log import EventLogger
from app.engine.monitors import ConfigError, build_driver, build_socket_monitor, describe_socket, tags_for
from app.engine.plc_poller import PLCPoller


class Session(QObject):
    status = Signal(str)
    plcState = Signal(str, str)        # "off" | "connecting" | "on" | "error", detail
    sockState = Signal(str, str)       # "off" | "on" | "error", detail
    tagValue = Signal(str, object, str)  # tag, value, HH:MM:SS.mmm
    events = Signal(list)              # rows logged since the last pump
    logPathChanged = Signal(str)
    fill = Signal(str, str)            # field key, value -- "Use as ..." from another page
    goto = Signal(str)                 # bring a page to the front
    configSaved = Signal()
    serviceState = Signal(str)
    # Private: the connect thread's answer, delivered on the UI thread.
    _connected = Signal(object, bool, str, list, int)

    def __init__(self, config_path: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        migrated = paths.migrate_legacy_config() if not config_path else None
        self.cfg = ConfigManager(config_path)
        self.migrated_from = migrated
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=20000)
        lg = self.cfg.logging
        self.logger = EventLogger(
            lg["log_path"], gui_queue=self._queue,
            max_bytes=int(lg.get("max_mb", 50)) * 1024 * 1024,
            max_rotated=int(lg.get("max_rotated", 30)),
            rotate_daily=bool(lg.get("rotate_daily", True)),
        )
        self.plc_driver = None
        self.plc_poller: PLCPoller | None = None
        self.sock_mon = None
        self.plc_detail = ""
        self.sock_detail = ""
        self.event_count = 0
        self.service = "UNKNOWN"
        self._pump = QTimer(self)
        self._pump.setInterval(100)
        self._pump.timeout.connect(self._drain)
        self._pump.start()
        self._connected.connect(self._on_connected)

    # ------------------------------------------------------------ the log

    def _drain(self) -> None:
        rows: list[dict] = []
        for _ in range(500):
            try:
                rows.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if rows:
            self.event_count += len(rows)
            self.events.emit(rows)

    def set_log_path(self, path: str) -> None:
        self.logger.set_log_path(path)
        self.cfg.set("logging", "log_path", path)
        self.save_config()
        self.logPathChanged.emit(path)

    # -------------------------------------------------------------- config

    def save_config(self) -> bool:
        try:
            self.cfg.save()
        except OSError as exc:
            self.status.emit(f"Could not save the configuration: {exc}")
            return False
        self.configSaved.emit()
        return True

    # ----------------------------------------------------------------- PLC

    @property
    def plc_running(self) -> bool:
        return self.plc_poller is not None

    def connect_plc(self) -> None:
        if self.plc_poller is not None:
            return
        plc = self.cfg.plc
        tags = tags_for(plc)
        if not tags:
            self.plcState.emit("error", "Add at least one tag to watch.")
            return
        try:
            driver = build_driver(plc)
            poll_ms = int(plc.get("poll_ms", 100))
        except (ConfigError, ValueError) as exc:
            self.plcState.emit("error", str(exc))
            return
        self.plcState.emit("connecting", f"{plc.get('type')} at {plc.get('ip')}")

        def work() -> None:
            try:
                ok, err = driver.connect()
            except Exception as exc:  # a driver that raises instead of returning
                ok, err = False, str(exc)
            self._connected.emit(driver, ok, err, tags, poll_ms)

        threading.Thread(target=work, daemon=True, name="plc-connect").start()

    def _on_connected(self, driver, ok: bool, err: str, tags: list, poll_ms: int) -> None:
        if not ok:
            self.plc_detail = err
            self.plcState.emit("error", err or "The PLC did not answer.")
            return
        self.plc_driver = driver
        self.plc_poller = PLCPoller(
            driver=driver, tags=tags, poll_ms=poll_ms, logger=self.logger,
            status_cb=self.status.emit,
            tag_update_cb=lambda t, v: self.tagValue.emit(
                t, v, datetime.now().strftime("%H:%M:%S.%f")[:-3]),
        )
        self.plc_poller.start()
        self.plc_detail = f"{getattr(driver, 'PLC_TYPE', 'PLC')} at {getattr(driver, 'ip', '?')}"
        self.plcState.emit("on", self.plc_detail)

    def disconnect_plc(self) -> None:
        poller, driver = self.plc_poller, self.plc_driver
        self.plc_poller = None
        self.plc_driver = None
        if poller is not None:
            poller.stop()
        if driver is not None:
            try:
                driver.disconnect()
            except Exception:
                pass
        self.plcState.emit("off", "")

    def update_tags(self) -> None:
        if self.plc_poller is not None:
            self.plc_poller.update_tags(tags_for(self.cfg.plc))

    # -------------------------------------------------------------- socket

    @property
    def sock_running(self) -> bool:
        return self.sock_mon is not None

    def start_socket(self) -> None:
        if self.sock_mon is not None:
            return
        try:
            monitor = build_socket_monitor(self.cfg.socket, self.logger, self.status.emit)
        except (ConfigError, ValueError) as exc:
            self.sockState.emit("error", str(exc))
            return
        started = monitor.start()
        if started is False:
            self.sockState.emit("error", "The capture could not start. See the Live log.")
            return
        self.sock_mon = monitor
        self.sock_detail = describe_socket(self.cfg.socket)
        self.sockState.emit("on", self.sock_detail)

    def stop_socket(self) -> None:
        monitor = self.sock_mon
        self.sock_mon = None
        if monitor is not None:
            threading.Thread(target=monitor.stop, daemon=True, name="sock-stop").start()
        self.sockState.emit("off", "")

    # ------------------------------------------------------------ shutdown

    def shutdown(self) -> None:
        self.disconnect_plc()
        monitor, self.sock_mon = self.sock_mon, None
        if monitor is not None:
            monitor.stop()

    def log_folder(self) -> str:
        return os.path.dirname(os.path.abspath(self.logger.log_path))

