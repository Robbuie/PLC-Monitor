"""The Trend page's chart: pyqtgraph, drawn in the theme's colours.

pyqtgraph draws through Qt itself, so panning and zooming a chart of a
day's changes stays smooth where matplotlib redrew the whole figure each
time. What it is given to draw comes from engine/trendview.py: the steps of
the changes in view, or one min/max stroke per pixel column when there are
more changes than pixels, worked out again whenever the time axis moves.

The charts are stacked one above the other on a shared time axis (or one
chart for all lines), with text tags as strips underneath. Moving the pointer
over them draws a vertical line through every chart and a readout of each
line's value at that moment -- the value *held* there, which on a log of
changes is the true one, not the nearest logged point.

The mouse moves time only: the wheel zooms the time axis, a drag pans it (or
draws a box in zoom mode), and each chart's value axis follows what is in view
-- unless the tag has a fixed scale, or "Same scale" puts every chart on one.
Each move is remembered, so Back and Forward step through the views.

A tag that only ever holds 0 and 1 is drawn as a lane, like a logic analyser:
a thin strip that is filled while the bit is on. Many bits fit on one screen
and an edge in one lines up by eye with an edge in another.

Two cursors, A and B, measure: dragged (or clicked into place) they snap to the
nearest change of any line within a few pixels -- hold Shift to place one
freely -- so the time between two edges is read exactly, not by eye.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QGraphicsTextItem

from app.engine.trendmath import EVENT_LANES
from app.engine.trendview import TextSeries
from app.theme import sheet

#: A span narrower than this many pixels is not labelled: the text would
#: overlap its neighbours. Zooming in labels it.
LABEL_MIN_PX = 46
#: A cursor within this many pixels of a change snaps to it.
SNAP_PX = 6
#: A view held this long counts as one step in the Back/Forward history.
SETTLE_MS = 450
#: Lane (bit) heights in pixels; the last row also carries the time axis.
BIT_H = 30
#: Left axis width, the same on every chart so their time axes line up.
AXIS_W = 64
#: The narrowest time range the chart will zoom to, in seconds: a few
#: milliseconds either side of a change is as close as a log can say.
MIN_RANGE_S = 0.004


def colour(token: str, alpha: float | None = None) -> QColor:
    """A theme token as a QColor. The soft tokens are written rgba(...),
    which QColor does not read; ``alpha`` overrides the token's own."""
    text = sheet.tokens()[token].strip()
    if text.startswith("rgba"):
        parts = [p.strip() for p in text[text.index("(") + 1:text.rindex(")")].split(",")]
        c = QColor(int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) > 3:
            c.setAlphaF(float(parts[3]))
    else:
        c = QColor(text)
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


def stamp(x: float, with_date: bool = False) -> str:
    when = datetime.fromtimestamp(x)
    text = f"{when:%H:%M:%S}.{when.microsecond // 1000:03d}"
    return f"{when:%Y-%m-%d} {text}" if with_date else text


def device_transform(item):
    """Item to pixel coordinates. pyqtgraph 0.14 renamed its own helper to
    deviceTransform_ (Qt's deviceTransform needs the view's transform)."""
    helper = getattr(item, "deviceTransform_", None)
    if helper is not None:
        return helper()
    return pg.GraphicsItem.deviceTransform(item)


def fmt(value: float | None) -> str:
    if value is None:
        return "-"
    if value != value:
        return "(no value)"
    return str(int(value)) if float(value).is_integer() and abs(value) < 1e15 else f"{value:.6g}"


class SpanItem(pg.GraphicsObject):
    """A text tag as a strip: a bar per value, from when it was taken to
    when the next one was. Neighbours alternate between two strengths of the
    tag's colour so values with no gap between them stay apart; an empty
    value is left blank. Values are written in their bar where it is wide
    enough at the current zoom. Drawn in pixels, merging bars narrower than a
    pixel, so a day of VINs costs no more to draw than the chart is wide."""

    def __init__(self, series: TextSeries, fill: QColor, chart: "TrendChart") -> None:
        super().__init__()
        self.series = series
        self.fill = fill
        self.chart = chart
        self.labelled: list[str] = []

    def boundingRect(self) -> QRectF:
        segs = self.series
        if not len(segs):
            return QRectF()
        x0 = segs.begins[0]
        x1 = max(self.chart.right_edge or x0, segs.begins[-1])
        return QRectF(x0, -0.5, max(x1 - x0, 1e-6), 1.0)

    def visible(self) -> list[tuple[float, float, str]]:
        vb = self.getViewBox()
        if vb is None:
            return []
        (lo, hi), _ = vb.viewRange()
        return self.series.segments(self.chart.right_edge, lo, hi)

    def labels_for_view(self) -> list[tuple[QRectF, str]]:
        """Each value wide enough to write, with its rectangle in pixels."""
        dt = device_transform(self)
        if dt is None:
            return []
        vb = self.getViewBox()
        view = vb.mapRectToDevice(vb.boundingRect()) if vb is not None else None
        out = []
        for begin, end, value in self.visible():
            if not value:
                continue
            rect = dt.mapRect(QRectF(begin, -0.4, end - begin, 0.8)).normalized()
            if view is not None:
                rect = rect.intersected(view)       # only the part in view counts
            if rect.width() >= LABEL_MIN_PX:
                out.append((rect, value))
            if len(out) >= 400:
                break
        return out

    def paint(self, p, *_args) -> None:
        dt = device_transform(self)
        if dt is None:
            return
        vb = self.getViewBox()
        view = vb.mapRectToDevice(vb.boundingRect()) if vb is not None else None
        p.save()
        p.resetTransform()
        if view is not None:
            p.setClipRect(view)
        p.setPen(Qt.NoPen)
        strong, soft = QColor(self.fill), QColor(self.fill)
        strong.setAlphaF(0.85)
        soft.setAlphaF(0.55)
        pending: QRectF | None = None
        k = 0
        for begin, end, value in self.visible():
            if not value:
                if pending is not None:
                    p.fillRect(pending, strong)
                    pending = None
                continue
            rect = dt.mapRect(QRectF(begin, -0.4, end - begin, 0.8)).normalized()
            if rect.width() < 2:
                # Too narrow to tell apart: merged into one block.
                rect.setWidth(max(rect.width(), 1.0))
                pending = rect if pending is None else pending.united(rect)
                continue
            if pending is not None:
                p.fillRect(pending, strong)
                pending = None
            k += 1
            p.fillRect(rect.adjusted(0, 0, -1, 0), soft if k % 2 else strong)
        if pending is not None:
            p.fillRect(pending, strong)
        font = QFont(p.font())
        font.setPointSizeF(8)
        p.setFont(font)
        p.setPen(colour("txt_0"))
        metrics = QFontMetricsF(font)
        self.labelled = []
        for rect, value in self.labels_for_view():
            text = metrics.elidedText(value, Qt.ElideRight, rect.width() - 6)
            p.drawText(rect, Qt.AlignCenter, text)
            self.labelled.append(value)
        p.restore()


class MarksItem(pg.GraphicsObject):
    """Thin vertical lines across a chart at given times: Find's matches.
    Drawn in pixels, only those in view, and ignored by autoscale."""

    def __init__(self, xs: list[float], pen_colour: QColor) -> None:
        super().__init__()
        self.xs = sorted(xs)
        self.pen_colour = pen_colour

    def boundingRect(self) -> QRectF:
        vb = self.getViewBox()
        return vb.viewRect() if vb is not None else QRectF()

    def viewRangeChanged(self) -> None:
        self.prepareGeometryChange()

    def paint(self, p, *_args) -> None:
        import bisect
        vb = self.getViewBox()
        dt = device_transform(self)
        if vb is None or dt is None or not self.xs:
            return
        (lo, hi), (ylo, yhi) = vb.viewRange()
        first, stop = bisect.bisect_left(self.xs, lo), bisect.bisect_right(self.xs, hi)
        p.save()
        p.resetTransform()
        pen = QPen(self.pen_colour)
        pen.setWidthF(1.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        last_px = None
        for x in self.xs[first:stop]:
            top = dt.map(QPointF(x, yhi))
            if last_px is not None and abs(top.x() - last_px) < 1:
                continue
            last_px = top.x()
            p.drawLine(top, dt.map(QPointF(x, ylo)))
        p.restore()


class EventItem(pg.GraphicsObject):
    """Socket messages, connection events and latency pairs as markers on
    four thin lanes (received, sent, status -- connections and errors --,
    latency), drawn
    in pixels in the Live log's own event colours. A latency pair is a bar
    from trigger to response, labelled with the time where it is wide enough;
    a trigger that got no answer is a marker in the error colour."""

    LANES = [lane for lane, _text in EVENT_LANES]

    def __init__(self, events: list, chart: "TrendChart") -> None:
        super().__init__()
        self.chart = chart
        self.by_lane: dict[str, list] = {lane: [] for lane in self.LANES}
        self.times: dict[str, list[float]] = {lane: [] for lane in self.LANES}
        for event in sorted(events, key=lambda e: e.t):
            self.add(event, update=False)

    def add(self, event, update: bool = True) -> None:
        import bisect
        lane = event.lane if event.lane in self.by_lane else "conn"
        times = self.times[lane]
        i = bisect.bisect_right(times, event.t)
        times.insert(i, event.t)
        self.by_lane[lane].insert(i, event)
        if update:
            self.prepareGeometryChange()
            self.update()

    def count(self) -> int:
        return sum(len(v) for v in self.times.values())

    def lane_y(self, lane: str) -> float:
        """Lanes from the top: received at 3.5 down to latency at 0.5."""
        return len(self.LANES) - 0.5 - self.LANES.index(lane)

    def boundingRect(self) -> QRectF:
        vb = self.getViewBox()
        return vb.viewRect() if vb is not None else QRectF()

    def viewRangeChanged(self) -> None:
        self.prepareGeometryChange()

    def visible(self, lane: str, lo: float, hi: float) -> list:
        import bisect
        times = self.times[lane]
        first = bisect.bisect_left(times, lo)
        if lane == "pair":
            # A pair that started before the view can still reach into it.
            first = max(first - 50, 0)
        return self.by_lane[lane][first:bisect.bisect_right(times, hi)]

    def near(self, x: float, within: float) -> list:
        found = []
        for lane in self.LANES:
            for e in self.visible(lane, x - within, x + within):
                if abs(e.t - x) <= within or (e.end is not None and e.t <= x <= e.end):
                    found.append(e)
        found.sort(key=lambda e: abs(e.t - x))
        return found

    def paint(self, p, *_args) -> None:
        vb = self.getViewBox()
        dt = device_transform(self)
        if vb is None or dt is None:
            return
        (lo, hi), _ = vb.viewRange()
        tones = {"recv": "ev_recv", "sent": "ev_sent", "conn": "ev_connect", "pair": "ev_info"}
        p.save()
        p.resetTransform()
        view = vb.mapRectToDevice(vb.boundingRect())
        p.setClipRect(view)
        font = QFont(p.font())
        font.setPointSizeF(7.5)
        p.setFont(font)
        for lane in self.LANES:
            y = self.lane_y(lane)
            last_px = None
            for e in self.visible(lane, lo, hi):
                tone = "ev_error" if e.error else tones[lane]
                top = dt.map(QPointF(e.t, y + 0.36))
                bottom = dt.map(QPointF(e.t, y - 0.36))
                if e.end is not None:
                    right = dt.map(QPointF(e.end, y))
                    rect = QRectF(top.x(), top.y(), max(right.x() - top.x(), 2.0),
                                  bottom.y() - top.y())
                    p.fillRect(rect, colour(f"{tone}_fill"))
                    p.fillRect(QRectF(rect.left(), rect.top(), 2, rect.height()), colour(tone))
                    if rect.width() > 52:
                        p.setPen(colour(f"{tone}_text"))
                        p.drawText(rect, Qt.AlignCenter, e.text.split(":")[0])
                    continue
                if last_px is not None and abs(top.x() - last_px) < 1:
                    continue            # one marker per pixel
                last_px = top.x()
                pen = QPen(colour(tone))
                pen.setWidthF(2.0 if e.error else 1.5)
                p.setPen(pen)
                p.drawLine(top, bottom)
        p.restore()


class FlagItem(pg.GraphicsObject):
    """A pen's flags on its chart: periods over or under a limit shaded in
    the 'down' colour, stuck periods in 'warn', and a counter's jumps and
    resets as a mark at the top. Not part of the autoscale."""

    def __init__(self, found: list) -> None:
        super().__init__()
        self.flags = sorted(found, key=lambda f: f.start)

    def boundingRect(self) -> QRectF:
        vb = self.getViewBox()
        return vb.viewRect() if vb is not None else QRectF()

    def viewRangeChanged(self) -> None:
        self.prepareGeometryChange()

    def paint(self, p, *_args) -> None:
        vb = self.getViewBox()
        dt = device_transform(self)
        if vb is None or dt is None or not self.flags:
            return
        (lo, hi), (ylo, yhi) = vb.viewRange()
        p.save()
        p.resetTransform()
        drawn = 0
        for f in self.flags:
            if f.end < lo or f.start > hi:
                continue
            drawn += 1
            if drawn > 3000:
                break
            top = dt.map(QPointF(max(f.start, lo), yhi))
            if f.kind in ("jump", "back"):
                tone = "down" if f.kind == "back" else "warn"
                pen = QPen(colour(tone))
                pen.setWidthF(2.0)
                p.setPen(pen)
                p.drawLine(top, QPointF(top.x(), top.y() + 10))
                p.drawEllipse(QPointF(top.x(), top.y() + 12), 2.5, 2.5)
                continue
            bottom = dt.map(QPointF(min(f.end, hi), ylo))
            tone = "warn" if f.kind == "stuck" else "down"
            p.fillRect(QRectF(top, bottom).normalized().adjusted(0, 0, 1, 0), colour(tone, 0.16))
        p.restore()


class Readout(QGraphicsTextItem):
    """The hover readout: text on the raised surface colour, with a border."""

    def paint(self, p, option, widget=None) -> None:
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(colour("line")))
        p.setBrush(colour("bg_1", 0.96))
        p.drawRoundedRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.restore()
        super().paint(p, option, widget)


class TrendChart(pg.GraphicsLayoutWidget):
    """Stacked charts on one time axis. The page hands it pens; it draws,
    redraws as the view moves, and reports what the pointer, the cursors and
    the person moving the chart are doing."""

    #: The pointer moved over the charts: time, or None when it left.
    hovered = Signal(object)
    #: Somebody panned or zoomed by hand (live mode stops following).
    moved = Signal()
    #: The visible time range changed and the lines were redrawn.
    viewChanged = Signal()
    #: Cursor A or B moved (or the cursors were turned on or off).
    cursorsMoved = Signal()
    #: Whether Back and Forward have anywhere to go.
    historyChanged = Signal(bool, bool)
    #: An event marker (socket message, latency pair) was clicked.
    eventClicked = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        pg.setConfigOptions(antialias=True)
        self.lines: list[dict] = []     # every number pen, lane or chart
        self.lanes: list[dict] = []     # text strips
        self.plots: list[pg.PlotItem] = []
        self.right_edge: float | None = None
        self.extent: tuple[float, float] | None = None
        self.arrange = "stacked"
        self.cursors: dict[str, float] | None = None      # {"A": t, "B": t} when on
        self.events: EventItem | None = None
        self.ghost_label = ""
        self._ghosts: list = []
        self._flag_items: list = []
        self._cursor_items: list = []                     # (which, line, plot)
        self._bands: list = []
        self._hover_lines: list = []
        self._tip = None
        self._marks: list = []
        self._mode = "pan"
        self._quiet = 0
        self._syncing = False
        self._back: list[tuple[float, float]] = []
        self._forward: list[tuple[float, float]] = []
        self._settled: tuple[float, float] | None = None
        self._gesture_from: tuple[float, float] | None = None
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self._settled_now)
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(0)
        self._redraw.timeout.connect(self.refresh)
        self.scene().sigMouseMoved.connect(self._mouse)
        self.scene().sigMouseClicked.connect(self._clicked)
        self.ci.setSpacing(0)
        self.ci.setContentsMargins(4, 6, 10, 4)
        self.apply_theme()

    # ------------------------------------------------------------- build

    def apply_theme(self) -> None:
        self.setBackground(colour("bg_2"))

    def clear_all(self) -> None:
        self.clear()
        self.lines, self.lanes, self.plots = [], [], []
        self._hover_lines, self._marks, self._cursor_items, self._bands = [], [], [], []
        self._ghosts, self._flag_items, self.events = [], [], None
        if self._tip is not None:
            self._tip.setVisible(False)

    def build(self, pens: list[dict], texts: list[tuple[str, TextSeries, str]],
              arrange: str, left: float, right: float, events: list | None = None) -> None:
        """Lay out the charts and draw.

        ``pens`` are the number tags, each {"tag", "series", "colour", and
        optionally "lane" (draw as a bit), "scale" ([low, high] or None),
        "units", "description"}. ``arrange`` is "stacked" (a chart per pen),
        "same" (a chart per pen, one value scale for all) or "shared" (one
        chart). Lanes and text strips follow the charts. ``left``/``right``
        are the range to show first; ``right`` is where the last values end.
        A pen may carry "flags" (engine/trendmath.Flag) and "rules" (its high
        and low limits are drawn). ``events`` (a list, even empty) adds the
        events strip at the bottom.
        """
        self.clear_all()
        self.arrange = arrange
        self.right_edge = right
        self.extent = (left, max(right, left + MIN_RANGE_S))
        lines = [p for p in pens if not p.get("lane")]
        bits = [p for p in pens if p.get("lane")]
        charts = (len(lines) if arrange in ("stacked", "same") else 1) if lines else 0
        kinds = ["chart"] * charts + ["bit"] * len(bits) + ["text"] * len(texts)
        if events is not None:
            kinds.append("events")
        first: pg.PlotItem | None = None
        for row, kind in enumerate(kinds):
            axis = pg.DateAxisItem(orientation="bottom")
            plot = self.addPlot(row=row, col=0, axisItems={"bottom": axis})
            plot._kind = kind
            self._style_plot(plot, kind)
            if first is None:
                first = plot
            else:
                plot.setXLink(first)
            last = row == len(kinds) - 1
            if kind == "chart":
                self.ci.layout.setRowStretchFactor(row, 4)
                plot.setMinimumHeight(70)
            else:
                self.ci.layout.setRowStretchFactor(row, 0 if kind == "bit" else 1)
                height = {"bit": BIT_H, "text": 44, "events": 80}[kind]
                plot.setMaximumHeight(height + (28 if last else 0))
                plot.setMinimumHeight(height - 4 + (28 if last else 0))
            plot.showAxis("bottom", last)
            self.plots.append(plot)
        for i, pen in enumerate(lines):
            plot = self.plots[i if arrange in ("stacked", "same") else 0]
            curve = pg.PlotDataItem(pen=pg.mkPen(QColor(pen["colour"]), width=2),
                                    connect="finite", name=pen["tag"])
            plot.addItem(curve)
            self.lines.append(dict(pen, plot=plot, curve=curve, lane=False))
        for j, pen in enumerate(bits):
            plot = self.plots[charts + j]
            fill = QColor(pen["colour"])
            fill.setAlphaF(0.35)
            curve = pg.PlotDataItem(pen=pg.mkPen(QColor(pen["colour"]), width=1.5),
                                    connect="finite", fillLevel=0, brush=pg.mkBrush(fill),
                                    name=pen["tag"])
            plot.addItem(curve)
            self.lines.append(dict(pen, plot=plot, curve=curve, lane=True))
        for j, (tag, series, hexcolour) in enumerate(texts):
            plot = self.plots[charts + len(bits) + j]
            item = SpanItem(series, QColor(hexcolour), self)
            plot.addItem(item)
            self.lanes.append({"tag": tag, "series": series, "plot": plot, "item": item,
                               "colour": hexcolour})
        self.events = None
        if events is not None:
            plot = self.plots[-1]
            self.events = EventItem(events, self)
            plot.addItem(self.events, ignoreBounds=True)
            plot.getAxis("left").setStyle(showValues=True)
            plot.getAxis("left").setTicks([[(self.events.lane_y(lane), text)
                                            for lane, text in EVENT_LANES]])
        self._flag_items = []
        for line in self.lines:
            if line.get("flags"):
                item = FlagItem(line["flags"])
                item.setZValue(-15)
                line["plot"].addItem(item, ignoreBounds=True)
                self._flag_items.append((line, item))
            rules = line.get("rules") or {}
            for key in ("high", "low"):
                if rules.get(key) is not None and not line["lane"]:
                    limit = pg.InfiniteLine(pos=rules[key], angle=0, movable=False,
                                            pen=pg.mkPen(colour("down", 0.7), width=1,
                                                         style=Qt.DashLine))
                    limit.setToolTip(f'{line["tag"]}: {key} limit {fmt(rules[key])}')
                    line["plot"].addItem(limit, ignoreBounds=True)
        self._ghosts = []
        self._name_charts()
        for plot in self.plots:
            line = pg.InfiniteLine(angle=90, movable=False,
                                   pen=pg.mkPen(colour("txt_2", 0.7), width=1,
                                                style=Qt.DashLine))
            line.setVisible(False)
            plot.addItem(line, ignoreBounds=True)
            self._hover_lines.append(line)
        self._back, self._forward = [], []
        self._settled = None
        self.historyChanged.emit(False, False)
        if first is not None:
            first.getViewBox().sigXRangeChanged.connect(self._x_changed)
            for plot in self.plots:
                plot.getViewBox().sigRangeChangedManually.connect(self._manual)
            self._apply_scales()
            self.show_range(*self.extent, record=False)
            self._settled = self.view_range()
            if self.cursors is not None:
                self._make_cursors()

    def _style_plot(self, plot: pg.PlotItem, kind: str) -> None:
        t = sheet.tokens()
        font = QFont(self.font())
        font.setPointSizeF(8)
        for name in ("left", "bottom"):
            ax = plot.getAxis(name)
            ax.setPen(pg.mkPen(QColor(t["line"])))
            ax.setTextPen(pg.mkPen(QColor(t["txt_2"])))
            ax.setStyle(tickFont=font, tickLength=-4 if name == "bottom" else 4)
        plot.getAxis("left").setWidth(AXIS_W)
        plot.hideButtons()
        plot.setMenuEnabled(False)
        vb = plot.getViewBox()
        vb.setMouseEnabled(x=True, y=False)
        vb.setMouseMode(pg.ViewBox.RectMode if self._mode == "zoom" else pg.ViewBox.PanMode)
        vb.setLimits(minXRange=MIN_RANGE_S)
        if kind == "chart":
            plot.showGrid(x=False, y=True, alpha=0.25)
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)
            vb.setAutoVisible(y=True)
            vb.setDefaultPadding(0.12)
        else:
            plot.getAxis("left").setTicks([[]])
            plot.getAxis("left").setStyle(showValues=False)
            self._fixed_y(plot)

    @staticmethod
    def _fixed_y(plot: pg.PlotItem) -> None:
        vb = plot.getViewBox()
        if plot._kind == "bit":
            vb.setYRange(-0.12, 1.12, padding=0)
        elif plot._kind == "events":
            vb.setYRange(0, len(EVENT_LANES), padding=0)
        else:
            vb.setYRange(-0.5, 0.5, padding=0)

    def _name_charts(self) -> None:
        """Each chart names its tag (or tags) in its corner, in ink on the
        card colour, with units; the line's own colour beside the name says
        which is which. A description follows, quieter."""
        t = sheet.tokens()
        by_plot: dict = {}
        for line in self.lines:
            by_plot.setdefault(id(line["plot"]), (line["plot"], []))[1].append(line)
        for lane in self.lanes:
            by_plot.setdefault(id(lane["plot"]), (lane["plot"], []))[1].append(lane)
        for plot, entries in by_plot.values():
            parts = []
            for e in entries:
                name = _escape(e["tag"])
                if e.get("units"):
                    name += f' <span style="color:{t["txt_2"]}">({_escape(e["units"])})</span>'
                text = (f'<span style="color:{e["colour"]}">&#9632;</span>&nbsp;'
                        f'<span style="color:{t["txt_1"]}">{name}</span>')
                if e.get("description") and len(entries) == 1:
                    text += (f'&nbsp;&nbsp;<span style="color:{t["txt_2"]}">'
                             f'{_escape(e["description"])}</span>')
                parts.append(text)
            label = pg.TextItem(html="&nbsp;&nbsp;&nbsp;".join(parts), anchor=(0, 0),
                                fill=pg.mkBrush(colour("bg_2", 0.6)))
            label.setParentItem(plot.getViewBox())
            label.setPos(2, -2 if plot._kind == "bit" else 1)
            plot._name_label = label

    # ------------------------------------------------------------ scales

    def _chart_plots(self) -> list[pg.PlotItem]:
        return [p for p in self.plots if p._kind == "chart"]

    def _fixed_range(self, plot) -> tuple[float, float] | None:
        """A chart's fixed scale: when every pen on it has one, the range
        that covers them all."""
        scales = [line.get("scale") for line in self.lines if line["plot"] is plot]
        if not scales or any(s is None for s in scales):
            return None
        return min(s[0] for s in scales), max(s[1] for s in scales)

    def _apply_scales(self) -> None:
        """Hand each chart's value axis back to what it should follow:
        its fixed scale, the shared scale, or what is in view."""
        for plot in self.plots:
            vb = plot.getViewBox()
            if plot._kind != "chart":
                self._fixed_y(plot)
                continue
            fixed = self._fixed_range(plot)
            if fixed is not None:
                vb.disableAutoRange(axis=pg.ViewBox.YAxis)
                vb.setYRange(*fixed, padding=0.02)
            elif self.arrange == "same":
                vb.disableAutoRange(axis=pg.ViewBox.YAxis)
            else:
                vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        if self.arrange == "same":
            self._same_scale()

    def _same_scale(self, drawn: dict | None = None) -> None:
        """One value scale for every chart that has no fixed one, fitted to
        what all of them show in view."""
        low, high = None, None
        for line in self.lines:
            if line["lane"] or self._fixed_range(line["plot"]) is not None:
                continue
            ys = (drawn or {}).get(id(line["curve"]))
            if ys is None:
                ys = line["curve"].yData
            if ys is None or not len(ys):
                continue
            finite = ys[np.isfinite(ys)]
            if not len(finite):
                continue
            low = float(finite.min()) if low is None else min(low, float(finite.min()))
            high = float(finite.max()) if high is None else max(high, float(finite.max()))
        if low is None:
            return
        if high == low:
            low, high = low - 1, high + 1
        for plot in self._chart_plots():
            if self._fixed_range(plot) is None:
                plot.getViewBox().setYRange(low, high, padding=0.12)

    # -------------------------------------------------------------- view

    def view_range(self) -> tuple[float, float] | None:
        if not self.plots:
            return None
        lo, hi = self.plots[0].getViewBox().viewRange()[0]
        return lo, hi

    def show_range(self, lo: float, hi: float, record: bool = True) -> None:
        """Show ``lo..hi``. ``record`` puts the view being left on the Back
        history: true for a person's action (Home, a Find step), false for
        live scrolling."""
        if not self.plots:
            return
        if record:
            self._remember()
        self._quiet += 1
        try:
            self.plots[0].getViewBox().setXRange(lo, max(hi, lo + MIN_RANGE_S), padding=0)
        finally:
            self._quiet -= 1
        if self._gesture_from is None:
            self._settled = self.view_range()
        self.refresh()

    def home(self) -> None:
        if self.extent:
            self.show_range(*self.extent)

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        for plot in self.plots:
            plot.getViewBox().setMouseMode(
                pg.ViewBox.RectMode if mode == "zoom" else pg.ViewBox.PanMode)

    def _manual(self, *_args) -> None:
        # A zoom box sets both axes and turns the value axis's autoscale off;
        # time is all the mouse is meant to move, so hand it back.
        self._apply_scales()
        if not self._quiet:
            self.moved.emit()

    def _x_changed(self, *_args) -> None:
        self._redraw.start()
        if self._quiet:
            return
        # A person's pan or zoom: the view before the gesture goes on the
        # history once it has settled, so a drag is one step, not a hundred.
        if self._gesture_from is None:
            self._gesture_from = self._settled
        self._settle.start()

    def _settled_now(self) -> None:
        start, self._gesture_from = self._gesture_from, None
        now = self.view_range()
        if start is not None and now is not None and not _close(start, now):
            self._back.append(start)
            del self._back[:-50]
            self._forward.clear()
            self.historyChanged.emit(True, False)
        self._settled = now

    def _remember(self) -> None:
        now = self._settled or self.view_range()
        if now is not None and (not self._back or not _close(self._back[-1], now)):
            self._back.append(now)
            del self._back[:-50]
            self._forward.clear()
        self.historyChanged.emit(bool(self._back), bool(self._forward))

    def back(self) -> bool:
        if not self._back:
            return False
        now = self.view_range()
        target = self._back.pop()
        if now is not None:
            self._forward.append(now)
        self.show_range(*target, record=False)
        self._settled = target
        self.historyChanged.emit(bool(self._back), bool(self._forward))
        self.moved.emit()
        return True

    def forward(self) -> bool:
        if not self._forward:
            return False
        now = self.view_range()
        target = self._forward.pop()
        if now is not None:
            self._back.append(now)
        self.show_range(*target, record=False)
        self._settled = target
        self.historyChanged.emit(bool(self._back), bool(self._forward))
        self.moved.emit()
        return True

    def refresh(self) -> None:
        """Work out the points of every line for what is in view."""
        span = self.view_range()
        if span is None:
            return
        lo, hi = span
        width = max(int(self.plots[0].getViewBox().width()), 50) if self.plots else 1000
        drawn = {}
        for line in self.lines:
            xs, ys = line["series"].view(lo, hi, width, right=self.right_edge)
            line["curve"].setData(xs, ys, connect="finite")
            drawn[id(line["curve"])] = ys
        for line, curve in self._ghosts:
            xs, ys = line["ghost"].view(lo, hi, width, right=self.right_edge)
            curve.setData(xs, ys, connect="finite")
        for lane in self.lanes:
            lane["item"].prepareGeometryChange()
            lane["item"].update()
        if self.arrange == "same":
            self._same_scale(drawn)
        self.viewChanged.emit()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        redraw = getattr(self, "_redraw", None)     # the base class resizes in __init__
        if redraw is not None:
            redraw.start()

    # ------------------------------------------------------ compare, flags

    def set_ghosts(self, ghosts: dict, label: str = "") -> None:
        """Draw another time's (or log's) values of the same pens, already
        shifted onto this time axis, as dashed lines in the pen's colour."""
        for line, curve in self._ghosts:
            line["plot"].removeItem(curve)
        self._ghosts = []
        self.ghost_label = label if ghosts else ""
        for line in self.lines:
            series = ghosts.get(line["tag"])
            if series is None:
                line.pop("ghost", None)
                continue
            ink = QColor(line["colour"])
            ink.setAlphaF(0.55)
            curve = pg.PlotDataItem(pen=pg.mkPen(ink, width=1.5, style=Qt.DashLine),
                                    connect="finite")
            curve.setZValue(-5)
            line["plot"].addItem(curve)
            line["ghost"] = series
            self._ghosts.append((line, curve))
        self.refresh()

    def set_flags(self, line: dict, found: list) -> None:
        """Replace one pen's flags (live: worked out again as values arrive)."""
        line["flags"] = found
        for entry, item in self._flag_items:
            if entry is line:
                item.flags = sorted(found, key=lambda f: f.start)
                item.update()
                return
        if found:
            item = FlagItem(found)
            item.setZValue(-15)
            line["plot"].addItem(item, ignoreBounds=True)
            self._flag_items.append((line, item))

    def add_event(self, event) -> None:
        if self.events is not None and event is not None:
            self.events.add(event)

    # ----------------------------------------------------------- cursors

    def set_cursors(self, on: bool) -> None:
        """Turn the A and B cursors on (a third and two thirds across the
        view, unless they were placed before and are still in it) or off."""
        if not on:
            self.cursors = None
            self._drop_cursors()
            self.cursorsMoved.emit()
            return
        span = self.view_range()
        if span is None:
            self.cursors = {"A": 0.0, "B": 0.0}
            return
        lo, hi = span
        old = self.cursors
        if not old or not (lo <= old["A"] <= hi and lo <= old["B"] <= hi):
            self.cursors = {"A": lo + (hi - lo) / 3, "B": lo + 2 * (hi - lo) / 3}
        self._make_cursors()
        self.cursorsMoved.emit()

    def _drop_cursors(self) -> None:
        for _which, line, plot in self._cursor_items:
            plot.removeItem(line)
        for band, plot in self._bands:
            plot.removeItem(band)
        self._cursor_items, self._bands = [], []

    def _make_cursors(self) -> None:
        self._drop_cursors()
        if self.cursors is None or not self.plots:
            return
        t = sheet.tokens()
        for k, plot in enumerate(self.plots):
            band = pg.LinearRegionItem(values=(self.cursors["A"], self.cursors["B"]),
                                       movable=False, brush=pg.mkBrush(colour("accent", 0.07)),
                                       pen=pg.mkPen(None))
            band.setZValue(-20)
            plot.addItem(band, ignoreBounds=True)
            self._bands.append((band, plot))
            for which, style in (("A", Qt.SolidLine), ("B", Qt.DashLine)):
                label = which if k == 0 else None
                line = pg.InfiniteLine(
                    pos=self.cursors[which], angle=90, movable=True,
                    pen=pg.mkPen(colour("accent"), width=1.5, style=style),
                    hoverPen=pg.mkPen(colour("accent"), width=3, style=style),
                    label=label,
                    labelOpts={"position": 0.96, "color": QColor(t["accent_ink"]),
                               "fill": pg.mkBrush(colour("bg_1", 0.9)), "movable": False}
                    if label else None)
                line.setZValue(30)
                line.setToolTip(f"Cursor {which}: drag. It snaps to the nearest change; "
                                "hold Shift to place it freely.")
                plot.addItem(line, ignoreBounds=True)
                line.sigPositionChanged.connect(
                    lambda item, w=which: self._cursor_dragged(w, item))
                self._cursor_items.append((which, line, plot))

    def _snap(self, x: float) -> float:
        """``x``, or the nearest change of any line or strip within
        SNAP_PX pixels of it. Shift held: ``x`` as it is."""
        from PySide6.QtWidgets import QApplication
        if QApplication.keyboardModifiers() & Qt.ShiftModifier or not self.plots:
            return x
        lo, hi = self.view_range()
        px = max(self.plots[0].getViewBox().width(), 1)
        within = SNAP_PX * (hi - lo) / px
        best = None
        for item in self.lines + self.lanes:
            found = item["series"].nearest_change(x, within)
            if found is not None and (best is None or abs(found - x) < abs(best - x)):
                best = found
        if self.events is not None:
            for e in self.events.near(x, within):
                for t in (e.t, e.end):
                    if t is not None and abs(t - x) <= within and (
                            best is None or abs(t - x) < abs(best - x)):
                        best = t
        return x if best is None else best

    def _cursor_dragged(self, which: str, item) -> None:
        if self._syncing or self.cursors is None:
            return
        self._syncing = True
        try:
            x = self._snap(item.value())
            self.place_cursor(which, x, snap=False)
        finally:
            self._syncing = False

    def place_cursor(self, which: str, x: float, snap: bool = True) -> None:
        """Put cursor ``which`` ("A" or "B") at time ``x`` on every chart."""
        if self.cursors is None:
            return
        if snap:
            x = self._snap(x)
        self.cursors[which] = x
        was = self._syncing
        self._syncing = True
        try:
            for w, line, _plot in self._cursor_items:
                if w == which and line.value() != x:
                    line.setValue(x)
            for band, _plot in self._bands:
                band.setRegion((self.cursors["A"], self.cursors["B"]))
        finally:
            self._syncing = was
        self.cursorsMoved.emit()

    def _clicked(self, ev) -> None:
        """A click on an event marker opens it in the Live log; a click on a
        chart with the cursors on moves the nearer cursor there."""
        if ev.button() != Qt.LeftButton or ev.double():
            return
        plot = self._plot_at(ev.scenePos())
        if plot is None:
            return
        x = plot.getViewBox().mapSceneToView(ev.scenePos()).x()
        if plot._kind == "events" and self.events is not None:
            found = self.events.near(x, self._px_seconds(4))
            if found:
                self.eventClicked.emit(found[0])
                return
        if self.cursors is None:
            return
        which = "A" if abs(x - self.cursors["A"]) <= abs(x - self.cursors["B"]) else "B"
        self.place_cursor(which, x)

    def _px_seconds(self, px: float) -> float:
        span = self.view_range()
        if span is None or not self.plots:
            return 0.0
        return px * (span[1] - span[0]) / max(self.plots[0].getViewBox().width(), 1)

    # -------------------------------------------------------------- find

    def set_marks(self, xs: list[float]) -> None:
        for plot, item in self._marks:
            plot.removeItem(item)
        self._marks = []
        if not xs:
            return
        for plot in self.plots:
            item = MarksItem(xs, colour("accent", 0.6))
            plot.addItem(item, ignoreBounds=True)
            self._marks.append((plot, item))

    # ------------------------------------------------------------- hover

    def _plot_at(self, pos) -> pg.PlotItem | None:
        for plot in self.plots:
            if plot.getViewBox().sceneBoundingRect().contains(pos):
                return plot
        return None

    def _mouse(self, pos) -> None:
        plot = self._plot_at(pos)
        if plot is None:
            self.hide_readout()
            return
        x = plot.getViewBox().mapSceneToView(pos).x()
        self.readout(x, plot, pos)

    def hide_readout(self) -> None:
        for line in self._hover_lines:
            line.setVisible(False)
        if self._tip is not None:
            self._tip.setVisible(False)
        self.hovered.emit(None)

    def readout_text(self, x: float, plot: pg.PlotItem) -> str:
        """What the readout says at time ``x`` over ``plot``: the time, then
        each of that chart's lines and the value it held then."""
        lines = [stamp(x, with_date=True)]
        near = self._px_seconds(4)
        for line in self.lines:
            if line["plot"] is plot:
                units = f' {line["units"]}' if line.get("units") else ""
                lines.append(f'{line["tag"]}   {fmt(line["series"].value_at(x))}{units}')
                if line.get("ghost") is not None:
                    lines.append(f'   {self.ghost_label or "compare"}   '
                                 f'{fmt(line["ghost"].value_at(x))}{units}')
                for f in [f for f in line.get("flags") or []
                          if f.start - near <= x <= f.end + near][:3]:
                    lines.append(f"   flag: {f.text}")
        if plot._kind == "events" and self.events is not None:
            found = self.events.near(x, near)
            for e in found[:6]:
                lines.append(f"{stamp(e.t)}  {e.text}")
            if len(found) > 6:
                lines.append(f"... and {len(found) - 6} more")
            if not found:
                lines.append("(no event here)")
        for lane in self.lanes:
            if lane["plot"] is plot:
                hit = lane["series"].at(x, self.right_edge)
                if hit and hit[2]:
                    lines.append(f'{lane["tag"]}   {hit[2]}')
                    lines.append(f"{stamp(hit[0])} to {stamp(hit[1])}")
                else:
                    lines.append(f'{lane["tag"]}   (no value)')
        return "\n".join(lines)

    def readout(self, x: float, plot: pg.PlotItem, pos=None) -> None:
        """The pointer line through every chart at ``x``, and the readout
        beside the pointer. The readout lives in the scene, not in a chart,
        so a short strip does not clip it."""
        for line in self._hover_lines:
            line.setPos(x)
            line.setVisible(True)
        text = self.readout_text(x, plot)
        if self._tip is None:
            self._tip = Readout()
            self._tip.setZValue(1000)
            self.scene().addItem(self._tip)
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        self._tip.setFont(font)
        self._tip.setDefaultTextColor(colour("txt_0"))
        self._tip.setPlainText(text)
        if pos is None:
            pos = plot.getViewBox().mapViewToScene(QPointF(x, 0))
        box = self._tip.boundingRect()
        scene = self.scene().sceneRect()
        left = pos.x() + 14
        if left + box.width() > scene.right() - 4:
            left = pos.x() - 14 - box.width()
        top = min(max(pos.y() - box.height() / 2, scene.top() + 2),
                  scene.bottom() - box.height() - 2)
        self._tip.setPos(left, top)
        self._tip.setVisible(True)
        self.hovered.emit(x)

    # ------------------------------------------------------------ export

    def export_png(self, path: str) -> None:
        try:
            import pyqtgraph.exporters as exporters
            exporter = exporters.ImageExporter(self.ci)
            exporter.parameters()["width"] = max(int(self.width() * 2), 800)
            exporter.export(path)
        except Exception:
            self.grab().save(path)


def _close(a: tuple[float, float], b: tuple[float, float]) -> bool:
    width = max(abs(a[1] - a[0]), 1e-9)
    return abs(a[0] - b[0]) < width * 1e-6 and abs(a[1] - b[1]) < width * 1e-6


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
