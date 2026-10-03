"""The user guide: README.md, rendered inside the application.

Before 1.0 the guide was a separate 90 KB README.html with drawings of the old
window, which went out of date the moment the window changed. Now there is one
document, README.md, shipped beside the exe and drawn here by Qt's own markdown
renderer in the theme's colours -- so the guide a customer reads is the same
file the repository shows, and it cannot fall behind.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout

from app import paths
from app.ui import widgets as w


def readme_path() -> str | None:
    candidates = [os.path.join(paths.program_folder(), "README.md")]
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), "README.md"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class GuideDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PLC Monitor guide")
        self.resize(820, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        view = QTextBrowser()
        view.setProperty("role", "guide")
        view.setOpenExternalLinks(True)
        path = readme_path()
        if path:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            # The changelog is for whoever builds it, not whoever runs it.
            cut = text.find("\n## Changelog")
            view.setMarkdown(text[:cut] if cut > 0 else text)
        else:
            view.setMarkdown("# PLC Monitor\n\nThe guide (README.md) was not found beside the program.")
        layout.addWidget(view, 1)
        layout.addLayout(w.row(None, w.button("Close", slot=self.accept), 12))
