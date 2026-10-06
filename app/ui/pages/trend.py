"""Trend: tag values over time, from the live log or any archive.

matplotlib, drawn in the theme's own colours and redrawn when the theme
changes. Lines come from the validated series palette (tokens.SERIES_LIGHT /
SERIES_DARK), assigned to a tag the first time it is plotted and kept: a tag is
the same colour all session however many others are ticked. Eight at most --
a ninth line is a chart nobody can read, so the list says so instead.

A log records changes, not samples, so each change is drawn as a step at the
moment it happened (logdata.series keeps the old value as well as the new).

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


class TrendPage(Page):
    key = "trend"
    title = "Trend"
    glyph = "trend"
    blurb = "Tag values over time, from the live log or an archive, with RobControl's robot signals."

    _loaded = Signal(object, list, str)
    _tags_found = Signal(list)
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
        self.arrange.changed.connect(lambda _k: self.plot() if self._lines else None)
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
        self.chart_status = w.label("", "hint")
        chart_card.add_action(self.chart_status)
        chart_card.header.addSpacing(10)
        chart_card.add_action(self.arrange)
        chart_card.header.addSpacing(10)
        self.home_btn = w.tool("home", "Back to the whole range", lambda: self._nav("home"))
        self.pan_btn = w.tool("pan", "Drag to pan", lambda: self._nav("pan"))
        self.zoom_btn = w.tool("search", "Drag a box to zoom", lambda: self._nav("zoom"))
        for button in (self.pan_btn, self.zoom_btn):
            button.setCheckable(True)
        for button in (self.home_btn, self.pan_btn, self.zoom_btn):
            chart_card.add_action(button)
        self.chart_holder = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_holder)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_card.body.addWidget(self.chart_holder, 1)
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

        def work():
            try:
                tags = logdata.numeric_tags(logdata.read_rows(path), include_bool=include_bool)
            except OSError:
                tags = []
            self._tags_found.emit(tags)

        threading.Thread(target=work, daemon=True).start()

    def _fill_tags(self, tags: list[str]) -> None:
        ticked = {self.tag_list.item(i).text() for i in range(self.tag_list.count())
                  if self.tag_list.item(i).checkState() == Qt.Checked}
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        for index, tag in enumerate(tags):
            item = QListWidgetItem(tag)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            on = tag in ticked if ticked else index < 4
            item.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.tag_list.addItem(item)
        self.tag_list.blockSignals(False)
        self._ticked()
        if not tags:
            self.tag_note.setText("No numeric tag changes in this file yet.")

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
        return [self.tag_list.item(i).text() for i in range(self.tag_list.count())
                if self.tag_list.item(i).checkState() == Qt.Checked]

    def _ticked(self, *_):
        n = len(self._selected())
        robots = len(self._ticked_robot_keys())
        total = self.tag_list.count()
        if n + robots > MAX_LINES:
            self.tag_note.setText(f"{n + robots} ticked, tags and robots together. The chart "
                                  f"draws {MAX_LINES} at most; untick some to choose which.")
            w.set_prop(self.tag_note, "tone", "warn")
        else:
            self.tag_note.setText(f"{n} of {total} ticked. Booleans plot as 0 and 1.")
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
        self._loading = True
        self.chart_status.setText("Loading...")

        def work():
            data: dict = {}
            if tags:
                try:
                    data.update(logdata.series(logdata.read_rows(path), tags, start, end))
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
        if problems and not any(data.get(tag, ([], []))[0] for tag in tags):
            self._empty("\n".join(problems))
            self.chart_status.setText("")
            return
        import matplotlib.dates as mdates
        from matplotlib.ticker import MaxNLocator
        t = sheet.tokens()
        present = [tag for tag in tags if data.get(tag, ([], []))[0]]
        self._lines = []
        if not present:
            self._empty(f"No values for these in {label}.")
            self.chart_status.setText("")
            return
        stacked = self.arrange.value() == "stacked" and len(present) > 1
        self.figure.clear()
        if stacked:
            axes = self.figure.subplots(len(present), 1, sharex=True, squeeze=False)[:, 0]
        else:
            axes = [self.figure.add_subplot(111)] * len(present)
        self._axes = list(dict.fromkeys(axes))
        self.axes = self._axes[0]
        points = 0
        for tag, ax in zip(present, axes):
            times, values = data[tag]
            ax.plot(times, values, label=tag, color=self._colour_for(tag), linewidth=2,
                    solid_joinstyle="round", solid_capstyle="round")
            self._lines.append((tag, times, values, ax))
            points += len(times)
        for ax in self._axes:
            self._style(ax)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4 if stacked else 8))
        locator = mdates.AutoDateLocator()
        bottom = self._axes[-1]
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
        else:
            self.axes.set_title(self._lines[0][0], color=t["txt_1"], fontsize=10, loc="left")
        self.figure.subplots_adjust(left=0.07, right=0.98, top=0.97, bottom=0.1,
                                    hspace=0.12 if stacked else 0.2)
        self._tip = None
        self.canvas.draw_idle()
        n = len(self._lines)
        self.chart_status.setText(f"{n} line{'s' if n != 1 else ''}, {points:,} points, {label}"
                                  f"  |  updated {time.strftime('%H:%M:%S')}"
                                  + ("  |  " + problems[0] if problems else ""))

    def _hover(self, event) -> None:
        """Nearest point under the pointer, as a small label: tag, time, value."""
        axes = getattr(self, "_axes", [])
        if self.figure is None or not self._lines or event.inaxes not in axes:
            if getattr(self, "_tip", None) is not None and self._tip.get_visible():
                self._tip.set_visible(False)
                self.canvas.draw_idle()
            return
        import matplotlib.dates as mdates
        best = None
        for tag, times, values, ax in self._lines:
            if ax is not event.inaxes:
                continue
            xs = mdates.date2num(times)
            for x, y, when in zip(xs, values, times):
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
            if self._lines:
                self.plot()
            else:
                self._empty("Pick tags on the left and press Plot.")
