"""Entry point: the window, the headless runner, or the Windows service.

    python -m app                 the window
    python -m app headless        monitors only, in this console, until Ctrl-C
    python -m app --config PATH   either of the above with another config file
    PLCMonitor.exe service        what the service control manager runs

The service and headless paths never import Qt, so the service starts quickly
and does not depend on a display.
"""

from __future__ import annotations

import argparse
import os
import sys


def _console_for_frozen_gui() -> None:
    """A windowed PyInstaller exe starts with no stdout. When it is run as the
    headless runner in a console of its own, attach to that console so the log
    lines appear somewhere."""
    if sys.stdout is not None or os.name != "nt":
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):
            ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["service"]:
        from app import service
        return service.host()

    parser = argparse.ArgumentParser(prog="PLCMonitor", description="PLC Monitor")
    parser.add_argument("mode", nargs="?", default="gui", choices=["gui", "headless"])
    parser.add_argument("--config", help="config file (default: the shared one in ProgramData)")
    args = parser.parse_args(argv)

    if args.mode == "headless":
        _console_for_frozen_gui()
        from app.engine.headless import run
        return run(config_path=args.config)

    return gui(args.config)


def gui(config_path: str | None = None) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    from app import APP_NAME, paths
    from app.core.settings import Settings

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Robbuie.PLCMonitor")
        except Exception:
            pass
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Robbuie")
    icon = os.path.join(paths.program_folder(), "assets", "icon.png")
    if getattr(sys, "frozen", False):
        icon = os.path.join(getattr(sys, "_MEIPASS", paths.program_folder()), "assets", "icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))

    from app.ui.session import Session
    from app.ui.window import MainWindow

    settings = Settings()
    session = Session(config_path)
    window = MainWindow(session, settings)
    window.show_restored()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
