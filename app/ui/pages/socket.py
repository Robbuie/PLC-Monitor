"""Socket: watch the messages between the PLC side and the receiving PC.

Four ways to see them, one segmented choice. Passive Sniff is first and the
default because it is the only one that changes nothing on the line; the other
three need the sender pointed at this PC, which is a change to a running system
and is said so beside them.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLineEdit, QScrollArea, QSpinBox, QSplitter

from app.engine.passive_sniffer import build_bpf_filter, list_interfaces, scapy_available
from app.service import is_admin
from app.ui import widgets as w
from app.ui.pages.base import Page

MODES = [("passive", "Passive sniff"), ("proxy", "Proxy"), ("server", "Server"), ("udp", "UDP listener")]

EXPLAIN = {
    "passive": (
        "Reads packets off the network adapter. Neither end knows it is there, nothing "
        "is redirected, and both directions are seen.",
        ["Needs Npcap (npcap.com, free, tick WinPcap API-compatible mode).",
         "Needs this window run as administrator.",
         "The adapter must be on the same segment as the traffic: a mirrored switch port, "
         "a hub, or this PC being one of the two ends.",
         "Our IP labels each message as received or sent; without it everything is 'recv'."],
    ),
    "proxy": (
        "Sits between the sender and the receiver and forwards everything, timestamping "
        "each message in both directions.",
        ["The sender must be reconfigured to connect to this PC's address and the listen "
         "port instead of the real receiver.",
         "Forward host and port are the real receiver.",
         "If the sender cannot be changed, use Passive sniff."],
    ),
    "server": (
        "Listens on a TCP port and logs whatever arrives. Nothing is forwarded.",
        ["For testing a sender against this PC, or when this PC is the receiver."],
    ),
    "udp": (
        "Binds a UDP port and logs every datagram that arrives.",
        ["For protocols that send datagrams rather than a connection."],
    ),
}


class SocketPage(Page):
    key = "socket"
    title = "Socket"
    glyph = "socket"
    blurb = ("Timestamp the messages that follow a PLC change. They go into the same "
             "log as the tags, so the two can be subtracted.")

    def __init__(self, session, parent=None) -> None:
        super().__init__(session, parent)
        self._ifaces: dict[str, str] = {}

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        self.body.addWidget(split, 1)

        card = w.Card("Monitor")
        self.mode = w.Segments(MODES)
        card.body.addWidget(self.mode)
        form = self.form = w.Form(label_width=112)
        card.body.addWidget(form)

        self.label_field = QLineEdit()
        form.add("label", "Label", self.label_field,
                 tip="Shown in the log's Source column, to tell monitors apart.")

        self.sniff_iface = QComboBox()
        refresh = w.tool("refresh", "List the adapters again", self._refresh_ifaces)
        form.add("iface", "Adapter", w.hbox(self.sniff_iface, refresh, stretch_at=0, spacing=4))
        self.sniff_host = QLineEdit()
        self.sniff_host.setProperty("role", "mono")
        self.sniff_host.setPlaceholderText("receiving PC or PLC address")
        form.add("host", "Filter host", self.sniff_host,
                 tip="Only packets to or from this address.")
        self.sniff_port = QLineEdit()
        self.sniff_port.setProperty("role", "mono")
        self.sniff_port.setPlaceholderText("e.g. 9000")
        form.add("fport", "Filter port", self.sniff_port)
        self.sniff_proto = QComboBox()
        self.sniff_proto.addItems(["any", "tcp", "udp"])
        form.add("proto", "Protocol", self.sniff_proto)
        self.our_ip = QLineEdit()
        self.our_ip.setProperty("role", "mono")
        self.our_ip.setPlaceholderText("optional")
        form.add("ourip", "Our IP", self.our_ip,
                 tip="This PC's address on that network, to label each message received or sent.")
        self.bpf = QLineEdit()
        self.bpf.setProperty("role", "mono")
        form.add("bpf", "BPF filter", self.bpf, hint="Built from the fields above; edit it to "
                 "use any Wireshark capture filter.")

        self.lhost = QLineEdit()
        self.lhost.setProperty("role", "mono")
        form.add("lhost", "Listen on", self.lhost, tip="0.0.0.0 listens on every adapter.")
        self.lport = QSpinBox()
        self.lport.setRange(1, 65535)
        form.add("lport", "Listen port", self.lport)
        self.fhost = QLineEdit()
        self.fhost.setProperty("role", "mono")
        form.add("fhost", "Forward to", self.fhost, tip="The real receiver's address.")
        self.fport = QSpinBox()
        self.fport.setRange(1, 65535)
        form.add("fport2", "Forward port", self.fport)

        self.service_switch = w.Switch()
        form.add("service", "Unattended", w.hbox(self.service_switch,
                 w.label("Run this monitor when running as a service", "hint", wrap=True), stretch_at=1, spacing=8))

        card.body.addWidget(w.rule())
        self.start_btn = w.button("Start", "primary", icon_name="play", slot=self._start)
        self.stop_btn = w.button("Stop", "danger", slot=session.stop_socket)
        self.stop_btn.setEnabled(False)
        card.body.addLayout(w.row(self.start_btn, self.stop_btn, None))
        self.state = w.label("Not running", "hint", wrap=True)
        card.body.addWidget(self.state)
        card.body.addStretch(1)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroller.setWidget(card)
        scroller.setMinimumWidth(380)
        scroller.setMaximumWidth(480)
        split.addWidget(scroller)

        about = w.Card("How this mode works")
        self.explain = w.label("", wrap=True)
        about.body.addWidget(self.explain)
        self.points = w.label("", "hint", wrap=True)
        self.points.setTextFormat(Qt.RichText)
        about.body.addWidget(self.points)
        self.ready = w.Banner("", "info")
        self.elevate = w.button("Restart as administrator", slot=self._elevate)
        self.ready.side.addWidget(self.elevate)
        about.body.addWidget(self.ready)
        about.body.addStretch(1)
        split.addWidget(about)
        split.setStretchFactor(1, 1)

        self.mode.changed.connect(self._mode_changed)
        for field in (self.sniff_host, self.sniff_port):
            field.textChanged.connect(self._update_bpf)
        self.sniff_proto.currentTextChanged.connect(self._update_bpf)
        session.sockState.connect(self._sock_state)
        session.fill.connect(self._fill)

        self._refresh_ifaces()
        self.load_from_config()

    def load_from_config(self) -> None:
        s = self.session.cfg.socket
        sniff = s.get("sniff", {}) or {}
        self.label_field.setText(str(s.get("source_label", "SOCKET")))
        self.lhost.setText(str(s.get("listen_host", "0.0.0.0")))
        self.lport.setValue(int(s.get("listen_port", 9000) or 9000))
        self.fhost.setText(str(s.get("forward_host", "")))
        self.fport.setValue(int(s.get("forward_port", 9001) or 9001))
        self.sniff_host.setText(str(sniff.get("host", "")))
        self.sniff_port.setText(str(sniff.get("port", "")))
        self.sniff_proto.setCurrentText(str(sniff.get("proto", "any")))
        self.our_ip.setText(str(sniff.get("our_ip", "")))
        for name, raw in self._ifaces.items():
            if raw and raw == sniff.get("iface"):
                self.sniff_iface.setCurrentText(name)
        self._update_bpf()
        if sniff.get("bpf"):
            self.bpf.setText(str(sniff["bpf"]))
        self.service_switch.setChecked(bool(s.get("enabled")))
        mode = s.get("mode", "passive")
        self.mode.set_value(mode)
        self._mode_changed(mode)

    def save_into_config(self) -> None:
        c = self.session.cfg
        c.set("socket", "mode", self.mode.value())
        c.set("socket", "source_label", self.label_field.text().strip() or "SOCKET")
        c.set("socket", "listen_host", self.lhost.text().strip() or "0.0.0.0")
        c.set("socket", "listen_port", self.lport.value())
        c.set("socket", "forward_host", self.fhost.text().strip())
        c.set("socket", "forward_port", self.fport.value())
        c.set("socket", "enabled", self.service_switch.isChecked())
        built = build_bpf_filter(self.sniff_host.text().strip(), self.sniff_port.text().strip(),
                                 self.sniff_proto.currentText())
        typed = self.bpf.text().strip()
        c.set("socket", "sniff", {
            "iface": self._ifaces.get(self.sniff_iface.currentText(), ""),
            "host": self.sniff_host.text().strip(),
            "port": self.sniff_port.text().strip(),
            "proto": self.sniff_proto.currentText(),
            "our_ip": self.our_ip.text().strip(),
            # Kept only when somebody edited it; otherwise it is rebuilt from
            # the fields each time, so changing a field changes the capture.
            "bpf": typed if typed and typed != built else "",
        })

    def _refresh_ifaces(self) -> None:
        current = self.sniff_iface.currentText()
        self.sniff_iface.clear()
        self._ifaces = {}
        for item in list_interfaces():
            self._ifaces[item["display"]] = item["name"]
            self.sniff_iface.addItem(item["display"])
        if current in self._ifaces:
            self.sniff_iface.setCurrentText(current)

    def _update_bpf(self, *_):
        self.bpf.setText(build_bpf_filter(self.sniff_host.text().strip(),
                                          self.sniff_port.text().strip(),
                                          self.sniff_proto.currentText()))

    def _mode_changed(self, mode: str) -> None:
        passive = mode == "passive"
        f = self.form
        for key in ("iface", "host", "fport", "proto", "ourip", "bpf"):
            f.show_row(key, passive)
        f.show_row("lhost", not passive)
        f.show_row("lport", not passive)
        f.show_row("fhost", mode == "proxy")
        f.show_row("fport2", mode == "proxy")
        text, points = EXPLAIN.get(mode, ("", []))
        self.explain.setText(text)
        self.points.setText("".join(f"<p style='margin:0 0 6px 0'>{p}</p>" for p in points))
        self._readiness(mode)

    def _readiness(self, mode: str) -> None:
        if mode != "passive":
            self.ready.set("")
            return
        ok, err = scapy_available()
        problems = []
        if not ok:
            problems.append("scapy is not installed in this build.")
        if not is_admin():
            problems.append("This window is not running as administrator, so the capture "
                            "will see nothing.")
        self.elevate.setVisible(not is_admin() and os.name == "nt")
        if problems:
            self.ready.set(" ".join(problems), "warn")
        else:
            self.ready.set("Ready to capture. Npcap is checked when the capture starts.", "info")

    def _elevate(self) -> None:
        """Start a second copy as administrator and close this one."""
        import ctypes
        import sys
        if self.session.plc_running or self.session.sock_running:
            w.info(self, "Restart", "Stop monitoring first: the new window starts with nothing running.")
            return
        self.save_into_config()
        self.session.save_config()
        exe = sys.executable
        args = "" if getattr(sys, "frozen", False) else "-m app"
        from app import paths
        if ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, paths.program_folder(), 1) > 32:
            self.window().close()

    def _start(self) -> None:
        self.save_into_config()
        self.session.save_config()
        self.session.start_socket()

    def _sock_state(self, state: str, detail: str) -> None:
        on = state == "on"
        self.start_btn.setEnabled(not on)
        self.stop_btn.setEnabled(on)
        self.mode.setEnabled(not on)
        if on:
            self.state.setText(f"Running: {detail}")
            w.set_prop(self.state, "tone", "good")
        elif state == "error":
            self.state.setText(detail)
            w.set_prop(self.state, "tone", "down")
        else:
            self.state.setText("Not running")
            w.set_prop(self.state, "tone", "")

    def _fill(self, key: str, value: str) -> None:
        if self.session.sock_running:
            return
        wanted = {"forward_host": "proxy", "forward_port": "proxy",
                  "sniff_host": "passive", "sniff_port": "passive"}.get(key)
        if wanted and self.mode.value() != wanted:
            self.mode.set_value(wanted)
            self._mode_changed(wanted)
        if key == "forward_host":
            self.fhost.setText(value)
        elif key == "forward_port":
            try:
                self.fport.setValue(int(value))
            except ValueError:
                pass
        elif key == "listen_port":
            try:
                self.lport.setValue(int(value))
            except ValueError:
                pass
        elif key == "sniff_host":
            self.sniff_host.setText(value)
        elif key == "sniff_port":
            self.sniff_port.setText(value)

    def shown(self) -> None:
        self._readiness(self.mode.value())

    def profile_changed(self) -> None:
        self.load_from_config()
