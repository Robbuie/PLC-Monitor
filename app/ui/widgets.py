"""The handful of building blocks every page is made of.

Cards, a form grid, a segmented choice, a switch, a banner. Each one is drawn
by the stylesheet through a `role` property rather than by setting colours on
it, so a theme change re-renders all of them and none of them carries a colour
of its own.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.theme import sheet
from app.ui import glyphs


def set_prop(widget: QWidget, name: str, value) -> None:
    """Set a style property and make the sheet look at it again."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def icon(name: str, size: int = 16, tone: str = "txt_1"):
    t = sheet.tokens()
    return glyphs.icon(name, colour=t[tone], muted=t["txt_2"], size=size, ratio=2.0)


def label(text: str = "", role: str | None = None, *, wrap: bool = False,
          selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    if wrap:
        widget.setWordWrap(True)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return widget


def button(text: str, role: str | None = None, *, icon_name: str | None = None,
           tip: str | None = None, slot=None) -> QPushButton:
    widget = QPushButton(text)
    if role:
        widget.setProperty("role", role)
    if icon_name:
        widget.setProperty("glyph", icon_name)
        widget.setIcon(icon(icon_name, 14, "bg_0" if role == "primary" else "txt_1"))
    if tip:
        widget.setToolTip(tip)
    if slot is not None:
        widget.clicked.connect(slot)
    widget.setCursor(Qt.PointingHandCursor)
    return widget


def tool(icon_name: str, tip: str, slot=None) -> QToolButton:
    widget = QToolButton()
    widget.setProperty("role", "nav")
    widget.setProperty("glyph", icon_name)
    widget.setIcon(icon(icon_name, 16))
    widget.setToolTip(tip)
    widget.setAccessibleName(tip)
    widget.setCursor(Qt.PointingHandCursor)
    if slot is not None:
        widget.clicked.connect(slot)
    return widget


def refresh_icons(root: QWidget) -> None:
    """Re-tint every glyph under `root` after a theme change."""
    for widget in root.findChildren(QAbstractButton):
        name = widget.property("glyph")
        if not name:
            continue
        tone = "bg_0" if widget.property("role") == "primary" else "txt_1"
        size = 16 if widget.property("role") == "nav" else 14
        widget.setIcon(icon(str(name), size, tone))


def rule() -> QFrame:
    line = QFrame()
    line.setProperty("role", "rule")
    return line


class Card(QFrame):
    """A raised surface with an optional title and a row of actions beside it."""

    def __init__(self, title: str = "", parent: QWidget | None = None, *,
                 margins: tuple[int, int, int, int] = (16, 14, 16, 16), spacing: int = 10) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*margins)
        outer.setSpacing(spacing)
        self.header = QHBoxLayout()
        self.header.setSpacing(6)
        self.title = label(title.upper(), "cardtitle")
        if title:
            self.header.addWidget(self.title)
        self.header.addStretch(1)
        if title:
            outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(spacing)
        outer.addLayout(self.body, 1)

    def add_action(self, widget: QWidget) -> None:
        self.header.addWidget(widget)


class Form(QWidget):
    """Label on the left, field on the right, rows that can be hidden.

    Rows are hidden rather than rebuilt when the driver or the mode changes,
    so a value typed into a field survives switching away and back.
    """

    def __init__(self, parent: QWidget | None = None, *, label_width: int = 120) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnMinimumWidth(0, label_width)
        self.grid.setColumnStretch(1, 1)
        self._rows: dict[str, list[QWidget]] = {}
        self._next = 0

    def add(self, key: str, text: str, field: QWidget, hint: str = "",
            tip: str = "") -> QWidget:
        for box in [field, *field.findChildren(QComboBox)]:
            if isinstance(box, QComboBox):
                # A combo sized to its longest entry -- an adapter's full name --
                # would set the card's minimum width.
                box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                box.setMinimumContentsLength(8)
        name = label(text, "fieldlabel")
        name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.grid.addWidget(name, self._next, 0)
        self.grid.addWidget(field, self._next, 1)
        widgets = [name, field]
        if tip:
            field.setToolTip(tip)
            name.setToolTip(tip)
        self._next += 1
        if hint:
            note = label(hint, "hint", wrap=True)
            self.grid.addWidget(note, self._next, 1)
            widgets.append(note)
            self._next += 1
        self._rows[key] = widgets
        return field

    def add_full(self, key: str, widget: QWidget) -> QWidget:
        self.grid.addWidget(widget, self._next, 0, 1, 2)
        self._rows[key] = [widget]
        self._next += 1
        return widget

    def show_row(self, key: str, visible: bool) -> None:
        for widget in self._rows.get(key, []):
            widget.setVisible(visible)

    def enable_row(self, key: str, enabled: bool) -> None:
        for widget in self._rows.get(key, []):
            widget.setEnabled(enabled)


def row(*widgets, stretch_at: int | None = None, spacing: int = 8) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for index, widget in enumerate(widgets):
        if widget is None:
            layout.addStretch(1)
        elif isinstance(widget, int):
            layout.addSpacing(widget)
        elif hasattr(widget, "addWidget") and not isinstance(widget, QWidget):
            layout.addLayout(widget)
        else:
            layout.addWidget(widget, 1 if stretch_at == index else 0)
    return layout


def hbox(*widgets, stretch_at: int | None = None, spacing: int = 8) -> QWidget:
    holder = QWidget()
    holder.setLayout(row(*widgets, stretch_at=stretch_at, spacing=spacing))
    return holder


class Segments(QWidget):
    """Mutually exclusive choices as one control: the radio buttons' replacement."""

    changed = Signal(str)

    def __init__(self, options: Iterable[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "segments")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, text in options:
            choice = QPushButton(text)
            choice.setProperty("role", "segment")
            choice.setCheckable(True)
            choice.setCursor(Qt.PointingHandCursor)
            choice.setMinimumWidth(choice.sizeHint().width() + 8)
            choice.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(choice)
            layout.addWidget(choice)
            self._buttons[key] = choice
        if self._buttons:
            next(iter(self._buttons.values())).setChecked(True)

    def value(self) -> str:
        for key, choice in self._buttons.items():
            if choice.isChecked():
                return key
        return ""

    def set_value(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)

    def set_tip(self, key: str, text: str) -> None:
        if key in self._buttons:
            self._buttons[key].setToolTip(text)


class Switch(QAbstractButton):
    """An on/off switch, painted from the tokens. From File Manager's Options."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(40, 22)

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = sheet.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        enabled = self.isEnabled()
        track = QRectF(0.5, 0.5, 39, 21)
        edge = QColor(t["accent_text"] if self.hasFocus() else (t["accent"] if on else t["line"]))
        painter.setPen(edge)
        fill = QColor(t["accent"] if on else t["bg_4"])
        if not enabled:
            fill.setAlphaF(0.4)
        painter.setBrush(fill)
        painter.drawRoundedRect(track, 10.5, 10.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(t["bg_0"] if on else t["txt_1"]))
        x = 21 if on else 4
        painter.drawEllipse(QRectF(x, 4, 14, 14))
        painter.end()


class Banner(QFrame):
    """A sentence across the top of a page that something needs attention."""

    def __init__(self, text: str = "", tone: str = "warn", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "banner")
        self.setProperty("tone", tone)
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        self.text = label(text, wrap=True)
        layout.addWidget(self.text, 1)
        self.actions = QHBoxLayout()
        layout.addLayout(self.actions)

    def set(self, text: str, tone: str | None = None) -> None:
        self.text.setText(text)
        if tone:
            set_prop(self, "tone", tone)
        self.setVisible(bool(text))


def chip(text: str = "", tone: str = "idle") -> QLabel:
    widget = QLabel(text)
    widget.setProperty("role", "chip")
    widget.setProperty("tone", tone)
    return widget


def set_chip(widget: QLabel, text: str, tone: str) -> None:
    widget.setText(text)
    set_prop(widget, "tone", tone)


def big_stat(caption: str) -> tuple[QWidget, QLabel]:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    value = label("-", "big")
    layout.addWidget(value)
    layout.addWidget(label(caption.upper(), "bigcaption"))
    return holder, value


# --------------------------------------------------------------- messages

def _box(parent, icon_kind, title: str, text: str, detail: str = "") -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(icon_kind)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    return box


def info(parent, title: str, text: str, detail: str = "") -> None:
    _box(parent, QMessageBox.Information, title, text, detail).exec()


def error(parent, title: str, text: str, detail: str = "") -> None:
    _box(parent, QMessageBox.Warning, title, text, detail).exec()


def ask(parent, title: str, text: str, detail: str = "", yes: str = "Continue") -> bool:
    box = _box(parent, QMessageBox.Question, title, text, detail)
    go = box.addButton(yes, QMessageBox.AcceptRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(go)
    box.exec()
    return box.clickedButton() is go
