"""Tags: connect to a PLC and watch tag values change.

The connection card on the left, the tag table on the right. The fields shown
follow the driver; the hidden ones keep what was typed, so switching from
Rockwell to Siemens and back loses nothing. Each driver keeps its own tag list
for the same reason.

The table is a model rather than a widget full of items, because a poll every
100 ms across fifty tags is five hundred updates a second and a model can
answer that with one repaint of the cells that changed. A value that has just
changed is washed in the PLC-change green for a moment and fades out, which is
the thing the eye looks for when watching a line run.
"""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyledItemDelegate,
    QTableView,
)

from app.core import siblings
from app.engine.libplctag_driver import CPU_TYPES, DATA_TYPES, PATH_HELP, PROTOCOLS
from app.engine.plc_drivers import DEFAULT_PORTS, DEFAULT_SLOTS, DRIVER_MAP, TAG_EXAMPLES
from app.theme import sheet
from app.ui import widgets as w
from app.ui.pages.base import Page

LPT = "Rockwell (libplctag)"
PYCOMM3 = "Rockwell (pycomm3)"
SIEMENS = "Siemens (snap7)"
MITSU = "Mitsubishi (MC Proto)"
AUTO_IFACE = "Automatic (Windows picks by route)"

#: Seconds a changed value stays lit.
FLASH = 1.6


class TagModel(QAbstractTableModel):
    #: A tag renamed or retyped in the table, as opposed to a live value.
    edited = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[dict] = []
        self.typed = False
        self._index: dict[str, int] = {}

    # --- structure
    def columns(self) -> list[str]:
        return (["Tag", "Type", "Value", "Last changed", "Changes"] if self.typed
                else ["Tag", "Value", "Last changed", "Changes"])

    def load(self, entries: list[dict], typed: bool) -> None:
        self.beginResetModel()
        self.typed = typed
        self.rows = [{"tag": e["tag"], "dtype": e.get("dtype", "DINT"), "value": "",
                      "changed": "", "count": 0, "lit": 0.0, "error": False}
                     for e in entries if e.get("tag")]
        self._reindex()
        self.endResetModel()

    def entries(self) -> list[dict]:
        return [{"tag": r["tag"], "dtype": r["dtype"]} for r in self.rows]

    def _reindex(self) -> None:
        self._index = {r["tag"]: i for i, r in enumerate(self.rows)}

    def add(self, tag: str, dtype: str) -> bool:
        if tag in self._index:
            return False
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows))
        self.rows.append({"tag": tag, "dtype": dtype, "value": "", "changed": "",
                          "count": 0, "lit": 0.0, "error": False})
        self._reindex()
        self.endInsertRows()
        return True

    def remove(self, indexes: list[int]) -> None:
        for i in sorted(set(indexes), reverse=True):
            self.beginRemoveRows(QModelIndex(), i, i)
            del self.rows[i]
            self.endRemoveRows()
        self._reindex()

    def clear_values(self) -> None:
        for r in self.rows:
            r.update(value="", changed="", count=0, lit=0.0, error=False)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, self.columnCount() - 1))

    # --- live values
    def update_value(self, tag: str, value, stamp: str) -> None:
        i = self._index.get(tag)
        if i is None:
            return
        r = self.rows[i]
        text = str(value)
        error = text.startswith("ERROR")
        if text != r["value"]:
            if r["value"] != "" and not error:
                r["count"] += 1
                r["changed"] = stamp
                r["lit"] = time.monotonic()
            elif r["value"] == "":
                r["changed"] = r["changed"] or ""
            r["value"] = text
            r["error"] = error
            self.dataChanged.emit(self.index(i, 0), self.index(i, self.columnCount() - 1))

    def any_lit(self) -> bool:
        now = time.monotonic()
        return any(now - r["lit"] < FLASH for r in self.rows)

    def repaint_lit(self) -> None:
        now = time.monotonic()
        for i, r in enumerate(self.rows):
            if r["lit"] and now - r["lit"] < FLASH + 0.2:
                self.dataChanged.emit(self.index(i, 0), self.index(i, self.columnCount() - 1),
                                      [Qt.BackgroundRole])

    # --- Qt
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns())

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.columns()[section].upper()
        return None

    def _key(self, column: int) -> str:
        return {"Tag": "tag", "Type": "dtype", "Value": "value", "Last changed": "changed",
                "Changes": "count"}[self.columns()[column]]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        key = self._key(index.column())
        t = sheet.tokens()
        if role in (Qt.DisplayRole, Qt.EditRole):
            value = r[key]
            if key == "value" and value == "":
                return "waiting" if role == Qt.DisplayRole else ""
            if key == "count":
                return str(value) if value else ""
            return str(value)
        if role == Qt.ForegroundRole:
            if key == "value" and r["error"]:
                return QColor(t["down"])
            if key == "value" and r["value"] == "":
                return QColor(t["txt_2"])
            if key in ("changed", "count", "dtype"):
                return QColor(t["txt_1"])
        if role == Qt.BackgroundRole and r["lit"]:
            age = time.monotonic() - r["lit"]
            if age < FLASH:
                colour = QColor(t["ev_plc"])
                colour.setAlphaF(0.22 * (1 - age / FLASH))
                return colour
        if role == Qt.FontRole and key in ("value", "changed"):
            from PySide6.QtGui import QFont
            font = QFont("Cascadia Mono")
            font.setStyleHint(QFont.Monospace)
            return font
        if role == Qt.TextAlignmentRole and key in ("count",):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and key == "value" and r["error"]:
            return r["value"]
        return None

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self._key(index.column()) in ("tag", "dtype"):
            return base | Qt.ItemIsEditable
        return base

    def setData(self, index, value, role=Qt.EditRole):  # noqa: N802
        if role != Qt.EditRole or not index.isValid():
            return False
        key = self._key(index.column())
        text = str(value).strip()
        if not text:
            return False
        r = self.rows[index.row()]
        if key == "tag":
            if text != r["tag"] and text in self._index:
                return False
            r["tag"] = text
            r.update(value="", changed="", count=0)
            self._reindex()
        elif key == "dtype":
            r["dtype"] = text.upper()
        self.dataChanged.emit(self.index(index.row(), 0),
                              self.index(index.row(), self.columnCount() - 1))
        self.edited.emit()
        return True


class TypeDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):  # noqa: N802
        box = QComboBox(parent)
        box.addItems(DATA_TYPES)
        return box

    def setEditorData(self, editor, index):  # noqa: N802
        editor.setCurrentText(str(index.data(Qt.EditRole)))

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.currentText(), Qt.EditRole)


class TagsPage(Page):
    key = "tags"
    title = "Tags"
    glyph = "tags"
    blurb = ("Poll tags and log each change with a millisecond timestamp. "
             "Choose the PLC family first; the fields follow it.")

    _dll = Signal(str)

    def __init__(self, session, parent=None) -> None:
        super().__init__(session, parent)
        self._current_driver = ""
        self._tags: dict[str, list[dict]] = {}
        self._iface_ips: dict[str, str] = {}

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        self.body.addWidget(split, 1)

        # ------------------------------------------------ connection card
        conn = w.Card("Connection")
        form = self.form = w.Form(label_width=108)
        conn.body.addWidget(form)

        self.driver = QComboBox()
        self.driver.addItems(list(DRIVER_MAP.keys()))
        form.add("driver", "PLC family", self.driver, tip=(
            "pycomm3: ControlLogix and CompactLogix over EtherNet/IP. The usual choice.\n"
            "libplctag: Rockwell families pycomm3 does not cover, through plctag.dll.\n"
            "snap7: Siemens S7-300/400/1200/1500.\n"
            "MC Proto: Mitsubishi iQ-R, Q, L and FX."))

        self.ip = QLineEdit()
        self.ip.setProperty("role", "mono")
        self.ip.setPlaceholderText("192.168.1.10")
        self.nc_button = w.tool("external", "Diagnose this address in NetControl",
                                self._open_netcontrol)
        form.add("ip", "IP address", w.hbox(self.ip, self.nc_button, stretch_at=0, spacing=4),
                 tip="The PLC's IPv4 address. This PC needs an adapter on the same subnet.")

        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        form.add("port", "Port", self.port,
                 tip="44818 EtherNet/IP, 102 Siemens S7, 5006 Mitsubishi MC. "
                     "The Network page can find it if you are unsure.")

        self.slot = QSpinBox()
        self.slot.setRange(0, 31)
        form.add("slot", "CPU slot", self.slot,
                 hint="The controller's slot in the chassis, not the Ethernet module's.",
                 tip="Example: Ethernet bridge in slot 0, controller in slot 1: enter 1.\n"
                     "CompactLogix is usually 0.")
        self.rack = QSpinBox()
        self.rack.setRange(0, 7)
        form.add("rack", "Rack", self.rack, tip="Usually 0 on a single-rack system.")
        self.s7slot = QSpinBox()
        self.s7slot.setRange(0, 31)
        form.add("s7slot", "Slot", self.s7slot,
                 tip="The CPU's slot. S7-300 is usually 2; S7-1200 and 1500 usually 1.")

        self.series = QComboBox()
        self.series.addItems(["Q", "L", "QnA", "iQ-L", "iQ-R"])
        form.add("series", "Series", self.series)
        self.frame = QComboBox()
        self.frame.addItems(["3E", "1E"])
        form.add("frame", "Frame", self.frame)

        self.lpt_path = QLineEdit()
        self.lpt_path.setProperty("role", "mono")
        path_help = w.tool("book", "What goes in the path", lambda: w.info(self, "libplctag path", PATH_HELP))
        form.add("lpt_path", "Path", w.hbox(self.lpt_path, path_help, stretch_at=0, spacing=4),
                 tip='"1,0" is the controller in slot 0. Leave empty for a Micro800 or MicroLogix.')
        self.lpt_cpu = QComboBox()
        self.lpt_cpu.setEditable(True)
        self.lpt_cpu.addItems(CPU_TYPES)
        form.add("lpt_cpu", "CPU type", self.lpt_cpu)
        self.lpt_proto = QComboBox()
        self.lpt_proto.addItems(PROTOCOLS)
        form.add("lpt_proto", "Protocol", self.lpt_proto)
        self.lpt_type = QComboBox()
        self.lpt_type.addItems(DATA_TYPES)
        form.add("lpt_type", "Default type", self.lpt_type,
                 tip="Used for any tag without its own type in the table.")
        self.lpt_timeout = QSpinBox()
        self.lpt_timeout.setRange(100, 60000)
        self.lpt_timeout.setSuffix(" ms")
        form.add("lpt_timeout", "Timeout", self.lpt_timeout)
        self.lpt_debug = QSpinBox()
        self.lpt_debug.setRange(0, 5)
        form.add("lpt_debug", "Debug level", self.lpt_debug,
                 tip="libplctag's own logging, 0 to 5. Goes to the console.")
        self.dll_state = w.label("", "hint", wrap=True)
        dll_get = w.button("Download", icon_name="download", slot=self._download_dll,
                           tip="Fetch plctag.dll from the libplctag releases on GitHub.")
        dll_pick = w.button("Browse...", slot=self._browse_dll)
        self.dll_path = ""
        form.add("lpt_dll", "plctag.dll", w.hbox(dll_get, dll_pick, None, spacing=6))
        form.add_full("lpt_dllstate", self.dll_state)
        self.attrib = w.label("", "mono", wrap=True, selectable=True)
        form.add("lpt_attrib", "Attributes", self.attrib)

        self.poll = QSpinBox()
        self.poll.setRange(10, 600000)
        self.poll.setSingleStep(50)
        self.poll.setSuffix(" ms")
        form.add("poll", "Poll every", self.poll,
                 tip="100 ms is ten reads a second per tag. Below 50 ms depends on the PLC and network.")
        self.iface = QComboBox()
        iface_refresh = w.tool("refresh", "List the adapters again", self._refresh_ifaces)
        form.add("iface", "Adapter", w.hbox(self.iface, iface_refresh, stretch_at=0, spacing=4),
                 tip="Which network adapter connects to the PLC. Needed when this PC is on "
                     "more than one network.")

        self.service_switch = w.Switch()
        form.add("service", "Unattended", w.hbox(self.service_switch,
                 w.label("Poll these tags when running as a service", "hint", wrap=True), stretch_at=1, spacing=8))

        conn.body.addWidget(w.rule())
        self.connect_btn = w.button("Connect", "primary", icon_name="play", slot=self._connect)
        self.disconnect_btn = w.button("Disconnect", "danger", slot=session.disconnect_plc)
        self.disconnect_btn.setEnabled(False)
        conn.body.addLayout(w.row(self.connect_btn, self.disconnect_btn, None))
        self.state = w.label("Not connected", "hint", wrap=True)
        conn.body.addWidget(self.state)
        conn.body.addStretch(1)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroller.setWidget(conn)
        scroller.setMinimumWidth(360)
        scroller.setMaximumWidth(460)
        split.addWidget(scroller)

        # ------------------------------------------------------- tag card
        table_card = w.Card("Tags")
        self.count = w.label("", "hint")
        table_card.add_action(self.count)
        self.new_tag = QLineEdit()
        self.new_tag.setProperty("role", "mono")
        self.new_tag.returnPressed.connect(self._add)
        self.new_type = QComboBox()
        self.new_type.addItems(DATA_TYPES)
        self.new_type.setCurrentText("DINT")
        add = w.button("Add", icon_name="plus", slot=self._add)
        remove = w.button("Remove", icon_name="minus", slot=self._remove,
                          tip="Remove the selected tags")
        table_card.body.addLayout(w.row(self.new_tag, self.new_type, add, remove, stretch_at=0))
        self.example = w.label("", "hint", wrap=True)
        table_card.body.addWidget(self.example)

        self.model = TagModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.type_delegate = TypeDelegate(self.table)
        table_card.body.addWidget(self.table, 1)
        table_card.body.addWidget(w.label("Double-click a tag to rename it. Values flash when they change.", "hint"))
        split.addWidget(table_card)
        split.setStretchFactor(1, 1)

        # ---------------------------------------------------------- wiring
        self.driver.currentTextChanged.connect(self._driver_changed)
        for field in (self.ip, self.lpt_path):
            field.textChanged.connect(self._update_attrib)
        for box in (self.lpt_cpu, self.lpt_proto, self.lpt_type):
            box.currentTextChanged.connect(self._update_attrib)
        self.model.rowsInserted.connect(self._tags_changed)
        self.model.rowsRemoved.connect(self._tags_changed)
        self.model.edited.connect(self._tags_changed)
        session.tagValue.connect(self.model.update_value)
        session.plcState.connect(self._plc_state)
        session.fill.connect(self._fill)
        self._dll.connect(self.dll_state.setText)
        self._flash = QTimer(self)
        self._flash.setInterval(60)
        self._flash.timeout.connect(self._tick)
        self.model.dataChanged.connect(lambda *_: self._flash.start() if not self._flash.isActive() else None)

        self._refresh_ifaces()
        self.load_from_config()

    # -------------------------------------------------------------- config

    def load_from_config(self) -> None:
        plc = self.session.cfg.plc
        saved = plc.get("tags_per_driver") or {}
        for key in DRIVER_MAP:
            entries = []
            for item in saved.get(key, []) or []:
                if isinstance(item, str):
                    entries.append({"tag": item, "dtype": "DINT"})
                elif isinstance(item, dict) and item.get("tag"):
                    entries.append({"tag": item["tag"], "dtype": item.get("dtype", "DINT")})
            self._tags[key] = entries
        kind = plc.get("type", PYCOMM3)
        if kind not in DRIVER_MAP:
            kind = PYCOMM3
        if not self._tags.get(kind) and plc.get("tags"):
            self._tags[kind] = [{"tag": t, "dtype": "DINT"} for t in plc["tags"] if t]
        self.ip.setText(str(plc.get("ip", "")))
        self.poll.setValue(int(plc.get("poll_ms", 100) or 100))
        lpt = plc.get("libplctag", {}) or {}
        self.lpt_path.setText(str(lpt.get("path", "1,0")))
        self.lpt_cpu.setCurrentText(str(lpt.get("cpu", "lgx")))
        self.lpt_proto.setCurrentText(str(lpt.get("protocol", "ab-eip")))
        self.lpt_type.setCurrentText(str(lpt.get("data_type", "DINT")))
        self.lpt_timeout.setValue(int(lpt.get("timeout_ms", 5000) or 5000))
        self.lpt_debug.setValue(int(lpt.get("debug", 0) or 0))
        self.dll_path = str(lpt.get("dll_path", "") or "")
        self.series.setCurrentText(str(plc.get("plc_sub_type", "Q")))
        self.frame.setCurrentText(str(plc.get("frame", "3E")))
        self.service_switch.setChecked(bool(plc.get("enabled")))
        source = str(plc.get("source_ip", "") or "")
        for name, ip in self._iface_ips.items():
            if ip == source and source:
                self.iface.setCurrentText(name)
        self._current_driver = ""
        self.driver.blockSignals(True)
        self.driver.setCurrentText(kind)
        self.driver.blockSignals(False)
        self._driver_changed(kind, keep_numbers=True)
        self.port.setValue(int(plc.get("port", DEFAULT_PORTS.get(kind, 44818)) or 1))
        self.slot.setValue(int(plc.get("slot", 0) or 0))
        self.s7slot.setValue(int(plc.get("slot", 1) or 0))
        self.rack.setValue(int(plc.get("rack", 0) or 0))

    def save_into_config(self) -> None:
        c = self.session.cfg
        kind = self.driver.currentText()
        self._tags[kind] = self.model.entries()
        c.set("plc", "type", kind)
        c.set("plc", "ip", self.ip.text().strip())
        c.set("plc", "port", self.port.value())
        c.set("plc", "slot", self.s7slot.value() if kind == SIEMENS else self.slot.value())
        c.set("plc", "rack", self.rack.value())
        c.set("plc", "poll_ms", self.poll.value())
        c.set("plc", "plc_sub_type", self.series.currentText())
        c.set("plc", "frame", self.frame.currentText())
        c.set("plc", "source_ip", self._iface_ips.get(self.iface.currentText(), ""))
        c.set("plc", "enabled", self.service_switch.isChecked())
        c.set("plc", "tags_per_driver", {k: list(v) for k, v in self._tags.items()})
        # The flat list is what pre-1.0 runners read; kept in step for them.
        c.set("plc", "tags", [e["tag"] for e in self._tags.get(kind, [])])
        c.set("plc", "libplctag", {
            "path": self.lpt_path.text().strip(),
            "cpu": self.lpt_cpu.currentText().strip() or "lgx",
            "protocol": self.lpt_proto.currentText(),
            "data_type": self.lpt_type.currentText(),
            "timeout_ms": self.lpt_timeout.value(),
            "debug": self.lpt_debug.value(),
            "dll_path": self.dll_path,
        })

    # ------------------------------------------------------------- driver

    def _driver_changed(self, kind: str, keep_numbers: bool = False) -> None:
        if self._current_driver:
            self._tags[self._current_driver] = self.model.entries()
        self._current_driver = kind
        is_lpt, is_s7, is_mc = kind == LPT, kind == SIEMENS, kind == MITSU
        f = self.form
        f.show_row("slot", not (is_lpt or is_s7 or is_mc))
        f.show_row("rack", is_s7)
        f.show_row("s7slot", is_s7)
        f.show_row("series", is_mc)
        f.show_row("frame", is_mc)
        f.show_row("port", not is_lpt)
        for key in ("lpt_path", "lpt_cpu", "lpt_proto", "lpt_type", "lpt_timeout",
                    "lpt_debug", "lpt_dll", "lpt_dllstate", "lpt_attrib"):
            f.show_row(key, is_lpt)
        if not keep_numbers:
            self.port.setValue(DEFAULT_PORTS.get(kind, 44818))
            if is_s7:
                self.s7slot.setValue(DEFAULT_SLOTS.get(kind, 1))
        self.new_type.setVisible(is_lpt)
        self.model.load(self._tags.get(kind, []), typed=is_lpt)
        self.table.setItemDelegateForColumn(1, self.type_delegate if is_lpt else None)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, self.model.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.new_tag.setPlaceholderText(TAG_EXAMPLES.get(kind, "").split("|")[0].strip()
                                        or "Tag name")
        self.example.setText("Formats: " + TAG_EXAMPLES.get(kind, "").replace("  |  ", ", "))
        self._tags_changed()
        if is_lpt:
            self._update_attrib()
            self._check_dll()

    def _update_attrib(self, *_):
        if self.driver.currentText() != LPT:
            return
        from app.engine.libplctag_driver import LibPlcTagConfig
        try:
            cfg = LibPlcTagConfig(gateway=self.ip.text().strip() or "x.x.x.x",
                                  path=self.lpt_path.text().strip(),
                                  cpu=self.lpt_cpu.currentText() or "lgx",
                                  protocol=self.lpt_proto.currentText() or "ab-eip",
                                  data_type=self.lpt_type.currentText() or "DINT")
            self.attrib.setText(cfg.build_attrib("<tag>"))
        except Exception:
            self.attrib.setText("")

    def _check_dll(self) -> None:
        from app.engine.libplctag_driver import find_dll
        found = self.dll_path if self.dll_path and os.path.exists(self.dll_path) else find_dll()
        self.dll_state.setText(f"Using {found}" if found else
                               "Not found yet. Download fetches the current release.")

    def _download_dll(self) -> None:
        self.dll_state.setText("Downloading...")

        def work():
            from app.engine.libplctag_driver import download_dll
            result = download_dll(progress_cb=self._dll.emit)
            self._dll.emit(f"Saved {result}" if result else
                           "Download failed. This PC may have no route to github.com; "
                           "copy plctag.dll here with Browse instead.")

        threading.Thread(target=work, daemon=True).start()

    def _browse_dll(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose plctag.dll", "",
                                              "DLL files (*.dll);;All files (*)")
        if path:
            self.dll_path = path
            self.dll_state.setText(f"Using {path}")

    def _refresh_ifaces(self) -> None:
        from app.engine.passive_sniffer import list_interfaces
        current = self.iface.currentText()
        self.iface.clear()
        self._iface_ips = {AUTO_IFACE: ""}
        self.iface.addItem(AUTO_IFACE)
        for item in list_interfaces():
            if item.get("ip"):
                name = f"{item.get('name', '')}  {item['ip']}"
                self._iface_ips[name] = item["ip"]
                self.iface.addItem(name)
        if current in self._iface_ips:
            self.iface.setCurrentText(current)

    # --------------------------------------------------------------- tags

    def _add(self) -> None:
        tag = self.new_tag.text().strip()
        if not tag:
            return
        if not self.model.add(tag, self.new_type.currentText()):
            self.session.status.emit(f"{tag} is already in the list.")
            return
        self.new_tag.clear()

    def _remove(self) -> None:
        rows = [i.row() for i in self.table.selectionModel().selectedRows()]
        if rows:
            self.model.remove(rows)

    def _tags_changed(self, *_):
        self._tags[self._current_driver] = self.model.entries()
        n = len(self.model.rows)
        self.count.setText(f"{n} tag{'s' if n != 1 else ''}")
        if self.session.plc_running:
            self.save_into_config()
            self.session.update_tags()

    def _tick(self) -> None:
        self.model.repaint_lit()
        if not self.model.any_lit():
            self._flash.stop()

    # ------------------------------------------------------------ connect

    def _connect(self) -> None:
        self.save_into_config()
        self.session.save_config()
        self.model.clear_values()
        self.session.connect_plc()

    def _plc_state(self, state: str, detail: str) -> None:
        running = state in ("on", "connecting")
        self.connect_btn.setEnabled(not running)
        self.disconnect_btn.setEnabled(state == "on")
        self.driver.setEnabled(not running)
        if state == "on":
            self.state.setText(f"Polling {detail} every {self.poll.value()} ms.")
            w.set_prop(self.state, "tone", "good")
        elif state == "connecting":
            self.state.setText(f"Connecting to {detail}... pycomm3 reads the tag list first, "
                               "which takes a few seconds.")
            w.set_prop(self.state, "tone", "")
        elif state == "error":
            self.state.setText(detail)
            w.set_prop(self.state, "tone", "down")
        else:
            self.state.setText("Not connected")
            w.set_prop(self.state, "tone", "")

    def _fill(self, key: str, value: str) -> None:
        if key == "plc_ip":
            self.ip.setText(value)
        elif key == "plc_port":
            try:
                self.port.setValue(int(value))
            except ValueError:
                pass
        elif key == "plc_driver" and value in DRIVER_MAP:
            self.driver.setCurrentText(value)

    def _open_netcontrol(self) -> None:
        from PySide6.QtWidgets import QApplication
        address = self.ip.text().strip()
        if address:
            QApplication.clipboard().setText(address)
        if siblings.launch(siblings.NETCONTROL):
            self.session.status.emit(f"NetControl opened. {address} is on the clipboard "
                                     "for its Diagnostics tab." if address else "NetControl opened.")

    def shown(self) -> None:
        self.nc_button.setVisible(siblings.location(siblings.NETCONTROL) is not None)

    def theme_changed(self) -> None:
        super().theme_changed()
        self.table.viewport().update()
