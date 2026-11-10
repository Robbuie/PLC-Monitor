"""Trend: tag values over time, from the live log or any archive.

matplotlib, drawn in the theme's own colours and redrawn when the theme
changes. Lines come from the validated series palette (tokens.SERIES_LIGHT /
SERIES_DARK), assigned to a tag the first time it is plotted and kept: a tag is
the same colour all session however many others are ticked. Eight at most --
a ninth line is a chart nobody can read, so the list says so instead.

A log records changes, not samples, so each change is drawn as a step at the
moment it happened (logdata.series keeps the old value as well as the new).

A tag whose values are text -- a VIN, a recipe, a status word -- is drawn as a
strip under the charts: one span per value, labelled where there is room,
on the same time axis. **Find** marks every place a value appears, on lines
and strips alike, and steps through them.

Robot signals come from RobControl's own recording (engine/robdata.py), listed
in a second card under the tags. They share the eight lines, the colours and
the time axis with the PLC tags, so a register on a robot and a tag in the PLC
can be read against each other. The card stays a single line on a PC without
RobControl, which is most of the PCs this goes on.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.core import siblings
from app.engine import logdata, robdata
from app.theme import sheet
from app.theme.tokens import LIGHT_THEMES, SERIES_DARK, SERIES_LIGHT
from app.ui import widgets as w
from app.ui.pages.base import Page

PRESETS = [("Last 30 seconds", 0.5), ("Last minute", 1), ("Last 5 minutes", 5),
           ("Last 15 minutes", 15), ("Last 30 minutes", 30), ("Last hour", 60),
           ("Last 4 hours", 240), ("Last 8 hours", 480), ("Last 12 hours", 720),
           ("Last 24 hours", 1440), ("Last 48 hours", 2880), ("Last 7 days", 10080),
           ("Everything in the file", None), ("Between...", -1)]

MAX_LINES = 8
#: Roles on a tag-list item: the tag's name, and "number" or "text".
TAG_ROLE = Qt.UserRole + 1
KIND_ROLE = Qt.UserRole + 2
#: A span narrower than this many pixels is not labelled: the text would
#: overlap its neighbours. Zooming in labels it.
LABEL_MIN_PX = 46


class TrendPage(Page):
    key = "trend"
    title = "Trend"
    glyph = "trend"
    blurb = "Tag values over time, from the live log or an archive, with RobControl's robot signals."

    _loaded = Signal(object, list, str)
    _tags_found = Signal(str, list)
    _robots_found = Signal(str, list, str)

    def __init__(self, session, parent=None) -> None:
        super().__init__(session, parent)
        self._colours: dict[str, int] = {}
        self._extra_files: list[str] = []
        self._robot_files: list[str] = []
        self._signals: dict[str, robdata.RobotSignal] = {}
        self._lines = []
        self._loading = False

        top = w.Card()
        self.source = QComboBox()
        self.source.setMinimumWidth(260)
        browse = w.button("Open file...", slot=self._browse,
                          tip="Any PLC Monitor CSV or .csv.gz, including logs from another PC.")
        self.range = QComboBox()
        for text, minutes in PRESETS:
            self.range.addItem(text, minutes)
        self.range.setCurrentIndex(5)
        self.start = QDateTimeEdit(QDateTime.currentDateTime().addSecs(-3600))
        self.end = QDateTimeEdit(QDateTime.currentDateTime())
        for edit in (self.start, self.end):
            edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            edit.setCalendarPopup(True)
        self.between = w.hbox(w.label("from", "hint"), self.start, w.label("to", "hint"), self.end,
                              spacing=6)
        self.between.setVisible(False)
        self.arrange = w.Segments([("stacked", "One per tag"), ("shared", "Together")])
        self.arrange.set_tip("stacked", "A chart per tag on the same time axis. Tags with "
                             "different ranges stay readable.")
        self.arrange.set_tip("shared", "Every tag on one axis. Only useful when their values "
                             "are of a similar size.")
        self.arrange.changed.connect(lambda _k: self.plot() if (self._lines or self._lanes) else None)
        self.plot_btn = w.button("Plot", "primary", icon_name="trend", slot=self.plot)
        self.auto = w.Switch()
        export = w.button("Export PNG", icon_name="download", slot=self._export)
        top.body.addLayout(w.row(w.label("Source", "fieldlabel"), self.source, browse, 16,
                                 w.label("Range", "fieldlabel"), self.range, self.between, None,
                                 self.auto, w.label("Refresh every 30 s", "hint"), 8,
                                 self.plot_btn, export))
        self.body.addWidget(top)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        self.body.addWidget(split, 1)
        self.left = QSplitter(Qt.Vertical)
        self.left.setChildrenCollapsible(False)
        self.left.setHandleWidth(10)

        tags_card = w.Card("Tags")
        tags_card.add_action(w.button("All", "quiet", slot=lambda: self._tick_all(True)))
        tags_card.add_action(w.button("None", "quiet", slot=lambda: self._tick_all(False)))
        tags_card.add_action(w.tool("refresh", "Read the tag names from the file again",
                                    self.reload_tags))
        self.tag_list = QListWidget()
        tags_card.body.addWidget(self.tag_list, 1)
        self.tag_note = w.label("", "hint", wrap=True)
        tags_card.body.addWidget(self.tag_note)
        self.left.addWidget(tags_card)

        robots_card = w.Card("Robots")
        robots_card.title.setToolTip("Signals recorded by RobControl. PLC Monitor reads the "
                                     "recording; it never talks to a robot.")
        robots_card.add_action(w.button("Open...", "quiet", slot=self._browse_robots,
                                        tip="A robcontrol.db, for example one copied from "
                                            "another PC. Opened read-only."))
        robots_card.add_action(w.tool("refresh", "Read the signal list again", self.reload_robots))
        self.robot_source = QComboBox()
        self.robot_source.setToolTip("Which RobControl database to read.")
        robots_card.body.addWidget(self.robot_source)
        self.robot_list = QListWidget()
        robots_card.body.addWidget(self.robot_list, 1)
        self.robot_note = w.label("", "hint", wrap=True)
        robots_card.body.addWidget(self.robot_note)
        self.robot_open = w.button("Open RobControl", "quiet", icon_name="external",
                                   slot=lambda: siblings.launch(siblings.ROBCONTROL))
        robots_card.body.addWidget(self.robot_open)
        self.left.addWidget(robots_card)
        self.robots_card = robots_card

        self.left.setMinimumWidth(220)
        self.left.setMaximumWidth(340)
        split.addWidget(self.left)

        chart_card = w.Card("Chart")
        # The status goes under the chart, not in the header: with Find there
        # too, a long status pushed the header's controls off the card.
        self.chart_status = w.label("", "hint")
        chart_card.add_action(self.arrange)
        chart_card.header.addSpacing(10)
        self.home_btn = w.tool("home", "Back to the whole range", lambda: self._nav("home"))
        self.pan_btn = w.tool("pan", "Drag to pan", lambda: self._nav("pan"))
        self.zoom_btn = w.tool("search", "Drag a box to zoom", lambda: self._nav("zoom"))
        for button in (self.pan_btn, self.zoom_btn):
            button.setCheckable(True)
        for button in (self.home_btn, self.pan_btn, self.zoom_btn):
            chart_card.add_action(button)
        chart_card.header.addSpacing(10)
        self.finder = QLineEdit()
        self.finder.setProperty("role", "search")
        self.finder.setPlaceholderText("Find a value")
        self.finder.setClearButtonEnabled(True)
        self.finder.setFixedWidth(180)
        self.finder.setToolTip("Marks every change to a value containing this text, on the "
                             "charts and the text strips. Enter or the arrows step through them.")
        self.finder.textChanged.connect(lambda _t: self._find_changed())
        self.finder.returnPressed.connect(lambda: self._step(1))
        self.find_prev = w.tool("back", "Previous match", lambda: self._step(-1))
        self.find_next = w.tool("forward", "Next match", lambda: self._step(1))
        self.find_log = w.tool("log", "Show this match in the Live log, with the events "
                                      "either side of it", self._show_in_log)
        self.find_log.setEnabled(False)
        for widget in (self.finder, self.find_prev, self.find_next, self.find_log):
            chart_card.add_action(widget)
        self._matches: list = []
        self._match_at = -1
        self._marks: list = []
        self._lanes: list = []
        self.chart_holder = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_holder)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_card.body.addWidget(self.chart_holder, 1)
        chart_card.body.addWidget(self.chart_status)
        split.addWidget(chart_card)
        split.setStretchFactor(1, 1)

        self._build_chart()

        self.range.currentIndexChanged.connect(
            lambda: self.between.setVisible(self.range.currentData() == -1))
        self.source.currentIndexChanged.connect(lambda _i: self.reload_tags())
        self.tag_list.itemChanged.connect(self._ticked)
        self.robot_list.itemChanged.connect(self._ticked)
        self.robot_source.currentIndexChanged.connect(lambda _i: self.reload_robots())
        self._loaded.connect(self._render)
        self._tags_found.connect(self._fill_tags)
        self._robots_found.connect(self._fill_robots)
        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self.plot)
        self.auto.toggled.connect(lambda on: self._timer.start() if on else self._timer.stop())
        session.logPathChanged.connect(lambda _p: self.refresh_sources())
        self.refresh_sources()
        self.refresh_robot_sources()

    # ------------------------------------------------------------- chart

    def _build_chart(self) -> None:
        try:
            import matplotlib
            matplotlib.use("QtAgg")
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
            from matplotlib.figure import Figure
        except Exception as exc:  # pragma: no cover - only without matplotlib
            self.figure = None
            self.chart_layout.addWidget(w.label(
                f"matplotlib is not available in this build, so there is no chart. ({exc})",
                "hint", wrap=True))
            return
        self.figure = Figure(figsize=(8, 4), dpi=100)
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.figure)
        # matplotlib's own toolbar does the work but is not shown: its icons
        # are its own and do not follow the theme. The three buttons in the
        # card's header drive it instead.
        self.toolbar = NavigationToolbar2QT(self.canvas, self.chart_holder)
        self.toolbar.setVisible(False)
        self.chart_layout.addWidget(self.canvas, 1)
        self._tip = None
        self.canvas.mpl_connect("motion_notify_event", self._hover)
        self._style()
        self._empty("Pick tags on the left and press Plot.")

    def _style(self, ax=None) -> None:
        if self.figure is None:
            return
        t = sheet.tokens()
        self.figure.set_facecolor(t["bg_2"])
        ax = ax or self.axes
        ax.set_facecolor(t["bg_2"])
        for side, spine in ax.spines.items():
            spine.set_visible(side in ("bottom", "left"))
            spine.set_color(t["line"])
        ax.tick_params(colors=t["txt_2"], labelsize=8, length=3)
        ax.yaxis.label.set_color(t["txt_2"])
        ax.xaxis.label.set_color(t["txt_2"])
        ax.grid(True, axis="y", color=t["line_soft"], linewidth=0.8)
        ax.set_axisbelow(True)

    def _empty(self, text: str) -> None:
        if self.figure is None:
            return
        self.figure.clear()
        self._lanes = []
        self._marks = []
        self._matches = []
        self.axes = self.figure.add_subplot(111)
        self._axes = [self.axes]
        ax = self.axes
        self._style()
        self.figure.subplots_adjust(left=0.07, right=0.98, top=0.95, bottom=0.14)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center", va="center",
                color=sheet.tokens()["txt_2"], fontsize=10)
        self.canvas.draw_idle()

    def _palette(self) -> tuple[str, ...]:
        return SERIES_LIGHT if sheet.tokens().get("theme_name") in LIGHT_THEMES else SERIES_DARK

    def _colour_for(self, tag: str) -> str:
        if tag not in self._colours:
            used = set(self._colours.values())
            free = next((i for i in range(MAX_LINES) if i not in used), len(self._colours) % MAX_LINES)
            self._colours[tag] = free
        return self._palette()[self._colours[tag]]

    # ----------------------------------------------------------- sources

    def refresh_sources(self) -> None:
        current = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("Live log", self.session.logger.log_path)
        for path in reversed(self.session.logger.list_archives()):
            self.source.addItem(os.path.basename(path), path)
        for path in self._extra_files:
            self.source.addItem(os.path.basename(path), path)
        index = self.source.findData(current)
        self.source.setCurrentIndex(max(0, index))
        self.source.blockSignals(False)
        self.reload_tags()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open a log", self.session.log_folder(),
                                              "PLC Monitor logs (*.csv *.csv.gz *.gz);;All files (*)")
        if path:
            if path not in self._extra_files:
                self._extra_files.append(path)
            self.refresh_sources()
            self.source.setCurrentIndex(self.source.findData(path))

    def reload_tags(self) -> None:
        path = self.source.currentData()
        if not path:
            return
        self.tag_note.setText("Reading tag names...")
        include_bool = True
        specs = self.session.group_specs()

        def work():
            try:
                tags = logdata.trend_tags(self.session.log_rows(path, specs))
            except OSError:
                tags = []
            self._tags_found.emit(path, tags)

        threading.Thread(target=work, daemon=True).start()

    def _fill_tags(self, path: str, tags: list) -> None:
        if path != self.source.currentData():
            return  # an earlier read, of a source no longer chosen, finishing late
        ticked = set(self._selected())
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        for index, (tag, kind) in enumerate(tags):
            item = QListWidgetItem(tag if kind == "number" else f"{tag}   text")
            item.setData(TAG_ROLE, tag)
            item.setData(KIND_ROLE, kind)
            if kind == "text":
                item.setToolTip("Text values: drawn as a strip of spans under the charts, "
                                "one per value. Find marks a value.")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            on = tag in ticked if ticked else index < 4
            item.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.tag_list.addItem(item)
        self.tag_list.blockSignals(False)
        self._ticked()
        if not tags:
            self.tag_note.setText("No tag changes in this file yet.")

    # ------------------------------------------------------------ robots

    def refresh_robot_sources(self) -> None:
        """RobControl's database on this PC first, then any opened by hand."""
        current = self.robot_source.currentData()
        paths = []
        found = robdata.default_db_path()
        if found:
            paths.append(found)
        paths += [p for p in self._robot_files if p not in paths]
        self.robot_source.blockSignals(True)
        self.robot_source.clear()
        for path in paths:
            text = "RobControl on this PC" if path == found else os.path.basename(path)
            self.robot_source.addItem(text, path)
            self.robot_source.setItemData(self.robot_source.count() - 1, path, Qt.ToolTipRole)
        index = self.robot_source.findData(current)
        self.robot_source.setCurrentIndex(max(0, index))
        self.robot_source.blockSignals(False)
        self.robot_source.setVisible(len(paths) > 1)
        self.reload_robots()

    def _browse_robots(self) -> None:
        start = os.path.dirname(robdata.default_db_path() or "") or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "Open a RobControl database", start,
                                              "RobControl database (*.db);;All files (*)")
        if path:
            if path not in self._robot_files:
                self._robot_files.append(path)
            self.refresh_robot_sources()
            self.robot_source.setCurrentIndex(self.robot_source.findData(path))

    def add_robot_database(self, path: str) -> None:
        """Read robot signals from `path` as well. For tools/preview.py and tests."""
        if path not in self._robot_files:
            self._robot_files.append(path)
        self.refresh_robot_sources()
        self.robot_source.setCurrentIndex(self.robot_source.findData(path))

    def reload_robots(self) -> None:
        path = self.robot_source.currentData()
        if not path:
            self._fill_robots("", [], "")
            return
        self.robot_note.setText("Reading the robot signals...")

        def work():
            try:
                found, error = robdata.signals(path), ""
            except robdata.RobDataError as exc:
                found, error = [], str(exc)
            self._robots_found.emit(path, found, error)

        threading.Thread(target=work, daemon=True).start()

    def _fill_robots(self, path: str, found: list, error: str) -> None:
        if path and path != self.robot_source.currentData():
            return  # an older read finishing after the source changed
        ticked = {key for key in self._ticked_robot_keys()}
        self._signals = {signal.key: signal for signal in found}
        self.robot_list.blockSignals(True)
        self.robot_list.clear()
        for signal in found:
            item = QListWidgetItem(signal.name)
            item.setData(Qt.UserRole, signal.key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if signal.key in ticked else Qt.Unchecked)
            if signal.last_ms is None:
                item.setToolTip("Never recorded yet.")
            else:
                last = datetime.fromtimestamp(signal.last_ms / 1000)
                item.setToolTip(f"Last sample {last:%Y-%m-%d %H:%M:%S}")
            self.robot_list.addItem(item)
        self.robot_list.blockSignals(False)
        installed = siblings.location(siblings.ROBCONTROL) is not None
        self.robot_list.setVisible(bool(found))
        self.robot_open.setVisible(installed and not found)
        if error:
            self.robot_note.setText(error)
            w.set_prop(self.robot_note, "tone", "warn")
        elif not path:
            self.robot_note.setText(
                "RobControl records robot registers, I/O and system variables; its recording "
                "shows here. None on this PC" + (" yet." if installed else
                                                 ". Open... reads a robcontrol.db from another PC."))
            w.set_prop(self.robot_note, "tone", "")
        elif not found:
            self.robot_note.setText("No signals in this recording. Add some on a robot's "
                                    "Trends tab in RobControl and start recording.")
            w.set_prop(self.robot_note, "tone", "")
        else:
            w.set_prop(self.robot_note, "tone", "")
        # A PC without RobControl gets a short card, not half the column.
        if found:
            self.robots_card.setMaximumHeight(16777215)
            self.left.setSizes([3, 2])
        else:
            QTimer.singleShot(0, self._fit_robots_card)
        self._ticked()

    def _fit_robots_card(self) -> None:
        """Shrink the card to its header and note. Measured once laid out,
        because the note wraps and its height depends on the column width."""
        if self.robot_list.count():
            return
        card = self.robots_card
        layout = card.layout()
        layout.activate()
        height = layout.totalHeightForWidth(max(card.width(), 220))
        card.setMaximumHeight(max(height, layout.totalMinimumSize().height()))

    def _ticked_robot_keys(self) -> list[str]:
        return [self.robot_list.item(i).data(Qt.UserRole) for i in range(self.robot_list.count())
                if self.robot_list.item(i).checkState() == Qt.Checked]

    def _selected_robots(self) -> list:
        return [self._signals[k] for k in self._ticked_robot_keys() if k in self._signals]

    def _tick_all(self, on: bool) -> None:
        self.tag_list.blockSignals(True)
        for i in range(self.tag_list.count()):
            self.tag_list.item(i).setCheckState(Qt.Checked if on and i < MAX_LINES else Qt.Unchecked)
        self.tag_list.blockSignals(False)
        self._ticked()

    def _selected(self) -> list[str]:
        return [self.tag_list.item(i).data(TAG_ROLE) or self.tag_list.item(i).text()
                for i in range(self.tag_list.count())
                if self.tag_list.item(i).checkState() == Qt.Checked]

    def _kinds(self) -> dict[str, str]:
        return {self.tag_list.item(i).data(TAG_ROLE): self.tag_list.item(i).data(KIND_ROLE)
                for i in range(self.tag_list.count())}

    def _ticked(self, *_):
        n = len(self._selected())
        robots = len(self._ticked_robot_keys())
        total = self.tag_list.count()
        if n + robots > MAX_LINES:
            self.tag_note.setText(f"{n + robots} ticked, tags and robots together. The chart "
                                  f"draws {MAX_LINES} at most; untick some to choose which.")
            w.set_prop(self.tag_note, "tone", "warn")
        else:
            self.tag_note.setText(f"{n} of {total} ticked. Booleans plot as 0 and 1; text "
                                  "tags as a strip of values.")
            w.set_prop(self.tag_note, "tone", "")
        if self.robot_list.count() and self.robot_note.property("tone") != "warn":
            self.robot_note.setText(f"{robots} of {self.robot_list.count()} ticked. RobControl "
                                    "reads a robot every 2 s at the fastest.")

    # -------------------------------------------------------------- plot

    def _window(self) -> tuple[int | None, int | None, str]:
        minutes = self.range.currentData()
        if minutes == -1:
            start = self.start.dateTime().toMSecsSinceEpoch()
            end = self.end.dateTime().toMSecsSinceEpoch()
            return start, end, f"{self.start.text()} to {self.end.text()}"
        if minutes is None:
            return None, None, "everything in the file"
        start = datetime.now() - timedelta(minutes=minutes)
        return int(start.timestamp() * 1000), None, self.range.currentText().lower()

    def plot(self) -> None:
        if self.figure is None or self._loading:
            return
        tags = self._selected()[:MAX_LINES]
        robots = self._selected_robots()[:MAX_LINES - len(tags)]
        if not tags and not robots:
            self._empty("Tick at least one tag or robot signal.")
            return
        path = self.source.currentData()
        db = self.robot_source.currentData()
        start, end, label = self._window()
        names = tags + [signal.name for signal in robots]
        specs = self.session.group_specs()
        self._loading = True
        self.chart_status.setText("Loading...")

        kinds = self._kinds()
        text_tags = [t for t in tags if kinds.get(t) == "text"]
        number_tags = [t for t in tags if t not in text_tags]

        def work():
            data: dict = {}
            if tags:
                try:
                    # One pass over the file, keeping only the ticked tags'
                    # changes: a day's log is a few hundred thousand rows.
                    wanted = set(tags)
                    rows = [r for r in self.session.log_rows(path, specs)
                            if r.get("event_type") == "PLC_CHANGED"
                            and (r.get("tag_or_addr") or "").strip() in wanted]
                    if number_tags:
                        data.update(logdata.series(rows, number_tags, start, end))
                    if text_tags:
                        data["__text__"] = logdata.text_series(rows, text_tags, start, end)
                except OSError as exc:
                    data["__error__"] = str(exc)
            if robots:
                try:
                    data.update(robdata.series(db, robots, start, end))
                except robdata.RobDataError as exc:
                    data["__robot_error__"] = str(exc)
            self._loaded.emit(data, names, label)

        threading.Thread(target=work, daemon=True).start()

    def _render(self, data: dict, tags: list[str], label: str) -> None:
        self._loading = False
        problems = []
        if "__error__" in data:
            problems.append(f"Could not read the log: {data['__error__']}")
        if "__robot_error__" in data:
            problems.append(f"Could not read the robots: {data['__robot_error__']}")
        texts = data.get("__text__", {})
        present = [tag for tag in tags if data.get(tag, ([], []))[0]]
        lanes = [tag for tag in tags if texts.get(tag)]
        if problems and not present and not lanes:
            self._empty("\n".join(problems))
            self.chart_status.setText("")
            return
        import matplotlib.dates as mdates
        from matplotlib.ticker import MaxNLocator
        t = sheet.tokens()
        self._lines = []
        self._lanes = []
        self._marks = []
        if not present and not lanes:
            self._empty(f"No values for these in {label}.")
            self.chart_status.setText("")
            return
        stacked = self.arrange.value() == "stacked" and len(present) > 1
        charts = (len(present) if stacked else 1) if present else 0
        # Number charts get three parts of the height each, text strips one.
        ratios = [3] * charts + [1] * len(lanes)
        self.figure.clear()
        grid = self.figure.add_gridspec(len(ratios), 1, height_ratios=ratios,
                                        hspace=0.12 if (stacked or lanes) else 0.2)
        first = None
        chart_axes = []
        for i in range(charts):
            ax = self.figure.add_subplot(grid[i, 0], sharex=first)
            first = first or ax
            chart_axes.append(ax)
        lane_axes = []
        for j in range(len(lanes)):
            ax = self.figure.add_subplot(grid[charts + j, 0], sharex=first)
            first = first or ax
            lane_axes.append(ax)
        axes_for_lines = chart_axes if stacked else [chart_axes[0]] * len(present) if present else []
        self._axes = chart_axes + lane_axes
        self.axes = self._axes[0]
        points = 0
        for tag, ax in zip(present, axes_for_lines):
            times, values = data[tag]
            ax.plot(times, values, label=tag, color=self._colour_for(tag), linewidth=2,
                    solid_joinstyle="round", solid_capstyle="round")
            self._lines.append((tag, times, values, ax))
            points += len(times)
        for ax in chart_axes:
            self._style(ax)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4 if stacked else 8))
            # Plain numbers on the axis: a vehicle number is 3206300, not
            # 3.2 with "1e6" in the corner.
            try:
                ax.ticklabel_format(axis="y", style="plain", useOffset=False)
            except AttributeError:
                pass
        # The strips need an end time for the last span: the end of the range
        # asked for, or the latest time anything here was seen.
        latest = [times[-1] for _tag, times, _values, _ax in self._lines]
        latest += [spans[-1][0] for spans in texts.values() if spans]
        _start_ms, end_ms, _label = self._window()
        right = datetime.fromtimestamp(end_ms / 1000) if end_ms else max(latest)
        if end_ms is None and self.range.currentData() is not None:
            right = max(right, datetime.now())
        earliest = [times[0] for _tag, times, _values, _ax in self._lines]
        earliest += [spans[0][0] for spans in texts.values() if spans]
        left = min(earliest)
        for tag, ax in zip(lanes, lane_axes):
            self._draw_lane(ax, tag, texts[tag], left, right)
            points += len(texts[tag])
        locator = mdates.AutoDateLocator()
        bottom = self._axes[-1]
        for ax in self._axes[:-1]:
            ax.tick_params(labelbottom=False)
        bottom.xaxis.set_major_locator(locator)
        bottom.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        if stacked:
            # Each chart names its tag in its own corner, in ink rather than
            # the line colour; the line beside the name carries identity.
            for tag, _times, _values, ax in self._lines:
                ax.text(0.005, 0.96, tag, transform=ax.transAxes, ha="left", va="top",
                        fontsize=8.5, color=t["txt_1"],
                        bbox=dict(boxstyle="round,pad=0.25", fc=t["bg_2"], ec="none", alpha=0.85))
        elif len(self._lines) >= 2:
            legend = self.axes.legend(loc="upper left", fontsize=8, frameon=True,
                                      ncols=min(4, len(self._lines)))
            legend.get_frame().set_facecolor(t["bg_1"])
            legend.get_frame().set_edgecolor(t["line_soft"])
            for text in legend.get_texts():
                text.set_color(t["txt_1"])
        elif self._lines:
            self.axes.set_title(self._lines[0][0], color=t["txt_1"], fontsize=10, loc="left")
        titled = bool(self._lines) and not stacked and len(self._lines) == 1
        self.figure.subplots_adjust(left=0.08, right=0.98, top=0.93 if titled else 0.97,
                                    bottom=0.1)
        bottom.callbacks.connect("xlim_changed", lambda _ax: self._label_lanes())
        self._label_lanes()
        self._tip = None
        self._find_changed(redraw=False)
        self.canvas.draw_idle()
        n = len(self._lines) + len(self._lanes)
        self.chart_status.setText(f"{n} tag{'s' if n != 1 else ''}, {points:,} points, {label}"
                                  f"  |  updated {time.strftime('%H:%M:%S')}"
                                  + ("  |  " + problems[0] if problems else ""))

    # ------------------------------------------------------- text strips

    def _draw_lane(self, ax, tag: str, spans: list, left, right) -> None:
        """A text tag as a strip: a bar per value, from when it was taken to
        when the next one was. Values alternate between two strengths of the
        tag's colour so neighbours with no gap stay apart; an empty value
        (a register cleared between messages) is left blank."""
        import matplotlib.dates as mdates
        t = sheet.tokens()
        colour = self._colour_for(tag)
        segments = []
        for i, (when, value) in enumerate(spans):
            begin = when
            if i == 0 and len(spans) > 1 and spans[1][0] == when:
                begin = left            # the value before the first change
            finish = spans[i + 1][0] if i + 1 < len(spans) else max(right, when)
            # Two changes in the same millisecond leave a span of no width:
            # nothing to draw, and never one stretched to the end of the day.
            if finish > begin:
                segments.append((begin, finish, value))
        x0 = mdates.date2num([b for b, _f, _v in segments])
        widths = mdates.date2num([f for _b, f, _v in segments]) - x0
        shades = [0.0 if not v else (0.55 if k % 2 else 0.85) for k, (_b, _f, v) in
                  enumerate(segments)]
        bars = ax.barh([0] * len(segments), widths, left=x0, height=0.8, color=colour,
                       edgecolor=t["bg_2"], linewidth=0.6)
        for bar, alpha in zip(bars, shades):
            bar.set_alpha(alpha)
        ax.set_ylim(-0.5, 0.5)
        ax.set_yticks([])
        ax.set_facecolor(t["bg_2"])
        for side, spine in ax.spines.items():
            spine.set_visible(side == "bottom")
            spine.set_color(t["line"])
        ax.tick_params(colors=t["txt_2"], labelsize=8, length=3)
        ax.text(0.005, 0.5, tag, transform=ax.transAxes, ha="left", va="center",
                fontsize=8.5, color=t["txt_1"], zorder=5,
                bbox=dict(boxstyle="round,pad=0.25", fc=t["bg_2"], ec="none", alpha=0.9))
        self._lanes.append({"tag": tag, "ax": ax, "segments": segments, "labels": []})

    def _label_lanes(self) -> None:
        """Write each value in its span where the span is wide enough, at
        the current zoom. Called again whenever the time axis moves."""
        if self.figure is None or not self._lanes:
            return
        import matplotlib.dates as mdates
        t = sheet.tokens()
        for lane in self._lanes:
            for text in lane["labels"]:
                text.remove()
            lane["labels"] = []
            ax = lane["ax"]
            lo, hi = ax.get_xlim()
            px_per_day = ax.bbox.width / max(hi - lo, 1e-12)
            drawn = 0
            for begin, finish, value in lane["segments"]:
                if not value:
                    continue
                b, f = mdates.date2num(begin), mdates.date2num(finish)
                if f < lo or b > hi:
                    continue
                if (min(f, hi) - max(b, lo)) * px_per_day < LABEL_MIN_PX:
                    continue
                lane["labels"].append(ax.text(
                    (max(b, lo) + min(f, hi)) / 2, 0, value, ha="center", va="center",
                    fontsize=8, color=t["txt_0"], clip_on=True))
                drawn += 1
                if drawn >= 400:
                    break
        self.canvas.draw_idle()

    # -------------------------------------------------------------- find

    def _find_changed(self, redraw: bool = True) -> None:
        """Every change to a value containing the find text, as (time, tag,
        value), marked with a thin line across all the charts."""
        import matplotlib.dates as mdates
        for mark in self._marks:
            try:
                mark.remove()
            except (ValueError, AttributeError):
                pass
        self._marks = []
        self._matches = []
        self._match_at = -1
        needle = self.finder.text().strip().lower()
        if needle and self.figure is not None:
            for lane in self._lanes:
                for begin, _finish, value in lane["segments"]:
                    if value and needle in value.lower():
                        self._matches.append((begin, lane["tag"], value))
            for tag, times, values, _ax in self._lines:
                previous = None
                for when, value in zip(times, values):
                    if value != value:          # NaN: a gap, no value
                        continue
                    # Whole numbers in full: a vehicle number 3206300, not
                    # %g's 3.2063e+06.
                    text = str(int(value)) if float(value).is_integer() else f"{value:g}"
                    if text != previous and needle == text.lower():
                        self._matches.append((when, tag, text))
                    previous = text
            self._matches.sort(key=lambda m: m[0])
            t = sheet.tokens()
            for when, _tag, _value in self._matches[:2000]:
                x = mdates.date2num(when)
                for ax in self._axes:
                    self._marks.append(ax.axvline(x, color=t["accent"], linewidth=1,
                                                  alpha=0.55, zorder=4))
        if needle:
            n = len(self._matches)
            self.chart_status.setText(f"{n:,} match{'es' if n != 1 else ''} for '{needle}'"
                                      if n else f"No value contains '{needle}' in this chart.")
        self.find_prev.setEnabled(bool(self._matches))
        self.find_next.setEnabled(bool(self._matches))
        self.find_log.setEnabled(False)
        if redraw and self.figure is not None:
            self.canvas.draw_idle()

    def _step(self, direction: int) -> None:
        """To the next (or previous) match: centred, at the current zoom if
        that already shows some detail, else zoomed to show its neighbours."""
        if not self._matches or self.figure is None:
            return
        import matplotlib.dates as mdates
        self._match_at = (self._match_at + direction) % len(self._matches)
        when, tag, value = self._matches[self._match_at]
        x = mdates.date2num(when)
        ax = self._axes[-1]
        lo, hi = ax.get_xlim()
        width = hi - lo
        full = [mdates.date2num(m[0]) for m in self._matches]
        span = (max(full) - min(full)) if len(full) > 1 else width
        if width > max(span / 10, 1 / 1440):
            width = max(span / 10, 2 / 1440)       # at least two minutes
        ax.set_xlim(x - width / 2, x + width / 2)
        # Relabel the strips for the new zoom. (The xlim_changed callback
        # does this for the toolbar's pan and zoom, but was not reliably
        # called for a limit set from code on a shared axis.)
        self._label_lanes()
        self.find_log.setEnabled(True)
        self.chart_status.setText(f"{self._match_at + 1:,} of {len(self._matches):,}: {tag} = "
                                  f"{value} at {when:%Y-%m-%d %H:%M:%S}.{when.microsecond // 1000:03d}")
        self.canvas.draw_idle()

    def _hover(self, event) -> None:
        """Nearest point under the pointer, as a small label: tag, time, value."""
        axes = getattr(self, "_axes", [])
        if self.figure is None or not (self._lines or self._lanes) or event.inaxes not in axes:
            if getattr(self, "_tip", None) is not None and self._tip.get_visible():
                self._tip.set_visible(False)
                self.canvas.draw_idle()
            return
        import matplotlib.dates as mdates
        lane = next((ln for ln in self._lanes if ln["ax"] is event.inaxes), None)
        if lane is not None:
            self._hover_lane(lane, event)
            return
        best = None
        for tag, times, values, ax in self._lines:
            if ax is not event.inaxes:
                continue
            xs = mdates.date2num(times)
            for x, y, when in zip(xs, values, times):
                if y != y:      # NaN: a gap in the line
                    continue
                px, py = ax.transData.transform((x, y))
                d = (px - event.x) ** 2 + (py - event.y) ** 2
                if best is None or d < best[0]:
                    best = (d, tag, when, y, x, ax)
        if best is None or best[0] > 30 ** 2:
            if self._tip is not None and self._tip.get_visible():
                self._tip.set_visible(False)
                self.canvas.draw_idle()
            return
        _, tag, when, value, x, ax = best
        t = sheet.tokens()
        text = f"{tag}\n{when.strftime('%H:%M:%S.%f')[:-3]}   {value:g}"
        if self._tip is not None and self._tip.axes is not ax:
            self._tip.remove()
            self._tip = None
        if self._tip is None:
            self._tip = ax.annotate(
                text, xy=(x, value), xytext=(12, 12), textcoords="offset points", fontsize=8,
                color=t["txt_0"], bbox=dict(boxstyle="round,pad=0.4", fc=t["bg_1"], ec=t["line"]))
        else:
            self._tip.xy = (x, value)
            self._tip.set_text(text)
            self._tip.set_visible(True)
        self.canvas.draw_idle()

    def _show_in_log(self) -> None:
        if not (0 <= self._match_at < len(self._matches)):
            return
        when, tag, value = self._matches[self._match_at]
        self.session.showInLog.emit(self.source.currentData() or "",
                                    int(when.timestamp() * 1000), tag, str(value))

    def _hover_lane(self, lane: dict, event) -> None:
        """Over a text strip: the value under the pointer and when it began."""
        import matplotlib.dates as mdates
        x = event.xdata
        hit = None
        for begin, finish, value in lane["segments"]:
            if mdates.date2num(begin) <= x <= mdates.date2num(finish):
                hit = (begin, finish, value)
                break
        ax = lane["ax"]
        if hit is None or not hit[2]:
            if self._tip is not None and self._tip.get_visible():
                self._tip.set_visible(False)
                self.canvas.draw_idle()
            return
        begin, finish, value = hit
        t = sheet.tokens()
        stamp = lambda d: f"{d:%H:%M:%S}.{d.microsecond // 1000:03d}"  # noqa: E731
        text = f"{lane['tag']}\n{value}\n{stamp(begin)} to {stamp(finish)}"
        if self._tip is not None and self._tip.axes is not ax:
            self._tip.remove()
            self._tip = None
        if self._tip is None:
            self._tip = ax.annotate(
                text, xy=(x, 0), xytext=(12, 10), textcoords="offset points", fontsize=8,
                color=t["txt_0"], zorder=6,
                bbox=dict(boxstyle="round,pad=0.4", fc=t["bg_1"], ec=t["line"]))
        else:
            self._tip.xy = (x, 0)
            self._tip.set_text(text)
            self._tip.set_visible(True)
        self.canvas.draw_idle()

    def _nav(self, what: str) -> None:
        if self.figure is None:
            return
        if what == "home":
            self.toolbar.home()
            return
        getattr(self.toolbar, what)()
        mode = str(getattr(self.toolbar, "mode", "")).lower()
        self.pan_btn.setChecked("pan" in mode)
        self.zoom_btn.setChecked("zoom" in mode)

    def _export(self) -> None:
        if self.figure is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export chart", "plc_trend.png", "PNG image (*.png)")
        if path:
            self.figure.savefig(path, dpi=150, facecolor=self.figure.get_facecolor())
            self.session.status.emit(f"Saved {path}")

    def shown(self) -> None:
        if self.source.count() and self.source.itemData(0) != self.session.logger.log_path:
            self.refresh_sources()
        QTimer.singleShot(0, self._fit_robots_card)
        # RobControl may have been installed, or started recording, since.
        if robdata.default_db_path() and self.robot_source.findData(robdata.default_db_path()) < 0:
            self.refresh_robot_sources()

    def theme_changed(self) -> None:
        super().theme_changed()
        if self.figure is not None:
            if self._lines or self._lanes:
                self.plot()
            else:
                self._empty("Pick tags on the left and press Plot.")
