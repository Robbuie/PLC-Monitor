"""Profiles in the window: the switcher in the title bar, the details form,
and the Manage profiles dialog.

The storage and the rules (unique names per site, a log folder each, what an
export leaves out) are app/engine/profiles.py; this is only how a person gets
at them. Switching itself -- stopping what runs, asking about unsaved edits,
reloading the pages -- is MainWindow.switch_profile, because it touches every
page.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.engine import profiles
from app.engine.profiles import Profile, ProfileError
from app.ui import widgets as w

#: How many recently used profiles head the switcher's menu.
RECENT = 5


class ProfileButton(QToolButton):
    """The open profile's name in the title bar. Clicking it lists the others.

    Recent ones first, then every profile grouped by site, then the actions.
    The menu is rebuilt each time it opens, so a profile made on another PC
    and copied in, or one the service was pointed at, shows without a restart.
    """

    chosen = Signal(str)          # a profile id
    action = Signal(str)          # "new" | "duplicate" | "edit" | "manage" | "import" | "export"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "profile")
        self.setProperty("glyph", "sort_down")
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setLayoutDirection(Qt.RightToLeft)       # the caret after the name
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("Profile")
        self.recent: list[str] = []
        self.current_id: str | None = None
        self._menu = QMenu(self)
        self._menu.setLayoutDirection(Qt.LeftToRight)
        self._menu.aboutToShow.connect(self._fill)
        self.setMenu(self._menu)

    def show_profile(self, text: str, tip: str, current_id: str | None) -> None:
        # Long names are elided rather than pushing the chips off the bar.
        metrics = self.fontMetrics()
        self.setText(metrics.elidedText(text, Qt.ElideMiddle, 260))
        self.setToolTip(tip)
        self.current_id = current_id

    def _fill(self) -> None:
        menu = self._menu
        menu.clear()
        every = profiles.list_profiles()
        by_id = {p.id: p for p in every}
        service_id = profiles.service_profile_id()

        def add(profile: Profile, text: str | None = None) -> None:
            label = text or profile.name
            if profile.line:
                label += f"   ({profile.line})"
            if profile.id == service_id:
                label += "   - service"
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(profile.id == self.current_id)
            if profile.notes:
                act.setToolTip(profile.notes)
            act.triggered.connect(lambda _=False, pid=profile.id: self.chosen.emit(pid))

        def header(text: str) -> None:
            # A disabled item rather than addSection: with a style sheet,
            # Qt draws a section as a bare line and drops its title.
            if not menu.isEmpty():
                menu.addSeparator()
            head = menu.addAction(text.upper())
            head.setEnabled(False)

        recent = [by_id[r] for r in self.recent if r in by_id][:RECENT]
        if len(every) > 6 and recent:
            header("Recent")
            for profile in recent:
                add(profile, profile.label)
        site = None
        for index, profile in enumerate(every):
            if index == 0 or profile.site != site:
                site = profile.site
                header(site or "No site")
            add(profile)
        menu.addSeparator()
        for key, text in (("new", "New profile..."), ("duplicate", "Duplicate this profile..."),
                          ("edit", "Rename or edit details..."), ("manage", "Manage profiles..."),
                          ("import", "Import profile..."), ("export", "Export this profile...")):
            act = menu.addAction(text)
            act.setEnabled(key in ("new", "manage", "import") or self.current_id is not None)
            act.triggered.connect(lambda _=False, k=key: self.action.emit(k))


class ProfileDetailsDialog(QDialog):
    """Name, site, line or cell, and notes -- for New, Duplicate and Edit."""

    def __init__(self, title: str, parent=None, *, profile: Profile | None = None,
                 sites: list[str] | None = None, ok_text: str = "Save",
                 check=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self._check = check
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        form = w.Form(label_width=90)
        self.name = QLineEdit(profile.name if profile else "")
        self.name.setPlaceholderText("Press PLC, Cell 4 robot, Packing line...")
        self.site = QLineEdit(profile.site if profile else "")
        self.site.setPlaceholderText("Customer or plant -- profiles are grouped by it")
        if sites:
            from PySide6.QtWidgets import QCompleter
            completer = QCompleter(sorted(set(s for s in sites if s)), self)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            self.site.setCompleter(completer)
        self.line = QLineEdit(profile.line if profile else "")
        self.line.setPlaceholderText("Line, cell or machine (optional)")
        self.notes = QPlainTextEdit(profile.notes if profile else "")
        self.notes.setPlaceholderText("Switch port, the other system's address, who to call...")
        self.notes.setFixedHeight(96)
        form.add("name", "Name", self.name)
        form.add("site", "Site", self.site)
        form.add("line", "Line / cell", self.line)
        form.add("notes", "Notes", self.notes)
        outer.addWidget(form)
        self.problem = w.label("", "hint", wrap=True)
        w.set_prop(self.problem, "tone", "down")
        self.problem.setVisible(False)
        outer.addWidget(self.problem)
        self.ok = w.button(ok_text, "primary", slot=self._accept)
        self.ok.setDefault(True)
        outer.addLayout(w.row(None, w.button("Cancel", slot=self.reject), self.ok))
        self.name.textChanged.connect(lambda t: self.ok.setEnabled(bool(t.strip())))
        self.ok.setEnabled(bool(self.name.text().strip()))
        self.name.setFocus()

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "site": self.site.text().strip(),
                "line": self.line.text().strip(), "notes": self.notes.toPlainText().strip()}

    def _accept(self) -> None:
        """Run the caller's save; a ProfileError keeps the dialog open with
        the reason under the fields, rather than losing what was typed."""
        if self._check is not None:
            try:
                self._check(self.values())
            except ProfileError as exc:
                self.problem.setText(str(exc))
                self.problem.setVisible(True)
                return
        self.accept()


class ManageProfilesDialog(QDialog):
    """Every profile, searchable, grouped by site, with what can be done to them."""

    opened = Signal(str)          # open this profile id in the window
    action = Signal(str, str)     # (action, profile id) for the window to carry out

    def __init__(self, current_id: str | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Profiles")
        self.resize(760, 520)
        self.current_id = current_id
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        outer.addWidget(w.label(
            "One profile per site, line or PLC. Each keeps its own settings, tags and log "
            "folder. Double-click to open one.", "hint", wrap=True))
        self.search = QLineEdit()
        self.search.setProperty("role", "search")
        self.search.setPlaceholderText("Find by name, site, line or notes")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        outer.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Line / cell", "Changed", ""])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tree.itemDoubleClicked.connect(lambda item, _c: self._open())
        self.tree.currentItemChanged.connect(lambda *_: self._selection())
        outer.addWidget(self.tree, 1)
        self.notes = w.label("", "hint", wrap=True, selectable=True)
        outer.addWidget(self.notes)

        self.open_btn = w.button("Open", "primary", slot=self._open)
        self.dup_btn = w.button("Duplicate...", slot=lambda: self._emit("duplicate"))
        self.edit_btn = w.button("Edit details...", slot=lambda: self._emit("edit"))
        self.export_btn = w.button("Export...", slot=lambda: self._emit("export"))
        self.delete_btn = w.button("Delete", "danger", slot=lambda: self._emit("delete"))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for b in (w.button("New...", icon_name="plus", slot=lambda: self.action.emit("new", "")),
                  w.button("Import...", slot=lambda: self.action.emit("import", "")),
                  self.dup_btn, self.edit_btn, self.export_btn, self.delete_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(w.button("Close", slot=self.reject))
        buttons.addWidget(self.open_btn)
        outer.addLayout(buttons)
        self.refresh()

    def selected_id(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def refresh(self, *_args, select: str | None = None) -> None:
        keep = select or self.selected_id() or self.current_id
        needle = self.search.text().strip().lower()
        service_id = profiles.service_profile_id()
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        chosen = None
        for p in profiles.list_profiles():
            hay = " ".join((p.name, p.site, p.line, p.notes)).lower()
            if needle and needle not in hay:
                continue
            key = p.site or "No site"
            group = groups.get(key)
            if group is None:
                group = QTreeWidgetItem([key, "", "", ""])
                group.setFlags(Qt.ItemIsEnabled)
                font = group.font(0)
                font.setBold(True)
                group.setFont(0, font)
                self.tree.addTopLevelItem(group)
                group.setExpanded(True)
                groups[key] = group
            marks = []
            if p.id == self.current_id:
                marks.append("open")
            if p.id == service_id:
                marks.append("service")
            item = QTreeWidgetItem([p.name, p.line, time.strftime("%Y-%m-%d %H:%M",
                                    time.localtime(p.modified)) if p.modified else "",
                                    ", ".join(marks)])
            item.setData(0, Qt.UserRole, p.id)
            item.setToolTip(0, p.path)
            group.addChild(item)
            if p.id == keep:
                chosen = item
        if chosen is None and groups:
            first = next(iter(groups.values()))
            chosen = first.child(0) if first.childCount() else None
        if chosen is not None:
            self.tree.setCurrentItem(chosen)
        self._selection()

    def _selection(self) -> None:
        pid = self.selected_id()
        profile = profiles.get(pid) if pid else None
        for b in (self.open_btn, self.dup_btn, self.edit_btn, self.export_btn, self.delete_btn):
            b.setEnabled(profile is not None)
        if profile is not None:
            self.open_btn.setEnabled(profile.id != self.current_id)
            self.delete_btn.setEnabled(profile.id != self.current_id)
        self.notes.setText(profile.notes if profile and profile.notes else "")

    def _open(self) -> None:
        pid = self.selected_id()
        if pid and pid != self.current_id:
            self.opened.emit(pid)

    def _emit(self, what: str) -> None:
        pid = self.selected_id()
        if pid:
            self.action.emit(what, pid)
