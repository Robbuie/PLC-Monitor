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
from app.engine import clock
from app.engine.plc_drivers import PLCDriver

#: Marks a tag that has not been read yet. A sentinel object rather than a
#: string, so a PLC string tag whose value happens to be that text still logs.
_UNSET = object()


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
        # When each tag was last read successfully, for the change window.
        self._last_seen_ns: dict[str, int] = {}
        #: How long the last full poll took, in ms.
        self.last_poll_ms: float | None = None

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
                self._last_seen_ns.pop(t, None)
        self.tags = list(tags)

    def update_poll_ms(self, poll_ms: int):
        self.poll_ms = poll_ms

    def _poll_loop(self):
        plc_label = f"{self.driver.PLC_TYPE}@{getattr(self.driver, 'ip', '?')}"
        self.logger.log(EV_PLC_CONNECTED, plc_label, notes="Polling started")
        self.status_cb(f"[PLC] Polling {plc_label} — {len(self.tags)} tag(s) every {self.poll_ms}ms")

        while not self._stop_event.is_set():
            t0 = time.perf_counter()
            try:
                self.poll_once(plc_label)
            except Exception as exc:
                # A driver that raises instead of returning an error must not
                # end polling silently: the thread would die, the chip would
                # stay green, and nothing more would be logged.
                self.logger.log(EV_PLC_ERROR, plc_label, notes=f"Poll failed: {exc}")
                self._stop_event.wait(max(self.poll_ms, 1000) / 1000)
                continue
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.last_poll_ms = elapsed_ms
            sleep_ms = max(0, self.poll_ms - elapsed_ms)
            if sleep_ms > 0:
                self._stop_event.wait(sleep_ms / 1000)

        self.logger.log(EV_PLC_DISC, plc_label, notes="Polling stopped")
        self.status_cb("[PLC] Polling stopped.")

    def poll_once(self, plc_label: str) -> None:
        """Read every tag once and log what changed.

        The tags go to the driver as one batch: a driver that can put them in
        one request (pycomm3, libplctag) answers in one network round trip
        instead of one per tag, which shortens the poll and with it the window
        in which a change can hide.
        """
        tags = list(self.tags)
        if not tags:
            return
        reader = getattr(self.driver, "read_tags", None)
        if reader is not None:
            results = reader(tags)
        else:  # a driver from outside this module that only reads one at a time
            results = {}
            for tag in tags:
                value, err = self.driver.read_tag(tag)
                results[tag] = (value, err, clock.now_ns())
        # Stamped when the answer came back: that is when this program learnt
        # of the value, the same moment every other row in the log is stamped
        # by. A batch shares one stamp; a driver that reads tag by tag gives
        # each its own (the optional third item), so the first tag of a slow
        # poll is not stamped as late as the last.
        batch_ns = clock.now_ns()

        for tag in tags:
            if self._stop_event.is_set():
                break
            value, err, *stamp = results.get(tag, (None, "No answer for this tag"))
            seen_ns = stamp[0] if stamp and stamp[0] else batch_ns

            if err:
                count = self._error_counts.get(tag, 0) + 1
                self._error_counts[tag] = count
                # Only log first error and every 10th to avoid log spam
                if count == 1 or count % 10 == 0:
                    self.logger.log(EV_PLC_ERROR, plc_label, tag_or_addr=tag,
                                    notes=f"Read error (x{count}): {err}")
                self.tag_update_cb(tag, f"ERROR: {err}")
                # The last good read keeps its time, so after a run of
                # errors the window covers the whole stretch the value was
                # unknown -- which is the honest answer.
                continue

            self._error_counts[tag] = 0
            self.tag_update_cb(tag, value)

            previous_ns = self._last_seen_ns.get(tag)
            self._last_seen_ns[tag] = seen_ns
            old = self._last_values.get(tag, _UNSET)
            if old is _UNSET:
                # First read — record without logging as a change
                self._last_values[tag] = value
            elif str(value) != str(old):
                window = None
                if previous_ns is not None:
                    window = max(0, round((seen_ns - previous_ns) / 1_000_000))
                self.logger.log(EV_PLC_CHANGED, plc_label,
                                tag_or_addr=tag,
                                old_value=old,
                                new_value=value,
                                ts_ns=seen_ns,
                                window_ms=window)
                self._last_values[tag] = value
