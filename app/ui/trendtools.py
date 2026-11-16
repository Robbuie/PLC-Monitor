"""Trend's dialogs for math pens and for comparing against another time.

A math pen is worked out from tags already in the log (engine/trendmath):
A minus B, a counter's rate, how long each state lasted. It is saved in the
profile and appears in the tag list like a tag.

Compare draws the same tags from another time -- the same hour yesterday,
last week, or a log from another PC -- as dashed lines on this chart, shifted
so the two line up.
"""

from __future__ import annotations

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDoubleSpinBox,
    QLineEdit,
    QVBoxLayout,
)

from app.engine.trendmath import MATH_OPS, NEEDS_B
from app.ui import widgets as w


class MathDialog(QDialog):
    """Name a pen and say how it is worked out. ``deleted`` is set when the
    person pressed Delete on an existing one."""

    def __init__(self, sources: list[str], *, name: str = "", spec: dict | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        spec = spec or {}
        self.deleted = False
        self.setWindowTitle("Math pen" if not name else f"Math pen: {name}")
        self.setMinimumWidth(480)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        form = w.Form(label_width=110)
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("Clamp to robot gap, Parts per minute...")
        self.op = QComboBox()
        for key, text, _needs in MATH_OPS:
            self.op.addItem(text, key)
        self.op.setCurrentIndex(max(self.op.findData(spec.get("op", "diff")), 0))
        self.a = QComboBox()
        self.b = QComboBox()
        for box in (self.a, self.b):
            box.setEditable(True)
            box.addItems(sources)
        self.a.setCurrentText(spec.get("a", sources[0] if sources else ""))
        self.b.setCurrentText(spec.get("b", sources[1] if len(sources) > 1 else ""))
        self.window_s = QDoubleSpinBox()
        self.window_s.setRange(0.1, 86400)
        self.window_s.setDecimals(1)
        self.window_s.setSuffix(" s")
        self.window_s.setValue(float(spec.get("window_s") or 60))
        self.per = QComboBox()
        self.per.addItem("per second", "s")
        self.per.addItem("per minute", "min")
        self.per.setCurrentIndex(1 if spec.get("per") == "min" else 0)
        self.value = QLineEdit(str(spec.get("value", "")))
        self.value.setPlaceholderText("blank: every state")
        form.add("name", "Name", self.name)
        form.add("op", "Worked out as", self.op)
        form.add("a", "A", self.a)
        form.add("b", "B", self.b)
        form.add("window", "Over the last", self.window_s,
                 hint="The rate is the change over this window, so a counter that "
                      "steps once a cycle reads as a steady cycles-per-minute.")
        form.add("per", "Rate", self.per)
        form.add("value", "Only the state", self.value,
                 hint="1 measures how long the tag was 1 each time (a pulse width).")
        outer.addWidget(form)
        self.form = form
        self.problem = w.label("", "hint", wrap=True)
        w.set_prop(self.problem, "tone", "down")
        self.problem.setVisible(False)
        outer.addWidget(self.problem)
        outer.addWidget(w.label("Saved in this profile. It is listed with the tags and "
                                "drawn, measured and exported like one.", "hint", wrap=True))
        self.ok = w.button("Save", "primary", slot=self._accept)
        self.ok.setDefault(True)
        buttons = [None, w.button("Cancel", slot=self.reject), self.ok]
        if name:
            buttons.insert(0, w.button("Delete", "danger", slot=self._delete))
        outer.addLayout(w.row(*buttons))
        self.op.currentIndexChanged.connect(self._rows)
        self._rows()

    def _rows(self) -> None:
        op = self.op.currentData()
        self.form.show_row("b", op in NEEDS_B)
        self.form.show_row("window", op == "rate")
        self.form.show_row("per", op == "rate")
        self.form.show_row("value", op == "duration")

    def result_name(self) -> str:
        return " ".join(self.name.text().split())

    def spec(self) -> dict:
        return {"op": self.op.currentData(), "a": self.a.currentText().strip(),
                "b": self.b.currentText().strip(), "window_s": self.window_s.value(),
                "per": self.per.currentData(), "value": self.value.text().strip()}

    def _accept(self) -> None:
        spec = self.spec()
        problem = ""
        if not self.result_name():
            problem = "Give it a name."
        elif not spec["a"]:
            problem = "Choose A."
        elif spec["op"] in NEEDS_B and not spec["b"]:
            problem = "Choose B."
        elif spec["op"] == "duration" and spec["value"]:
            try:
                float(spec["value"])
            except ValueError:
                problem = "The state is a number: 1, 0, 3..."
        if problem:
            self.problem.setText(problem)
            self.problem.setVisible(True)
            return
        self.accept()

    def _delete(self) -> None:
        if w.ask(self, "Delete math pen", f"Delete '{self.result_name()}'?", yes="Delete"):
            self.deleted = True
            self.accept()


SHIFTS = [("day", "The same time, a day earlier", 86400.0),
          ("week", "The same time, a week earlier", 7 * 86400.0),
          ("none", "The same time (another log)", 0.0),
          ("start", "From a moment I choose", None)]


class CompareDialog(QDialog):
    """Which log, and which time of it, to draw beside this one. ``stopped``
    is set when Stop comparing was pressed."""

    def __init__(self, sources: list[tuple[str, str]], current: str, view_left: float,
                 *, comparing: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.stopped = False
        self.view_left = view_left
        self.setWindowTitle("Compare with another time")
        self.setMinimumWidth(500)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        form = w.Form(label_width=90)
        self.source = QComboBox()
        for label, path in sources:
            self.source.addItem(label, path)
        self.source.setCurrentIndex(max(self.source.findData(current), 0))
        self.shift = QComboBox()
        for key, text, _seconds in SHIFTS:
            self.shift.addItem(text, key)
        self.start = QDateTimeEdit(QDateTime.fromSecsSinceEpoch(int(view_left - 86400)))
        self.start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.start.setCalendarPopup(True)
        form.add("source", "Log", self.source,
                 hint="An archive holds earlier days; Open file... on the page adds a log "
                      "from another PC.")
        form.add("shift", "Time", self.shift)
        form.add("start", "Starting", self.start,
                 hint="Lined up with the left edge of the chart.")
        outer.addWidget(form)
        self.form = form
        outer.addWidget(w.label("The ticked tags are drawn dashed in their own colours; the "
                                "readout gives both values.", "hint", wrap=True))
        ok = w.button("Compare", "primary", slot=self.accept)
        ok.setDefault(True)
        buttons = [None, w.button("Cancel", slot=self.reject), ok]
        if comparing:
            buttons.insert(0, w.button("Stop comparing", slot=self._stop))
        outer.addLayout(w.row(*buttons))
        self.shift.currentIndexChanged.connect(
            lambda: form.show_row("start", self.shift.currentData() == "start"))
        form.show_row("start", False)

    def _stop(self) -> None:
        self.stopped = True
        self.accept()

    def choice(self) -> tuple[str, float, str]:
        """(log path, seconds to add to its times, a label for the readout)."""
        key = self.shift.currentData()
        if key == "start":
            offset = self.view_left - self.start.dateTime().toSecsSinceEpoch()
            label = f"from {self.start.text()}"
        else:
            offset = next(sec for k, _t, sec in SHIFTS if k == key)
            label = {"day": "a day earlier", "week": "a week earlier",
                     "none": self.source.currentText()}[key]
        return self.source.currentData(), float(offset), label

