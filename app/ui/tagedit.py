"""Editing one tag: its name, and how its registers are read.

Also how several consecutive registers become one grouped tag -- D1916 to
D1919 read as the 8-character VIN they hold.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QSpinBox, QVBoxLayout

from app.engine.tagspec import FORMATS, WIDTH, TagSpec
from app.ui import widgets as w


class TagEditDialog(QDialog):
    def __init__(self, spec: TagSpec, title: str, typed: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self._dtype = spec.dtype
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        form = w.Form(label_width=110)
        self.name = QLineEdit(spec.name)
        self.name.setPlaceholderText("Plant VIN, Sent VIN, Part count... (optional)")
        self.address = QLineEdit(spec.address)
        self.address.setProperty("role", "mono")
        self.count = QSpinBox()
        self.count.setRange(1, 960)
        self.count.setValue(spec.count)
        self.fmt = QComboBox()
        for key, label in FORMATS.items():
            self.fmt.addItem(label, key)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(spec.fmt)))
        self.swap_bytes = w.Switch()
        self.swap_bytes.setChecked(spec.swap_bytes)
        self.swap_words = w.Switch()
        self.swap_words.setChecked(spec.swap_words)
        form.add("name", "Name", self.name,
                 hint="What the log, Trend and Latency call it. Blank: the address.")
        form.add("address", "Address", self.address,
                 hint="The first register: D1916, W100, DB10,WORD4, Data[16].")
        form.add("count", "Registers", self.count,
                 hint="How many consecutive registers, read in one request so they "
                      "come from the same PLC scan.")
        form.add("fmt", "Read as", self.fmt)
        form.add("swapb", "Swap bytes", w.hbox(self.swap_bytes, w.label(
            "Text: first character in the high byte (Mitsubishi puts it low).", "hint",
            wrap=True), stretch_at=1))
        form.add("swapw", "Swap words", w.hbox(self.swap_words, w.label(
            "32-bit and BCD: high word first (Mitsubishi puts the low word first).", "hint",
            wrap=True), stretch_at=1))
        outer.addWidget(form)
        self.preview = w.label("", "hint", wrap=True)
        outer.addWidget(self.preview)
        self.ok = w.button("Save", "primary", slot=self.accept)
        self.ok.setDefault(True)
        outer.addLayout(w.row(None, w.button("Cancel", slot=self.reject), self.ok))
        for signal in (self.name.textChanged, self.address.textChanged,
                       self.count.valueChanged, self.fmt.currentIndexChanged,
                       self.swap_bytes.toggled, self.swap_words.toggled):
            signal.connect(lambda *_: self._update())
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self._update()

    def _fmt_changed(self, _index: int) -> None:
        # A 32-bit format needs at least two registers; offer it.
        width = WIDTH.get(self.fmt.currentData() or "", 1)
        if self.count.value() < width:
            self.count.setValue(width)

    def spec(self) -> TagSpec:
        return TagSpec(address=self.address.text().strip(), count=self.count.value(),
                       fmt=self.fmt.currentData() or "", name=self.name.text().strip(),
                       swap_bytes=self.swap_bytes.isChecked(),
                       swap_words=self.swap_words.isChecked(), dtype=self._dtype)

    def _update(self) -> None:
        spec = self.spec()
        fmt = spec.fmt
        problem = ""
        if not spec.address:
            problem = "Type the first register's address."
        elif WIDTH.get(fmt) and spec.count % WIDTH[fmt]:
            problem = f"{FORMATS[fmt]} needs an even number of registers."
        self.swap_bytes.setEnabled(fmt in ("ascii", "digits"))
        self.swap_words.setEnabled(fmt in ("int32", "uint32", "float32", "bcd"))
        self.ok.setEnabled(not problem)
        if problem:
            self.preview.setText(problem)
            w.set_prop(self.preview, "tone", "down")
        else:
            self.preview.setText(f"Logged as {spec.key}: {spec.describe()}.")
            w.set_prop(self.preview, "tone", "")
