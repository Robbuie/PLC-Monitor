"""Choosing which tags to bring in from an exported tag list.

A controller export holds hundreds or thousands of tags, most of them not
wanted here, and every tag added makes each poll longer. So the list opens
with nothing ticked, can be searched, and by default hides what the poller
cannot log as one value (UDTs, arrays).
"""

from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHeaderView, QLineEdit, QTableView, QVBoxLayout

from app.engine.tagimport import TagEntry
from app.ui import widgets as w


class _Filter(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.text = ""
        self.basic_only = True

    def set(self, text: str | None = None, basic_only: bool | None = None) -> None:
        # Qt 6.9 replaced invalidateFilter() with a begin/end pair; use
        # whichever this PySide6 has, so neither an old nor a new one warns.
        begin = getattr(self, "beginFilterChange", None)
        if begin is not None:
            begin()
        if text is not None:
            self.text = text.strip().lower()
        if basic_only is not None:
            self.basic_only = basic_only
        if begin is not None:
            self.endFilterChange()
        else:
            self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent) -> bool:  # noqa: N802
        model = self.sourceModel()
        entry: TagEntry = model.item(row, 0).data(Qt.UserRole)
        if self.basic_only and not entry.basic:
            return False
        if self.text:
            hay = f"{entry.tag} {entry.dtype} {entry.description}".lower()
            return self.text in hay
        return True


class TagPickerDialog(QDialog):
    def __init__(self, entries: list[TagEntry], existing: set[str], source: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import tags")
        self.resize(780, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        outer.addWidget(w.label(
            f"{len(entries):,} tags in {source}. Tick the ones to watch -- every tag makes "
            "each poll a little longer.", "hint", wrap=True))
        self.search = QLineEdit()
        self.search.setProperty("role", "search")
        self.search.setPlaceholderText("Find by name, type or description")
        self.search.setClearButtonEnabled(True)
        self.basic = w.Switch()
        self.basic.setChecked(True)
        outer.addLayout(w.row(self.search, 12, self.basic,
                              w.label("Only single values (hide UDTs and arrays)", "hint"),
                              stretch_at=0))

        self.model = QStandardItemModel(0, 3, self)
        self.model.setHorizontalHeaderLabels(["Tag", "Type", "Description"])
        for entry in entries:
            name = QStandardItem(entry.tag)
            name.setData(entry, Qt.UserRole)
            name.setCheckable(True)
            name.setEditable(False)
            if entry.tag in existing:
                name.setEnabled(False)
                name.setToolTip("Already in the list")
            kind = QStandardItem(entry.dtype)
            kind.setEditable(False)
            desc = QStandardItem(entry.description)
            desc.setEditable(False)
            desc.setToolTip(entry.description)
            self.model.appendRow([name, kind, desc])
        self.proxy = _Filter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 120)
        outer.addWidget(self.table, 1)

        self.count = w.label("", "hint")
        self.add_btn = w.button("Add", "primary", slot=self.accept)
        outer.addLayout(w.row(
            w.button("Tick all shown", slot=lambda: self._tick_shown(True)),
            w.button("Untick all", slot=lambda: self._tick_all(False)),
            self.count, None, w.button("Cancel", slot=self.reject), self.add_btn))

        self.search.textChanged.connect(lambda t: self.proxy.set(text=t))
        self.basic.toggled.connect(lambda on: self.proxy.set(basic_only=on))
        self.model.itemChanged.connect(lambda *_: self._update())
        # A list with no type information has nothing to hide.
        if not any(e.dtype for e in entries):
            self.basic.setChecked(False)
            self.basic.setEnabled(False)
        self._update()

    def _items(self):
        for row in range(self.model.rowCount()):
            yield self.model.item(row, 0)

    def _tick_shown(self, on: bool) -> None:
        self.model.blockSignals(True)
        for row in range(self.proxy.rowCount()):
            item = self.model.itemFromIndex(self.proxy.mapToSource(self.proxy.index(row, 0)))
            if item.isEnabled():
                item.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.model.blockSignals(False)
        self.table.viewport().update()
        self._update()

    def _tick_all(self, on: bool) -> None:
        self.model.blockSignals(True)
        for item in self._items():
            if item.isEnabled():
                item.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.model.blockSignals(False)
        self.table.viewport().update()
        self._update()

    def chosen(self) -> list[TagEntry]:
        return [item.data(Qt.UserRole) for item in self._items()
                if item.isEnabled() and item.checkState() == Qt.Checked]

    def _update(self) -> None:
        n = len(self.chosen())
        self.count.setText(f"{n:,} ticked")
        self.add_btn.setEnabled(n > 0)
        self.add_btn.setText(f"Add {n:,}" if n else "Add")
