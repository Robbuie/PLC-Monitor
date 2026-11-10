"""Saying "PLC Monitor is running here" to its own installer.

An update replaces the program's files. Before 1.5.2 the installer deleted the
old `_internal` folder first and then copied the new one -- and if a copy of
PLC Monitor was still running (the window a moment after it closed, a headless
runner, or the Windows service), the copy failed half way and left a program
that could not start: "Failed to start embedded python interpreter!".

So every running copy holds a named mutex, and the installer
(packaging/installer.iss, InitializeSetup) waits for it to go away before it
touches anything, and gives up cleanly -- nothing changed -- if it does not.
The name is in both places; tests/test_packaging.py checks they agree.

Two names: one in the session's namespace (the window, a headless runner), and
one in the global namespace so a service in session 0 is seen too. Creating
the global one can be refused to a non-administrator; the local one is still
there for the installer to see.

No Qt here: the service uses it.
"""

from __future__ import annotations

import os

MUTEX_NAMES = ("PLCMonitorRunning", "Global\\PLCMonitorRunning")

_handles: list = []


def hold_running_mutex() -> int:
    """Create the mutexes and keep them for the life of the process.
    Returns how many were created. A no-op off Windows."""
    if os.name != "nt" or _handles:
        return len(_handles)
    try:
        import ctypes
        from ctypes import wintypes
        create = ctypes.windll.kernel32.CreateMutexW
        create.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        create.restype = wintypes.HANDLE
    except Exception:
        return 0
    for name in MUTEX_NAMES:
        try:
            handle = create(None, False, name)
        except Exception:
            handle = None
        if handle:
            # Never closed: Windows releases it when the process ends, which
            # is exactly the moment the installer is waiting for.
            _handles.append(handle)
    return len(_handles)
