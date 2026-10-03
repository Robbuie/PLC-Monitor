"""
plc_poller.py
Polls configured PLC tags on a fixed interval.
Logs value changes via EventLogger.
"""

from __future__ import annotations
import threading
import time
from typing import Callable
from app.engine.event_log import EventLogger, EV_PLC_CHANGED, EV_PLC_ERROR, EV_PLC_CONNECTED, EV_PLC_DISC, EV_INFO
from app.engine.plc_drivers import PLCDriver


class PLCPoller:
    def __init__(self, driver: PLCDriver, tags: list[str], poll_ms: int,
                 logger: EventLogger,
                 status_cb: Callable[[str], None] | None = None,
                 tag_update_cb: Callable[[str, object], None] | None = None):
        """
        driver        – connected PLCDriver instance
        tags          – list of tag name strings to monitor
        poll_ms       – polling interval in milliseconds
        logger        – EventLogger instance
        status_cb     – called with status strings (for GUI status bar)
        tag_update_cb – called with (tag_name, current_value) on every poll
        """
        self.driver       = driver
        self.tags         = list(tags)
        self.poll_ms      = poll_ms
        self.logger       = logger
        self.status_cb    = status_cb or (lambda m: None)
        self.tag_update_cb = tag_update_cb or (lambda t, v: None)

        self._stop_event  = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_values: dict[str, object] = {}
        self._error_counts: dict[str, int] = {}

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def update_tags(self, tags: list[str]):
        """Hot-update the tag list without restarting."""
        # Remove stale entries
        for t in list(self._last_values.keys()):
            if t not in tags:
                del self._last_values[t]
        self.tags = list(tags)

    def update_poll_ms(self, poll_ms: int):
        self.poll_ms = poll_ms

    def _poll_loop(self):
        plc_label = f"{self.driver.PLC_TYPE}@{getattr(self.driver, 'ip', '?')}"
        self.logger.log(EV_PLC_CONNECTED, plc_label, notes="Polling started")
        self.status_cb(f"[PLC] Polling {plc_label} — {len(self.tags)} tag(s) every {self.poll_ms}ms")

        while not self._stop_event.is_set():
            t0 = time.perf_counter()

            for tag in list(self.tags):
                if self._stop_event.is_set():
                    break

                value, err = self.driver.read_tag(tag)

                if err:
                    count = self._error_counts.get(tag, 0) + 1
                    self._error_counts[tag] = count
                    # Only log first error and every 10th to avoid log spam
                    if count == 1 or count % 10 == 0:
                        self.logger.log(EV_PLC_ERROR, plc_label, tag_or_addr=tag,
                                        notes=f"Read error (x{count}): {err}")
                    self.tag_update_cb(tag, f"ERROR: {err}")
                    continue

                self._error_counts[tag] = 0
                self.tag_update_cb(tag, value)

                old = self._last_values.get(tag, "__UNSET__")
                if old == "__UNSET__":
                    # First read — record without logging as a change
                    self._last_values[tag] = value
                elif str(value) != str(old):
                    self.logger.log(EV_PLC_CHANGED, plc_label,
                                    tag_or_addr=tag,
                                    old_value=old,
                                    new_value=value)
                    self._last_values[tag] = value

            elapsed_ms = (time.perf_counter() - t0) * 1000
            sleep_ms = max(0, self.poll_ms - elapsed_ms)
            if sleep_ms > 0:
                self._stop_event.wait(sleep_ms / 1000)

        self.logger.log(EV_PLC_DISC, plc_label, notes="Polling stopped")
        self.status_cb("[PLC] Polling stopped.")
