"""Browse PLC: pick tags from the controller's own tag list.

The tree is the controller as Studio 5000's Controller Organizer shows it --
controller tags, each program, the I/O modules -- and a structure or an array
opens into its members and elements, down to the single values that can be
ticked. Nothing below a row is made until it is opened, so a controller with
thousands of UDT instances opens as fast as one with ten.

Search switches the tree for a flat list of matches (which still open), and
ticks are kept by address, so ticking in a search and then clearing it loses
nothing.

The list is read on a thread of its own: pycomm3's upload takes seconds on a
big controller, and the window must not freeze while it does.
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QDialog, QHeaderView, QLineEdit, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from app.engine import tagbrowse
from app.engine.tagbrowse import BrowseError, Database, Node
from app.theme import sheet
from app.ui import widgets as w

#: Characters typed before a search runs: one letter matches most of a
#: controller.
MIN_SEARCH = 2
NODE = Qt.UserRole
LOADED = Qt.UserRole + 1


class TagBrowserDialog(QDialog):
    _done = Signal(object, str)
    _progress = Signal(str)

    def __init__(self, load: Callable[[Callable[[str], None]], Database], existing: set[str],
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Browse PLC tags")
        self.resize(900, 640)
        self.existing = existing
        self.db: Database | None = None
        #: address -> node, for every ticked value, shown or not.
        self.ticked: dict[str, Node] = {}
        self._filling = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        self.summary = w.label("", "hint", wrap=True)
        outer.addWidget(self.summary)
        self.search = QLineEdit()
        self.search.setProperty("role", "search")
        self.search.setPlaceholderText("Find a tag or member by name or data type -- "
                                       "Cycle, ACC, Station1, REAL")
        self.search.setClearButtonEnabled(True)
        self.search.setEnabled(False)
        outer.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["TAG", "DATA TYPE", "ADDRESS"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 330)
        self.tree.setColumnWidth(1, 150)
        outer.addWidget(self.tree, 1)
        self.state = w.label("", "hint", wrap=True)
        outer.addWidget(self.state)

        self.count = w.label("", "hint")
        self.add_btn = w.button("Add", "primary", slot=self.accept)
        self.untick = w.button("Untick all", slot=self._untick_all)
        outer.addLayout(w.row(self.untick, self.count, None,
                              w.button("Cancel", slot=self.reject), self.add_btn))

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refill)
        self.search.textChanged.connect(lambda *_: self._timer.start())
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self._done.connect(self._loaded)
        self._progress.connect(self.summary.setText)
        self._update()

        self.summary.setText("Reading the tag list...")
        self.state.setText("The controller is asked to describe its tags -- a read; nothing is "
                           "written. A large program can take several seconds.")

        def work():
            try:
                db = load(self._progress.emit)
                self._done.emit(db, "")
            except BrowseError as exc:
                self._done.emit(None, str(exc))
            except Exception as exc:          # a driver's own surprise
                self._done.emit(None, f"Could not read the tag list: {exc}")

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ loading

    def _loaded(self, db: Database | None, error: str) -> None:
        if db is None:
            self.summary.setText("The tag list could not be read.")
            self.state.setText(error)
            w.set_prop(self.state, "tone", "down")
            return
        self.db = db
        programs = len(db.programs)
        self.summary.setText(
            f"{db.count:,} tags from {db.source}"
            + (f", {programs} program{'s' if programs != 1 else ''}" if programs else "")
            + ". Open a tag to reach its members; tick the values to watch -- "
              "every tag makes each poll a little longer.")
        self.state.setText("")
        w.set_prop(self.state, "tone", "")
        self.search.setEnabled(True)
        self.search.setFocus()
        self._refill()
        if self.tree.topLevelItemCount():
            self.tree.topLevelItem(0).setExpanded(True)

    # --------------------------------------------------------------- tree

    def _item(self, node: Node) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node.label, node.type_label,
                                node.path if node.kind != "scope" else ""])
        item.setData(0, NODE, node)
        item.setToolTip(0, node.path or node.label)
        if node.kind != "scope":
            font = QFont("Cascadia Mono")
            font.setStyleHint(QFont.Monospace)
            item.setFont(2, font)
        if node.kind == "scope":
            bold = item.font(0)
            bold.setBold(True)
            item.setFont(0, bold)
        if node.readable:
            if node.path in self.existing:
                # Already watched: shown ticked and greyed, so it reads as
                # "have it" rather than as something still to choose.
                item.setFlags(Qt.ItemIsSelectable)
                item.setCheckState(0, Qt.Checked)
                item.setToolTip(0, f"{node.path} is already in the list")
                grey = QColor(sheet.tokens()["txt_2"])
                for column in range(3):
                    item.setForeground(column, grey)
                item.setText(1, f"{node.type_label}  (in list)")
            else:
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked if node.path in self.ticked else Qt.Unchecked)
        else:
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            # A placeholder child, so the arrow shows before it is opened.
            item.addChild(QTreeWidgetItem([""]))
            item.setData(0, LOADED, False)
        return item

    def _refill(self) -> None:
        if self.db is None:
            return
        self._filling = True
        self.tree.clear()
        text = self.search.text().strip()
        if len(text) >= MIN_SEARCH:
            found, cut = tagbrowse.search(self.db, text)
            for node in found:
                self.tree.addTopLevelItem(self._item(node))
            self.state.setText(
                "No tag or member matches." if not found else
                f"First {len(found):,} matches -- type more to narrow it." if cut else
                f"{len(found):,} match{'es' if len(found) != 1 else ''}. An array is listed "
                "once; open it for its elements.")
        else:
            for node in tagbrowse.tree(self.db):
                self.tree.addTopLevelItem(self._item(node))
            self.state.setText("")
        self._filling = False

    def _expand(self, item: QTreeWidgetItem) -> None:
        if item.data(0, LOADED) is not False:
            return
        node: Node = item.data(0, NODE)
        self._filling = True
        item.takeChildren()
        item.setData(0, LOADED, True)
        children = [self._item(child) for child in node.children()]
        item.addChildren(children)
        self._filling = False
        # A structure with a single member, or a scope of one tag, is opened
        # straight on through.
        if len(children) == 1 and children[0].data(0, LOADED) is False:
            children[0].setExpanded(True)

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        node: Node = item.data(0, NODE)
        if node is None or not node.readable:
            return
        if item.checkState(0) == Qt.Checked:
            self.ticked[node.path] = node
        else:
            self.ticked.pop(node.path, None)
        self._update()

    def _double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        node: Node = item.data(0, NODE)
        if node is not None and node.readable and item.flags() & Qt.ItemIsUserCheckable:
            item.setCheckState(0, Qt.Unchecked if item.checkState(0) == Qt.Checked
                               else Qt.Checked)

    def _untick_all(self) -> None:
        self.ticked.clear()
        self._filling = True
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(0, Qt.Unchecked)
            stack.extend(item.child(i) for i in range(item.childCount()))
        self._filling = False
        self._update()

    def _update(self) -> None:
        n = len(self.ticked)
        self.count.setText(f"{n:,} ticked" if n else "")
        self.count.setToolTip("\n".join(sorted(self.ticked)[:40]))
        self.add_btn.setEnabled(n > 0)
        self.add_btn.setText(f"Add {n:,}" if n else "Add")
        self.untick.setEnabled(n > 0)

    def chosen(self) -> list[tuple[str, str]]:
        """(address, data type) for every ticked value, in address order."""
        return [(path, node.entry_dtype) for path, node in sorted(self.ticked.items())]
