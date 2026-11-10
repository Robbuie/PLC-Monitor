"""What every page has: a title, a sentence on what it is for, and a body."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from app.ui import widgets as w


class Page(QWidget):
    key = ""
    title = ""
    glyph = ""
    blurb = ""

    def __init__(self, session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.setProperty("role", "page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 14, 22, 16)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(w.label(self.title, "pagetitle"))
        self.subtitle = w.label(self.blurb, "pagesub", wrap=True)
        words.addWidget(self.subtitle)
        head.addLayout(words, 1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(6)
        head.addLayout(self.header_actions)
        outer.addLayout(head)

        self.banner = w.Banner()
        self.banner.setVisible(False)
        outer.addWidget(self.banner)

        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        outer.addLayout(self.body, 1)

    def shown(self) -> None:
        """Called when the page comes to the front."""

    def theme_changed(self) -> None:
        """Called after the stylesheet is re-applied."""
        w.refresh_icons(self)

    def save_into_config(self) -> None:
        """Copy what is on screen into the session's config (not to disk)."""

    def profile_changed(self) -> None:
        """Another profile is open: show its settings. The log-reading pages
        follow `logPathChanged` instead."""
