"""Network: find the address and port that need watching.

Two tools. **Connections** lists every TCP and UDP socket open on this PC with
the program that owns it -- usually the quickest way to find which port the
receiving software is listening on. **Port check** asks one device which ports
answer, gently by default.

The port check follows NetControl's rules because it touches the same networks:
one host at a time, nothing written, known PLC ports first, four connections at
once with a pause between them unless somebody chooses faster and is told where
faster belongs. When NetControl is installed, a button hands the device to it --
its service check verifies more protocols and keeps a record. When it is not,
everything here still works.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QLineEdit,
    QMenu,
    QProgressBar,
    QSpinBox,
    QSplitter,
    QTableView,
)

from app.core import siblings
from app.engine import network_discovery as nd
from app.engine.passive_sniffer import list_interfaces
from app.theme import sheet
from app.ui import widgets as w
from app.ui.pages.base import Page

AUTO_IFACE = "Automatic (Windows picks by route)"


class RowsModel(QAbstractTableModel):
    def __init__(self, columns: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = []
        self.tone_key = ""

    def load(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def add(self, row: dict) -> int:
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows))
        self.rows.append(row)
        self.endInsertRows()
        return len(self.rows) - 1

    def touch(self, index: int) -> None:
        self.dataChanged.emit(self.index(index, 0), self.index(index, len(self.columns) - 1))

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.columns[section][1].upper()
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self.columns[index.column()][0]
        if role == Qt.DisplayRole:
            return str(row.get(key, ""))
        if role == Qt.ForegroundRole:
            t = sheet.tokens()
            tone = row.get("_tone")
            if key in ("state", "verified") and tone:
                return QColor(t[tone])
            if key in ("pid", "banner", "label"):
                return QColor(t["txt_1"])
        if role == Qt.FontRole and key in ("local", "remote", "port", "pid"):
            font = QFont("Cascadia Mono")
            font.setStyleHint(QFont.Monospace)
            return font
        return None


def _table(model) -> QTableView:
    view = QTableView()
    view.setModel(model)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.setSelectionMode(QAbstractItemView.SingleSelection)
    view.setEditTriggers(QAbstractItemView.NoEditTriggers)
    view.verticalHeader().setVisible(False)
    view.setShowGrid(False)
    view.setWordWrap(False)
    view.setAlternatingRowColors(True)
    view.setContextMenuPolicy(Qt.CustomContextMenu)
    header = view.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    header.setHighlightSections(False)
    header.setStretchLastSection(True)
    return view


STATE_TONE = {"ESTABLISHED": "good", "LISTEN": "info", "CLOSE_WAIT": "warn", "TIME_WAIT": "txt_2"}


class NetworkPage(Page):
    key = "network"
    title = "Network"
    glyph = "network"
    blurb = "Find the address and port to watch: this PC's connections, or what a device answers on."

    _conns = Signal(list)
    _found = Signal(int, str)
    _progress = Signal(int, int)
    _verified = Signal(int, bool, str)
    _scan_done = Signal()

    def __init__(self, session, parent=None) -> None:
        super().__init__(session, parent)
        self._ifaces: dict[str, str] = {}
        self._stop: threading.Event | None = None
        self._all_conns: list[dict] = []
        self.nc_btn = w.button("Open in NetControl", icon_name="external", slot=self._netcontrol,
                               tip="Hand this device to NetControl: ping, protocol-verified "
                                   "service check, link and error counters.")
        self.actions.addWidget(self.nc_btn)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        self.body.addWidget(split, 1)

        # ------------------------------------------------------ connections
        conns = w.Card("Connections on this PC")
        self.filter = QLineEdit()
        self.filter.setProperty("role", "search")
        self.filter.setPlaceholderText("Filter by program, address or port")
        self.filter.setClearButtonEnabled(True)
        self.auto = w.Switch()
        conns.body.addLayout(w.row(self.filter, w.tool("refresh", "Refresh", self.refresh_connections),
                                   12, self.auto, w.label("Refresh every 2 s", "hint"), stretch_at=0))
        self.conn_model = RowsModel([("proto", "Proto"), ("process", "Program"), ("pid", "PID"),
                                     ("local", "Local"), ("remote", "Remote"), ("state", "State")], self)
        self.conn_view = _table(self.conn_model)
        for column, width in enumerate((60, 180, 70, 200, 200)):
            self.conn_view.horizontalHeader().resizeSection(column, width)
        self.conn_view.customContextMenuRequested.connect(self._conn_menu)
        self.conn_view.doubleClicked.connect(lambda _i: self._conn_menu(None))
        conns.body.addWidget(self.conn_view, 1)
        conns.body.addWidget(w.label("Right-click a row to use its address on another page.", "hint"))
        split.addWidget(conns)

        # ------------------------------------------------------- port check
        scan = w.Card("Port check")
        self.host = QLineEdit()
        self.host.setProperty("role", "mono")
        self.host.setPlaceholderText("Device address")
        self.which = w.Segments([("plc", "PLC ports"), ("range", "Range")])
        self.which.set_tip("plc", ", ".join(str(p) for p in nd.PLC_PORTS))
        self.first = QSpinBox()
        self.first.setRange(1, 65535)
        self.first.setValue(1)
        self.last = QSpinBox()
        self.last.setRange(1, 65535)
        self.last.setValue(1024)
        self.span = w.hbox(self.first, w.label("to", "hint"), self.last, spacing=6)
        self.span.setVisible(False)
        self.pace = QComboBox()
        for key, text in nd.SCAN_PROFILE_LABELS.items():
            self.pace.addItem(text, key)
        self.iface = QComboBox()
        self.scan_btn = w.button("Check", "primary", icon_name="play", slot=self._scan)
        self.stop_btn = w.button("Stop", "danger", slot=self._stop_scan)
        self.stop_btn.setEnabled(False)
        scan.body.addLayout(w.row(self.host, self.which, self.span, self.pace, self.scan_btn,
                                  self.stop_btn, stretch_at=0))
        scan.body.addLayout(w.row(w.label("Adapter", "fieldlabel"), self.iface,
                                  w.tool("refresh", "List the adapters again", self._refresh_ifaces),
                                  None, stretch_at=1))
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        scan.body.addWidget(self.progress)
        self.scan_model = RowsModel([("port", "Port"), ("label", "Known as"),
                                     ("verified", "Answered as"), ("banner", "Banner")], self)
        self.scan_view = _table(self.scan_model)
        for column, width in enumerate((70, 200, 300)):
            self.scan_view.horizontalHeader().resizeSection(column, width)
        self.scan_view.customContextMenuRequested.connect(self._scan_menu)
        self.scan_view.doubleClicked.connect(lambda _i: self._scan_menu(None))
        scan.body.addWidget(self.scan_view, 1)
        self.scan_note = w.label("Opens a connection to each port and closes it. Known PLC "
                                 "ports get one read-only identity request.", "hint", wrap=True)
        scan.body.addWidget(self.scan_note)
        split.addWidget(scan)

        self.filter.textChanged.connect(self._apply_filter)
        self.which.changed.connect(lambda k: self.span.setVisible(k == "range"))
        self.pace.currentIndexChanged.connect(self._pace_changed)
        self._conns.connect(self._show_conns)
        self._found.connect(self._add_port)
        self._progress.connect(self._show_progress)
        self._verified.connect(self._show_verified)
        self._scan_done.connect(self._finished)
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh_connections)
        self.auto.toggled.connect(lambda on: self._timer.start() if on else self._timer.stop())
        session.fill.connect(lambda key, value: self.host.setText(value) if key == "scan_host" else None)
        self._refresh_ifaces()

    # ------------------------------------------------------- connections

    def refresh_connections(self) -> None:
        if not nd.HAS_PSUTIL:
            self.banner.set("psutil is missing from this build, so connections cannot be listed.", "warn")
            return
        threading.Thread(target=lambda: self._conns.emit(nd.get_connections()), daemon=True).start()

    def _show_conns(self, rows: list[dict]) -> None:
        for row in rows:
            row["_tone"] = STATE_TONE.get(row.get("state", ""), "")
        self._all_conns = rows
        self._apply_filter()

    def _apply_filter(self) -> None:
        text = self.filter.text().strip().lower()
        rows = [r for r in self._all_conns if "error" not in r and
                (not text or text in " ".join(str(v) for k, v in r.items() if not k.startswith("_")).lower())]
        errors = [r["error"] for r in self._all_conns if "error" in r]
        if errors:
            self.banner.set(errors[0], "warn")
        self.conn_model.load(rows)

    def _conn_menu(self, pos) -> None:
        index = self.conn_view.currentIndex()
        if not index.isValid():
            return
        row = self.conn_model.rows[index.row()]
        rhost, rport = row.get("rhost", ""), row.get("rport", "")
        lport = row.get("lport", "")
        menu = QMenu(self)
        if rhost:
            menu.addAction(f"Watch {rhost}:{rport} with Passive sniff",
                           lambda: self._send({"sniff_host": rhost, "sniff_port": rport}, "socket"))
            menu.addAction(f"Proxy forward to {rhost}:{rport}",
                           lambda: self._send({"forward_host": rhost, "forward_port": rport}, "socket"))
            menu.addAction(f"Use {rhost} as the PLC address",
                           lambda: self._send({"plc_ip": rhost}, "tags"))
            menu.addAction(f"Check {rhost}'s ports", lambda: self.host.setText(rhost))
        if lport:
            menu.addAction(f"Listen on port {lport}", lambda: self._send({"listen_port": lport}, "socket"))
        menu.addSeparator()
        menu.addAction("Copy row", lambda: QApplication.clipboard().setText(
            "\t".join(str(row.get(k, "")) for k, _ in self.conn_model.columns)))
        menu.exec(self.conn_view.viewport().mapToGlobal(pos) if pos is not None else
                  self.conn_view.mapToGlobal(self.conn_view.visualRect(index).bottomLeft()))

    def _send(self, values: dict, page: str) -> None:
        for key, value in values.items():
            self.session.fill.emit(key, str(value))
        self.session.goto.emit(page)
        self.session.status.emit("Filled in on the " + ("Socket" if page == "socket" else "Tags") +
                                 " page.")

    # -------------------------------------------------------- port check

    def _refresh_ifaces(self) -> None:
        current = self.iface.currentText()
        self.iface.clear()
        self._ifaces = {AUTO_IFACE: ""}
        self.iface.addItem(AUTO_IFACE)
        for item in list_interfaces():
            if item.get("ip"):
                name = f"{item.get('name', '')}  {item['ip']}"
                self._ifaces[name] = item["ip"]
                self.iface.addItem(name)
        if current in self._ifaces:
            self.iface.setCurrentText(current)

    def _pace_changed(self) -> None:
        if self.pace.currentData() == "fast":
            self.scan_note.setText("Fast opens 128 connections at once. Some PLC and drive "
                                   "Ethernet cards drop off the network under that. Use it on "
                                   "a bench, not a running line.")
            w.set_prop(self.scan_note, "tone", "warn")
        else:
            self.scan_note.setText("Opens a connection to each port and closes it. Known PLC "
                                   "ports get one read-only identity request.")
            w.set_prop(self.scan_note, "tone", "")

    def _scan(self) -> None:
        host = self.host.text().strip()
        if not host:
            w.info(self, "Port check", "Enter the device's address.")
            return
        if host.endswith(".255") or host in ("255.255.255.255", "0.0.0.0"):
            w.info(self, "Port check", "That is a broadcast address. The port check asks one device at a time.")
            return
        if self.which.value() == "range":
            first, last = self.first.value(), self.last.value()
            if first > last:
                first, last = last, first
            ports = list(range(first, last + 1))
            if self.pace.currentData() == "gentle" and len(ports) > 5000:
                if not w.ask(self, "Port check", f"{len(ports):,} ports at the gentle pace takes "
                             f"about {len(ports) * 0.05 / 4 / 60:.0f} minutes.", yes="Check them"):
                    return
        else:
            ports = list(nd.PLC_PORTS)
        workers, pause = nd.SCAN_PROFILES[self.pace.currentData()]
        source = self._ifaces.get(self.iface.currentText(), "")
        self.scan_model.load([])
        self._stop = threading.Event()
        self.scan_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(ports))
        self.progress.setValue(0)
        stop = self._stop
        self._scan_host = host
        self._scan_source = source

        def work():
            nd.scan_ports(host, ports=ports, timeout=0.6, max_workers=workers, pause=pause,
                          progress_cb=self._progress.emit, result_cb=self._found.emit,
                          stop_event=stop, source_ip=source)
            self._scan_done.emit()

        threading.Thread(target=work, daemon=True).start()

    def _show_progress(self, done: int, total: int) -> None:
        self.progress.setValue(done)
        self.session.status.emit(f"Checked {done:,} of {total:,} ports on {self._scan_host}")

    def _add_port(self, port: int, banner: str) -> None:
        row = {"port": port, "label": nd.port_label(port), "banner": banner, "verified": ""}
        index = self.scan_model.add(row)
        if port in nd.AUTO_VERIFY_PORTS:
            row["verified"] = "checking..."
            self.scan_model.touch(index)
            host, source = self._scan_host, self._scan_source

            def verify():
                ok, _driver, desc = nd.verify_port(host, port, source)
                self._verified.emit(index, ok, desc)

            threading.Thread(target=verify, daemon=True).start()

    def _show_verified(self, index: int, ok: bool, desc: str) -> None:
        if index >= len(self.scan_model.rows):
            return
        row = self.scan_model.rows[index]
        row["verified"] = desc if ok else "open, not a PLC protocol"
        row["_tone"] = "good" if ok else ""
        row["_driver"] = _driver_for(desc)
        self.scan_model.touch(index)

    def _stop_scan(self) -> None:
        if self._stop:
            self._stop.set()

    def _finished(self) -> None:
        self.scan_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setVisible(False)
        n = len(self.scan_model.rows)
        stopped = self._stop is not None and self._stop.is_set()
        self.session.status.emit(f"{'Stopped' if stopped else 'Done'}: {n} open port"
                                 f"{'s' if n != 1 else ''} on {self._scan_host}.")

    def _scan_menu(self, pos) -> None:
        index = self.scan_view.currentIndex()
        if not index.isValid():
            return
        row = self.scan_model.rows[index.row()]
        host, port = self._scan_host, str(row["port"])
        menu = QMenu(self)
        driver = row.get("_driver")
        if driver:
            menu.addAction(f"Monitor {host} as {driver}",
                           lambda: self._send({"plc_driver": driver, "plc_ip": host,
                                               "plc_port": port}, "tags"))
        menu.addAction(f"Use {host}:{port} as the PLC", lambda: self._send(
            {"plc_ip": host, "plc_port": port}, "tags"))
        menu.addAction(f"Watch {host}:{port} with Passive sniff",
                       lambda: self._send({"sniff_host": host, "sniff_port": port}, "socket"))
        menu.addAction(f"Proxy forward to {host}:{port}",
                       lambda: self._send({"forward_host": host, "forward_port": port}, "socket"))
        menu.addAction("Check this port again", lambda: self._recheck(index.row()))
        menu.exec(self.scan_view.viewport().mapToGlobal(pos) if pos is not None else
                  self.scan_view.mapToGlobal(self.scan_view.visualRect(index).bottomLeft()))

    def _recheck(self, index: int) -> None:
        row = self.scan_model.rows[index]
        row["verified"] = "checking..."
        self.scan_model.touch(index)
        host, port, source = self._scan_host, int(row["port"]), self._scan_source
        threading.Thread(target=lambda: self._verified.emit(
            index, *(lambda r: (r[0], r[2]))(nd.verify_port(host, port, source))), daemon=True).start()

    # -------------------------------------------------------- NetControl

    def _netcontrol(self) -> None:
        address = self.host.text().strip()
        if not address:
            index = self.conn_view.currentIndex()
            if index.isValid():
                address = self.conn_model.rows[index.row()].get("rhost", "")
        if address:
            QApplication.clipboard().setText(address)
        if siblings.launch(siblings.NETCONTROL):
            self.session.status.emit(f"NetControl opened. {address} is on the clipboard for its "
                                     "Diagnostics tab." if address else "NetControl opened.")
        else:
            self.session.status.emit("NetControl could not be started.")

    def shown(self) -> None:
        self.nc_btn.setVisible(siblings.location(siblings.NETCONTROL) is not None)
        if not self._all_conns:
            self.refresh_connections()


def _driver_for(desc: str) -> str:
    text = desc.lower()
    if "rockwell" in text or "ethernet/ip" in text:
        return "Rockwell (pycomm3)"
    if "siemens" in text:
        return "Siemens (snap7)"
    if "mitsubishi" in text:
        return "Mitsubishi (MC Proto)"
    return ""
