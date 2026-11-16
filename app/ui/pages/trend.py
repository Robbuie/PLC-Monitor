"""Trend: tag values over time, from the live log or any archive, or live.

Drawn with pyqtgraph (app/ui/trendchart.py) in the theme's own colours, and
redrawn when the theme changes. Lines come from the validated series palette
(tokens.SERIES_LIGHT / SERIES_DARK), assigned to a tag the first time it is
plotted and kept: a tag is the same colour all session however many others
are ticked. Eight at most -- a ninth line is a chart nobody can read, so the
list says so instead.

A log records changes, not samples, so each value is drawn as a level from
the moment it was logged until the next change (engine/trendview.py). When
there are more changes in view than pixels across, each pixel column is drawn
as a stroke from the lowest to the highest value held in it, so a spike of a
few milliseconds in a day-long view is still there to see.

**Live** follows the PLC: the chart is fed from the rows the monitor logs as
they happen (Session.events), not by reading the file again, and scrolls to
keep now at the right edge. Panning or zooming by hand stops the scrolling so
the moment being looked at stays put; Home picks it up again.

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

import csv
import math
import os
import threading
import time
from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core import siblings
from app.engine import logdata, robdata, trendmath, trendprefs, trendview
from app.engine.trendview import StepSeries, TextSeries
from app.theme import sheet
from app.theme.tokens import LIGHT_THEMES, SERIES_DARK, SERIES_LIGHT
from app.ui import widgets as w
from app.ui.pages.base import Page
from app.engine.trendview import duration
from app.ui.trendpens import PenDialog, PenTable, stamp
from app.ui.trendtools import CompareDialog, MathDialog

PRESETS = [("Last 30 seconds", 0.5), ("Last minute", 1), ("Last 5 minutes", 5),
           ("Last 15 minutes", 15), ("Last 30 minutes", 30), ("Last hour", 60),
           ("Last 4 hours", 240), ("Last 8 hours", 480), ("Last 12 hours", 720),
           ("Last 24 hours", 1440), ("Last 48 hours", 2880), ("Last 7 days", 10080),
           ("Everything in the file", None), ("Between...", -1)]

MAX_LINES = 8
#: Roles on a tag-list item: the tag's name, and "number" or "text".
TAG_ROLE = Qt.UserRole + 1
KIND_ROLE = Qt.UserRole + 2
#: Live: how often the chart scrolls and redraws, and how often RobControl's
#: recording is read again (it samples a robot every 2 s at the fastest).
LIVE_TICK_MS = 250
ROBOT_EVERY_TICKS = 20
#: Find marks at most this many places; past that the lines are a solid wash.
MAX_MARKS = 5000
EMPTY_TEXT = "Pick tags on the left and press Plot, or turn on Live."


class Choice(QComboBox):
    """A short drop-down with Segments' value()/set_value()/changed, for a
    header with no room for a row of buttons. set_value does not announce."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], tips: dict | None = None) -> None:
        super().__init__()
        for key, text in options:
            self.addItem(text, key)
            if tips and key in tips:
                self.setItemData(self.count() - 1, tips[key], Qt.ToolTipRole)
        self.currentIndexChanged.connect(lambda _i: self.changed.emit(self.value()))

    def value(self) -> str:
        return self.currentData() or ""

    def set_value(self, key: str) -> None:
        index = self.findData(key)
        if index >= 0:
            self.blockSignals(True)
            self.setCurrentIndex(index)
            self.blockSignals(False)


class TrendPage(Page):
    key = "trend"
    title = "Trend"
    glyph = "trend"
    blurb = "Tag values over time, live or from a log, with RobControl's robot signals."

    _loaded = Signal(object, list, str)
    _tags_found = Signal(str, list)
    _robots_found = Signal(str, list, str)
    _robots_more = Signal(object)
    _compared = Signal(object, str)

    def __init__(self, session, parent=None) -> None:
        super().__init__(session, parent)
        self._colours: dict[str, int] = {}
        self._extra_files: list[str] = []
        self._robot_files: list[str] = []
        self._signals: dict[str, robdata.RobotSignal] = {}
        self._lines: list[dict] = []
        self._lanes: list[dict] = []
        self._loading = False
        self._last: tuple | None = None        # (data, names, label) of the last render
        self._held: list[dict] = []            # live rows that arrived while loading
        self._following = True
        self._ticks = 0
        self._live_robots: list = []
        self._robot_db = ""
        self._robots_busy = False
        self.chart = None
        self._sources: dict[str, StepSeries] = {}      # PLC tags read, shown or behind a math pen
        self._dirty = False
        self._compare: tuple[str, float, str] | None = None
        self.cycle_view = None

        top = w.Card()
        self.source = QComboBox()
        self.source.setMinimumWidth(240)
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
        self.arrange = Choice(
            [("stacked", "Chart per tag"), ("same", "Same scale"), ("shared", "One chart")],
            {"stacked": "A chart per tag on the same time axis. Tags with different ranges "
                        "stay readable.",
             "same": "A chart per tag, all on one value scale, so their sizes compare. A tag "
                     "with a fixed scale keeps it.",
             "shared": "Every tag on one chart. Only useful when their values are of a "
                       "similar size."})
        self.arrange.setToolTip("How the tags share the charts.")
        self.arrange.changed.connect(lambda _k: self._rerender())
        self.plot_btn = w.button("Plot", "primary", icon_name="trend", slot=self.plot)
        self.live = w.Switch()
        self.live.setToolTip("Follow the PLC as it changes: the chart scrolls with the values "
                             "the monitor is logging now. Reads the live log.")
        live_label = w.label("Live", "hint")
        live_label.setToolTip(self.live.toolTip())
        self.view = w.Segments([("time", "Time"), ("cycles", "Cycles")])
        self.view.set_tip("time", "The tags over time.")
        self.view.set_tip("cycles", "Every cycle laid over the others, from a trigger: the odd "
                          "one out stands out.")
        self.view.changed.connect(self._view_changed)
        self.compare_btn = w.button("Compare", tip="Draw the same tags from another time -- "
                                    "yesterday, last week, another log -- dashed, on this chart.",
                                    slot=self._compare_dialog)
        self.layouts_btn = w.button("Layouts", tip="Saved sets of tags, robots, arrangement "
                                    "and range for this profile.")
        self.layouts_menu = QMenu(self)
        self.layouts_menu.aboutToShow.connect(self._fill_layouts_menu)
        self.layouts_btn.setMenu(self.layouts_menu)
        export = w.button("Export", icon_name="download")
        self.export_menu = QMenu(self)
        self.export_menu.addAction("Chart as PNG...", self._export)
        self.export_menu.addAction("Data in view as CSV...", lambda: self._export_csv(False))
        self.export_cursors_action = self.export_menu.addAction(
            "Data between the cursors as CSV...", lambda: self._export_csv(True))
        self.export_menu.aboutToShow.connect(lambda: self.export_cursors_action.setEnabled(
            self.chart is not None and self.chart.cursors is not None))
        export.setMenu(self.export_menu)
        top.body.addLayout(w.row(w.label("Source", "fieldlabel"), self.source, browse, 16,
                                 w.label("Range", "fieldlabel"), self.range, self.between, None,
                                 self.compare_btn, self.layouts_btn, 12, self.live,
                                 live_label, 8, self.plot_btn, export))
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
        tags_card.body.addWidget(w.button(
            "New math pen...", "quiet", icon_name="plus", slot=self._new_math,
            tip="A pen worked out from others: A minus B, a counter's rate, how long a state "
                "lasted."))
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

        events_card = w.Card("Events")
        events_card.title.setToolTip("Markers on the time axis, under the charts. Click one "
                                     "to see it in the Live log.")
        self.show_events = QCheckBox("Socket messages")
        self.show_events.setToolTip("Received and sent messages, connections and errors, "
                                    "from the same log.")
        self.show_pairs = QCheckBox("Latency pairs")
        self.pairs_note = w.label("", "hint", wrap=True)
        for box in (self.show_events, self.show_pairs):
            box.toggled.connect(lambda _on: self.plot() if (self._lines or self._lanes) else None)
            events_card.body.addWidget(box)
        events_card.body.addWidget(self.pairs_note)
        self.left.addWidget(events_card)
        self.events_card = events_card
        events_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.tag_list.setMinimumHeight(110)
        self.left.setStretchFactor(0, 4)
        self.left.setStretchFactor(1, 2)
        self.left.setStretchFactor(2, 0)
        session.latencySpecChanged.connect(self._pairs_note)

        self.left.setMinimumWidth(220)
        self.left.setMaximumWidth(340)
        split.addWidget(self.left)

        chart_card = w.Card("Chart")
        # The status goes under the chart, not in the header: with Find there
        # too, a long status pushed the header's controls off the card.
        self.chart_status = w.label("", "hint")
        chart_card.title.setVisible(False)
        # The view switch stays at the left in both views; the time tools
        # are on the right and go away in Cycles.
        chart_card.header.insertWidget(1, self.view)
        self._time_tools = [self.arrange]
        chart_card.add_action(self.arrange)
        chart_card.header.addSpacing(10)
        self.home_btn = w.tool("home", "Back to the whole range (in Live: follow now again)",
                               lambda: self._nav("home"))
        self.view_back = w.tool("back", "The view before this one", lambda: self._nav("back"))
        self.view_forward = w.tool("forward", "The next view again", lambda: self._nav("forward"))
        self.view_back.setEnabled(False)
        self.view_forward.setEnabled(False)
        self.pan_btn = w.tool("pan", "Drag to move along the time axis. The wheel zooms it.",
                              lambda: self._nav("pan"))
        self.zoom_btn = w.tool("search", "Drag a box to zoom to that time", lambda: self._nav("zoom"))
        for button in (self.pan_btn, self.zoom_btn):
            button.setCheckable(True)
        self.pan_btn.setChecked(True)
        for button in (self.home_btn, self.view_back, self.view_forward, self.pan_btn,
                       self.zoom_btn):
            chart_card.add_action(button)
        chart_card.header.addSpacing(10)
        self.cursor_btn = w.button("Cursors", "quiet",
                                   tip="Two cursors, A and B, to measure between. Drag them, or "
                                       "click a chart to move the nearer one. They snap to the "
                                       "nearest change; hold Shift to place one freely.")
        self.cursor_btn.setCheckable(True)
        self.cursor_btn.toggled.connect(self._cursors_toggled)
        chart_card.add_action(self.cursor_btn)
        chart_card.header.addSpacing(10)
        self.finder = QLineEdit()
        self.finder.setProperty("role", "search")
        self.finder.setPlaceholderText("Find a value")
        self.finder.setClearButtonEnabled(True)
        self.finder.setFixedWidth(140)
        self.finder.setToolTip("Marks every change to a value containing this text, on the "
                             "charts and the text strips. Enter or the arrows step through them.")
        self.finder.textChanged.connect(lambda _t: self._find_changed())
        self.finder.returnPressed.connect(lambda: self._step(1))
        self.find_prev = w.tool("sort_up", "Previous match", lambda: self._step(-1))
        self.find_next = w.tool("sort_down", "Next match", lambda: self._step(1))
        self.find_log = w.tool("log", "Show this match in the Live log, with the events "
                                      "either side of it", self._show_in_log)
        self.find_log.setEnabled(False)
        for widget in (self.finder, self.find_prev, self.find_next, self.find_log):
            chart_card.add_action(widget)
        self._time_tools += [self.home_btn, self.view_back, self.view_forward, self.pan_btn,
                             self.zoom_btn, self.cursor_btn, self.finder, self.find_prev,
                             self.find_next, self.find_log]
        self._matches: list = []
        self._match_at = -1
        self.chart_holder = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_holder)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        # The chart over its value bar, with a handle between them.
        self.chart_split = QSplitter(Qt.Vertical)
        self.chart_split.setChildrenCollapsible(False)
        self.chart_split.setHandleWidth(8)
        self.chart_split.addWidget(self.chart_holder)
        values = QWidget()
        values_layout = QVBoxLayout(values)
        values_layout.setContentsMargins(0, 0, 0, 0)
        values_layout.setSpacing(4)
        self.cursor_info = w.label("", "hint")
        self.cursor_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        values_layout.addWidget(self.cursor_info)
        self.pen_table = PenTable()
        self.pen_table.edit.connect(self._edit_pen)
        self.pen_table.nextFlag.connect(self._next_flag)
        values_layout.addWidget(self.pen_table, 1)
        self.chart_split.addWidget(values)
        self.chart_split.setStretchFactor(0, 1)
        self.chart_split.setStretchFactor(1, 0)
        self.values_panel = values
        values.setVisible(False)
        self.views = QStackedWidget()
        self.views.addWidget(self.chart_split)
        chart_card.body.addWidget(self.views, 1)
        chart_card.body.addWidget(self.chart_status)
        self.chart_card = chart_card
        self._pointer: float | None = None
        self._stats_timer = QTimer(self)
        self._stats_timer.setSingleShot(True)
        self._stats_timer.setInterval(60)
        self._stats_timer.timeout.connect(self._update_stats)
        split.addWidget(chart_card)
        split.setStretchFactor(1, 1)

        self._build_chart()

        self.range.currentIndexChanged.connect(
            lambda: self.between.setVisible(self.range.currentData() == -1))
        self.source.currentIndexChanged.connect(lambda _i: self._source_changed())
        self.tag_list.itemChanged.connect(self._ticked)
        self.robot_list.itemChanged.connect(self._ticked)
        self.robot_source.currentIndexChanged.connect(lambda _i: self.reload_robots())
        self._loaded.connect(self._render)
        self._tags_found.connect(self._fill_tags)
        self._robots_found.connect(self._fill_robots)
        self._robots_more.connect(self._robot_samples)
        self._compared.connect(self._show_compare)
        self.tag_list.itemDoubleClicked.connect(self._tag_double_clicked)
        self._timer = QTimer(self)
        self._timer.setInterval(LIVE_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.live.toggled.connect(self._live_toggled)
        session.events.connect(self._live_rows)
        session.logPathChanged.connect(lambda _p: self.refresh_sources())
        self.refresh_sources()
        self.refresh_robot_sources()
        self._pairs_note()

    # ------------------------------------------------------------- chart

    def _build_chart(self) -> None:
        self.empty_label = w.label(EMPTY_TEXT, "hint", wrap=True)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.chart_layout.addWidget(self.empty_label, 1)
        try:
            from app.ui.trendchart import TrendChart
        except Exception as exc:  # pragma: no cover - only without pyqtgraph
            self.chart = None
            self.empty_label.setText(
                f"pyqtgraph is not available in this build, so there is no chart. ({exc})")
            return
        self.chart = TrendChart()
        self.chart.moved.connect(self._moved)
        self.chart.hovered.connect(self._hovered)
        self.chart.cursorsMoved.connect(self._cursors_moved)
        self.chart.viewChanged.connect(lambda: self._stats_timer.start())
        self.chart.historyChanged.connect(self._history)
        self.chart.eventClicked.connect(self._event_clicked)
        from app.ui.trendcycles import CycleView
        self.cycle_view = CycleView()
        self.cycle_view.jump.connect(self._jump_to_cycle)
        self.views.addWidget(self.cycle_view)
        self.chart_layout.addWidget(self.chart, 1)
        self._empty(EMPTY_TEXT)

    def _empty(self, text: str) -> None:
        self._lines, self._lanes, self._matches = [], [], []
        self._last = None
        if hasattr(self, "values_panel"):
            self.values_panel.setVisible(False)
            self.pen_table.show_pens([])
        self.empty_label.setText(text)
        self.empty_label.setVisible(True)
        if self.chart is not None:
            self.chart.clear_all()
            self.chart.setVisible(False)

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
        self._add_math_items(ticked)
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
        try:
            if self.robot_list.count():
                return
        except RuntimeError:
            return      # the page went before the timer fired (a closed window, a test)
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
            self.tag_note.setText(f"{n} of {total} ticked. 0/1 tags draw as lanes; text "
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

    def _source_changed(self) -> None:
        # Live reads the live log; picking an archive is looking at the past.
        if self.source.currentIndex() != 0 and self.live.isChecked():
            self.live.setChecked(False)
        self.reload_tags()

    # -------------------------------------------------------------- plot

    def plot(self) -> None:
        if self.chart is None or self._loading:
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
        self._held = []
        self.chart_status.setText("Loading...")

        kinds = self._kinds()
        text_tags = [t for t in tags if kinds.get(t) == "text"]
        math_tags = [t for t in tags if kinds.get(t) == "math"]
        number_tags = [t for t in tags if t not in text_tags and t not in math_tags]
        specs_math = {name: spec for name, spec in
                      trendprefs.math_pens(self.session.cfg.as_dict()).items() if name in math_tags}
        # A math pen's sources are read even when they are not ticked.
        plc_numbers = {t for t, k in kinds.items() if k == "number"}
        load = list(number_tags)
        for spec in specs_math.values():
            for source in (spec["a"], spec["b"]):
                if source in plc_numbers and source not in load:
                    load.append(source)
        want_events = self.show_events.isChecked()
        latency = self.session.latency_spec if self.show_pairs.isChecked() else None

        def work():
            data: dict = {"__robots__": [s.name for s in robots], "__db__": db,
                          "__signals__": list(robots), "__math__": specs_math,
                          "__events__": [] if (want_events or latency) else None}
            if load or text_tags or want_events or latency:
                try:
                    # One pass over the file, keeping only what is wanted: the
                    # ticked tags' changes, events, the latency pair rows. A
                    # day's log is a few hundred thousand rows.
                    wanted = set(load) | set(text_tags)
                    rows, pair_rows, events = [], [], []
                    for r in self.session.log_rows(path, specs):
                        event = r.get("event_type")
                        if event == "PLC_CHANGED" and (r.get("tag_or_addr") or "").strip() in wanted:
                            rows.append(r)
                        if want_events and trendmath.is_event_row(r):
                            marker = trendmath.event_of(r)
                            if marker is not None and (start is None or marker.t * 1000 >= start) \
                                    and (end is None or marker.t * 1000 <= end):
                                events.append(marker)
                        if latency and (latency["trigger"].matches(r)
                                        or latency["response"].matches(r)):
                            pair_rows.append(r)
                    if load:
                        for tag, (times, values) in logdata.series(
                                rows, load, start, end).items():
                            data[tag] = StepSeries.from_datetimes(times, values)
                    if text_tags:
                        data["__text__"] = logdata.text_series(rows, text_tags, start, end)
                    if latency:
                        result = logdata.measure(pair_rows, latency["trigger"],
                                                 latency["response"],
                                                 window_ms=latency["window_ms"], start_ms=start,
                                                 end_ms=end, same_value=latency["same_value"])
                        trigger = latency["trigger"]
                        events += trendmath.pair_events(
                            result, trigger.tag if trigger.kind == "plc" else "")
                    if data["__events__"] is not None:
                        data["__events__"] = events
                except OSError as exc:
                    data["__error__"] = str(exc)
            if robots:
                try:
                    for name, (times, values) in robdata.series(db, robots, start, end).items():
                        data[name] = StepSeries.from_datetimes(times, values)
                except robdata.RobDataError as exc:
                    data["__robot_error__"] = str(exc)
            self._loaded.emit(data, names, label)

        threading.Thread(target=work, daemon=True).start()

    def _render(self, data: dict, tags: list[str], label: str, keep_view: bool = False) -> None:
        """Draw what plot() read. ``data`` maps each tag to a StepSeries (or
        the (times, values) lists logdata.series gives), and "__text__" to the
        text tags' spans (or TextSeries)."""
        self._loading = False
        held, self._held = self._held, []
        if self.chart is None:
            return
        problems = []
        if "__error__" in data:
            problems.append(f"Could not read the log: {data['__error__']}")
        if "__robot_error__" in data:
            problems.append(f"Could not read the robots: {data['__robot_error__']}")
        for tag in tags:
            if isinstance(data.get(tag), tuple):
                data[tag] = StepSeries.from_datetimes(*data[tag])
        # Math pens, from whatever was read (their sources may not be ticked).
        sources = {k: v for k, v in data.items()
                   if isinstance(v, StepSeries) and k not in data.get("__math__", {})}
        for name, spec in data.get("__math__", {}).items():
            computed = trendmath.compute_math(spec, sources)
            if computed is None:
                missing = [x for x in (spec["a"], spec["b"] if spec["op"] in trendmath.NEEDS_B
                                       else "") if x and x not in sources]
                problems.append(f"{name} needs {', '.join(missing) or 'its tags'}, not in this log.")
                computed = StepSeries()
            data[name] = computed
        texts = data.get("__text__", {})
        live = self.live.isChecked()
        if live:
            # Live draws every ticked tag, even one with no history yet: its
            # first change may be a second away.
            kinds = self._kinds()
            for tag in tags:
                if kinds.get(tag) == "text":
                    texts.setdefault(tag, [])
                elif not isinstance(data.get(tag), StepSeries):
                    data[tag] = StepSeries()
            present = [tag for tag in tags if isinstance(data.get(tag), StepSeries)]
            lanes = [tag for tag in tags if tag in texts]
        else:
            present = [tag for tag in tags
                       if isinstance(data.get(tag), StepSeries) and len(data[tag])]
            lanes = [tag for tag in tags if texts.get(tag)]
        events = data.get("__events__")
        if not present and not lanes and not events:
            self._empty("\n".join(problems) if problems else f"No values for these in {label}.")
            self.chart_status.setText("")
            return
        previous_view = self.chart.view_range() if keep_view else None

        # The time range: what was asked for where it has ends, else what the
        # data covers. Up to now for a range counted back from now.
        start_ms, end_ms, _label = self._window()
        firsts = [min(e.t for e in events)] if events else []
        lasts = [max(e.end or e.t for e in events)] if events else []
        for tag in present:
            if len(data[tag]):
                firsts.append(data[tag].first_time)
                lasts.append(data[tag].last_time)
        for tag in lanes:
            series = texts[tag]
            if isinstance(series, TextSeries):
                if len(series):
                    firsts.append(series.begins[0])
                    lasts.append(series.begins[-1])
            elif series:
                firsts.append(series[0][0].timestamp())
                lasts.append(series[-1][0].timestamp())
        now = time.time()
        fixed = self.range.currentData() == -1
        if fixed and start_ms is not None:
            left = start_ms / 1000
        elif live and start_ms is not None:
            left = start_ms / 1000
        else:
            left = min(firsts) if firsts else now - self._window_seconds()
        if end_ms:
            right = end_ms / 1000
        else:
            right = max(lasts) if lasts else now
            if self.range.currentData() is not None or live:
                right = max(right, now)
        for tag in lanes:
            if not isinstance(texts[tag], TextSeries):
                texts[tag] = TextSeries.from_spans(texts[tag], left)
        data["__text__"] = texts

        config = self.session.cfg.as_dict()
        pens = []
        maths = data.get("__math__", {})
        for tag in present:
            prefs = trendprefs.pen(config, tag)
            series = data[tag]
            lane = prefs["draw"] == "lane" or (prefs["draw"] == "auto" and series.is_boolean())
            rules = trendprefs.rules(prefs)
            pens.append({"tag": tag, "series": series, "colour": self._colour_for(tag),
                         "lane": lane, "scale": prefs["scale"], "units": prefs["units"],
                         "description": prefs["description"], "rules": rules,
                         "flags": trendmath.flags(series, rules, right) if rules else [],
                         "math": trendmath.describe_math(maths[tag]) if tag in maths else "",
                         "kind": "number"})
        strips = [(tag, texts[tag], self._colour_for(tag)) for tag in lanes]
        self.empty_label.setVisible(False)
        self.chart.setVisible(True)
        self.chart.build(pens, strips, self.arrange.value(), left, right, events=events)
        for lane in self.chart.lanes:
            lane["kind"] = "text"
            lane["description"] = trendprefs.pen(config, lane["tag"])["description"]
        self.pen_table.show_pens(self.chart.lines + self.chart.lanes)
        self.values_panel.setVisible(True)
        if self.cursor_btn.isChecked() and self.chart.cursors is None:
            self.chart.set_cursors(True)
        self._lines, self._lanes = self.chart.lines, self.chart.lanes
        robots = set(data.get("__robots__", []))
        for line in self._lines:
            line["robot"] = line["tag"] in robots
        self._last = (data, tags, label)
        self._sources = {k: v for k, v in data.items() if isinstance(v, StepSeries)
                         and k not in maths and k not in robots}
        self._dirty = False
        if self.cycle_view is not None:
            self.cycle_view.set_sources(self._lines, self._lanes, self.chart.right_edge)
        if self._compare is not None:
            self._load_compare()
        self._live_robots = [s for s in data.get("__signals__", []) if s.name in present]
        self._robot_db = data.get("__db__") or ""
        self._problem = problems[0] if problems else ""
        if previous_view:
            self.chart.show_range(*previous_view)
        if held:
            self._apply_rows(held)
        if self.live.isChecked():
            self._follow(True)
        self._find_changed()
        self._update_status()
        self._cursors_moved()
        self._update_stats()        # now, not on the timer: no flash of empty cells

    def _rerender(self) -> None:
        """Draw the last data again -- another arrangement, another theme --
        without reading the file again, and keep the time in view."""
        if self._last is not None and not self._loading:
            self._render(*self._last, keep_view=True)

    def _update_status(self) -> None:
        if self.chart is None or not (self._lines or self._lanes) or self._loading:
            return
        if self.finder.text().strip() and self._match_at < 0:
            return      # Find's count is showing
        n = len(self._lines) + len(self._lanes)
        changes = sum(len(line["series"]) for line in self._lines) + \
            sum(len(lane["series"]) for lane in self._lanes)
        label = self._last[2] if self._last else ""
        if self.live.isChecked():
            state = ("live, following now" if self._following else
                     "live, scrolling paused (Home follows again)")
        else:
            state = f"{label}  |  updated {time.strftime('%H:%M:%S')}"
        text = f"{n} tag{'s' if n != 1 else ''}, {changes:,} changes, {state}"
        if self.chart.events is not None:
            text += f"  |  {self.chart.events.count():,} events"
        if self.chart.ghost_label:
            text += f"  |  compared with {self.chart.ghost_label}"
        if getattr(self, "_problem", ""):
            text += "  |  " + self._problem
        self.chart_status.setText(text)

    # -------------------------------------------------------------- live

    def _live_toggled(self, on: bool) -> None:
        if on:
            if self.source.currentIndex() != 0:
                self.source.setCurrentIndex(0)
            if self.range.currentData() == -1:
                # A fixed "between" has an end; following now needs a window.
                self.range.setCurrentIndex(self.range.findData(5))
            self._following = True
            self._timer.start()
            self.plot()
        else:
            self._timer.stop()
            self._update_status()

    def _live_rows(self, rows: list) -> None:
        """Rows the monitor has just logged (Session.events)."""
        if not self.live.isChecked() or self.source.currentIndex() != 0:
            return
        if self._loading:
            self._held.extend(rows)     # applied once the history is drawn
            return
        self._apply_rows(rows)

    def _apply_rows(self, rows: list) -> None:
        if self.chart is None:
            return
        lines = self._sources
        lanes = {lane["tag"]: lane["series"] for lane in self._lanes}
        events = self.chart.events is not None and self.show_events.isChecked()
        if not lines and not lanes and not events:
            return
        for row in rows:
            if row.get("event_type") != logdata.PLC_CHANGED:
                if events and trendmath.is_event_row(row):
                    self.chart.add_event(trendmath.event_of(row))
                continue
            tag = (row.get("tag_or_addr") or "").strip()
            if tag not in lines and tag not in lanes:
                continue
            ms = logdata.row_ms(row)
            if ms is None:
                continue
            new = str(row.get("new_value") or "").strip()
            if tag in lines:
                number = logdata.number(new)
                if number is None and new:
                    continue
                if lines[tag].append(ms / 1000, math.nan if number is None else number):
                    self._dirty = True
            else:
                lanes[tag].append(ms / 1000, new)

    def _tick(self) -> None:
        """Live: move the right edge to now, scroll if following, redraw."""
        if self.chart is None or not (self._lines or self._lanes) or self._loading:
            return
        now = time.time()
        self.chart.right_edge = now
        self._ticks += 1
        if self._following:
            self._follow(False)
        else:
            self.chart.refresh()
        if self._live_robots and self._ticks % ROBOT_EVERY_TICKS == 0:
            self._read_robots_since()
        if self._ticks % 4 == 0:
            self._live_recompute()
            self._update_status()
            self._update_values()

    def _window_seconds(self) -> float:
        minutes = self.range.currentData()
        if minutes not in (None, -1):
            return minutes * 60
        span = self.chart.view_range() if self.chart is not None else None
        return (span[1] - span[0]) if span else 300.0

    def _follow(self, resume: bool) -> None:
        if resume:
            self._following = True
        now = time.time()
        if self.chart is not None:
            self.chart.right_edge = max(self.chart.right_edge or now, now)
            self.chart.show_range(now - self._window_seconds(), now)

    def _moved(self) -> None:
        if self.live.isChecked() and self._following:
            self._following = False
            self._update_status()

    def _read_robots_since(self) -> None:
        """Live robots: RobControl's samples since the newest one drawn, read
        on a worker thread (the database is RobControl's, opened read-only)."""
        if self._robots_busy or not self._robot_db:
            return
        series = {line["tag"]: line["series"] for line in self._lines if line.get("robot")}
        signals = [s for s in self._live_robots if s.name in series]
        if not signals:
            return
        since = min(int((series[s.name].last_time or 0) * 1000) for s in signals)
        db = self._robot_db
        self._robots_busy = True

        def work():
            try:
                found = robdata.series(db, signals, since)
            except robdata.RobDataError:
                found = {}
            self._robots_more.emit(found)

        threading.Thread(target=work, daemon=True).start()

    def _robot_samples(self, found: dict) -> None:
        self._robots_busy = False
        series = {line["tag"]: line["series"] for line in self._lines if line.get("robot")}
        for name, (times, values) in found.items():
            if name in series:
                for when, value in zip(times, values):
                    series[name].append(when.timestamp(), value)

    # -------------------------------------------------------------- find

    def _find_changed(self, redraw: bool = True) -> None:
        """Every change to a value containing the find text, as (time, tag,
        value), marked with a thin line across all the charts."""
        self._matches = []
        self._match_at = -1
        needle = self.finder.text().strip().lower()
        if needle and self.chart is not None:
            right = self.chart.right_edge
            for lane in self._lanes:
                for begin, _end, value in lane["series"].segments(right):
                    if value and needle in value.lower():
                        self._matches.append((begin, lane["tag"], value))
            for line in self._lines:
                for when, text in trendview.number_matches(line["series"], needle):
                    self._matches.append((when, line["tag"], text))
            self._matches.sort(key=lambda m: m[0])
        if self.chart is not None:
            self.chart.set_marks([m[0] for m in self._matches[:MAX_MARKS]])
        if needle:
            n = len(self._matches)
            self.chart_status.setText(f"{n:,} match{'es' if n != 1 else ''} for '{needle}'"
                                      if n else f"No value contains '{needle}' in this chart.")
        else:
            self._update_status()
        self.find_prev.setEnabled(bool(self._matches))
        self.find_next.setEnabled(bool(self._matches))
        self.find_log.setEnabled(False)

    def _step(self, direction: int) -> None:
        """To the next (or previous) match: centred, at the current zoom if
        that already shows some detail, else zoomed to show its neighbours."""
        if not self._matches or self.chart is None:
            return
        self._match_at = (self._match_at + direction) % len(self._matches)
        x, tag, value = self._matches[self._match_at]
        lo, hi = self.chart.view_range() or (x - 60, x + 60)
        width = hi - lo
        full = [m[0] for m in self._matches]
        span = (max(full) - min(full)) if len(full) > 1 else width
        if width > max(span / 10, 60):
            width = max(span / 10, 120)            # at least two minutes
        if self.live.isChecked():
            self._following = False
        self.chart.show_range(x - width / 2, x + width / 2)
        self.find_log.setEnabled(True)
        when = datetime.fromtimestamp(x)
        self.chart_status.setText(f"{self._match_at + 1:,} of {len(self._matches):,}: {tag} = "
                                  f"{value} at {when:%Y-%m-%d %H:%M:%S}.{when.microsecond // 1000:03d}")

    def _show_in_log(self) -> None:
        if not (0 <= self._match_at < len(self._matches)):
            return
        x, tag, value = self._matches[self._match_at]
        self.session.showInLog.emit(self.source.currentData() or "", int(round(x * 1000)),
                                    tag, str(value))

    def _nav(self, what: str) -> None:
        if self.chart is None:
            return
        if what == "home":
            if self.live.isChecked():
                self._follow(True)
                self._update_status()
            else:
                self.chart.home()
            return
        if what in ("back", "forward"):
            if self.live.isChecked():
                self._following = False
            getattr(self.chart, what)()
            self._update_status()
            return
        if what == "zoom" and not self.zoom_btn.isChecked():
            what = "pan"
        self.pan_btn.setChecked(what == "pan")
        self.zoom_btn.setChecked(what == "zoom")
        self.chart.set_mode(what)

    def _export(self) -> None:
        if self.chart is None or not (self._lines or self._lanes):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export chart", "plc_trend.png", "PNG image (*.png)")
        if path:
            self.chart.export_png(path)
            self.session.status.emit(f"Saved {path}")

    def _live_recompute(self) -> None:
        """Live, once a second: math pens and flags again from what came in."""
        if not self._dirty or self.chart is None or self._last is None:
            return
        self._dirty = False
        maths = self._last[0].get("__math__", {})
        right = self.chart.right_edge
        for line in self._lines:
            if line["tag"] in maths:
                computed = trendmath.compute_math(maths[line["tag"]], self._sources)
                if computed is not None:
                    line["series"] = computed
                    self._last[0][line["tag"]] = computed
            if line.get("rules"):
                self.chart.set_flags(line, trendmath.flags(line["series"], line["rules"], right))
        self._stats_timer.start()

    # ------------------------------------------------------------- events

    def _pairs_note(self) -> None:
        spec = self.session.latency_spec
        if spec is None:
            self.pairs_note.setText("Measure on the Latency page first.")
            self.show_pairs.setToolTip("Trend draws the pairs the Latency page last measured: "
                                       "a bar from each trigger to its response.")
        else:
            self.pairs_note.setText("")
            self.show_pairs.setToolTip(f"From the Latency page: {spec['label']}. A bar from "
                                       "each trigger to its response; a red mark where none came.")
        self.pairs_note.setVisible(spec is None)
        self.show_pairs.setEnabled(spec is not None)

    def _event_clicked(self, event) -> None:
        self.session.showInLog.emit(self.source.currentData() or "", int(round(event.t * 1000)),
                                    event.tag, "")

    # ------------------------------------------------------------ flags

    def _next_flag(self, tag: str) -> None:
        line = next((ln for ln in self._lines if ln["tag"] == tag), None)
        if line is None or not line.get("flags") or self.chart is None:
            return
        lo, hi = self.chart.view_range()
        centre = (lo + hi) / 2
        flags = line["flags"]
        after = [f for f in flags if f.start > centre + 1e-6]
        flag = after[0] if after else flags[0]
        width = max(hi - lo, (flag.end - flag.start) * 3, 1.0)
        mid = (flag.start + flag.end) / 2
        if self.live.isChecked():
            self._following = False
        self.chart.show_range(mid - width / 2, mid + width / 2)
        n = flags.index(flag) + 1
        when = datetime.fromtimestamp(flag.start)
        self.chart_status.setText(f"{tag}: flag {n:,} of {len(flags):,} at {when:%Y-%m-%d %H:%M:%S}"
                                  f" -- {flag.text}")

    # ------------------------------------------------------------- math

    def _add_math_items(self, ticked: set) -> None:
        for name, spec in trendprefs.math_pens(self.session.cfg.as_dict()).items():
            item = QListWidgetItem(f"{name}   math")
            item.setData(TAG_ROLE, name)
            item.setData(KIND_ROLE, "math")
            item.setToolTip(f"Math pen: {trendmath.describe_math(spec)}. Double-click to "
                            "change or delete it.")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in ticked else Qt.Unchecked)
            self.tag_list.addItem(item)

    def _math_sources(self) -> list[str]:
        kinds = self._kinds()
        return sorted((t for t, k in kinds.items() if k == "number"), key=str.lower)

    def _new_math(self) -> None:
        self._math_dialog("", None)

    def _tag_double_clicked(self, item) -> None:
        if item.data(KIND_ROLE) == "math":
            name = item.data(TAG_ROLE)
            self._math_dialog(name, trendprefs.math_pens(self.session.cfg.as_dict()).get(name))

    def _math_dialog(self, name: str, spec: dict | None) -> None:
        dialog = MathDialog(self._math_sources(), name=name, spec=spec, parent=self)
        if not dialog.exec():
            return
        config = self.session.cfg.as_dict()
        ticked = set(self._selected())
        if name:
            trendprefs.delete_math(config, name)
        if not dialog.deleted:
            try:
                new = trendprefs.save_math(config, dialog.result_name(), dialog.spec())
            except ValueError as exc:
                w.error(self, "Math pen", str(exc))
                return
            ticked.discard(name)
            ticked.add(new)
        self.session.save_config()
        # Rebuild the math entries in the list, keeping the ticks.
        self.tag_list.blockSignals(True)
        for i in reversed(range(self.tag_list.count())):
            if self.tag_list.item(i).data(KIND_ROLE) == "math":
                self.tag_list.takeItem(i)
        self._add_math_items(ticked)
        self.tag_list.blockSignals(False)
        self._ticked()
        if self._lines or self._lanes:
            self.plot()

    # ---------------------------------------------------------- compare

    def _compare_dialog(self) -> None:
        if self.chart is None or not self._lines:
            w.info(self, "Compare", "Plot some tags first; Compare draws the same tags from "
                                    "another time beside them.")
            return
        sources = [(self.source.itemText(i), self.source.itemData(i))
                   for i in range(self.source.count())]
        dialog = CompareDialog(sources, self.source.currentData(), self.chart.extent[0],
                               comparing=self._compare is not None, parent=self)
        if not dialog.exec():
            return
        if dialog.stopped:
            self._compare = None
            self.chart.set_ghosts({})
            self.compare_btn.setText("Compare")
            self._update_status()
            return
        self._compare = dialog.choice()
        self._load_compare()

    def _load_compare(self) -> None:
        """Read the comparison's tags on a worker thread, shifted onto this
        chart's time axis."""
        if self._compare is None or self.chart is None or self.chart.extent is None:
            return
        path, offset, label = self._compare
        x0, x1 = self.chart.extent
        maths = self._last[0].get("__math__", {}) if self._last else {}
        tags = [line["tag"] for line in self._lines
                if not line.get("robot") and line["tag"] not in maths]
        for spec in maths.values():
            for source in (spec["a"], spec["b"]):
                if source and source not in tags:
                    tags.append(source)
        specs = self.session.group_specs()
        self.chart_status.setText(f"Reading {label} to compare...")

        def work():
            ghosts = {}
            try:
                wanted = set(tags)
                rows = [r for r in self.session.log_rows(path, specs)
                        if r.get("event_type") == "PLC_CHANGED"
                        and (r.get("tag_or_addr") or "").strip() in wanted]
                found = logdata.series(rows, tags, int((x0 - offset) * 1000),
                                       int((x1 - offset) * 1000))
                for tag, (times, values) in found.items():
                    if times:
                        ghosts[tag] = StepSeries([t.timestamp() + offset for t in times], values)
                for name, spec in maths.items():
                    computed = trendmath.compute_math(spec, ghosts)
                    if computed is not None:
                        ghosts[name] = computed
            except OSError as exc:
                ghosts = {"__error__": str(exc)}
            self._compared.emit(ghosts, label)

        threading.Thread(target=work, daemon=True).start()

    def _show_compare(self, ghosts: dict, label: str) -> None:
        if self.chart is None or self._compare is None:
            return
        if "__error__" in ghosts:
            self.chart_status.setText(f"Could not read the log to compare: {ghosts['__error__']}")
            return
        self.chart.set_ghosts(ghosts, label)
        self.compare_btn.setText("Comparing")
        shown = sum(1 for line in self._lines if line["tag"] in ghosts)
        self._update_status()
        if not shown:
            self.chart_status.setText(f"Nothing for these tags {label}.")

    # ----------------------------------------------------------- cycles

    def _view_changed(self, key: str) -> None:
        cycles = key == "cycles"
        self.views.setCurrentIndex(1 if cycles and self.cycle_view is not None else 0)
        for widget in self._time_tools:
            widget.setVisible(not cycles)
        if cycles and self.cycle_view is not None and self.chart is not None:
            self.cycle_view.set_sources(self._lines, self._lanes, self.chart.right_edge)

    def _jump_to_cycle(self, trigger: float, before: float, after: float) -> None:
        self.view.set_value("time")
        self._view_changed("time")
        if self.chart is None:
            return
        if self.live.isChecked():
            self._following = False
        pad = (before + after) * 0.1
        self.chart.show_range(trigger - before - pad, trigger + after + pad)
        if self.chart.cursors is not None:
            self.chart.place_cursor("A", trigger, snap=False)
        when = datetime.fromtimestamp(trigger)
        self.chart_status.setText(f"Cycle starting {when:%Y-%m-%d %H:%M:%S}."
                                  f"{when.microsecond // 1000:03d}")

    # ------------------------------------------------------- value bar

    def _hovered(self, x) -> None:
        self._pointer = x
        self._update_values()

    def _history(self, can_back: bool, can_forward: bool) -> None:
        self.view_back.setEnabled(can_back)
        self.view_forward.setEnabled(can_forward)

    def _cursors_toggled(self, on: bool) -> None:
        if self.chart is None:
            return
        self.chart.set_cursors(on and bool(self._lines or self._lanes))
        self.pen_table.set_cursors(on)
        self._cursors_moved()

    def _cursors_moved(self) -> None:
        cursors = self.chart.cursors if self.chart is not None else None
        if cursors:
            a, b = cursors["A"], cursors["B"]
            self.cursor_info.setText(
                f"A  {stamp(a)}     B  {stamp(b)}     B - A  {duration(b - a)}"
                "     (min, max, average and changes between A and B)")
        else:
            self.cursor_info.setText("Min, max, average and changes are for what is in view. "
                                     "Cursors measures between two moments.")
        self._update_values()
        self._stats_timer.start()

    def _update_values(self) -> None:
        if self.chart is None or not self.pen_table.pens:
            return
        self.pen_table.update_values(self._pointer, self.chart.right_edge, self.chart.cursors,
                                     self.chart.right_edge)

    def _update_stats(self) -> None:
        if self.chart is None or not self.pen_table.pens:
            return
        cursors = self.chart.cursors
        if cursors:
            x0, x1 = cursors["A"], cursors["B"]
        else:
            span = self.chart.view_range()
            if span is None:
                return
            x0, x1 = span
        self.pen_table.update_stats(x0, x1, self.chart.right_edge)

    def _edit_pen(self, tag: str) -> None:
        config = self.session.cfg.as_dict()
        pen = next((p for p in self.pen_table.pens if p["tag"] == tag), None)
        if pen is None:
            return
        text = pen["kind"] == "text"
        boolean = not text and pen["series"].is_boolean()
        dialog = PenDialog(tag, trendprefs.pen(config, tag), text=text, boolean=boolean,
                           parent=self)
        if dialog.exec():
            trendprefs.set_pen(config, tag, dialog.values())
            self.session.save_config()
            self._rerender()

    # ---------------------------------------------------------- layouts

    def _fill_layouts_menu(self) -> None:
        menu = self.layouts_menu
        menu.clear()
        saved = trendprefs.layouts(self.session.cfg.as_dict())
        if saved:
            for name in saved:
                menu.addAction(name, lambda n=name: self.load_layout(n))
            menu.addSeparator()
        else:
            empty = menu.addAction("No layouts saved in this profile yet")
            empty.setEnabled(False)
        menu.addAction("Save current as...", self._save_layout)
        if saved:
            delete = menu.addMenu("Delete")
            for name in saved:
                delete.addAction(name, lambda n=name: self._delete_layout(n))

    def current_layout(self) -> dict:
        minutes = self.range.currentData()
        return {"tags": self._selected(), "robots": [s.name for s in self._selected_robots()],
                "arrange": self.arrange.value(),
                "range": None if minutes == -1 else minutes,
                "cursors": self.cursor_btn.isChecked(),
                "events": self.show_events.isChecked(), "pairs": self.show_pairs.isChecked()}

    def _save_layout(self) -> None:
        name, ok = QInputDialog.getText(self, "Save layout", "Name for this set of tags:")
        name = " ".join(name.split())
        if not ok or not name:
            return
        config = self.session.cfg.as_dict()
        if name in trendprefs.layouts(config) and not w.ask(
                self, "Save layout", f"Replace the layout '{name}'?", yes="Replace"):
            return
        trendprefs.save_layout(config, name, self.current_layout())
        if self.session.save_config():
            self.session.status.emit(f"Saved the trend layout '{name}' in this profile.")

    def _delete_layout(self, name: str) -> None:
        if not w.ask(self, "Delete layout", f"Delete the layout '{name}'?", yes="Delete"):
            return
        if trendprefs.delete_layout(self.session.cfg.as_dict(), name):
            self.session.save_config()

    def load_layout(self, name: str) -> None:
        """Tick a saved layout's tags and robots, set its arrangement and
        range, and plot. Tags this log does not have are named, not lost."""
        layout = trendprefs.layouts(self.session.cfg.as_dict()).get(name)
        if layout is None:
            return
        wanted = set(layout["tags"])
        self.tag_list.blockSignals(True)
        found = set()
        for i in range(self.tag_list.count()):
            item = self.tag_list.item(i)
            tag = item.data(TAG_ROLE)
            item.setCheckState(Qt.Checked if tag in wanted else Qt.Unchecked)
            if tag in wanted:
                found.add(tag)
        self.tag_list.blockSignals(False)
        robots = set(layout["robots"])
        self.robot_list.blockSignals(True)
        for i in range(self.robot_list.count()):
            item = self.robot_list.item(i)
            signal = self._signals.get(item.data(Qt.UserRole))
            on = signal is not None and signal.name in robots
            item.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.robot_list.blockSignals(False)
        self._ticked()
        missing = sorted(wanted - found)
        if missing:
            self.tag_note.setText(f"Layout '{name}': not in this log: {', '.join(missing)}.")
            w.set_prop(self.tag_note, "tone", "warn")
        self.arrange.set_value(layout["arrange"])
        index = self.range.findData(layout["range"])
        if index >= 0:
            self.range.setCurrentIndex(index)
        self.cursor_btn.setChecked(layout["cursors"])
        for box, key in ((self.show_events, "events"), (self.show_pairs, "pairs")):
            box.blockSignals(True)
            box.setChecked(layout[key] and box.isEnabled())
            box.blockSignals(False)
        self.plot()

    # ----------------------------------------------------------- export

    def _export_csv(self, between_cursors: bool) -> None:
        if self.chart is None or not (self._lines or self._lanes):
            return
        if between_cursors and self.chart.cursors:
            x0, x1 = sorted((self.chart.cursors["A"], self.chart.cursors["B"]))
        else:
            x0, x1 = self.chart.view_range()
        path, _ = QFileDialog.getSaveFileName(self, "Export data", "plc_trend.csv",
                                              "CSV (*.csv)")
        if path:
            count = self.write_csv(path, x0, x1)
            self.session.status.emit(f"Saved {count:,} rows to {path}")

    def write_csv(self, path: str, x0: float, x1: float) -> int:
        """The tags' values from ``x0`` to ``x1``: for each, the value it held
        at the start, then every change -- the same shape as the log, so it
        opens in Excel and reads back into anything that reads the log."""
        numbers = {line["tag"]: line["series"] for line in self._lines}
        texts = {lane["tag"]: lane["series"] for lane in self._lanes}
        units = {line["tag"]: line.get("units", "") for line in self._lines}
        rows = trendview.export_rows(numbers, texts, x0, x1, self.chart.right_edge)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            out = csv.writer(handle)
            out.writerow(["timestamp_ms", "timestamp_human", "tag", "value", "units", "note"])
            for ms, tag, value, note in rows:
                when = datetime.fromtimestamp(ms / 1000)
                out.writerow([ms, f"{when:%Y-%m-%d %H:%M:%S}.{ms % 1000:03d}", tag, value,
                              units.get(tag, ""), note])
        return len(rows)

    def shown(self) -> None:
        if self.source.count() and self.source.itemData(0) != self.session.logger.log_path:
            self.refresh_sources()
        QTimer.singleShot(0, self._fit_robots_card)
        # RobControl may have been installed, or started recording, since.
        if robdata.default_db_path() and self.robot_source.findData(robdata.default_db_path()) < 0:
            self.refresh_robot_sources()

    def theme_changed(self) -> None:
        super().theme_changed()
        if self.chart is not None:
            self.chart.apply_theme()
            if self.cycle_view is not None:
                self.cycle_view.theme_changed()
            if self._last is not None:
                self._rerender()
