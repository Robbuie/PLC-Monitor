"""The row at the top of the window: the mark, the menus, what is running.

The same idea as File Manager's and NetControl's title bars -- one row that
both drags the window and holds the menus, instead of a title bar and a menu
bar stacked. The middle carries two chips, PLC and socket, in the semantic
colours, because the one thing anybody walking past a monitoring PC wants to
know is whether it is monitoring.

Anything in this row that is not a control drags the window; a double-click
maximises. With the Windows title bar chosen instead (View menu), the caption
buttons hide and Windows draws its own above this row.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenuBar, QToolButton, QWidget

from app.theme import sheet
from app.ui import glyphs
from app.ui import widgets as w

HEIGHT = 42


class TitleBar(QWidget):
    minimizeRequested = Signal()
    maximizeRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "titlebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)

        self.mark = QToolButton()
        self.mark.setProperty("role", "mark")
        self.mark.setFocusPolicy(Qt.NoFocus)
        self.mark.setToolTip("PLC Monitor")
        self.name = QLabel("PLC Monitor")
        self.name.setProperty("role", "apptitle")
        self.menus = QMenuBar()
        self.menus.setNativeMenuBar(False)
        from app.ui.profiles_ui import ProfileButton
        self.profile = ProfileButton()

        self.plc_chip = w.chip("PLC  off", "idle")
        self.sock_chip = w.chip("Socket  off", "idle")
        self.svc_chip = w.chip("", "info")
        self.svc_chip.setVisible(False)

        self.min_button = self._caption("Minimize", self.minimizeRequested)
        self.max_button = self._caption("Maximize", self.maximizeRequested)
        self.close_button = self._caption("Close", self.closeRequested)
        self.close_button.setProperty("kind", "close")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.mark)
        row.addWidget(self.name)
        row.addWidget(self.menus)
        row.addWidget(self.profile)
        row.addStretch(1)
        row.addWidget(self.svc_chip)
        row.addSpacing(6)
        row.addWidget(self.plc_chip)
        row.addSpacing(6)
        row.addWidget(self.sock_chip)
        row.addSpacing(14)
        row.addWidget(self.min_button)
        row.addWidget(self.max_button)
        row.addWidget(self.close_button)
        self._maximized = False

    def _caption(self, name: str, signal) -> QToolButton:
        button = QToolButton()
        button.setProperty("role", "caption")
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setFocusPolicy(Qt.NoFocus)
        button.setFixedSize(46, HEIGHT)
        button.clicked.connect(signal)
        return button

    def set_native(self, native: bool) -> None:
        for button in (self.min_button, self.max_button, self.close_button):
            button.setVisible(not native)

    def apply_tokens(self) -> None:
        t = sheet.tokens()
        ratio = float(self.devicePixelRatioF() or 1.0)
        self.mark.setIcon(glyphs.icon("trend", colour=t["bg_0"], muted=t["bg_0"], size=15, ratio=ratio))
        self._paint_captions()

    def set_maximized(self, maximized: bool) -> None:
        self._maximized = maximized
        self.max_button.setToolTip("Restore" if maximized else "Maximize")
        self._paint_captions()

    def _paint_captions(self) -> None:
        t = sheet.tokens()
        ratio = float(self.devicePixelRatioF() or 1.0)
        for button, name in ((self.min_button, "minimize"),
                             (self.max_button, "restore" if self._maximized else "maximize"),
                             (self.close_button, "close")):
            button.setIcon(glyphs.icon(name, colour=t["txt_1"], muted=t["txt_1"], size=14, ratio=ratio))

    def _is_caption(self, pos: QPoint) -> bool:
        child = self.childAt(pos)
        return child is None or child is self or child is self.name

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._is_caption(event.position().toPoint()):
            handle = self.window().windowHandle()
            if handle is not None and (self.window().windowFlags() & Qt.FramelessWindowHint):
                handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._is_caption(event.position().toPoint()):
            if self.window().windowFlags() & Qt.FramelessWindowHint:
                self.maximizeRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
