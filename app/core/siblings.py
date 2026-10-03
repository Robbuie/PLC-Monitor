"""The other applications in the family, when they happen to be installed.

PLC Monitor has to stand on its own: it goes onto customer PCs that will never
have NetControl or File Manager. So nothing here is required, nothing is
assumed, and every caller has a plain path for when the answer is None. When a
sibling *is* there, a button appears that hands the job to the tool that does
it better:

  * **NetControl** for anything about a device on the network -- whether it
    answers, what it is, its link and error counters. Its service check is
    protocol-verified and gentle by rule; PLC Monitor's scanner is kept for the
    machine without it.
  * **File Manager** for the log folder, which is where somebody goes to copy
    a day's archives onto a stick.

Found through each installer's own uninstall key, the same way each
application finds itself for its updater, rather than by guessing a path: the
per-user install and the all-users one land in different places.

NetControl takes no command-line target yet, so the address is put on the
clipboard and the status line says so. When it learns `--target`, `ARGS` is
the one place to change.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Sibling:
    name: str
    app_id: str
    exe: str

    @property
    def uninstall_key(self) -> str:
        return rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{self.app_id}_is1"


NETCONTROL = Sibling("NetControl", "{B2934E15-79BE-411A-9305-543B01455706}", "NetControl.exe")
FILE_MANAGER = Sibling("File Manager", "{8B4A17D2-3C61-4F0E-9E5B-2A7D6C914F83}", "FileManager.exe")

_cache: dict[str, str | None] = {}


def location(sibling: Sibling) -> str | None:
    """The sibling's exe, or None if it is not installed on this machine."""
    if sibling.app_id in _cache:
        return _cache[sibling.app_id]
    found = None
    if os.name == "nt":
        try:
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, sibling.uninstall_key) as key:
                        folder, _ = winreg.QueryValueEx(key, "InstallLocation")
                except OSError:
                    continue
                candidate = os.path.join(str(folder), sibling.exe)
                if os.path.isfile(candidate):
                    found = candidate
                    break
        except ImportError:
            pass
    _cache[sibling.app_id] = found
    return found


def forget() -> None:
    _cache.clear()


def launch(sibling: Sibling, *args: str) -> bool:
    exe = location(sibling)
    if not exe:
        return False
    try:
        subprocess.Popen([exe, *args], close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return True
    except OSError:
        return False
