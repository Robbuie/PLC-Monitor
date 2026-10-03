"""Latency: the question this application exists to answer, answered.

Pick what starts the clock (usually a tag changing) and what stops it (usually
the next socket message), and every occurrence in the log is paired and timed.
Before 1.0 this was a pandas snippet in the README.

Triggers that got no response inside the window are listed, not dropped: the
message that never came is the most important result this page can show.
"""

from __future__ import annotations

import csv
import os
import threading
from datetime import datetime, timedelta

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QLineEdit,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.engine import logdata
from app.theme import sheet
from app.theme.tokens import LIGHT_THEMES, SERIES_DARK, SERIES_LIGHT
from app.ui import widgets as w
from app.ui.pages.base import Page

RANGES = [("Everything in the file", None), ("Last 15 minutes", 15), ("Last hour", 60),
          ("Last 4 hours", 240), ("Last 24 hours", 1440), ("Last 7 days", 10080)]

EVENT_CHOICES = [("Received", ("SOCK_RECV",)), ("Sent", ("SOCK_SENT",)),
                 ("Either", ("SOCK_RECV", "SOCK_SENT"))]


class MatcherEditor(QWidget):
    """One side of the measurement: a PLC change, or a socket message."""

    def __init__(self, default: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.kind = w.Segments([("plc", "Tag change"), ("socket", "Socket message")])
        self.kind.set_value(default)
        layout.addWidget(self.kind)
        self.form = w.Form(label_width=86)
        layout.addWidget(self.form)
        self.tag = QComboBox()
        self.tag.setEditable(True)
        self.tag.setMinimumWidth(200)
        self.form.add("tag", "Tag", self.tag)
        self.value = QLineEdit()
        self.value.setPlaceholderText("any value")
        self.form.add("value", "Changes to", self.value,
                      tip="Only count changes to this value, e.g. True or 1. Blank for any change.")
        self.events = QComboBox()
        for text, kinds in EVENT_CHOICES:
            self.events.addItem(text, kinds)
        self.form.add("events", "Direction", self.events)
        self.source = QComboBox()
        self.source.setEditable(True)
        self.form.add("source", "Monitor", self.source, tip="Blank for any socket monitor.")
        self.contains = QLineEdit()
        self.contains.setPlaceholderText("anything")
        self.form.add("contains", "Containing", self.contains,
                      tip="Only messages whose text or hex contains this.")
        self.kind.changed.connect(self._kind)
        self._kind(default)

    def _kind(self, kind: str) -> None:
        plc = kind == "plc"
        for key in ("tag", "value"):
            self.form.show_row(key, plc)
        for key in ("events", "source", "contains"):
            self.form.show_row(key, not plc)

    def set_choices(self, tags: list[str], sources: list[str]) -> None:
        for box, items in ((self.tag, tags), (self.source, [""] + sources)):
            current = box.currentText()
            box.blockSignals(True)
            box.clear()
            box.addItems(items)
            if current:
                box.setCurrentText(current)
            box.blockSignals(False)

    def matcher(self) -> logdata.Matcher:
        if self.kind.value() == "plc":
            return logdata.Matcher(kind="plc", tag=self.tag.currentText().strip(),
                                   value=self.value.text().strip())
        return logdata.Matcher(kind="socket", events=tuple(self.events.currentData()),
                               source=self.source.currentText().strip(),
                               contains=self.contains.text().strip())


class PairModel(QAbstractTableModel):
    HEAD = ["Trigger at", "Trigger", "Response", "Latency"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pairs: list[logdata.Pair] = []

    def load(self, pairs) -> None:
        self.beginResetModel()
        self.pairs = list(pairs)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.pairs)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEAD)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEAD[section].upper()
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        p = self.pairs[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return datetime.fromtimestamp(p.trigger_ms / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            if col == 1:
                return p.trigger_text
            if col == 2:
                return p.response_text or "no response"
            return "" if p.delta_ms is None else f"{p.delta_ms:,} ms"
        if role == Qt.ForegroundRole:
            t = sheet.tokens()
            if p.delta_ms is None and col in (2, 3):
                return QColor(t["warn"])
            if col in (1, 2):
                return QColor(t["txt_1"])
        if role == Qt.TextAlignmentRole and col == 3:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.FontRole and col in (0, 3):
            font = QFont("Cascadia Mono")
            font.setStyleHint(QFont.Monospace)
            return font
        return None


class LatencyPage(Page):
    key = "latency"
    title = "Latency"
    glyph = "latency"
    blurb = ("Time from a tag change to the socket message that follows it, for every "
             "occurrence in a log.")

    _done = Signal(object, str)
    _choices = Signal(list, list)

    def __init__(self, session, parent=None) -> None:
        super().__init__(session, parent)
        self._extra: list[str] = []
        self._result: logdata.LatencyResult | None = None

        setup = w.Card("Measure")
        self.source = QComboBox()
        self.source.setMinimumWidth(240)
        self.range = QComboBox()
        for text, minutes in RANGES:
            self.range.addItem(text, minutes)
        setup.body.addLayout(w.row(
            w.label("Log", "fieldlabel"), self.source,
            w.button("Open file...", slot=self._browse),
            16, w.label("Range", "fieldlabel"), self.range, None))
        sides = w.row(spacing=24)
        self.trigger = MatcherEditor("plc")
        self.response = MatcherEditor("socket")
        for name, editor in (("Start the clock when", self.trigger),
                             ("Stop it at the next", self.response)):
            column = QVBoxLayout()
            column.setSpacing(6)
            column.addWidget(w.label(name.upper(), "cardtitle"))
            column.addWidget(editor)
            column.addStretch(1)
            sides.addLayout(column, 1)
        setup.body.addLayout(sides)
        self.give_up = QSpinBox()
        self.give_up.setRange(1, 3_600_000)
        self.give_up.setValue(5000)
        self.give_up.setSuffix(" ms")
        self.give_up.setSingleStep(500)
        self.go = w.button("Measure", "primary", icon_name="latency", slot=self.measure)
        self.export_btn = w.button("Export CSV", icon_name="download", slot=self._export)
        self.export_btn.setEnabled(False)
        setup.body.addLayout(w.row(
            w.label("Give up after", "fieldlabel"), self.give_up,
            w.label("A response later than this, or after the next trigger, does not count.",
                    "hint"), None, self.export_btn, self.go))
        self.body.addWidget(setup)

        stats = w.Card()
        tiles = w.row(spacing=28)
        self.tiles: dict[str, object] = {}
        for key, caption in (("count", "Answered"), ("min", "Fastest"), ("median", "Median"),
                             ("mean", "Mean"), ("p95", "95th percentile"), ("max", "Slowest"),
                             ("unanswered", "No response")):
            holder, value = w.big_stat(caption)
            self.tiles[key] = value
            tiles.addWidget(holder)
        tiles.addStretch(1)
        stats.body.addLayout(tiles)
        self.summary = w.label("Nothing measured yet.", "hint", wrap=True)
        stats.body.addWidget(self.summary)
        self.body.addWidget(stats)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        self.body.addWidget(split, 1)
        table_card = w.Card("Every trigger")
        self.model = PairModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table_card.body.addWidget(self.table, 1)
        split.addWidget(table_card)

        chart_card = w.Card("Distribution")
        self.chart_holder = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_holder)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_card.body.addWidget(self.chart_holder, 1)
        chart_card.setMinimumWidth(320)
        split.addWidget(chart_card)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self._build_chart()

        self._done.connect(self._show)
        self._choices.connect(lambda tags, srcs: (self.trigger.set_choices(tags, srcs),
                                                 self.response.set_choices(tags, srcs)))
        self.source.currentIndexChanged.connect(lambda _i: self._read_choices())
        session.logPathChanged.connect(lambda _p: self.refresh_sources())
        self.refresh_sources()

    # ----------------------------------------------------------- sources

    def refresh_sources(self) -> None:
        current = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("Live log", self.session.logger.log_path)
        for path in reversed(self.session.logger.list_archives()):
            self.source.addItem(os.path.basename(path), path)
        for path in self._extra:
            self.source.addItem(os.path.basename(path), path)
        self.source.setCurrentIndex(max(0, self.source.findData(current)))
        self.source.blockSignals(False)
        self._read_choices()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open a log", self.session.log_folder(),
                                              "PLC Monitor logs (*.csv *.csv.gz *.gz);;All files (*)")
        if path:
            if path not in self._extra:
                self._extra.append(path)
            self.refresh_sources()
            self.source.setCurrentIndex(self.source.findData(path))

    def _read_choices(self) -> None:
        path = self.source.currentData()
        if not path:
            return

        def work():
            try:
                tags, labels = logdata.sources(logdata.read_rows(path))
            except OSError:
                tags, labels = [], []
            self._choices.emit(tags, labels)

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ measure

    def measure(self) -> None:
        trigger, response = self.trigger.matcher(), self.response.matcher()
        if trigger.kind == "plc" and not trigger.tag:
            w.info(self, "Latency", "Choose the tag whose change starts the clock.")
            return
        path = self.source.currentData()
        minutes = self.range.currentData()
        start = None
        if minutes:
            start = int((datetime.now() - timedelta(minutes=minutes)).timestamp() * 1000)
        window = self.give_up.value()
        self.go.setEnabled(False)
        self.summary.setText("Measuring...")
        label = f"{trigger.describe()} to the next {response.describe()}"

        def work():
            try:
                result = logdata.measure(logdata.read_rows(path), trigger, response,
                                         window_ms=window, start_ms=start)
            except OSError as exc:
                result = exc
            self._done.emit(result, label)

        threading.Thread(target=work, daemon=True).start()

    def _show(self, result, label: str) -> None:
        self.go.setEnabled(True)
        if isinstance(result, Exception):
            self.summary.setText(f"Could not read the log: {result}")
            return
        self._result = result
        self.model.load(result.pairs)
        stats = result.stats()
        fmt = lambda v: "-" if v is None else (f"{v:,.0f}" if v >= 100 else f"{v:,.1f}")  # noqa: E731
        for key in ("min", "median", "mean", "p95", "max"):
            self.tiles[key].setText(f"{fmt(stats.get(key))} ms" if stats else "-")
        self.tiles["count"].setText(f"{stats.get('count', 0):,}")
        self.tiles["unanswered"].setText(f"{result.unanswered:,}")
        w.set_prop(self.tiles["unanswered"], "tone", "warn" if result.unanswered else "")
        if not result.pairs:
            self.summary.setText(f"No triggers found: {label}.")
        else:
            spread = f" Standard deviation {stats['stdev']:,.1f} ms." if stats else ""
            self.summary.setText(f"{len(result.pairs):,} triggers: {label}.{spread}")
        self.export_btn.setEnabled(bool(result.pairs))
        self._draw(result.answered)

    # -------------------------------------------------------------- chart

    def _build_chart(self) -> None:
        try:
            import matplotlib
            matplotlib.use("QtAgg")
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except Exception:  # pragma: no cover
            self.figure = None
            self.chart_layout.addWidget(w.label("matplotlib is not available.", "hint"))
            return
        self.figure = Figure(figsize=(4, 3), dpi=100)
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.chart_layout.addWidget(self.canvas, 1)
        self._draw([])

    def _draw(self, values: list[int]) -> None:
        if self.figure is None:
            return
        t = sheet.tokens()
        ax = self.axes
        ax.clear()
        self.figure.set_facecolor(t["bg_2"])
        ax.set_facecolor(t["bg_2"])
        for side, spine in ax.spines.items():
            spine.set_visible(side == "bottom")
            spine.set_color(t["line"])
        ax.tick_params(colors=t["txt_2"], labelsize=8, length=3)
        ax.grid(True, axis="y", color=t["line_soft"], linewidth=0.8)
        ax.set_axisbelow(True)
        if not values:
            ax.set_xticks([])
            ax.set_yticks([])
            ax.text(0.5, 0.5, "Latency histogram", transform=ax.transAxes, ha="center",
                    va="center", color=t["txt_2"], fontsize=10)
        else:
            palette = SERIES_LIGHT if t.get("theme_name") in LIGHT_THEMES else SERIES_DARK
            bins = min(40, max(5, int(len(values) ** 0.5) + 1))
            ax.hist(values, bins=bins, color=palette[0], edgecolor=t["bg_2"], linewidth=1.5)
            ax.set_xlabel("ms", color=t["txt_2"], fontsize=8)
            ax.set_ylabel("triggers", color=t["txt_2"], fontsize=8)
            stats = self._result.stats() if self._result else {}
            if "p95" in stats:
                ax.axvline(stats["p95"], color=t["txt_1"], linewidth=1, linestyle=(0, (3, 3)))
                ax.text(stats["p95"], ax.get_ylim()[1] * 0.96, " p95", color=t["txt_1"],
                        fontsize=8, va="top")
        self.figure.subplots_adjust(left=0.14, right=0.97, top=0.95, bottom=0.2)
        self.canvas.draw_idle()

    def _export(self) -> None:
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export latency pairs", "latency.csv",
                                              "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as handle:
            out = csv.writer(handle)
            out.writerow(["trigger_ms", "trigger_time", "trigger", "response_ms", "response",
                          "latency_ms"])
            for p in self._result.pairs:
                out.writerow([p.trigger_ms,
                              datetime.fromtimestamp(p.trigger_ms / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                              p.trigger_text, p.response_ms or "", p.response_text,
                              "" if p.delta_ms is None else p.delta_ms])
        self.session.status.emit(f"Saved {path}")

    def shown(self) -> None:
        self._read_choices()

    def theme_changed(self) -> None:
        super().theme_changed()
        self._draw(self._result.answered if self._result else [])
