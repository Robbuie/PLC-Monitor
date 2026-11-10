"""The main window: title bar, rail, the page in front, and a status line.

Frameless by default, with its own title bar, the way File Manager and
NetControl are -- the menus sit in the title bar and the chips beside them say
what is running. Windows' own title bar is one click away in the View menu,
for a remote desktop session or a PC where a frameless window misbehaves; the
rest of the window is the same either way.

Resizing a frameless window is the system's job: a press within a few pixels of
an edge is handed to `startSystemResize`, so snapping and the resize cursor
behave as they do everywhere else on the machine.
"""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import QByteArray, QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app import __version__, paths
from app.core import siblings, themeswitch, updates
from app.core.settings import Settings
from app.theme import sheet
from app.ui import widgets as w
from app.ui.appearance import AppearanceDialog
from app.ui.guide import GuideDialog
from app.ui.pages.latency import LatencyPage
from app.ui.pages.log import LogPage, open_folder
from app.ui.pages.network import NetworkPage
from app.ui.pages.service import ServicePage
from app.ui.pages.socket import SocketPage
from app.ui.pages.tags import TagsPage
from app.ui.pages.trend import TrendPage
from app.ui.rail import Rail
from app.ui.session import Session
from app.ui.titlebar import TitleBar

EDGE = 5
PAGES = [TagsPage, SocketPage, LogPage, TrendPage, LatencyPage, NetworkPage, ServicePage]


class MainWindow(QMainWindow):
    def __init__(self, session: Session, settings: Settings) -> None:
        super().__init__()
        self.session = session
        self.settings = settings
        self.native = bool(settings.get("window.native_frame"))
        self.setWindowTitle("PLC Monitor")
        self.setMinimumSize(1000, 660)
        if not self.native:
            self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)

        root = QWidget()
        root.setProperty("role", "window")
        root.setAttribute(Qt.WA_StyledBackground, True)
        root.setMouseTracking(True)
        self.setCentralWidget(root)
        self.root = root
        outer = QVBoxLayout(root)
        outer.setSpacing(0)
        self.outer = outer

        self.titlebar = TitleBar()
        self.titlebar.set_native(self.native)
        self.titlebar.minimizeRequested.connect(self.showMinimized)
        self.titlebar.maximizeRequested.connect(self._toggle_max)
        self.titlebar.closeRequested.connect(self.close)
        outer.addWidget(self.titlebar)

        middle = QHBoxLayout()
        middle.setSpacing(0)
        self.pages = {cls.key: cls(session) for cls in PAGES}
        self.stack = QStackedWidget()
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.rail = Rail(self.pages)
        self.rail.selected.connect(self.show_page)
        middle.addWidget(self.rail)
        middle.addWidget(self.stack, 1)
        outer.addLayout(middle, 1)

        bar = QFrame()
        bar.setProperty("role", "statusbar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(28)
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(14, 0, 14, 0)
        self.status = QLabel("Ready")
        self.status.setProperty("role", "status")
        self.status_right = QLabel(f"v{__version__}")
        self.status_right.setProperty("role", "statusright")
        bar_row.addWidget(self.status, 1)
        bar_row.addWidget(self.status_right)
        outer.addWidget(bar)
        self._margins()

        self._build_menus()
        session.status.connect(self._say)
        session.plcState.connect(self._plc_chip)
        session.sockState.connect(self._sock_chip)
        session.serviceState.connect(self._svc_chip)
        session.goto.connect(self.show_page)

        self._stats = QTimer(self)
        self._stats.setInterval(2000)
        self._stats.timeout.connect(self._refresh_stats)
        self._stats.start()
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(60_000)
        self._theme_timer.timeout.connect(self._follow_theme)
        self._theme_timer.start()

        self.updates = updates.Updates(settings, __version__, self)
        self.updates.available.connect(self._update_available)
        self.updates.uptodate.connect(lambda v: w.info(self, "Updates", f"PLC Monitor {v} is the newest version."))
        self.updates.problem.connect(lambda m: w.info(self, "Updates", m))
        self.updates.progress.connect(self._update_progress)
        self.updates.ready.connect(self._update_ready)
        self._progress: QProgressDialog | None = None

        QApplication.instance().installEventFilter(self)
        self.apply_theme()
        self._restore()
        self.show_page(str(settings.get("page") or "tags"))
        if session.migrated_from:
            QTimer.singleShot(400, lambda: self._say(
                f"Settings carried over from {session.migrated_from} to {session.cfg.path}"))
        self.updates.start_if_wanted()

    # ------------------------------------------------------------- pages

    def show_page(self, key: str) -> None:
        page = self.pages.get(key) or self.pages["tags"]
        self.stack.setCurrentWidget(page)
        self.rail.set_current(page.key)
        self.settings.set("page", page.key)
        page.shown()

    # ------------------------------------------------------------- menus

    def _build_menus(self) -> None:
        bar = self.titlebar.menus

        def act(menu, text, slot, shortcut=None, tip=""):
            action = QAction(text, self)
            action.triggered.connect(slot)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                self.addAction(action)
            if tip:
                action.setStatusTip(tip)
                action.setToolTip(tip)
            menu.addAction(action)
            return action

        file_menu = bar.addMenu("&File")
        act(file_menu, "Save config", self.save_config, "Ctrl+S")
        act(file_menu, "Open log folder", lambda: open_folder(self.session.log_folder()))
        act(file_menu, "Open data folder", lambda: open_folder(paths.ensure_data_dir()))
        file_menu.addSeparator()
        act(file_menu, "Exit", self.close)

        mon = bar.addMenu("&Monitor")
        self.a_plc = act(mon, "Connect to PLC", lambda: self.pages["tags"]._connect(), "F5")
        self.a_plc_off = act(mon, "Disconnect PLC", self.session.disconnect_plc, "Shift+F5")
        mon.addSeparator()
        self.a_sock = act(mon, "Start socket monitor", lambda: self.pages["socket"]._start(), "F6")
        self.a_sock_off = act(mon, "Stop socket monitor", self.session.stop_socket, "Shift+F6")
        mon.addSeparator()
        act(mon, "Stop everything", self._stop_all, "Ctrl+.")
        self.a_plc_off.setEnabled(False)
        self.a_sock_off.setEnabled(False)

        view = bar.addMenu("&View")
        for i, cls in enumerate(PAGES, start=1):
            act(view, cls.title, lambda _=False, k=cls.key: self.show_page(k), f"Ctrl+{i}")
        view.addSeparator()
        act(view, "Appearance...", self._appearance, "Ctrl+,")
        native = QAction("Use the Windows title bar", self)
        native.setCheckable(True)
        native.setChecked(self.native)
        native.toggled.connect(self._set_native)
        view.addAction(native)

        tools = bar.addMenu("&Tools")
        nc = act(tools, "Open NetControl", lambda: siblings.launch(siblings.NETCONTROL),
                 tip="Device diagnostics, BOOTP/DHCP and the protocol-verified service check.")
        fm = act(tools, "Open the log folder in File Manager",
                 lambda: siblings.launch(siblings.FILE_MANAGER, self.session.log_folder()))
        tools.aboutToShow.connect(lambda: (nc.setVisible(siblings.location(siblings.NETCONTROL) is not None),
                                           fm.setVisible(siblings.location(siblings.FILE_MANAGER) is not None)))
        act(tools, "Port check...", lambda: self.show_page("network"))
        act(tools, "Latency...", lambda: self.show_page("latency"))

        help_menu = bar.addMenu("&Help")
        act(help_menu, "User guide", lambda: GuideDialog(self).exec(), "F1")
        act(help_menu, "Check for updates", lambda: self.updates.check(manual=True))
        act(help_menu, "Releases page", lambda: webbrowser.open(f"https://github.com/{updates.REPO}/releases"))
        help_menu.addSeparator()
        act(help_menu, "About PLC Monitor", self._about)

    def _about(self) -> None:
        w.info(self, "About PLC Monitor", f"PLC Monitor {__version__}",
               "Timestamps PLC tag changes and socket messages to the millisecond, "
               "so the time between them can be measured.\n\n"
               f"Config: {self.session.cfg.path}\nLog: {self.session.logger.log_path}")

    # ------------------------------------------------------------- theme

    def apply_theme(self) -> None:
        s = self.settings
        theme = themeswitch.current(s, windows="ask")
        sheet.apply(QApplication.instance(), theme, s.get("accent"), s.get("density"),
                    font=s.get("font"), corners=s.get("corners"))
        self._applied_theme = theme
        self.titlebar.apply_tokens()
        w.refresh_icons(self)
        self.rail.refresh_icons()
        for page in self.pages.values():
            page.theme_changed()

    def _follow_theme(self) -> None:
        if self.settings.get("theme.follow") == "off":
            return
        if themeswitch.current(self.settings) != getattr(self, "_applied_theme", None):
            self.apply_theme()

    def _appearance(self) -> None:
        dialog = AppearanceDialog(self.settings, self)
        dialog.changed.connect(lambda _k, _v: (self.apply_theme(), self.settings.save()))
        dialog.exec()

    def _set_native(self, on: bool) -> None:
        self.settings.set("window.native_frame", on)
        self.settings.save()
        w.info(self, "Title bar", "This takes effect the next time PLC Monitor starts.")

    # ------------------------------------------------------------ status

    def _say(self, text: str) -> None:
        self.status.setText(text)

    def _plc_chip(self, state: str, detail: str) -> None:
        text, tone = {"on": ("PLC  polling", "good"), "connecting": ("PLC  connecting", "info"),
                      "reconnecting": ("PLC  reconnecting", "warn"),
                      "error": ("PLC  failed", "down")}.get(state, ("PLC  off", "idle"))
        w.set_chip(self.titlebar.plc_chip, text, tone)
        self.titlebar.plc_chip.setToolTip(detail)
        self.a_plc.setEnabled(state not in ("on", "connecting", "reconnecting"))
        self.a_plc_off.setEnabled(state in ("on", "reconnecting"))
        if state == "error":
            self._say(f"PLC: {detail.splitlines()[0] if detail else 'failed'}")

    def _sock_chip(self, state: str, detail: str) -> None:
        text, tone = {"on": ("Socket  running", "good"), "error": ("Socket  failed", "down")}.get(
            state, ("Socket  off", "idle"))
        w.set_chip(self.titlebar.sock_chip, text, tone)
        self.titlebar.sock_chip.setToolTip(detail)
        self.a_sock.setEnabled(state != "on")
        self.a_sock_off.setEnabled(state == "on")
        if state == "error":
            self._say(f"Socket: {detail.splitlines()[0] if detail else 'failed'}")

    def _svc_chip(self, state: str) -> None:
        running = state == "RUNNING"
        self.titlebar.svc_chip.setVisible(running)
        if running:
            w.set_chip(self.titlebar.svc_chip, "Service  running", "info")
            self.titlebar.svc_chip.setToolTip("The PLC Monitor service is logging in the background.")
        self.rail.set_stat("service", {"RUNNING": "running", "STOPPED": "stopped",
                                       "NOT_INSTALLED": "not installed"}.get(state, "-"))

    def _refresh_stats(self) -> None:
        self.rail.set_stat("events", f"{self.session.event_count:,}")
        self.rail.set_stat("log", f"{self.session.logger.current_size_mb():.1f} MB")

    def _stop_all(self) -> None:
        self.session.disconnect_plc()
        self.session.stop_socket()

    def save_config(self) -> None:
        for page in self.pages.values():
            page.save_into_config()
        if self.session.save_config():
            self._say(f"Config saved to {self.session.cfg.path}")

    # ------------------------------------------------------------ frame

    def _margins(self) -> None:
        edge = 0 if (self.native or self.isMaximized()) else EDGE
        self.outer.setContentsMargins(edge, edge, edge, edge)

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.WindowStateChange:
            self.titlebar.set_maximized(self.isMaximized())
            self._margins()
        super().changeEvent(event)

    def _edges(self, pos: QPoint) -> Qt.Edges:
        edges = Qt.Edges()
        if self.native or self.isMaximized():
            return edges
        r = self.rect()
        if pos.x() <= EDGE:
            edges |= Qt.LeftEdge
        if pos.x() >= r.width() - EDGE:
            edges |= Qt.RightEdge
        if pos.y() <= EDGE:
            edges |= Qt.TopEdge
        if pos.y() >= r.height() - EDGE:
            edges |= Qt.BottomEdge
        return edges

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.HoverMove) and \
                isinstance(obj, QWidget) and QWidget.window(obj) is self and not self.native:
            pos = self.mapFromGlobal(event.globalPosition().toPoint()) if hasattr(event, "globalPosition") else None
            if pos is not None:
                edges = self._edges(pos)
                if kind == QEvent.MouseButtonPress and edges and event.button() == Qt.LeftButton:
                    handle = self.windowHandle()
                    if handle is not None and handle.startSystemResize(edges):
                        return True
                if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
                    cursor = Qt.SizeFDiagCursor
                elif edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
                    cursor = Qt.SizeBDiagCursor
                elif edges & (Qt.LeftEdge | Qt.RightEdge):
                    cursor = Qt.SizeHorCursor
                elif edges & (Qt.TopEdge | Qt.BottomEdge):
                    cursor = Qt.SizeVerCursor
                else:
                    cursor = None
                if cursor is not None:
                    self.setCursor(cursor)
                elif self.cursor().shape() in (Qt.SizeFDiagCursor, Qt.SizeBDiagCursor,
                                               Qt.SizeHorCursor, Qt.SizeVerCursor):
                    self.unsetCursor()
        return super().eventFilter(obj, event)

    # ---------------------------------------------------------- geometry

    def _restore(self) -> None:
        geometry = self.settings.get("window.geometry")
        if geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            except Exception:
                pass
        else:
            self.resize(1360, 860)

    def show_restored(self) -> None:
        if self.settings.get("window.maximized"):
            self.showMaximized()
        else:
            self.show()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.session.plc_running or self.session.sock_running:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("PLC Monitor")
            box.setText("Monitoring is running. Stop it and close?")
            box.setInformativeText("Nothing more is logged once the window closes. To keep "
                                   "logging without a window, install the service.")
            stop = box.addButton("Stop and close", QMessageBox.AcceptRole)
            box.addButton("Keep running", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not stop:
                event.ignore()
                return
        for page in self.pages.values():
            page.save_into_config()
        self.session.save_config()
        self.settings.set("window.maximized", self.isMaximized())
        if not self.isMaximized():
            self.settings.set("window.geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))
        self.settings.save()
        self.session.shutdown()
        self.updates.shutdown()
        self.updates.install_staged()
        event.accept()

    # ----------------------------------------------------------- updates

    def _update_available(self, release) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Update available")
        box.setText(f"PLC Monitor {release.version} is available. This is {__version__}.")
        box.setInformativeText("It downloads now and installs when PLC Monitor closes. "
                               "A running service is not affected until it is restarted.")
        get = box.addButton("Download", QMessageBox.AcceptRole)
        skip = box.addButton("Skip this version", QMessageBox.DestructiveRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is get:
            self._progress = QProgressDialog("Downloading the update...", "Cancel", 0, 100, self)
            self._progress.setWindowTitle("Update")
            self._progress.canceled.connect(self.updates.cancel)
            self._progress.show()
            self.updates.accept(release)
        elif box.clickedButton() is skip:
            self.updates.skip(release)
            self.settings.save()

    def _update_progress(self, done: int, total: int) -> None:
        if self._progress is not None and total:
            self._progress.setValue(int(done * 100 / total))

    def _update_ready(self, release) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        w.info(self, "Update ready", f"PLC Monitor {release.version} installs when you close this window.")
