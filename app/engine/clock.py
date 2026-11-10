"""The one clock every log row is stamped with.

Before Python 3.13, `time.time()` and `datetime.now()` on Windows read
`GetSystemTimeAsFileTime`, which only moves when the system timer ticks --
every 15.6 ms on a default install. A tool that reports latency to the
millisecond cannot stamp with that: two events 10 ms apart could share a
timestamp, and every measurement would come out in 15-16 ms steps.

Python 3.13 switched to `GetSystemTimePreciseAsFileTime` (microseconds). This
module calls that function directly on older Pythons, so the stamp is precise
however the program was built, and falls back to `time.time_ns()` anywhere it
is unavailable (other platforms, Windows 7, a ctypes failure).

No Qt here: the service uses it.
"""

from __future__ import annotations

import sys
import time

#: 100-ns intervals between 1601-01-01 (FILETIME's epoch) and 1970-01-01.
_EPOCH_DIFF_100NS = 116_444_736_000_000_000


def _precise_windows_clock():
    """A callable returning epoch nanoseconds from the precise Windows clock,
    or None when it cannot be had."""
    if sys.platform != "win32" or sys.version_info >= (3, 13):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        func = ctypes.windll.kernel32.GetSystemTimePreciseAsFileTime
        func.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
        func.restype = None
        def now_ns() -> int:
            # A fresh buffer per call: the poller, the sniffer and the socket
            # threads all stamp at once, and a shared one could be read
            # half-overwritten by another thread.
            filetime = wintypes.FILETIME()
            func(ctypes.byref(filetime))
            ticks = (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime
            return (ticks - _EPOCH_DIFF_100NS) * 100

        # One call to prove it works, and that it agrees with the coarse clock
        # to within a second -- a wrong epoch here would silently shift every
        # timestamp in every log.
        if abs(now_ns() - time.time_ns()) > 1_000_000_000:
            return None
        return now_ns
    except Exception:
        return None


_precise = _precise_windows_clock()

#: Which clock is in use, for the log's start-up line.
SOURCE = "GetSystemTimePreciseAsFileTime" if _precise else "time.time_ns"


def now_ns() -> int:
    """Wall-clock time as integer nanoseconds since the Unix epoch."""
    if _precise is not None:
        return _precise()
    return time.time_ns()


def now_ms() -> int:
    return now_ns() // 1_000_000
