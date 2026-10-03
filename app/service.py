"""The Windows service: hosting it, and installing, starting and stopping it.

Two halves.

**Hosting.** `PLCMonitorService` is what the service control manager talks to.
It runs `app.engine.headless.run` until told to stop. In the installed build
the service's command line is `PLCMonitor.exe service`, and `app/__main__.py`
hands that straight to `host()` -- which is the part that was missing before
1.0: the exe was registered as a service but never told Windows it had started,
so the service manager killed it after thirty seconds with error 1053. From
source, pywin32's own `pythonservice.exe` hosts the class instead, through
`windows_service.py` at the repository root.

**Control.** Install, start, stop, remove and status, for the Service page.
All of them are `sc.exe`. Everything except status needs an administrator, so
when the window is not elevated each one runs through a single UAC prompt
(`run_elevated`) rather than failing with "access denied" and leaving the
person to work out why.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time

SERVICE_NAME = "PLCMonitorService"
DISPLAY_NAME = "PLC Monitor Service"
DESCRIPTION = ("Polls PLC tags and watches socket traffic, logging every change "
               "with a millisecond timestamp for latency analysis.")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# pythonservice.exe imports this file by path, with only its own folder on
# sys.path; the repository root has to be added for `app` to import.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


if HAS_WIN32:
    class PLCMonitorService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = DISPLAY_NAME
        _svc_description_ = DESCRIPTION

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = threading.Event()
            self._wait_stop = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):  # noqa: N802 - pywin32 naming
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop_event.set()
            win32event.SetEvent(self._wait_stop)

        def SvcDoRun(self):  # noqa: N802
            servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                                  servicemanager.PYS_SERVICE_STARTED,
                                  (self._svc_name_, ""))
            self._configure_file_logging()
            try:
                from app.engine.headless import run
                run(stop_event=self._stop_event)
            except Exception as exc:  # pragma: no cover - reported to Event Viewer
                servicemanager.LogErrorMsg(f"{SERVICE_NAME} error: {exc!r}")
                raise
            servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                                  servicemanager.PYS_SERVICE_STOPPED,
                                  (self._svc_name_, ""))

        @staticmethod
        def _configure_file_logging() -> None:
            """A service has no console, so its own messages go to a file
            beside the logs -- the first place anybody looks when it is not
            doing what they expected."""
            import logging
            from app import paths
            try:
                os.makedirs(paths.log_dir(), exist_ok=True)
                handler = logging.FileHandler(os.path.join(paths.log_dir(), "service.log"),
                                              encoding="utf-8")
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s.%(msecs)03d  %(levelname)-8s  %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S"))
                root = logging.getLogger()
                root.addHandler(handler)
                root.setLevel(logging.INFO)
            except OSError:
                pass


def host() -> int:
    """Called as `PLCMonitor.exe service` by the service control manager."""
    if not HAS_WIN32:
        print("pywin32 is not available; the service cannot be hosted.")
        return 1
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(PLCMonitorService)
    servicemanager.StartServiceCtrlDispatcher()
    return 0


# --------------------------------------------------------------------------
# Control
# --------------------------------------------------------------------------


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_commands() -> list[list[str]]:
    """The commands that register the service, in order."""
    if _frozen():
        exe = os.path.abspath(sys.executable)
        return [
            ["sc", "create", SERVICE_NAME, "binPath=", f'"{exe}" service',
             "DisplayName=", DISPLAY_NAME, "start=", "auto"],
            ["sc", "description", SERVICE_NAME, DESCRIPTION],
            # Restart after a crash, a minute apart. A monitor that died at
            # 02:00 and stayed down is the failure this service is meant to
            # prevent.
            ["sc", "failure", SERVICE_NAME, "reset=", "86400",
             "actions=", "restart/60000/restart/60000/restart/60000"],
        ]
    script = os.path.join(_ROOT, "windows_service.py")
    return [[sys.executable, script, "--startup", "auto", "install"],
            ["sc", "failure", SERVICE_NAME, "reset=", "86400",
             "actions=", "restart/60000/restart/60000/restart/60000"]]


def remove_commands() -> list[list[str]]:
    return [["sc", "stop", SERVICE_NAME], ["sc", "delete", SERVICE_NAME]]


def start_commands() -> list[list[str]]:
    return [["sc", "start", SERVICE_NAME]]


def stop_commands() -> list[list[str]]:
    return [["sc", "stop", SERVICE_NAME]]


def run_commands(commands: list[list[str]]) -> tuple[bool, str]:
    """Run them here if this process may, or through one UAC prompt if not."""
    if os.name != "nt":
        return False, "The Windows service is only available on Windows."
    if is_admin():
        out, ok = [], True
        for command in commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True,
                                        timeout=30, creationflags=_NO_WINDOW)
                out.append(f"> {' '.join(command)}\n{result.stdout}{result.stderr}".rstrip())
                ok = ok and result.returncode == 0
            except (OSError, subprocess.SubprocessError) as exc:
                out.append(f"> {' '.join(command)}\n{exc}")
                ok = False
        return ok, "\n\n".join(out)
    return run_elevated(commands)


def _quote(arg: str) -> str:
    if not arg or any(ch in arg for ch in ' "\t&|<>^'):
        return '"' + arg.replace('"', '""') + '"'
    return arg


def run_elevated(commands: list[list[str]]) -> tuple[bool, str]:
    """One UAC prompt for the lot, with the output brought back.

    An elevated process cannot hand its output to an unelevated parent over a
    pipe, so a small batch file runs the commands and writes everything to a
    file beside it, and this waits for that process and reads the file.
    """
    import ctypes
    from ctypes import wintypes

    folder = tempfile.mkdtemp(prefix="plcmon-")
    out_path = os.path.join(folder, "out.txt")
    script = os.path.join(folder, "run.cmd")
    lines = ["@echo off", f'set OUT="{out_path}"']
    for command in commands:
        line = " ".join(_quote(part) for part in command)
        lines.append(f"echo ^> {line.replace('>', '^>')} >> %OUT%")
        lines.append(f"{line} >> %OUT% 2>&1")
        lines.append("echo. >> %OUT%")
    with open(script, "w", encoding="mbcs" if os.name == "nt" else "utf-8") as handle:
        handle.write("\r\n".join(lines) + "\r\n")

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    info.lpParameters = f'/c "{script}"'
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        return False, "Windows did not run it as administrator (the prompt was declined or failed)."
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 60_000)
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    time.sleep(0.1)
    try:
        with open(out_path, "r", encoding="mbcs", errors="replace") as handle:
            text = handle.read().strip()
    except OSError:
        text = ""
    return code.value == 0, text or "(no output)"


def query() -> tuple[str, str]:
    """(state, raw output). state is RUNNING, STOPPED, ..., or NOT_INSTALLED."""
    if os.name != "nt":
        return "UNAVAILABLE", "The Windows service is only available on Windows."
    try:
        result = subprocess.run(["sc", "query", SERVICE_NAME], capture_output=True,
                                text=True, timeout=10, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return "UNKNOWN", str(exc)
    text = (result.stdout or "") + (result.stderr or "")
    if "1060" in text:
        return "NOT_INSTALLED", text.strip()
    for line in text.splitlines():
        if "STATE" in line and ":" in line:
            parts = line.split(":", 1)[1].split()
            if len(parts) >= 2:
                return parts[1], text.strip()
    return "UNKNOWN", text.strip()
