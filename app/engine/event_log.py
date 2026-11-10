"""
event_log.py
Unified event logger with automatic log rotation and gzip compression.

Rotation triggers (whichever hits first):
  - File reaches max_bytes (default 50 MB)
  - Midnight rollover (daily files)

When a file is rotated it is immediately compressed to .gz and the
original is removed — so disk usage stays low.

Naming:
  plc_monitor_log.csv               ← current file (always this name)
  plc_monitor_log_2024-01-15.csv.gz ← previous days, compressed
"""

import csv
import gzip
import os
import queue
import shutil
import threading
from datetime import datetime, date

from app.engine import clock

EV_PLC_CHANGED   = "PLC_CHANGED"
EV_PLC_ERROR     = "PLC_ERROR"
EV_PLC_CONNECTED = "PLC_CONNECTED"
EV_PLC_DISC      = "PLC_DISCONNECTED"
EV_SOCK_RECV     = "SOCK_RECV"
EV_SOCK_SENT     = "SOCK_SENT"
EV_SOCK_CONN     = "SOCK_CONNECT"
EV_SOCK_DISC     = "SOCK_DISCONNECT"
EV_SOCK_ERROR    = "SOCK_ERROR"
EV_INFO          = "INFO"

CSV_COLUMNS = [
    "timestamp_ms", "timestamp_human", "event_type",
    "source", "tag_or_addr", "old_value", "new_value", "raw_hex", "notes",
    # Added in 1.2. For a PLC change: milliseconds between the read that saw
    # the change and the previous read of that tag. The change happened
    # somewhere inside that window, so a latency measured from this row may be
    # up to this much too short. Empty for everything else.
    "window_ms",
]

DEFAULT_MAX_BYTES   = 50 * 1024 * 1024   # 50 MB
DEFAULT_MAX_ROTATED = 30                  # keep 30 compressed files


def _compress_file(src: str) -> str | None:
    """Compress src to src.gz, delete src. Returns .gz path or None on error."""
    gz_path = src + ".gz"
    try:
        with open(src, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        os.remove(src)
        return gz_path
    except Exception:
        return None


def _header_of(path: str) -> list[str] | None:
    """The column names on the first line of a log, or None if it has none."""
    try:
        with open(path, "r", newline="", encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return None
    if not first.strip():
        return None
    return next(csv.reader([first]))


class EventLogger:
    """
    Thread-safe CSV event logger with automatic rotation and gzip compression.

    Parameters
    ----------
    log_path      : str   Path to the current (active) log file.
    gui_queue     : queue.Queue | None  If set, every row dict is put here for the GUI.
    max_bytes     : int   Rotate when the file exceeds this size. Default 50 MB.
    max_rotated   : int   Maximum number of compressed archive files to keep.
    rotate_daily  : bool  Also rotate at midnight regardless of file size.
    """

    def __init__(self, log_path: str,
                 gui_queue: "queue.Queue | None" = None,
                 max_bytes: int = DEFAULT_MAX_BYTES,
                 max_rotated: int = DEFAULT_MAX_ROTATED,
                 rotate_daily: bool = True):
        self.log_path     = log_path
        self.gui_queue    = gui_queue
        self.max_bytes    = max_bytes
        self.max_rotated  = max_rotated
        self.rotate_daily = rotate_daily

        self._lock      = threading.Lock()
        self._log_date  = date.today()
        self._ensure_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, event_type: str, source: str, tag_or_addr: str = "",
            old_value=None, new_value=None,
            raw_data: bytes = b"", notes: str = "",
            ts_ns: int | None = None, window_ms: int | None = None) -> dict:
        """Append one row. `ts_ns` stamps the row with a time taken earlier --
        the moment a packet was captured, say, rather than the moment this
        got round to writing it."""

        ns     = clock.now_ns() if ts_ns is None else int(ts_ns)
        ts_ms  = ns // 1_000_000
        # Built from the integer milliseconds rather than strftime("%f") of a
        # float, which can render .123 as .122999 and so print the wrong ms.
        now    = datetime.fromtimestamp(ts_ms // 1000)
        ts_hum = f"{now:%Y-%m-%d %H:%M:%S}.{ts_ms % 1000:03d}"

        row = {
            "timestamp_ms":    ts_ms,
            "timestamp_human": ts_hum,
            "event_type":      event_type,
            "source":          source,
            "tag_or_addr":     tag_or_addr,
            "old_value":       "" if old_value is None else str(old_value),
            "new_value":       "" if new_value is None else str(new_value),
            "raw_hex":         raw_data.hex() if raw_data else "",
            "notes":           notes,
            "window_ms":       "" if window_ms is None else int(window_ms),
        }

        with self._lock:
            self._maybe_rotate(now)
            with open(self.log_path, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerow(row)

        if self.gui_queue is not None:
            try:
                self.gui_queue.put_nowait(row)
            except queue.Full:
                pass

        return row

    def set_log_path(self, new_path: str):
        with self._lock:
            self.log_path = new_path
            self._log_date = date.today()
            self._ensure_file()

    def rotate_now(self) -> str | None:
        """Force an immediate rotation. Returns the .gz archive path or None."""
        with self._lock:
            return self._rotate(datetime.now())

    def list_archives(self) -> list[str]:
        """Return sorted list of .gz archive files alongside the current log."""
        folder = os.path.dirname(os.path.abspath(self.log_path))
        stem   = os.path.splitext(os.path.basename(self.log_path))[0]
        files  = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.startswith(stem) and f.endswith(".csv.gz")
        ]
        return sorted(files)

    def current_size_mb(self) -> float:
        try:
            return os.path.getsize(self.log_path) / (1024 * 1024)
        except OSError:
            return 0.0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_file(self):
        folder = os.path.dirname(os.path.abspath(self.log_path))
        os.makedirs(folder, exist_ok=True)
        if os.path.exists(self.log_path) and _header_of(self.log_path) not in (None, CSV_COLUMNS):
            # A log started by an older version has fewer columns. Appending
            # rows with more would shift every value under the wrong heading,
            # so that file is archived as it is and a fresh one begun.
            self._rotate(datetime.now())
        if not os.path.exists(self.log_path) or os.path.getsize(self.log_path) == 0:
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()

    def _maybe_rotate(self, now: datetime):
        """Called inside the lock. Rotates if size or day threshold met."""
        should_rotate = False

        if self.rotate_daily and now.date() != self._log_date:
            should_rotate = True

        if not should_rotate and self.max_bytes > 0:
            try:
                if os.path.getsize(self.log_path) >= self.max_bytes:
                    should_rotate = True
            except OSError:
                pass

        if should_rotate:
            self._rotate(now)

    def _rotate(self, now: datetime) -> str | None:
        """
        Rename current log to a dated archive name, compress it,
        then start a fresh current log. Called inside the lock.
        """
        if not os.path.exists(self.log_path):
            return None
        if os.path.getsize(self.log_path) == 0:
            return None

        # Build archive name: same folder, dated stem
        folder   = os.path.dirname(os.path.abspath(self.log_path))
        basename = os.path.basename(self.log_path)
        stem, _  = os.path.splitext(basename)
        ts_str   = now.strftime("%Y-%m-%d_%H-%M-%S")
        archive_csv = os.path.join(folder, f"{stem}_{ts_str}.csv")

        try:
            shutil.copy2(self.log_path, archive_csv)
            # Truncate (don't delete) the current file so any open handles survive
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()
        except Exception:
            return None

        # Compress in a background thread so we don't block logging
        def _bg_compress():
            gz = _compress_file(archive_csv)
            if gz:
                self._prune_archives()

        threading.Thread(target=_bg_compress, daemon=True).start()

        self._log_date = now.date()
        return archive_csv + ".gz"

    def _prune_archives(self):
        """Delete oldest archives beyond max_rotated."""
        if self.max_rotated <= 0:
            return
        archives = self.list_archives()
        excess   = len(archives) - self.max_rotated
        for old in archives[:excess]:
            try:
                os.remove(old)
            except OSError:
                pass
