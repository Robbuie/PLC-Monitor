"""Live log: every event as it is written, and the files it is written to.

It also opens a log file -- an archive, or one from another PC -- in the same
table, with grouped tags decoded, so a day's events can be filtered and
searched. **Filter** hides what does not match; **Find** keeps every row and
jumps from one match to the next, centred, so the events either side of a
vehicle number are on screen with it.

The table holds the last few thousand rows -- the CSV always has every one, so
the cap only bounds memory, and it says so. Event types are badges in the
semantic colours (a PLC change is the same green on every theme), and the
filter and search narrow the view without touching what is logged.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelectionModel,
    QModelIndex,
    QRectF,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
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
        # A list, not a deque: the filter and Find index into the middle of
        # it, which a deque does in linear time -- fine for the live few
        # thousand rows, not for a day's log opened from a file.
        self.rows: list[dict] = []
        #: Show the date in the time column: on for a file, which can span days.
        self.show_date = False

    def load(self, rows: list[dict]) -> None:
        """Replace everything with `rows` (a file being viewed), uncapped."""
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def append(self, batch: list[dict]) -> None:
        if not batch:
            return
        batch = batch[-self.cap:]
        overflow = len(self.rows) + len(batch) - self.cap
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            del self.rows[:overflow]
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
                if self.show_date:
                    return text[5:] if len(text) > 5 else text
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
        self._refilter(lambda: setattr(self, "kinds", kinds))

    def set_text(self, text: str) -> None:
        self._refilter(lambda: setattr(self, "text", text.lower().strip()))

    def _refilter(self, change) -> None:
        # Qt 6.9 replaced invalidateFilter() with a begin/end pair; use
        # whichever this PySide6 has, so neither warns.
        begin = getattr(self, "beginFilterChange", None)
        if begin is not None:
            begin()
            change()
            self.endFilterChange()
        else:
            change()
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
        # The sheet sets sizes in pixels, so pointSizeF() is -1 here.
        if font.pixelSize() > 0:
            font.setPixelSize(max(10, font.pixelSize() - 1))
        else:
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
    blurb = ("Every event as it is written to the CSV, newest at the bottom -- or any log "
             "file, opened to search.")

    _file_loaded = Signal(str, object, str)

    def __init__(self, session, parent=None) -> None:
        super().__init__(session, parent)
        cap = int(session.cfg.logging.get("max_gui_rows", 5000) or 5000)
        self._live_cap = cap
        #: The file shown instead of the live log, or None.
        self.viewing: str | None = None
        self._pending_find: tuple | None = None
        self._found: list[int] = []
        self._found_at = -1

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
        self.search.setPlaceholderText("Filter: only rows containing...")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Hides every row that does not contain this text.")
        self.finder = QLineEdit()
        self.finder.setProperty("role", "search")
        self.finder.setPlaceholderText("Find a value, e.g. a VIN")
        self.finder.setClearButtonEnabled(True)
        self.finder.setToolTip("Keeps every row and jumps to each one containing this text, "
                             "centred, with the rows either side. Enter for the next.")
        self.finder.textChanged.connect(lambda _t: self._find_changed())
        self.finder.returnPressed.connect(lambda: self._step(1))
        self.find_prev = w.tool("back", "Previous match", lambda: self._step(-1))
        self.find_next = w.tool("forward", "Next match", lambda: self._step(1))
        self.find_count = w.label("", "hint")
        self.follow = w.Switch()
        self.follow.setChecked(True)
        follow_label = w.label("Follow", "hint")
        clear = w.button("Clear view", slot=self._clear,
                         tip="Clears this table only. The CSV keeps everything.")
        open_btn = w.button("Open file...", slot=self._browse,
                            tip="Show a log file here instead of the live log: an archive, or "
                                "a log from another PC (.csv or .csv.gz).")
        events.body.addLayout(w.row(self.kind, self.search, 12, self.follow, follow_label, clear,
                                    open_btn, stretch_at=1))
        events.body.addLayout(w.row(self.finder, self.find_prev, self.find_next, self.find_count,
                                    None))

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
        self.size_label = w.label("", "hint")
        files.body.addWidget(self.size_label)
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
        self.archives.itemDoubleClicked.connect(lambda item: self.view_file(item.data(Qt.UserRole)))
        self.archives.setToolTip("Double-click to show an archive here, to filter and search.")
        files.body.addWidget(self.archives, 1)
        files.setMinimumWidth(320)
        files.setMaximumWidth(420)
        split.addWidget(files)
        split.setStretchFactor(0, 1)

        self.kind.currentIndexChanged.connect(self._filter_changed)
        self.search.textChanged.connect(self.proxy.set_text)
        self.search.textChanged.connect(lambda _t: self._find_changed())
        self.proxy.rowsInserted.connect(self._after_insert)
        self.proxy.modelReset.connect(self._update_count)
        self.proxy.layoutChanged.connect(self._update_count)
        session.events.connect(self._live_rows)
        session.showInLog.connect(self.show_in_log)
        self._file_loaded.connect(self._show_file)
        back = w.button("Back to live", slot=self.back_to_live)
        self.banner.side.addWidget(back)
        self.find_prev.setEnabled(False)
        self.find_next.setEnabled(False)
        session.logPathChanged.connect(lambda _p: self._refresh_files())
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_size)
        self._timer.start()
        self._load_settings()
        self._refresh_files()

    # ------------------------------------------------------- viewing a file

    def _live_rows(self, rows: list[dict]) -> None:
        # While a file is shown, live rows go to the CSV as always but not
        # into this table; Back to live reloads the recent ones from the CSV.
        if self.viewing is None:
            self.model.append(rows)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a log", self.session.log_folder(),
            "PLC Monitor logs (*.csv *.csv.gz *.gz);;All files (*)")
        if path:
            self.view_file(path)

    def view_file(self, path: str, then_find: tuple | None = None) -> None:
        """Show `path` in the table. Read on a worker thread: a day's log is
        a few hundred thousand rows. `then_find` = (ms, tag, value) selects
        that row once loaded."""
        if not path:
            return
        self._pending_find = then_find
        if self.viewing == path and then_find is None:
            return
        if self.viewing == path and then_find is not None:
            self._apply_pending_find()
            return
        self.banner.set(f"Reading {os.path.basename(path)}...", "info")
        specs = self.session.group_specs()
        if path == self.session.logger.log_path:
            self.session.logger.flush(1.0)

        def work():
            try:
                rows = list(self.session.log_rows(path, specs))
                error = ""
            except (OSError, EOFError) as exc:
                rows, error = [], str(exc)
            self._file_loaded.emit(path, rows, error)

        threading.Thread(target=work, daemon=True).start()

    def _show_file(self, path: str, rows, error: str) -> None:
        if not path:
            # Back to live: the recent rows read back from the live CSV.
            if self.viewing is None:
                self.model.load(rows)
                self.follow.setChecked(True)
                self.table.scrollToBottom()
                self._update_count()
                self._find_changed()
            return
        if error:
            self.banner.set(f"Could not read {os.path.basename(path)}: {error}", "warn")
            return
        self.viewing = path
        self.follow.setChecked(False)
        self.model.show_date = True
        self.model.load(rows)
        self.cap_note.setText(f"{len(rows):,} events in {path}")
        self.banner.set(f"Showing {os.path.basename(path)} -- {len(rows):,} events, not the "
                        "live log. Filter and Find work on it.", "info")
        self._update_count()
        self._find_changed()
        self._apply_pending_find()

    def back_to_live(self) -> None:
        """The live log again, with its most recent rows read back from the
        CSV so the table is not empty."""
        self.viewing = None
        self.model.show_date = False
        self.banner.set("")
        path = self.session.logger.log_path
        self.cap_note.setText(f"Showing the last {self._live_cap:,} events. The CSV file has "
                              "all of them.")
        specs = self.session.group_specs()
        self.session.logger.flush(1.0)

        def work():
            try:
                rows = list(self.session.log_rows(path, specs))[-self._live_cap:]
            except (OSError, EOFError):
                rows = []
            self._file_loaded.emit("", rows, "")

        self.model.load([])
        threading.Thread(target=work, daemon=True).start()

    def show_in_log(self, path: str, ms: int, tag: str, value: str) -> None:
        """From the Trend page: this file, at this change."""
        self.session.goto.emit("log")
        self.view_file(path, then_find=(ms, tag, value))

    def _apply_pending_find(self) -> None:
        if not self._pending_find:
            return
        ms, tag, value = self._pending_find
        self._pending_find = None
        # The filter could hide the row; it is for narrowing, Find for this.
        self.search.clear()
        self.kind.setCurrentIndex(0)
        best = None
        for i, row in enumerate(self.model.rows):
            if (row.get("tag_or_addr") or "").strip() != tag:
                continue
            try:
                distance = abs(int(row.get("timestamp_ms") or 0) - ms)
            except ValueError:
                continue
            if best is None or distance < best[0]:
                best = (distance, i)
        self.finder.blockSignals(True)
        self.finder.setText(value)
        self.finder.blockSignals(False)
        self._find_changed()
        if best is not None:
            self._select(best[1])
            if best[1] in self._found:
                self._found_at = self._found.index(best[1])
                self._show_count()

    # ---------------------------------------------------------------- find

    def _find_changed(self) -> None:
        """Which rows (of those the filter shows) contain the find text."""
        needle = self.finder.text().strip().lower()
        self._found = []
        self._found_at = -1
        if needle:
            rows = self.model.rows
            for proxy_row in range(self.proxy.rowCount()):
                source = self.proxy.mapToSource(self.proxy.index(proxy_row, 0)).row()
                row = rows[source]
                if any(needle in str(row.get(k, "")).lower()
                       for k in ("tag_or_addr", "old_value", "new_value", "notes", "source")):
                    self._found.append(source)
        self.find_prev.setEnabled(bool(self._found))
        self.find_next.setEnabled(bool(self._found))
        self._show_count()

    def _show_count(self) -> None:
        if not self.finder.text().strip():
            self.find_count.setText("")
        elif not self._found:
            self.find_count.setText("No match")
        elif self._found_at < 0:
            self.find_count.setText(f"{len(self._found):,} matches -- Enter for the first")
        else:
            row = self.model.rows[self._found[self._found_at]]
            self.find_count.setText(f"{self._found_at + 1:,} of {len(self._found):,}  "
                                    f"{row.get('timestamp_human', '')}")

    def _step(self, direction: int) -> None:
        if not self._found:
            return
        self.follow.setChecked(False)
        self._found_at = (self._found_at + direction) % len(self._found)
        self._select(self._found[self._found_at])
        self._show_count()

    def _select(self, source_row: int) -> None:
        """Select a row and put it in the middle of the table, so the events
        before and after it are in view."""
        index = self.proxy.mapFromSource(self.model.index(source_row, 0))
        if not index.isValid():
            return
        self.table.selectionModel().select(
            index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self.table.setCurrentIndex(index)
        self.table.scrollTo(index, QAbstractItemView.PositionAtCenter)

    def _load_settings(self) -> None:
        lg = self.session.cfg.logging
        self.max_mb.setValue(int(lg.get("max_mb", 50) or 50))
        self.keep.setValue(int(lg.get("max_rotated", 30) or 30))
        self.daily.setChecked(bool(lg.get("rotate_daily", True)))

    def _filter_changed(self) -> None:
        key = self.kind.currentData()
        self.proxy.set_kinds(FILTER_SETS.get(key) if key else None)
        self._update_count()
        self._find_changed()

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
        self.size_label.setText(f"{self.session.logger.current_size_mb():.2f} MB in the current file")

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

    def profile_changed(self) -> None:
        # The rows on screen are the old profile's; its log has them all.
        if self.viewing is not None:
            self.viewing = None
            self.model.show_date = False
            self.banner.set("")
        self._clear()
        self._load_settings()
