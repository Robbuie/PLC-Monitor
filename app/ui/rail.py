"""The rail down the left: the pages, and a glance at what is running.

Grouped the way the work goes -- watch, then analyse, then set up -- with the
current page in the accent, File Manager's rail rows. The card at the bottom is
the numbers somebody checks without opening a page: events this session, the
size of today's log, and the service.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QPushButton, QVBoxLayout, QWidget

from app.ui import widgets as w

GROUPS = [
    ("Monitor", ["tags", "socket", "log"]),
    ("Analyse", ["trend", "latency"]),
    ("Set up", ["network", "service"]),
]


class Rail(QFrame):
    selected = Signal(str)

    def __init__(self, pages: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "rail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(208)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 12)
        layout.setSpacing(2)
        self.rows: dict[str, QPushButton] = {}
        for heading, keys in GROUPS:
            layout.addWidget(w.label(heading.upper(), "railhead"))
            for key in keys:
                page = pages[key]
                row = QPushButton(f"  {page.title}")
                row.setProperty("role", "railrow")
                row.setProperty("glyph", page.glyph)
                row.setCursor(Qt.PointingHandCursor)
                row.clicked.connect(lambda _=False, k=key: self.selected.emit(k))
                layout.addWidget(row)
                self.rows[key] = row
        layout.addStretch(1)

        self.card = QFrame()
        self.card.setProperty("role", "railstatus")
        self.card.setAttribute(Qt.WA_StyledBackground, True)
        grid = QGridLayout(self.card)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.stats: dict[str, object] = {}
        for i, (key, text) in enumerate((("events", "Events"), ("log", "Log size"),
                                         ("service", "Service"))):
            grid.addWidget(w.label(text, "railstat"), i, 0)
            value = w.label("-", "railstatvalue")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(value, i, 1)
            self.stats[key] = value
        layout.addWidget(self.card)
        self.refresh_icons()

    def set_current(self, key: str) -> None:
        for name, row in self.rows.items():
            w.set_prop(row, "state", "current" if name == key else "")
        self.refresh_icons()

    def refresh_icons(self) -> None:
        for name, row in self.rows.items():
            tone = "accent_ink" if row.property("state") == "current" else "txt_2"
            row.setIcon(w.icon(str(row.property("glyph")), 16, tone))

    def set_stat(self, key: str, text: str) -> None:
        self.stats[key].setText(text)
