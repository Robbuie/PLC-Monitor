"""Where PLC Monitor keeps things, and the one-time move from where it used to.

Two kinds of file, kept apart on purpose:

  * **Site data** -- the monitor configuration, the CSV logs, a downloaded
    plctag.dll. The window and the Windows service both read and write these,
    and the service runs as LocalSystem, which has no user profile worth the
    name. So they live in `%PROGRAMDATA%\\PLC Monitor`, the one folder both can
    reach. Before 1.0 they lived beside the script or the exe, which an update
    replaces and which a per-user install puts somewhere the service would have
    to be told about.
  * **Personal preferences** -- theme, accent, window size, the page that was
    open. These are one person's and go in `%LOCALAPPDATA%\\PLC Monitor`, the
    same place NetControl keeps its appearance file.

Nothing here imports Qt, because the service and the headless runner use it.

`PLC_MONITOR_DATA` overrides the site folder; the tests and a portable copy on
a stick both use it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

APP_DIR_NAME = "PLC Monitor"
CONFIG_NAME = "plc_monitor_config.json"
LOG_NAME = "plc_monitor_log.csv"

#: Environment overrides. CONFIG_ENV is the one windows_service.py has always
#: honoured, kept so an existing service install does not lose its config.
DATA_ENV = "PLC_MONITOR_DATA"
CONFIG_ENV = "PLC_MONITOR_CONFIG"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def program_folder() -> str:
    """The folder the running program lives in: the exe's, or the source root."""
    if frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    override = os.environ.get(DATA_ENV)
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP_DIR_NAME)


def user_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_NAME)


def config_path() -> str:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return override
    return os.path.join(data_dir(), CONFIG_NAME)


def log_dir() -> str:
    return os.path.join(data_dir(), "logs")


def default_log_path() -> str:
    return os.path.join(log_dir(), LOG_NAME)


def lib_dir() -> str:
    """Where Download puts plctag.dll. Survives updates, unlike the app folder."""
    return os.path.join(data_dir(), "lib")


def ui_settings_path() -> str:
    return os.path.join(user_dir(), "ui.json")


def ensure_data_dir() -> str:
    """Create the site folder, and make it writable by every local user.

    `%PROGRAMDATA%` lets an ordinary user create a folder, but a file the
    service later creates inside it belongs to SYSTEM and would be read-only to
    the window -- so the window could no longer rotate a log the service wrote.
    The folder's creator owns it and may change its access list, so the grant
    is made once, here, at creation, with no elevation. If it fails the folder
    still works for whoever made it; the grant is a convenience for sharing it
    with the service, not a precondition.
    """
    folder = data_dir()
    if os.path.isdir(folder):
        return folder
    os.makedirs(folder, exist_ok=True)
    if os.name == "nt" and not os.environ.get(DATA_ENV):
        try:
            # S-1-5-32-545 is BUILTIN\Users, named by SID so a localised
            # Windows that calls the group something else still matches.
            subprocess.run(
                ["icacls", folder, "/grant", "*S-1-5-32-545:(OI)(CI)M"],
                capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return folder


def legacy_config_candidates() -> list[str]:
    """Where a pre-1.0 build kept its config: beside main.py or the exe."""
    places = [program_folder(), os.path.join(program_folder(), "plc_monitor"),
              os.getcwd()]
    seen: list[str] = []
    for place in places:
        path = os.path.join(place, CONFIG_NAME)
        if path not in seen:
            seen.append(path)
    return seen


def migrate_legacy_config() -> str | None:
    """Copy a pre-1.0 config into the site folder, once. Returns what was copied.

    Copied, not moved: the old build may still be on the machine and still
    running, and taking its config away would break it for no gain. Only when
    there is no config in the new place yet, so it can never overwrite one.
    """
    target = config_path()
    if os.path.exists(target):
        return None
    for candidate in legacy_config_candidates():
        if os.path.isfile(candidate) and os.path.abspath(candidate) != os.path.abspath(target):
            try:
                ensure_data_dir()
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(candidate, target)
                return candidate
            except OSError:
                return None
    return None
