"""Live log: every event as it is written, and the files it is written to.

The table holds the last few thousand rows -- the CSV always has every one, so
the cap only bounds memory, and it says so. Event types are badges in the
semantic colours (a PLC change is the same green on every theme), and the
filter and search narrow the view without touching what is logged.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import deque

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRectF,
    QSortFilterProxyModel,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QLineEdit,
    QListWidget,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableView,
)

from app.core import siblings
from app.theme import sheet
from app.ui import widgets as w
from app.ui.pages.base import Page

COLUMNS = [("timestamp_human", "Time"), ("event_type", "Event"), ("source", "Source"),
           ("tag_or_addr", "Tag / address"), ("old_value", "Old"), ("new_value", "New"),
           ("notes", "Notes")]

EVENT_TONE = {
    "PLC_CHANGED": "plc", "SOCK_RECV": "recv", "SOCK_SENT": "sent",
    "PLC_CONNECTED": "connect", "SOCK_CONNECT": "connect",
    "PLC_ERROR": "error", "SOCK_ERROR": "error", "PLC_DISCONNECTED": "error",
    "SOCK_DISCONNECT": "info", "INFO": "info",
}

FILTERS = [("", "All events"), ("PLC", "PLC changes"), ("SOCK", "Socket messages"),
           ("ERR", "Errors"), ("CONN", "Connections"), ("INFO", "Info")]

FILTER_SETS = {
    "PLC": {"PLC_CHANGED"}, "SOCK": {"SOCK_RECV", "SOCK_SENT"},
    "ERR": {"PLC_ERROR", "SOCK_ERROR", "PLC_DISCONNECTED"},
    "CONN": {"PLC_CONNECTED", "SOCK_CONNECT", "SOCK_DISCONNECT", "PLC_DISCONNECTED"},
    "INFO": {"INFO"},
}


def open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - a path this program chose
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def open_folder(path: str) -> None:
    """In File Manager when it is installed, Explorer when it is not."""
    if not siblings.launch(siblings.FILE_MANAGER, path):
        open_path(path)


class EventModel(QAbstractTableModel):
    def __init__(self, cap: int, parent=None) -> None:
        super().__init__(parent)
        self.cap = max(100, cap)
        self.rows: deque[dict] = deque()

    def append(self, batch: list[dict]) -> None:
        if not batch:
            return
        batch = batch[-self.cap:]
        overflow = len(self.rows) + len(batch) - self.cap
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            for _ in range(overflow):
                self.rows.popleft()
            self.endRemoveRows()
        start = len(self.rows)
        self.beginInsertRows(QModelIndex(), start, start + len(batch) - 1)
        self.rows.extend(batch)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.rows.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section][1].upper()
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = COLUMNS[index.column()][0]
        if role == Qt.DisplayRole:
            text = str(row.get(key, ""))
            if key == "timestamp_human":
                return text[11:] if len(text) > 11 else text
            if key == "notes" and len(text) > 240:
                return text[:237] + "..."
            return text
        if role == Qt.ToolTipRole:
            if key == "notes":
                return str(row.get("notes", ""))[:2000]
            if key == "timestamp_human":
                return str(row.get("timestamp_human", ""))
        if role == Qt.FontRole and key in ("timestamp_human", "old_value", "new_value"):
            font = QFont("Cascadia Mono")
            font.setStyleHint(QFont.Monospace)
            return font
        if role == Qt.ForegroundRole and key in ("source", "notes"):
            return QColor(sheet.tokens()["txt_1"])
        if role == Qt.UserRole:
            return row
        return None


class EventFilter(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.kinds: set[str] | None = None
        self.text = ""

    def set_kinds(self, kinds: set[str] | None) -> None:
        self.kinds = kinds
        self.invalidateFilter()

    def set_text(self, text: str) -> None:
        self.text = text.lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):  # noqa: N802
        row = self.sourceModel().rows[source_row]
        if self.kinds is not None and row.get("event_type") not in self.kinds:
            return False
        if self.text:
            hay = " ".join(str(v) for v in row.values()).lower()
            return self.text in hay
        return True


class BadgeDelegate(QStyledItemDelegate):
    """The event type as a rounded badge in its semantic colour."""

    def paint(self, painter: QPainter, option, index) -> None:
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(sheet.tokens()["accent_row"]))
        text = str(index.data(Qt.DisplayRole) or "")
        tone = EVENT_TONE.get(text, "info")
        t = sheet.tokens()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont(option.font)
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.0))
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        label = text.replace("_", " ").title().replace("Plc", "PLC").replace("Sock ", "")
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(label) + 16
        height = min(option.rect.height() - 6, metrics.height() + 4)
        box = QRectF(option.rect.left() + 6, option.rect.center().y() - height / 2, width, height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_rgba(t[f"ev_{tone}_fill"]))
        painter.drawRoundedRect(box, height / 2, height / 2)
        painter.setPen(QColor(t[f"ev_{tone}_text"]))
        painter.drawText(box, Qt.AlignCenter, label)
        painter.restore()


def _rgba(text: str) -> QColor:
    """`rgba(r, g, b, a)` as the tokens write it, to a QColor."""
    inner = text[text.index("(") + 1:text.rindex(")")]
    parts = [p.strip() for p in inner.split(",")]
    colour = QColor(int(parts[0]), int(parts[1]), int(parts[2]))
    if len(parts) > 3:
        colour.setAlphaF(float(parts[3]))
    return colour


class LogPage(Page):
    key = "log"
    title = "Live log"
    glyph = "log"
    blurb = "Every event as it is written to the CSV, newest at the bottom."

    def __init__(self, session, parent=None) -> None:
        super().__init__(session, parent)
        cap = int(session.cfg.logging.get("max_gui_rows", 5000) or 5000)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        self.body.addWidget(split, 1)

        events = w.Card("Events")
        self.shown_count = w.label("", "hint")
        events.add_action(self.shown_count)
        self.kind = QComboBox()
        for key, text in FILTERS:
            self.kind.addItem(text, key)
        self.search = QLineEdit()
        self.search.setProperty("role", "search")
        self.search.setPlaceholderText("Search tag, address, value or payload")
        self.search.setClearButtonEnabled(True)
        self.follow = w.Switch()
        self.follow.setChecked(True)
        follow_label = w.label("Follow", "hint")
        clear = w.button("Clear view", slot=self._clear,
                         tip="Clears this table only. The CSV keeps everything.")
        events.body.addLayout(w.row(self.kind, self.search, 12, self.follow, follow_label, clear,
                                    stretch_at=1))

        self.model = EventModel(cap, self)
        self.proxy = EventFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setItemDelegateForColumn(1, BadgeDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setHighlightSections(False)
        for column, width in enumerate((118, 118, 200, 210, 84, 84)):
            header.resizeSection(column, width)
        header.setStretchLastSection(True)
        events.body.addWidget(self.table, 1)
        self.cap_note = w.label(f"Showing the last {cap:,} events. The CSV file has all of them.",
                                "hint")
        events.body.addWidget(self.cap_note)
        split.addWidget(events)

        files = w.Card("Log files")
        self.path = w.label("", "mono", wrap=True, selectable=True)
        files.body.addWidget(self.path)
        self.size = w.label("", "hint")
        files.body.addWidget(self.size)
        files.body.addLayout(w.row(
            w.button("Open", icon_name="external", slot=self._open_file, tip="Open the CSV"),
            w.button("Folder", icon_name="folder", slot=self._open_folder,
                     tip="Open the folder with the log and its archives"),
            w.button("Move...", slot=self._change_path,
                     tip="Write the log somewhere else from now on."),
            None))
        files.body.addWidget(w.rule())
        form = w.Form(label_width=110)
        self.max_mb = QSpinBox()
        self.max_mb.setRange(1, 10000)
        self.max_mb.setSuffix(" MB")
        form.add("mb", "Rotate at", self.max_mb,
                 tip="The file is compressed to .csv.gz at this size and a new one started. "
                     "50 MB compresses to 3-5 MB.")
        self.keep = QSpinBox()
        self.keep.setRange(1, 10000)
        form.add("keep", "Keep archives", self.keep,
                 tip="The oldest archive is deleted once there are more than this.")
        self.daily = w.Switch()
        form.add("daily", "Daily", w.hbox(self.daily, w.label("Also at midnight", "hint", wrap=True),
                                         stretch_at=1, spacing=8))
        files.body.addWidget(form)
        files.body.addLayout(w.row(w.button("Apply", "primary", slot=self._apply),
                                   w.button("Rotate now", slot=self._rotate), None))
        files.body.addWidget(w.rule())
        files.body.addWidget(w.label("ARCHIVES", "cardtitle"))
        self.archives = QListWidget()
        self.archives.itemDoubleClicked.connect(lambda item: open_path(item.data(Qt.UserRole)))
        files.body.addWidget(self.archives, 1)
        files.setMinimumWidth(320)
        files.setMaximumWidth(420)
        split.addWidget(files)
        split.setStretchFactor(0, 1)

        self.kind.currentIndexChanged.connect(self._filter_changed)
        self.search.textChanged.connect(self.proxy.set_text)
        self.proxy.rowsInserted.connect(self._after_insert)
        self.proxy.modelReset.connect(self._update_count)
        self.proxy.layoutChanged.connect(self._update_count)
        session.events.connect(self.model.append)
        session.logPathChanged.connect(lambda _p: self._refresh_files())
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_size)
        self._timer.start()
        self._load_settings()
        self._refresh_files()

    def _load_settings(self) -> None:
        lg = self.session.cfg.logging
        self.max_mb.setValue(int(lg.get("max_mb", 50) or 50))
        self.keep.setValue(int(lg.get("max_rotated", 30) or 30))
        self.daily.setChecked(bool(lg.get("rotate_daily", True)))

    def _filter_changed(self) -> None:
        key = self.kind.currentData()
        self.proxy.set_kinds(FILTER_SETS.get(key) if key else None)
        self._update_count()

    def _after_insert(self, *_):
        self._update_count()
        if self.follow.isChecked():
            self.table.scrollToBottom()

    def _update_count(self) -> None:
        shown, total = self.proxy.rowCount(), self.model.rowCount()
        self.shown_count.setText(f"{shown:,} shown" if shown == total else f"{shown:,} of {total:,}")

    def _clear(self) -> None:
        self.model.clear()
        self._update_count()

    def _apply(self) -> None:
        lg = self.session.logger
        lg.max_bytes = self.max_mb.value() * 1024 * 1024
        lg.max_rotated = self.keep.value()
        lg.rotate_daily = self.daily.isChecked()
        c = self.session.cfg
        c.set("logging", "max_mb", self.max_mb.value())
        c.set("logging", "max_rotated", self.keep.value())
        c.set("logging", "rotate_daily", self.daily.isChecked())
        if self.session.save_config():
            self.session.status.emit(f"Log rotation: at {self.max_mb.value()} MB"
                                     f"{' and at midnight' if self.daily.isChecked() else ''}, "
                                     f"keeping {self.keep.value()} archives.")

    def _rotate(self) -> None:
        result = self.session.logger.rotate_now()
        self.session.status.emit(f"Rotated to {os.path.basename(result)}" if result
                                 else "Nothing to rotate: the log is empty.")
        QTimer.singleShot(1500, self._refresh_files)

    def _refresh_files(self) -> None:
        self.path.setText(self.session.logger.log_path)
        self._refresh_size()
        self.archives.clear()
        for path in reversed(self.session.logger.list_archives()):
            name = os.path.basename(path)
            try:
                size = os.path.getsize(path) / 1024
                text = f"{name}   {size:,.0f} KB"
            except OSError:
                text = name
            self.archives.addItem(text)
            self.archives.item(self.archives.count() - 1).setData(Qt.UserRole, path)
        if self.archives.count() == 0:
            self.archives.addItem("No archives yet")
            self.archives.item(0).setFlags(Qt.NoItemFlags)

    def _refresh_size(self) -> None:
        self.size.setText(f"{self.session.logger.current_size_mb():.2f} MB in the current file")

    def _open_file(self) -> None:
        path = self.session.logger.log_path
        if os.path.exists(path):
            open_path(path)
        else:
            w.info(self, "Log file", "There is no log file yet.", path)

    def _open_folder(self) -> None:
        open_folder(self.session.log_folder())

    def _change_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Write the log to", self.session.logger.log_path,
                                              "CSV files (*.csv)")
        if path:
            self.session.set_log_path(path)

    def shown(self) -> None:
        self._refresh_files()

    def theme_changed(self) -> None:
        super().theme_changed()
        self.table.viewport().update()
