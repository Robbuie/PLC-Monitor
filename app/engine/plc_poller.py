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
from app.engine.tagspec import SpecError, TagSpec

#: Marks a tag that has not been read yet. A sentinel object rather than a
#: string, so a PLC string tag whose value happens to be that text still logs.
_UNSET = object()

#: Every tag failing for this long, after reads had worked, means the
#: connection is gone rather than a tag being wrong: reconnect.
RECONNECT_AFTER_S = 3.0
#: Pauses between reconnect attempts; the last repeats until it works.
RECONNECT_BACKOFF_S = (2.0, 5.0, 10.0, 15.0)


class PLCPoller:
    def __init__(self, driver: PLCDriver, tags: list, poll_ms: int,
                 logger: EventLogger,
                 status_cb: Callable[[str], None] | None = None,
                 tag_update_cb: Callable[[str, object], None] | None = None,
                 state_cb: Callable[[str, str], None] | None = None):
        """
        driver        – connected PLCDriver instance
        tags          – the tags to monitor: TagSpec objects, or plain
                        address strings (each one register, as read)
        poll_ms       – polling interval in milliseconds
        logger        – EventLogger instance
        status_cb     – called with status strings (for GUI status bar)
        tag_update_cb – called with (tag_name, current_value) on every poll
        state_cb      – called with ("reconnecting" | "on", detail) while it
                        reconnects after losing the PLC, and when it is back
        """
        self.driver       = driver
        self.specs        = _as_specs(tags)
        self.poll_ms      = poll_ms
        self.logger       = logger
        self.status_cb    = status_cb or (lambda m: None)
        self.tag_update_cb = tag_update_cb or (lambda t, v: None)
        self.state_cb     = state_cb or (lambda s, d: None)

        self._stop_event  = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_values: dict[str, object] = {}
        self._error_counts: dict[str, int] = {}
        # When each tag was last read successfully, for the change window.
        self._last_seen_ns: dict[str, int] = {}
        #: How long the last full poll took, in ms.
        self.last_poll_ms: float | None = None
        # Reconnect bookkeeping: whether any read has worked on this
        # connection, and since when every read has failed.
        self._had_success = False
        self._failing_since: float | None = None
        #: How many times the connection has been re-established.
        self.reconnects = 0

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def tags(self) -> list[str]:
        """The names the tags are logged under."""
        return [spec.key for spec in self.specs]

    def update_tags(self, tags: list):
        """Hot-update the tag list without restarting."""
        specs = _as_specs(tags)
        keys = {spec.key for spec in specs}
        for t in list(self._last_values.keys()):
            if t not in keys:
                del self._last_values[t]
                self._last_seen_ns.pop(t, None)
        self.specs = specs

    def update_poll_ms(self, poll_ms: int):
        self.poll_ms = poll_ms

    def _poll_loop(self):
        plc_label = f"{self.driver.PLC_TYPE}@{getattr(self.driver, 'ip', '?')}"
        self.logger.log(EV_PLC_CONNECTED, plc_label, notes="Polling started")
        self.status_cb(f"[PLC] Polling {plc_label} — {len(self.tags)} tag(s) every {self.poll_ms}ms")

        while not self._stop_event.is_set():
            t0 = time.perf_counter()
            try:
                good, bad = self.poll_once(plc_label)
                self._watch_connection(good, bad, plc_label)
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

    def poll_once(self, plc_label: str) -> tuple[int, int]:
        """Read every tag once and log what changed. Returns how many tags
        answered and how many failed.

        The tags go to the driver as one batch: a driver that can put them in
        one request (pycomm3, libplctag) answers in one network round trip
        instead of one per tag, which shortens the poll and with it the window
        in which a change can hide.
        """
        specs = list(self.specs)
        if not specs:
            return 0, 0
        good = bad = 0
        for spec, value, err, seen_ns in self._read_all(specs):
            if self._stop_event.is_set():
                break
            tag = spec.key

            if err:
                count = self._error_counts.get(tag, 0) + 1
                self._error_counts[tag] = count
                # Only log first error and every 10th to avoid log spam
                if count == 1 or count % 10 == 0:
                    self.logger.log(EV_PLC_ERROR, plc_label, tag_or_addr=tag,
                                    notes=f"Read error (x{count}): {err}")
                self.tag_update_cb(tag, f"ERROR: {err}")
                bad += 1
                # The last good read keeps its time, so after a run of
                # errors the window covers the whole stretch the value was
                # unknown -- which is the honest answer.
                continue

            self._error_counts[tag] = 0
            self.tag_update_cb(tag, value)
            good += 1

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
                                window_ms=window,
                                # Where a named or grouped tag came from, so
                                # the log stands on its own.
                                notes=spec.describe() if (spec.grouped or spec.name) else "")
                self._last_values[tag] = value
        return good, bad

    # ------------------------------------------------------------ reconnect

    def _watch_connection(self, good: int, bad: int, plc_label: str) -> None:
        """Reconnect when the PLC has stopped answering altogether.

        Before 1.3 a PLC that was power-cycled, or a cable pulled and put
        back, left the poller logging a read error every tenth poll for the
        rest of the shift: nothing ever opened a new connection. Only a
        connection that has worked counts -- a list of wrong tag names fails
        every read too, and reconnecting would not fix that.
        """
        if good:
            self._had_success = True
            self._failing_since = None
            return
        if not bad or not self._had_success:
            return
        now = time.monotonic()
        if self._failing_since is None:
            self._failing_since = now
        elif now - self._failing_since >= RECONNECT_AFTER_S:
            self._reconnect(plc_label)

    def _read_all(self, specs: list[TagSpec]) -> list[tuple]:
        """[(spec, value, error, seen_ns)] for every tag, in list order.

        Single tags go to the driver as one batch: a driver that can put them
        in one request (pycomm3, libplctag) answers in one network round trip
        instead of one per tag. A grouped tag is one block read -- its
        registers in one request, so they come from the same PLC scan -- and
        is decoded here.

        Stamped when the answer came back: that is when this program learnt
        of the value, the same moment every other row in the log is stamped
        by. A batch shares one stamp; a driver that reads tag by tag gives
        each its own (the optional third item), so the first tag of a slow
        poll is not stamped as late as the last.
        """
        plain = [s for s in specs if not s.grouped]
        results: dict[str, tuple] = {}
        if plain:
            addresses = list(dict.fromkeys(s.address for s in plain))
            reader = getattr(self.driver, "read_tags", None)
            if reader is not None:
                results = reader(addresses)
            else:  # a driver from outside this module that only reads one at a time
                for address in addresses:
                    value, err = self.driver.read_tag(address)
                    results[address] = (value, err, clock.now_ns())
        batch_ns = clock.now_ns()
        out = []
        block = getattr(self.driver, "read_block", None)
        for spec in specs:
            if not spec.grouped:
                value, err, *stamp = results.get(spec.address, (None, "No answer for this tag"))
                out.append((spec, value, err, stamp[0] if stamp and stamp[0] else batch_ns))
                continue
            if block is None:
                out.append((spec, None, "This PLC driver cannot read grouped tags.", clock.now_ns()))
                continue
            words, err = block(spec.address, spec.count)
            seen = clock.now_ns()
            value = None
            if not err:
                try:
                    value = spec.decode(words)
                except (SpecError, ValueError, TypeError) as exc:
                    err = f"Could not decode {spec.describe()}: {exc}"
            out.append((spec, value, err, seen))
        return out

    def _probe(self) -> tuple[bool, str]:
        try:
            results = self._read_all(list(self.specs))
        except Exception as exc:
            return False, str(exc)
        errors = [r[2] for r in results if r[2]]
        if len(errors) < len(results):
            return True, ""
        first = (errors[0] if errors else "no answer").splitlines()[0]
        return False, f"Connected, but no tag answered: {first}"

    def _reconnect(self, plc_label: str) -> None:
        lost_at = self._failing_since or time.monotonic()
        self.logger.log(EV_PLC_ERROR, plc_label,
                        notes="No tag has answered for "
                              f"{time.monotonic() - lost_at:.0f} s; reconnecting")
        self.status_cb(f"[PLC] Lost {plc_label}; reconnecting")
        attempt = 0
        last_err = None
        while not self._stop_event.is_set():
            self.state_cb("reconnecting", f"Lost {plc_label}; reconnecting")
            try:
                self.driver.disconnect()
            except Exception:
                pass
            try:
                ok, err = self.driver.connect()
            except Exception as exc:  # a driver that raises instead of returning
                ok, err = False, str(exc)
            if ok and not self._stop_event.is_set():
                # A connect that succeeded proves little on its own: the
                # libplctag driver's connect only loads the library, and a
                # gateway can accept a session while the CPU behind it is
                # still booting. A read that answers is the proof.
                ok, err = self._probe()
            if ok and self._stop_event.is_set():
                # Stopped while connecting. The owner may already have called
                # disconnect and moved on; close what was just opened.
                try:
                    self.driver.disconnect()
                except Exception:
                    pass
                return
            if ok:
                self.reconnects += 1
                self._failing_since = None
                self.logger.log(EV_PLC_CONNECTED, plc_label,
                                notes=f"Reconnected after {time.monotonic() - lost_at:.0f} s")
                self.status_cb(f"[PLC] Reconnected to {plc_label}")
                self.state_cb("on", plc_label)
                return
            if err != last_err:
                self.logger.log(EV_PLC_ERROR, plc_label, notes=f"Reconnect failed: {err}")
                last_err = err
            pause = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
            attempt += 1
            self._stop_event.wait(pause)


def _as_specs(tags) -> list[TagSpec]:
    return [t if isinstance(t, TagSpec) else TagSpec(address=str(t)) for t in tags or []]
