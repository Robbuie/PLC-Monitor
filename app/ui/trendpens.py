"""The Trend page's value bar and the dialog behind each of its rows.

The value bar is the table under the chart, one row per pen: the value at
the pointer (or the latest, when the pointer is elsewhere), the values at
cursors A and B and the difference between them, and the lowest, highest,
time-weighted average and number of changes -- over what is in view, or
between the cursors when they are on. It is what Rockwell's trend calls the
value bar, with the delta its cursors give, laid out so eight pens can be
read at a glance.

Double-clicking a row opens PenDialog: a description, units, a fixed scale,
and whether a 0/1 tag is drawn as a lane or a chart. Those are saved in the
profile (engine/trendprefs.py).
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.engine.trendview import text_of
from app.ui import widgets as w

COLUMNS = ["Tag", "At pointer", "A", "B", "B - A", "Min", "Max", "Average", "Changes", "Flags"]
TAG, POINTER, A, B, DELTA, MIN, MAX, AVG, CHANGES, FLAGS = range(len(COLUMNS))
CURSOR_COLUMNS = (A, B, DELTA)


def stamp(x: float) -> str:
    when = datetime.fromtimestamp(x)
    return f"{when:%H:%M:%S}.{when.microsecond // 1000:03d}"


def _swatch(hexcolour: str) -> QIcon:
    pixmap = QPixmap(10, 10)
    pixmap.fill(QColor(hexcolour))
    return QIcon(pixmap)


class PenTable(QTableWidget):
    """One row per pen. The page calls show_pens() after drawing, then
    update_values() / update_stats() as the pointer, cursors and view move."""

    #: A row was double-clicked: its tag.
    edit = Signal(str)
    #: A row's Flags cell was clicked: go to that pen's next flag.
    nextFlag = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setFocusPolicy(Qt.NoFocus)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(TAG, QHeaderView.Stretch)
        for col in range(1, len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setMinimumSectionSize(64)
        self.horizontalHeaderItem(AVG).setToolTip(
            "Time-weighted: a value held for an hour counts for an hour, not as one row.")
        self.horizontalHeaderItem(CHANGES).setToolTip("How many times the value changed.")
        self.horizontalHeaderItem(DELTA).setToolTip("The value at B less the value at A.")
        self.horizontalHeaderItem(FLAGS).setToolTip(
            "What the tag's rules point out -- over or under a limit, no change for too "
            "long, a counter jumping. Click the count to go to the next one.")
        self.cellClicked.connect(self._clicked)
        self.pens: list[dict] = []
        self.cellDoubleClicked.connect(self._double)
        self.setToolTip("Double-click a tag for its description, units and scale.")
        self.set_cursors(False)

    def _clicked(self, row: int, col: int) -> None:
        if col == FLAGS and 0 <= row < len(self.pens) and self.pens[row].get("flags"):
            self.nextFlag.emit(self.pens[row]["tag"])

    def _double(self, row: int, _col: int) -> None:
        if 0 <= row < len(self.pens):
            self.edit.emit(self.pens[row]["tag"])

    def set_cursors(self, on: bool) -> None:
        for col in CURSOR_COLUMNS:
            self.setColumnHidden(col, not on)
        where = "between A and B" if on else "in view"
        for col, name in ((MIN, "Min"), (MAX, "Max"), (AVG, "Average"), (CHANGES, "Changes")):
            self.horizontalHeaderItem(col).setText(name)
            self.horizontalHeaderItem(col).setStatusTip(where)

    def show_pens(self, pens: list[dict]) -> None:
        """``pens``: {"tag", "colour", "series", "kind": "number"|"text",
        "units", "description"} in the order the chart draws them."""
        self.pens = pens
        self.setRowCount(len(pens))
        for row, pen in enumerate(pens):
            name = pen["tag"] + (f'  ({pen["units"]})' if pen.get("units") else "")
            item = QTableWidgetItem(_swatch(pen["colour"]), name)
            tip = pen["tag"]
            if pen.get("description"):
                tip += "\n" + pen["description"]
            if pen.get("math"):
                tip += "\nMath pen: " + pen["math"]
            item.setToolTip(tip + "\nDouble-click for description, units, scale and flags.")
            self.setItem(row, TAG, item)
            for col in range(1, len(COLUMNS)):
                cell = QTableWidgetItem("")
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.setItem(row, col, cell)
        self.fit_height()

    def fit_height(self) -> None:
        rows = max(self.rowCount(), 1)
        height = self.horizontalHeader().height() + sum(
            self.rowHeight(r) for r in range(self.rowCount())) + 4
        self.setMinimumHeight(min(height, self.horizontalHeader().height() + 3 * 26))
        self.setMaximumHeight(max(height, self.horizontalHeader().height() + rows * 20))

    def _set(self, row: int, col: int, text: str) -> None:
        item = self.item(row, col)
        if item is not None and item.text() != text:
            item.setText(text)

    def update_values(self, pointer: float | None, latest: float | None,
                      cursors: dict | None, right: float | None) -> None:
        """The point-in-time columns: at the pointer (or the latest value,
        when the pointer is off the chart) and at the cursors."""
        self.horizontalHeaderItem(POINTER).setText("At pointer" if pointer is not None
                                                   else "Latest")
        at = pointer if pointer is not None else latest
        for row, pen in enumerate(self.pens):
            series = pen["series"]
            if pen["kind"] == "text":
                def value(x, s=series):
                    hit = s.at(x, right) if x is not None else None
                    return hit[2] if hit else ""
                self._set(row, POINTER, value(at))
                if cursors:
                    self._set(row, A, value(cursors["A"]))
                    self._set(row, B, value(cursors["B"]))
                    self._set(row, DELTA, "changed" if value(cursors["A"]) != value(cursors["B"])
                              else "same")
                continue
            self._set(row, POINTER, text_of(series.value_at(at)) if at is not None else "")
            if cursors:
                va, vb = series.value_at(cursors["A"]), series.value_at(cursors["B"])
                self._set(row, A, text_of(va))
                self._set(row, B, text_of(vb))
                ok = va is not None and vb is not None and va == va and vb == vb
                self._set(row, DELTA, text_of(vb - va) if ok else "")

    def update_stats(self, x0: float, x1: float, right: float | None) -> None:
        if x1 < x0:
            x0, x1 = x1, x0
        for row, pen in enumerate(self.pens):
            series = pen["series"]
            if pen["kind"] == "text":
                for col in (MIN, MAX, AVG):
                    self._set(row, col, "")
                self._set(row, CHANGES, f"{series.changes_between(x0, x1):,}")
                continue
            st = series.stats(x0, x1, right)
            self._set(row, MIN, text_of(st["min"]))
            self._set(row, MAX, text_of(st["max"]))
            avg = st["avg"]
            self._set(row, AVG, "" if avg is None else f"{avg:.5g}")
            self._set(row, CHANGES, f'{st["changes"]:,}')
            found = [f for f in pen.get("flags") or [] if f.end >= x0 and f.start <= x1]
            self._set(row, FLAGS, f"{len(found):,}" if found else ("0" if pen.get("rules")
                                                                   else ""))
            item = self.item(row, FLAGS)
            if item is not None:
                item.setToolTip("\n".join(f.text for f in found[:8])
                                + (f"\n... and {len(found) - 8} more" if len(found) > 8 else ""))


class PenDialog(QDialog):
    """Description, units, scale, drawing and flag rules for one tag."""

    def __init__(self, tag: str, settings: dict, *, text: bool = False,
                 boolean: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Pen: {tag}")
        self.setMinimumWidth(440)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        form = w.Form(label_width=100)
        self.description = QLineEdit(settings.get("description", ""))
        self.description.setPlaceholderText("Conveyor 3 speed, Press 2 clamp closed...")
        self.units = QLineEdit(settings.get("units", ""))
        self.units.setPlaceholderText("mm/s, degC, parts...")
        self.scale = QComboBox()
        self.scale.addItem("Automatic: fit what is in view", "auto")
        self.scale.addItem("Fixed", "fixed")
        scale = settings.get("scale")
        self.low = QLineEdit("" if not scale else text_of(scale[0]))
        self.high = QLineEdit("" if not scale else text_of(scale[1]))
        for edit, hint in ((self.low, "low"), (self.high, "high")):
            # C locale: a decimal point whatever Windows' region says, so the
            # value parses the same on every PC the profile is opened on.
            validator = QDoubleValidator(edit)
            validator.setLocale(QLocale.c())
            validator.setNotation(QDoubleValidator.StandardNotation)
            edit.setValidator(validator)
            edit.setPlaceholderText(hint)
        self.scale.setCurrentIndex(1 if scale else 0)
        self.draw = QComboBox()
        self.draw.addItem("Automatic: a lane for a 0/1 tag", "auto")
        self.draw.addItem("A chart", "line")
        self.draw.addItem("A lane, like a logic analyser", "lane")
        self.draw.setCurrentIndex(max(self.draw.findData(settings.get("draw", "auto")), 0))
        form.add("description", "Description", self.description)
        if not text:
            form.add("units", "Units", self.units)
            form.add("scale", "Value scale", self.scale)
            form.add("range", "Range", w.hbox(self.low, w.label("to", "hint"), self.high,
                                              spacing=6))
            form.add("draw", "Draw as", self.draw,
                     hint="This tag only holds 0 and 1." if boolean else "")
            self.rule_high = self._number(settings.get("high"), "no limit")
            self.rule_low = self._number(settings.get("low"), "no limit")
            self.rule_stuck = self._number(settings.get("stuck_s"), "never")
            self.rule_jump = self._number(settings.get("jump"), "not a counter")
            form.add("high", "Flag over", self.rule_high,
                     hint="Shaded on the chart and counted in the value bar's Flags.")
            form.add("low", "Flag under", self.rule_low)
            form.add("stuck", "Flag no change for", w.hbox(self.rule_stuck,
                                                         w.label("seconds", "hint"), spacing=6))
            form.add("jump", "Counter step over", self.rule_jump,
                     hint="For a counter: a step bigger than this (missed pulses) or any "
                          "step backwards is flagged. 1 for a counter that counts in ones.")
        outer.addWidget(form)
        self.problem = w.label("", "hint", wrap=True)
        w.set_prop(self.problem, "tone", "down")
        self.problem.setVisible(False)
        outer.addWidget(self.problem)
        note = w.label("Saved in this profile, so they travel with it when it is exported.",
                       "hint", wrap=True)
        outer.addWidget(note)
        self.ok = w.button("Save", "primary", slot=self._accept)
        self.ok.setDefault(True)
        outer.addLayout(w.row(None, w.button("Cancel", slot=self.reject), self.ok))
        self.scale.currentIndexChanged.connect(self._scale_mode)
        self._form = form
        self._scale_mode()

    @staticmethod
    def _number(value, placeholder: str) -> QLineEdit:
        edit = QLineEdit("" if value is None else text_of(value))
        validator = QDoubleValidator(edit)
        validator.setLocale(QLocale.c())
        validator.setNotation(QDoubleValidator.StandardNotation)
        edit.setValidator(validator)
        edit.setPlaceholderText(placeholder)
        return edit

    def _scale_mode(self) -> None:
        self._form.enable_row("range", self.scale.currentData() == "fixed")

    def values(self) -> dict:
        scale = None
        if self.scale.currentData() == "fixed":
            try:
                scale = [float(self.low.text()), float(self.high.text())]
            except ValueError:
                scale = None
        out = {"description": self.description.text().strip(),
               "units": self.units.text().strip(), "scale": scale,
               "draw": self.draw.currentData()}
        for key, edit in (("high", getattr(self, "rule_high", None)),
                          ("low", getattr(self, "rule_low", None)),
                          ("stuck_s", getattr(self, "rule_stuck", None)),
                          ("jump", getattr(self, "rule_jump", None))):
            if edit is not None:
                try:
                    out[key] = float(edit.text()) if edit.text().strip() else None
                except ValueError:
                    out[key] = None
        return out

    def _accept(self) -> None:
        values = self.values()
        if self.scale.currentData() == "fixed":
            scale = values["scale"]
            if scale is None or scale[0] >= scale[1]:
                self.problem.setText("A fixed scale needs a low and a high, low below high.")
                self.problem.setVisible(True)
                return
        self.accept()
