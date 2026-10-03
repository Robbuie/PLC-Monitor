"""Service: keep monitoring with nobody logged in.

The service runs the same monitors from the same saved config as this window,
so the page is a checklist rather than a form: what it will run (taken from the
Unattended switches on the Tags and Socket pages), where it reads and writes,
and the four buttons. Each button that needs an administrator asks for one
through a single UAC prompt.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QPlainTextEdit

from app import paths
from app import service as svc
from app.engine.monitors import describe_socket, tags_for
from app.ui import widgets as w
from app.ui.pages.base import Page

STATE_TEXT = {
    "RUNNING": ("Running", "good"), "STOPPED": ("Stopped", "warn"),
    "START_PENDING": ("Starting", "info"), "STOP_PENDING": ("Stopping", "info"),
    "NOT_INSTALLED": ("Not installed", "idle"), "UNAVAILABLE": ("Windows only", "idle"),
}


class ServicePage(Page):
    key = "service"
    title = "Service"
    glyph = "service"
    blurb = ("Run the monitors as a Windows service: starts at boot, needs nobody logged "
             "in, and writes to the same log.")

    _result = Signal(bool, str)
    _state = Signal(str, str)

    def __init__(self, session, parent=None) -> None:
        super().__init__(session, parent)

        top = w.row(spacing=12)
        status = w.Card("Status")
        self.chip = w.chip("Checking", "idle")
        status.body.addLayout(w.row(self.chip, None))
        self.status_text = w.label("", "hint", wrap=True)
        status.body.addWidget(self.status_text)
        self.install_btn = w.button("Install", "primary", slot=self._install,
                                    tip="Saves the config, then registers the service to start at boot.")
        self.start_btn = w.button("Start", icon_name="play", slot=lambda: self._run(svc.start_commands(), "Starting"))
        self.stop_btn = w.button("Stop", icon_name="stop", slot=lambda: self._run(svc.stop_commands(), "Stopping"))
        self.remove_btn = w.button("Remove", "danger", slot=self._remove)
        status.body.addLayout(w.row(self.install_btn, self.start_btn, self.stop_btn, self.remove_btn, None))
        self.admin_note = w.label("", "hint", wrap=True)
        status.body.addWidget(self.admin_note)
        status.body.addStretch(1)
        top.addWidget(status, 1)

        plan = w.Card("What it will run")
        self.plan_plc = w.label("", wrap=True)
        self.plan_sock = w.label("", wrap=True)
        plan.body.addWidget(self.plan_plc)
        plan.body.addWidget(self.plan_sock)
        plan.body.addWidget(w.label("Change these with the Unattended switch on the Tags and Socket "
                                    "pages. Save, then restart the service to pick up a change.",
                                    "hint", wrap=True))
        plan.body.addLayout(w.row(w.button("Save config", "primary", slot=self._save), None))
        plan.body.addStretch(1)
        top.addWidget(plan, 1)
        self.body.addLayout(top)

        where = w.Card("Where it reads and writes")
        form = w.Form(label_width=110)
        self.cfg_path = w.label("", "mono", wrap=True, selectable=True)
        self.log_path = w.label("", "mono", wrap=True, selectable=True)
        self.svc_log = w.label("", "mono", wrap=True, selectable=True)
        form.add("cfg", "Config", self.cfg_path)
        form.add("log", "Event log", self.log_path)
        form.add("svclog", "Service log", self.svc_log,
                 hint="The service's own messages: connection attempts and errors. Also in "
                      "Event Viewer under Windows Logs, Application.")
        where.body.addWidget(form)
        where.body.addLayout(w.row(
            w.button("Run headless here", icon_name="play", slot=self._headless,
                     tip="Runs the monitors in a console window with no service, until you close it."),
            None))
        self.body.addWidget(where)

        out = w.Card("Output")
        self.output = QPlainTextEdit()
        self.output.setProperty("role", "console")
        self.output.setReadOnly(True)
        out.body.addWidget(self.output, 1)
        self.body.addWidget(out, 1)

        self._result.connect(self._finished)
        self._state.connect(self._show_state)
        session.configSaved.connect(self._describe)
        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._describe()
        self.refresh()

    def refresh(self) -> None:
        threading.Thread(target=lambda: self._state.emit(*svc.query()), daemon=True).start()

    def _show_state(self, state: str, raw: str) -> None:
        text, tone = STATE_TEXT.get(state, (state.title().replace("_", " "), "idle"))
        w.set_chip(self.chip, text, tone)
        self.session.service = state
        self.session.serviceState.emit(state)
        installed = state not in ("NOT_INSTALLED", "UNAVAILABLE", "UNKNOWN")
        windows = state != "UNAVAILABLE"
        self.install_btn.setEnabled(windows and not installed)
        self.remove_btn.setEnabled(installed)
        self.start_btn.setEnabled(installed and state == "STOPPED")
        self.stop_btn.setEnabled(state == "RUNNING")
        if state == "RUNNING":
            self.status_text.setText("Logging in the background. If this window also connects, "
                                     "every change is logged twice: stop the service first, or "
                                     "only watch the Live log here.")
        elif state == "NOT_INSTALLED":
            self.status_text.setText("Set up the Tags and Socket pages, turn on Unattended for the "
                                     "ones the service should run, then Install.")
        elif state == "STOPPED":
            self.status_text.setText("Installed and set to start at boot, but not running now.")
        else:
            self.status_text.setText("")
        self.admin_note.setText("" if svc.is_admin() or not windows else
                                "Each button asks Windows for administrator rights once.")

    def _describe(self) -> None:
        c = self.session.cfg
        tags = tags_for(c.plc)
        if c.plc.get("enabled") and tags:
            self.plan_plc.setText(f"Poll {len(tags)} tag{'s' if len(tags) != 1 else ''} on "
                                  f"{c.plc.get('type')} at {c.plc.get('ip')} every "
                                  f"{c.plc.get('poll_ms')} ms, retrying every 15 s if it does not answer.")
            w.set_prop(self.plan_plc, "tone", "")
        elif c.plc.get("enabled"):
            self.plan_plc.setText("PLC polling is on, but there are no tags for the chosen PLC.")
            w.set_prop(self.plan_plc, "tone", "warn")
        else:
            self.plan_plc.setText("PLC: not run by the service.")
            w.set_prop(self.plan_plc, "tone", "")
        if c.socket.get("enabled"):
            text = f"Socket: {describe_socket(c.socket)}."
            if c.socket.get("mode") == "passive":
                text += " Passive sniff needs Npcap on this PC."
            self.plan_sock.setText(text)
        else:
            self.plan_sock.setText("Socket: not run by the service.")
        self.cfg_path.setText(c.path)
        self.log_path.setText(c.logging.get("log_path", ""))
        self.svc_log.setText(os.path.join(paths.log_dir(), "service.log"))

    def _save(self) -> None:
        for page in self.window().findChildren(Page):
            page.save_into_config()
        if self.session.save_config():
            self.session.status.emit(f"Config saved to {self.session.cfg.path}")

    def _run(self, commands: list[list[str]], doing: str) -> None:
        self.output.setPlainText(f"{doing}...")
        for button in (self.install_btn, self.start_btn, self.stop_btn, self.remove_btn):
            button.setEnabled(False)
        threading.Thread(target=lambda: self._result.emit(*svc.run_commands(commands)),
                         daemon=True).start()

    def _finished(self, ok: bool, text: str) -> None:
        self.output.setPlainText(text)
        QTimer.singleShot(1200, self.refresh)

    def _install(self) -> None:
        self._save()
        if not (self.session.cfg.plc.get("enabled") or self.session.cfg.socket.get("enabled")):
            if not w.ask(self, "Install service", "Neither monitor is set to run unattended.",
                         "The service would start and log nothing. Turn on Unattended on the "
                         "Tags or Socket page first, or install anyway.", yes="Install anyway"):
                return
        self._run(svc.install_commands(), "Installing")

    def _remove(self) -> None:
        if w.ask(self, "Remove service", "Stop the service and remove it?",
                 "The config and the logs are kept.", yes="Remove"):
            self._run(svc.remove_commands(), "Removing")

    def _headless(self) -> None:
        self._save()
        if getattr(sys, "frozen", False):
            command = [sys.executable, "headless"]
        else:
            command = [sys.executable, "-m", "app", "headless"]
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        try:
            subprocess.Popen(command, creationflags=flags, cwd=paths.program_folder())
            self.session.status.emit("Headless runner started in its own console. Close it to stop.")
        except OSError as exc:
            w.error(self, "Headless", "Could not start the headless runner.", str(exc))

    def shown(self) -> None:
        self._describe()
        self.refresh()
