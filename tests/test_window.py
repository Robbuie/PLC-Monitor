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


def test_mouse_moves_over_every_page_raise_nothing(isolated_data):
    """1.0.0 shipped with a page attribute called `window` that hid
    QWidget.window(), and the window's edge-resize filter raised on every
    mouse move. Offscreen there is no mouse, so it is sent one."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import sys
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    from app.core.settings import Settings
    from app.ui.session import Session
    from app.ui.window import MainWindow

    errors = []
    old_hook = sys.excepthook
    sys.excepthook = lambda *exc: errors.append(exc)
    try:
        window = MainWindow(Session(), Settings(os.path.join(isolated_data, "ui.json")))
        window.show()
        for key, page in window.pages.items():
            window.show_page(key)
            for widget in [page, *page.findChildren(QWidget)]:
                event = QMouseEvent(QEvent.MouseMove, QPointF(2, 2), QPointF(2, 2),
                                    Qt.NoButton, Qt.NoButton, Qt.NoModifier)
                QApplication.sendEvent(widget, event)
        app.processEvents()
        window.session.shutdown()
    finally:
        sys.excepthook = old_hook
    assert errors == []


def test_no_attribute_hides_a_qt_method():
    """`self.window = ...` or `self.size = ...` in a widget silently breaks
    every Python caller of that Qt method. Read from the source, since every
    class under app/ui is a widget or lives beside one."""
    import re
    from PySide6.QtWidgets import QMainWindow
    qt_names = {n for n in dir(QMainWindow) if not n.startswith("_")}
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "ui")
    assign = re.compile(r"\bself\.([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=(?!=)")
    clashes = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(base, name), encoding="utf-8") as handle:
                    for number, line in enumerate(handle, 1):
                        for attr in assign.findall(line):
                            if attr in qt_names:
                                clashes.append(f"{name}:{number} self.{attr}")
    assert clashes == []


def test_reconnect_states_reach_the_chip_and_stale_ones_are_dropped(isolated_data):
    """The poller reports "reconnecting" and "on" from its own thread. Once
    Disconnect has been pressed, a late report from that poller must not
    light the chip again."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.core.settings import Settings
    from app.ui.session import Session
    from app.ui.window import MainWindow

    session = Session()
    window = MainWindow(session, Settings(os.path.join(isolated_data, "ui.json")))
    seen = []
    session.plcState.connect(lambda state, detail: seen.append(state))

    class Current:
        pass

    current = Current()
    session.plc_poller = current
    session.plc_detail = "Fake at 1.2.3.4"
    session._pollerState.emit(current, "reconnecting", "Lost Fake; reconnecting")
    app.processEvents()
    assert seen[-1] == "reconnecting"
    assert "reconnecting" in window.titlebar.plc_chip.text()
    assert window.a_plc_off.isEnabled() and not window.a_plc.isEnabled()
    tags = window.pages["tags"]
    assert tags.disconnect_btn.isEnabled() and not tags.connect_btn.isEnabled()

    session._pollerState.emit(current, "on", "")
    app.processEvents()
    assert seen[-1] == "on"

    session.plc_poller = None                    # Disconnect pressed
    session._pollerState.emit(current, "on", "")
    app.processEvents()
    assert seen[-1] == "on" and len(seen) == 2   # the stale report was dropped
    session.shutdown()
    window.close()
