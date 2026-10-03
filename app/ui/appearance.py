"""Appearance: theme, accent, density -- the family's three axes.

Every change applies at once and the dialog has no OK button, for File
Manager's reason: a switch that applies on OK is one somebody flips, sees no
change behind the dialog, and flips back. The themes and accents are File
Manager's, by name and by value, so a person who has set one of the family up
the way they like it can set this one the same.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
)

from app.theme import sheet
from app.theme.tokens import ACCENT_LABELS, ACCENTS, DENSITY_LABELS, THEME_LABELS, THEMES
from app.ui import widgets as w


class ThemeTile(QAbstractButton):
    """A theme drawn as a little window in its own greys."""

    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(THEME_LABELS.get(name, name))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(104, 78)

    def paintEvent(self, _event) -> None:  # noqa: N802
        g = THEMES[self.name]
        t = sheet.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(2, 2, self.width() - 4, 52)
        p.setPen(QColor(t["accent"]) if self.isChecked() else QColor(t["line"]))
        p.setBrush(QColor(g["bg_0"]))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(g["bg_2"]))
        p.drawRoundedRect(QRectF(26, 10, box.width() - 32, 38), 5, 5)
        p.setBrush(QColor(g["bg_1"]))
        p.drawRoundedRect(QRectF(8, 10, 14, 38), 4, 4)
        p.setBrush(QColor(g["txt_1"]))
        for i in range(3):
            p.drawRoundedRect(QRectF(32, 17 + i * 9, (box.width() - 48) * (0.9 - i * 0.2), 3), 1.5, 1.5)
        p.setBrush(QColor(t["accent"]))
        p.drawRoundedRect(QRectF(8, 10, 14, 6), 3, 3)
        p.setPen(QColor(t["txt_0"] if self.isChecked() else t["txt_1"]))
        p.drawText(QRectF(0, 56, self.width(), 20), Qt.AlignCenter, THEME_LABELS.get(self.name, self.name))
        p.end()


class AccentDot(QAbstractButton):
    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(ACCENT_LABELS.get(name, name))
        self.setFixedSize(30, 30)

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = sheet.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isChecked():
            p.setPen(QColor(t["txt_0"]))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(1, 1, 28, 28))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*ACCENTS[self.name]))
        p.drawEllipse(QRectF(5, 5, 20, 20))
        p.end()


class AppearanceDialog(QDialog):
    changed = Signal(str, object)

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Appearance")
        self.settings = settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)

        outer.addWidget(w.label("THEME", "cardtitle"))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.themes = QButtonGroup(self)
        for i, name in enumerate(THEMES):
            tile = ThemeTile(name)
            tile.setFixedSize(tile.sizeHint())
            tile.setChecked(name == settings.get("theme"))
            tile.clicked.connect(lambda _=False, n=name: self._set("theme", n))
            self.themes.addButton(tile)
            grid.addWidget(tile, i // 6, i % 6)
        outer.addLayout(grid)

        self.follow = w.Switch()
        self.follow.setChecked(settings.get("theme.follow") == "windows")
        self.follow.toggled.connect(lambda on: self._set("theme.follow", "windows" if on else "off"))
        outer.addLayout(w.row(self.follow, w.label(
            "Follow Windows light and dark mode (Light and Dark themes)", "hint"), None))

        outer.addWidget(w.label("ACCENT", "cardtitle"))
        accents = QHBoxLayout()
        accents.setSpacing(6)
        self.accent_group = QButtonGroup(self)
        for name in ACCENTS:
            dot = AccentDot(name)
            dot.setChecked(name == settings.get("accent"))
            dot.clicked.connect(lambda _=False, n=name: self._set("accent", n))
            self.accent_group.addButton(dot)
            accents.addWidget(dot)
        accents.addStretch(1)
        outer.addLayout(accents)

        outer.addWidget(w.label("DENSITY", "cardtitle"))
        self.density = w.Segments(list(DENSITY_LABELS.items()))
        self.density.set_value(settings.get("density"))
        self.density.changed.connect(lambda key: self._set("density", key))
        outer.addWidget(self.density)

        outer.addWidget(w.label("CORNERS", "cardtitle"))
        self.corners = w.Segments([("round", "Rounded"), ("square", "Square")])
        self.corners.set_value(settings.get("corners"))
        self.corners.changed.connect(lambda key: self._set("corners", key))
        outer.addWidget(self.corners)

        outer.addWidget(w.label("Changes apply as you choose them.", "hint"))
        outer.addLayout(w.row(None, w.button("Close", slot=self.accept)))

    def _set(self, key: str, value) -> None:
        self.settings.set(key, value)
        self.changed.emit(key, value)
        self.update()
        for child in self.findChildren(QAbstractButton):
            child.update()
