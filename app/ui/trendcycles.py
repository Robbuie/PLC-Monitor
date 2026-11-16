"""Trend's Cycles view: every machine cycle laid over the others.

Pick the moment a cycle starts -- Cycle_Start rises, a VIN changes, a
register takes the value 3 -- and how much before and after it to look at.
Each plotted pen is then drawn once per cycle on a time axis that counts
from that moment, so a hundred cycles sit on top of each other. Where they
agree the lines pile into one; a cycle that ran late or did something
different stands out.

The shaded band is the envelope: at each moment of the cycle, where most of
the reference cycles were (5th to 95th percentile; with under ten, lowest to
highest), with their median as a dashed line. The table lists the cycles,
the one furthest outside the envelope first. Untick a cycle's Reference box
to leave a known-bad one out of the envelope. Click a cycle to pick it out on
the chart; double-click (or Show on the time chart) to see it in time.

An oscilloscope's trigger, then, but over every cycle in the log at once.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QHeaderView,
    QLineEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.engine import trendmath
from app.theme import sheet
from app.ui import widgets as w
from app.ui.trendchart import AXIS_W, colour

#: At most this many cycles are drawn as lines (all of them count for the
#: envelope and the table): past that the overlay is a solid wash.
MAX_DRAWN = 400
POINTS = 600


def _stamp(x: float) -> str:
    when = datetime.fromtimestamp(x)
    return f"{when:%H:%M:%S}.{when.microsecond // 1000:03d}"


def _steps(grid: np.ndarray, m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cycles x grid samples as one NaN-separated line of steps: each sample
    held until the next grid point, so an edge stays an edge."""
    n, k = m.shape
    xs = np.empty((n, 2 * k + 1))
    ys = np.empty((n, 2 * k + 1))
    step = grid[1] - grid[0] if k > 1 else 0.0
    xs[:, 0:2 * k:2] = grid
    xs[:, 1:2 * k:2] = grid + step
    ys[:, 0:2 * k:2] = m
    ys[:, 1:2 * k:2] = m
    xs[:, -1] = np.nan
    ys[:, -1] = np.nan
    return xs.ravel(), ys.ravel()


class CycleView(QWidget):
    #: Show this cycle on the time chart: (trigger time, before, after).
    jump = Signal(float, float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lines: list[dict] = []
        self.lanes: list[dict] = []
        self.right: float | None = None
        self.result: trendmath.Cycles | None = None
        self.picked: int | None = None
        self._colours: dict[str, str] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.trigger = QComboBox()
        self.trigger.setMinimumWidth(180)
        self.trigger.setToolTip("The tag whose change starts each cycle.")
        self.edge = QComboBox()
        for key, text in trendmath.EDGES:
            self.edge.addItem(text, key)
        self.value = QLineEdit()
        self.value.setPlaceholderText("value")
        self.value.setFixedWidth(110)
        self.before = QDoubleSpinBox()
        self.after = QDoubleSpinBox()
        for box, default, sign in ((self.before, 2.0, "-"), (self.after, 10.0, "+")):
            box.setRange(0.0, 86400.0)
            box.setDecimals(3)
            box.setPrefix(sign)
            box.setSuffix(" s")
            box.setValue(default)
        self.before.setToolTip("How long before the start of each cycle to show.")
        self.after.setToolTip("How long after the start of each cycle to show.")
        self.run_btn = w.button("Overlay", "primary", icon_name="trend", slot=self.run,
                                tip="Find every cycle start in what is plotted and lay the "
                                    "cycles over each other.")
        self.show_btn = w.button("Show in Time", "quiet", slot=self._jump_picked,
                                 tip="Go to the picked cycle on the time chart.")
        self.show_btn.setEnabled(False)
        outer.addLayout(w.row(w.label("Cycle starts when", "fieldlabel"), self.trigger,
                              self.edge, self.value, 12, w.label("Window", "fieldlabel"),
                              self.before, w.label("to", "hint"), self.after,
                              None, self.show_btn, self.run_btn))
        self.note = w.label("", "hint", wrap=True)
        outer.addWidget(self.note)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)
        self.chart = pg.GraphicsLayoutWidget()
        self.chart.ci.setSpacing(0)
        self.chart.ci.setContentsMargins(4, 6, 10, 4)
        split.addWidget(self.chart)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Cycle started", "Off the envelope", "Reference"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setMinimumWidth(320)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeaderItem(1).setToolTip(
            "How far the cycle strays outside the band most cycles stay in, averaged over "
            "the cycle and added up over the pens, as a share of each pen's range.")
        self.table.horizontalHeaderItem(2).setToolTip(
            "Untick a cycle you know was bad to leave it out of the envelope.")
        split.addWidget(self.table)
        split.setStretchFactor(0, 1)
        outer.addWidget(split, 1)

        self.table.itemSelectionChanged.connect(self._picked_row)
        self.table.itemChanged.connect(self._reference_changed)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._jump_picked())
        self.edge.currentIndexChanged.connect(self._edge_changed)
        self.trigger.currentIndexChanged.connect(self._edge_changed)
        self._edge_changed()
        self._message("Plot some tags on the Time view, choose what starts a cycle, and press "
                      "Overlay cycles.")

    # ------------------------------------------------------------ inputs

    def set_sources(self, lines: list[dict], lanes: list[dict], right: float | None) -> None:
        """The pens on the time chart: what can trigger and what is drawn."""
        self.lines, self.lanes, self.right = lines, lanes, right
        current = self.trigger.currentText()
        self.trigger.blockSignals(True)
        self.trigger.clear()
        for line in lines:
            self.trigger.addItem(line["tag"], "number")
        for lane in lanes:
            self.trigger.addItem(lane["tag"], "text")
        index = self.trigger.findText(current)
        if index < 0:
            # A bit is the usual cycle start.
            index = next((i for i, line in enumerate(lines) if line.get("lane")), 0)
        self.trigger.setCurrentIndex(index)
        self.trigger.blockSignals(False)
        self._edge_changed()

    def _edge_changed(self) -> None:
        text = self.trigger.currentData() == "text"
        model = self.edge.model()
        for i in range(self.edge.count()):
            key = self.edge.itemData(i)
            item = model.item(i)
            if item is not None:
                item.setEnabled(not text or key in ("equals", "any"))
        if text and self.edge.currentData() not in ("equals", "any"):
            self.edge.setCurrentIndex(self.edge.findData("any"))
        self.value.setVisible(self.edge.currentData() == "equals")

    def _series(self, name: str):
        for line in self.lines:
            if line["tag"] == name:
                return line["series"]
        for lane in self.lanes:
            if lane["tag"] == name:
                return lane["series"]
        return None

    # --------------------------------------------------------------- run

    def run(self, good: np.ndarray | None = None) -> None:
        name = self.trigger.currentText()
        series = self._series(name)
        if series is None:
            self._message("Plot some tags on the Time view first.")
            return
        triggers = trendmath.trigger_times(series, self.edge.currentData(),
                                           self.value.text().strip())
        if not len(triggers):
            self._message(f"{name} never {self.edge.currentText()}"
                          f"{' ' + self.value.text() if self.edge.currentData() == 'equals' else ''}"
                          " in what is plotted.")
            return
        pens = {line["tag"]: line["series"] for line in self.lines}
        self._colours = {line["tag"]: line["colour"] for line in self.lines}
        self.result = trendmath.cycles(triggers, pens, self.before.value(), self.after.value(),
                                       points=POINTS, good=good, right=self.right)
        self._draw()
        self._fill_table()
        n = len(self.result)
        drawn = min(n, MAX_DRAWN)
        what = f"{n:,} cycle{'s' if n != 1 else ''}"
        if drawn < n:
            what += f" (the latest {drawn} drawn; all of them count for the band)"
        reference = int(self.result.good.sum())
        self.note.setText(f"{what}, each from {name} {self.edge.currentText()}"
                          f"{' ' + self.value.text() if self.edge.currentData() == 'equals' else ''}"
                          f". Band: {reference} reference cycle{'s' if reference != 1 else ''}.")

    def _message(self, text: str) -> None:
        self.chart.clear()
        self.table.setRowCount(0)
        self.result = None
        self.picked = None
        self.show_btn.setEnabled(False)
        self.note.setText(text)

    def _draw(self) -> None:
        result = self.result
        self.chart.clear()
        self.chart.setBackground(colour("bg_2"))
        t = sheet.tokens()
        font = QFont(self.font())
        font.setPointSizeF(8)
        first = None
        names = [line["tag"] for line in self.lines if line["tag"] in result.samples]
        self._plots = {}
        for row, name in enumerate(names):
            plot = self.chart.addPlot(row=row, col=0)
            for axis in ("left", "bottom"):
                ax = plot.getAxis(axis)
                ax.setPen(pg.mkPen(QColor(t["line"])))
                ax.setTextPen(pg.mkPen(QColor(t["txt_2"])))
                ax.setStyle(tickFont=font)
            plot.getAxis("left").setWidth(AXIS_W)
            plot.hideButtons()
            plot.setMenuEnabled(False)
            plot.getViewBox().setMouseEnabled(x=True, y=False)
            plot.showGrid(x=True, y=True, alpha=0.2)
            plot.showAxis("bottom", row == len(names) - 1)
            if row == len(names) - 1:
                plot.setLabel("bottom", "seconds from the start of the cycle",
                              color=t["txt_2"])
            if first is None:
                first = plot
            else:
                plot.setXLink(first)
            ink = QColor(self._colours[name])
            band = QColor(ink)
            band.setAlphaF(0.18)
            low = pg.PlotDataItem(result.grid, result.low[name], pen=pg.mkPen(None))
            high = pg.PlotDataItem(result.grid, result.high[name], pen=pg.mkPen(None))
            plot.addItem(low)
            plot.addItem(high)
            plot.addItem(pg.FillBetweenItem(low, high, brush=pg.mkBrush(band)))
            faint = QColor(ink)
            faint.setAlphaF(0.22 if len(result) > 20 else 0.5)
            m = result.samples[name][-MAX_DRAWN:]
            xs, ys = _steps(result.grid, m)
            plot.addItem(pg.PlotDataItem(xs, ys, pen=pg.mkPen(faint, width=1),
                                         connect="finite", antialias=False))
            mid = pg.PlotDataItem(result.grid, result.mid[name],
                                  pen=pg.mkPen(ink, width=1.5, style=Qt.DashLine))
            plot.addItem(mid)
            zero = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen(colour("accent"), width=1))
            plot.addItem(zero, ignoreBounds=True)
            pick = pg.PlotDataItem(pen=pg.mkPen(colour("accent"), width=2.5), connect="finite")
            pick.setZValue(10)
            plot.addItem(pick)
            label = pg.TextItem(html=f'<span style="color:{self._colours[name]}">&#9632;</span>'
                                     f'&nbsp;<span style="color:{t["txt_1"]}">{name}</span>',
                                anchor=(0, 0), fill=pg.mkBrush(colour("bg_2", 0.6)))
            label.setParentItem(plot.getViewBox())
            label.setPos(2, 1)
            self._plots[name] = (plot, pick)
        if first is not None:
            first.setXRange(-result.before, result.after, padding=0.01)

    def _fill_table(self) -> None:
        result = self.result
        self.table.blockSignals(True)
        self.table.setRowCount(len(result))
        for row, index in enumerate(result.ranked()):
            when = float(result.triggers[index])
            stamp = QTableWidgetItem(_stamp(when))
            stamp.setData(Qt.UserRole, index)
            stamp.setToolTip(datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M:%S"))
            deviation = result.deviation[index]
            score = QTableWidgetItem("incomplete" if deviation != deviation
                                     else f"{deviation * 100:.1f} %")
            if deviation != deviation:
                score.setToolTip("The log ends before this cycle's window does: left out of "
                                 "the band and the ranking.")
            score.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            reference = QTableWidgetItem("")
            reference.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            reference.setCheckState(Qt.Checked if result.good[index] else Qt.Unchecked)
            self.table.setItem(row, 0, stamp)
            self.table.setItem(row, 1, score)
            self.table.setItem(row, 2, reference)
        self.table.blockSignals(False)
        if len(result):
            self.table.selectRow(0)

    def _reference_changed(self, item: QTableWidgetItem) -> None:
        if self.result is None or item.column() != 2:
            return
        good = self.result.good.copy()
        for row in range(self.table.rowCount()):
            index = self.table.item(row, 0).data(Qt.UserRole)
            good[index] = self.table.item(row, 2).checkState() == Qt.Checked
        if not good.any():
            return
        picked = self.picked
        self.run(good)
        if picked is not None:
            self.pick(picked)

    def _picked_row(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or self.result is None:
            return
        self.pick(self.table.item(rows[0].row(), 0).data(Qt.UserRole))

    def pick(self, index: int) -> None:
        """Draw cycle ``index`` over the others in the accent colour."""
        if self.result is None or not (0 <= index < len(self.result)):
            return
        self.picked = index
        self.show_btn.setEnabled(True)
        for name, (_plot, pick) in self._plots.items():
            m = self.result.samples[name][index:index + 1]
            xs, ys = _steps(self.result.grid, m)
            pick.setData(xs, ys, connect="finite")

    def _jump_picked(self) -> None:
        if self.result is None or self.picked is None:
            return
        self.jump.emit(float(self.result.triggers[self.picked]), self.result.before,
                       self.result.after)

    def theme_changed(self) -> None:
        if self.result is not None:
            picked = self.picked
            self._draw()
            if picked is not None:
                self.pick(picked)
