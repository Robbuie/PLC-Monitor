"""The window builds, every page shows, and a theme change reaches them all.

Says nothing about a PLC: nothing answers here."""

import os

import pytest

pytest.importorskip("PySide6")


def test_every_page_shows_in_every_theme(isolated_data):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.core.settings import Settings
    from app.ui.session import Session
    from app.ui.window import MainWindow

    settings = Settings(os.path.join(isolated_data, "ui.json"))
    window = MainWindow(Session(), settings)
    window.show()
    for theme in ("dark", "light", "control", "contrast"):
        settings.set("theme", theme)
        window.apply_theme()
        for key in window.pages:
            window.show_page(key)
            app.processEvents()
    window.save_config()
    window.session.shutdown()
    window.close()
