"""What the Trend chart draws, worked out without Qt.

A log records changes, not samples: a value holds from the moment it was
logged until the next change. Two series types keep that meaning:

* `StepSeries` -- a number. `view()` turns the changes inside the visible
  time range into the points of a line, either the steps themselves (when
  there are few enough to see) or, when there are more changes than pixels,
  one vertical stroke per pixel column from the lowest to the highest value
  held in it. That is min/max decimation: a 3 ms spike in an eight-hour view
  still shows as a full-height stroke, where averaging or plain sample
  skipping would lose it.
* `TextSeries` -- a VIN, a recipe, a status word. A span per value.

Times are epoch seconds as floats (what pyqtgraph's date axis takes); a float
holds today's epoch to well under a microsecond, so milliseconds survive.
Both types can be appended to while the chart is live.
"""

from __future__ import annotations

import bisect
import math
from datetime import datetime
from typing import Iterable

import numpy as np

#: A series keeps at most this many changes; live mode drops the oldest
#: beyond it so a window left open for a week does not grow without bound.
MAX_POINTS = 2_000_000


def epoch(when: datetime) -> float:
    return when.timestamp()


class StepSeries:
    """A numeric tag's changes: value ``v[i]`` holds from ``t[i]`` until
    ``t[i + 1]`` (or the right edge of the chart for the last one). NaN is a
    gap -- no value -- and breaks the line.

    Kept in numpy buffers that double as they fill, so a live append is cheap
    and drawing never has to copy a list of two million floats into an array.
    """

    def __init__(self, times: Iterable[float] = (), values: Iterable[float] = ()) -> None:
        t = np.asarray(list(times) if not isinstance(times, np.ndarray) else times, dtype=float)
        v = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, dtype=float)
        self._n = len(t)
        capacity = max(self._n * 2, 256)
        self._tb = np.empty(capacity)
        self._vb = np.empty(capacity)
        self._tb[:self._n] = t
        self._vb[:self._n] = v

    @classmethod
    def from_datetimes(cls, times: list[datetime], values: list[float]) -> "StepSeries":
        return cls(np.fromiter((epoch(t) for t in times), dtype=float, count=len(times)),
                   np.asarray(values, dtype=float))

    def __len__(self) -> int:
        return self._n

    @property
    def last_time(self) -> float | None:
        return float(self._tb[self._n - 1]) if self._n else None

    @property
    def first_time(self) -> float | None:
        return float(self._tb[0]) if self._n else None

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        return self._tb[:self._n], self._vb[:self._n]

    def append(self, t: float, value: float) -> bool:
        """Add a change. One older than the last is dropped (it is already in
        the history read from the file); returns whether it was added."""
        n = self._n
        if n:
            last_t, last_v = self._tb[n - 1], self._vb[n - 1]
            if t < last_t or (t == last_t and _same(float(value), float(last_v))):
                return False
        if n == len(self._tb):
            if n >= MAX_POINTS:
                # Full: keep the newer half.
                keep = MAX_POINTS // 2
                self._tb[:keep] = self._tb[n - keep:n]
                self._vb[:keep] = self._vb[n - keep:n]
                n = self._n = keep
            else:
                self._tb = np.concatenate([self._tb, np.empty(len(self._tb))])
                self._vb = np.concatenate([self._vb, np.empty(len(self._vb))])
        self._tb[n] = t
        self._vb[n] = value
        self._n = n + 1
        return True

    def value_at(self, x: float) -> float | None:
        """The value held at time ``x``: None before the first change, NaN in a gap."""
        i = int(np.searchsorted(self._tb[:self._n], x, side="right")) - 1
        return None if i < 0 else float(self._vb[i])

    def count_between(self, x0: float, x1: float) -> int:
        t = self._tb[:self._n]
        return int(np.searchsorted(t, x1, side="right") - np.searchsorted(t, x0, side="right"))

    def changes(self) -> np.ndarray:
        """Indices where the value really changed: a log row's old value,
        drawn at the moment of the change, repeats the level before it and
        is not a change of its own."""
        t, v = self.arrays()
        if len(v) < 2:
            return np.arange(len(v))
        same = (v[1:] == v[:-1]) | (np.isnan(v[1:]) & np.isnan(v[:-1]))
        return np.concatenate(([0], np.nonzero(~same)[0] + 1))

    def _changes_in(self, a: int, b: int) -> np.ndarray:
        """Indices in ``a..b`` (b exclusive) whose value differs from the one
        before: local, so a cursor or the stats never scan the whole series."""
        t, v = self.arrays()
        a = max(a, 0)
        if b - a <= 0:
            return np.empty(0, dtype=np.intp)
        lo = max(a - 1, 0)
        vv = v[lo:b]
        same = (vv[1:] == vv[:-1]) | (np.isnan(vv[1:]) & np.isnan(vv[:-1]))
        idx = np.nonzero(~same)[0] + lo + 1
        if a == 0:
            idx = np.concatenate(([0], idx))
        return idx[idx >= a]

    def change_times(self, x0: float | None = None, x1: float | None = None) -> np.ndarray:
        t, _v = self.arrays()
        a = 0 if x0 is None else int(np.searchsorted(t, x0, side="left"))
        b = len(t) if x1 is None else int(np.searchsorted(t, x1, side="right"))
        return t[self._changes_in(a, b)]

    def nearest_change(self, x: float, within: float) -> float | None:
        """The change closest to ``x``, if one is no further than ``within``
        seconds away: what a cursor snaps to."""
        times = self.change_times(x - within, x + within)
        if not len(times):
            return None
        return float(times[np.argmin(np.abs(times - x))])

    def stats(self, x0: float, x1: float, right: float | None = None) -> dict:
        """Lowest, highest and time-weighted average of the value held over
        ``x0..x1``, and how many times it changed in there. A value held for
        an hour counts for an hour, not as one point -- on a log of changes a
        plain mean of the rows would weigh a busy minute over a quiet hour.
        Nothing after ``right`` (now, or the end of the data) counts."""
        out = {"min": None, "max": None, "avg": None, "changes": 0}
        t, v = self.arrays()
        if not len(t):
            return out
        end = float(t[-1]) if right is None else max(float(right), float(t[-1]))
        x1 = min(x1, end)
        if x1 <= x0:
            value = self.value_at(x0)
            if value is not None and value == value:
                out.update(min=value, max=value, avg=value)
            return out
        a = max(int(np.searchsorted(t, x0, side="right")) - 1, 0)
        b = int(np.searchsorted(t, x1, side="right"))
        tt, vv = t[a:b].copy(), v[a:b]
        if not len(tt):
            return out
        tt[0] = max(tt[0], x0)
        ends = np.append(tt[1:], x1)
        widths = np.clip(ends - tt, 0, None)
        ok = ~np.isnan(vv) & (tt <= x1)
        if ok.any():
            out["min"] = float(np.min(vv[ok]))
            out["max"] = float(np.max(vv[ok]))
            weight = widths[ok].sum()
            out["avg"] = float((vv[ok] * widths[ok]).sum() / weight) if weight > 0 else \
                float(vv[ok][-1])
        first = int(np.searchsorted(t, x0, side="right"))
        out["changes"] = int(len(self._changes_in(first, b)))
        return out

    def is_boolean(self) -> bool:
        """Only 0 and 1 (and gaps): a bit, drawn as a lane, not a chart."""
        _t, v = self.arrays()
        finite = v[~np.isnan(v)]
        return bool(len(finite)) and bool(np.all((finite == 0) | (finite == 1)))

    def view(self, x0: float, x1: float, buckets: int,
             right: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Points to draw for the time range ``x0..x1`` on a chart ``buckets``
        pixels wide, as a plain connected line (NaN breaks it).

        ``right`` is where the last value stops: now, on a live chart, or
        the end of the range asked for. Nothing is drawn after it.
        """
        t, v = self.arrays()
        if not len(t) or x1 <= x0:
            return np.empty(0), np.empty(0)
        right = float(t[-1]) if right is None else max(float(right), float(t[-1]))
        buckets = max(int(buckets), 1)
        # The change in force at x0 (if any) up to the last one inside the view.
        a = max(int(np.searchsorted(t, x0, side="right")) - 1, 0)
        b = int(np.searchsorted(t, x1, side="right"))
        if b - a <= buckets:
            return _steps(t[a:b], v[a:b], t[b] if b < len(t) else right)
        return _minmax(t, v, x0, min(x1, right), buckets)


def _same(a: float, b: float) -> bool:
    return a == b or (math.isnan(a) and math.isnan(b))


def _steps(t: np.ndarray, v: np.ndarray, end: float) -> tuple[np.ndarray, np.ndarray]:
    """Each value as a level from its change to the next: the line joins the
    end of one level to the start of the next at the same x, so it steps."""
    if not len(t):
        return np.empty(0), np.empty(0)
    ends = np.append(t[1:], max(end, t[-1]))
    xs = np.column_stack([t, ends]).ravel()
    ys = np.repeat(v, 2)
    return xs, ys


def _minmax(t: np.ndarray, v: np.ndarray, x0: float, x1: float,
            buckets: int) -> tuple[np.ndarray, np.ndarray]:
    """One vertical stroke per bucket: from the value held entering it,
    through its lowest and highest, to the value it leaves with. The leaving
    value is the next bucket's entering one, so the strokes join into a line."""
    edges = np.linspace(x0, x1, buckets + 1)
    # Changes strictly after each edge: bucket k holds t[s[k]:s[k+1]].
    s = np.searchsorted(t, edges, side="right")
    enter_i = s[:-1] - 1
    enter = np.where(enter_i >= 0, v[np.clip(enter_i, 0, None)], np.nan)
    lo, hi, leave = enter.copy(), enter.copy(), enter.copy()
    filled = s[1:] > s[:-1]
    if filled.any():
        starts = s[:-1][filled]
        # reduceat over consecutive non-empty slices: each slice runs to the
        # next start, so add the end of the last one as a stop.
        stops = s[1:][filled]
        idx = np.empty(len(starts) * 2, dtype=np.intp)
        idx[0::2], idx[1::2] = starts, stops
        with np.errstate(invalid="ignore"):
            if stops[-1] >= len(v):
                # reduceat cannot take an index equal to the length.
                padded = np.append(v, np.nan)
            else:
                padded = v
            mins = np.fmin.reduceat(padded, idx)[0::2]
            maxs = np.fmax.reduceat(padded, idx)[0::2]
        lo[filled] = np.fmin(enter[filled], mins)
        hi[filled] = np.fmax(enter[filled], maxs)
        leave[filled] = v[stops - 1]
    xs = np.repeat(edges[:-1], 4)
    ys = np.column_stack([enter, lo, hi, leave]).ravel()
    # The last bucket ends at x1: carry its leaving value there.
    return np.append(xs, x1), np.append(ys, leave[-1])


class TextSeries:
    """A text tag: value ``values[i]`` holds from ``begins[i]`` to the next
    begin (or the right edge). An empty value is a gap and is not drawn."""

    def __init__(self, begins: Iterable[float] = (), values: Iterable[str] = ()) -> None:
        self.begins: list[float] = [float(b) for b in begins]
        self.values: list[str] = [str(v) for v in values]

    @classmethod
    def from_spans(cls, spans: list[tuple[datetime, str]], left: float) -> "TextSeries":
        """From logdata.text_series' [(when, value)]. The first change carries
        the value before it at the same moment: that one is drawn from the
        left edge. Spans of no width (two changes in one millisecond) go."""
        begins: list[float] = []
        values: list[str] = []
        for i, (when, value) in enumerate(spans):
            begin = epoch(when)
            if i == 0 and len(spans) > 1 and spans[1][0] == when:
                begin = min(left, begin)
            following = epoch(spans[i + 1][0]) if i + 1 < len(spans) else None
            if following is not None and following <= begin:
                continue
            begins.append(begin)
            values.append(value)
        return cls(begins, values)

    def __len__(self) -> int:
        return len(self.begins)

    @property
    def last_time(self) -> float | None:
        return self.begins[-1] if self.begins else None

    def append(self, t: float, value: str) -> bool:
        if self.begins and (t < self.begins[-1] or
                            (t == self.begins[-1] and value == self.values[-1])):
            return False
        if self.begins and t == self.begins[-1]:
            self.values[-1] = value     # a second change in the same ms replaces the first
            return True
        self.begins.append(float(t))
        self.values.append(str(value))
        if len(self.begins) > MAX_POINTS:
            cut = len(self.begins) - MAX_POINTS
            del self.begins[:cut]
            del self.values[:cut]
        return True

    def segments(self, right: float | None = None, x0: float | None = None,
                 x1: float | None = None) -> list[tuple[float, float, str]]:
        """[(begin, end, value)], optionally only those overlapping ``x0..x1``."""
        n = len(self.begins)
        if not n:
            return []
        last_end = max(right, self.begins[-1]) if right is not None else self.begins[-1]
        first = 0 if x0 is None else max(bisect.bisect_right(self.begins, x0) - 1, 0)
        stop = n if x1 is None else bisect.bisect_right(self.begins, x1)
        out = []
        for i in range(first, stop):
            end = self.begins[i + 1] if i + 1 < n else last_end
            if end > self.begins[i] or i + 1 == n:
                out.append((self.begins[i], end, self.values[i]))
        return out

    def nearest_change(self, x: float, within: float) -> float | None:
        if not self.begins:
            return None
        i = bisect.bisect_left(self.begins, x)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(self.begins):
                d = abs(self.begins[j] - x)
                if d <= within and (best is None or d < abs(best - x)):
                    best = self.begins[j]
        return best

    def changes_between(self, x0: float, x1: float) -> int:
        return max(bisect.bisect_right(self.begins, x1) - bisect.bisect_right(self.begins, x0), 0)

    def at(self, x: float, right: float | None = None) -> tuple[float, float, str] | None:
        i = bisect.bisect_right(self.begins, x) - 1
        if i < 0:
            return None
        end = self.begins[i + 1] if i + 1 < len(self.begins) else (
            max(right, self.begins[i]) if right is not None else self.begins[i])
        if x > end:
            return None
        return self.begins[i], end, self.values[i]


def number_matches(series: StepSeries, needle: str) -> list[tuple[float, str]]:
    """Each time a numeric tag took the value ``needle`` (typed as text),
    as (time, text). Whole numbers compare in full: 3206300, not 3.2063e+06."""
    try:
        target = float(needle)
    except ValueError:
        return []
    t, v = series.arrays()
    if not len(t):
        return []
    hit = v == target
    previous = np.concatenate(([False], hit[:-1]))
    found = np.nonzero(hit & ~previous)[0]
    text = str(int(target)) if target.is_integer() else f"{target:g}"
    return [(float(t[i]), text) for i in found]


def duration(seconds: float) -> str:
    """A time difference as a person says it: 212 ms, 2.333 s, 4 min 2.1 s."""
    sign = "-" if seconds < 0 else ""
    s = abs(seconds)
    if s < 1:
        return f"{sign}{s * 1000:.0f} ms"
    if s < 60:
        return f"{sign}{s:.3f} s"
    if s < 3600:
        minutes, rest = divmod(s, 60)
        return f"{sign}{int(minutes)} min {rest:.1f} s"
    hours, rest = divmod(s, 3600)
    return f"{sign}{int(hours)} h {int(rest // 60)} min"


def text_of(value: float | None) -> str:
    """A value as a person reads it: whole numbers in full, no float noise."""
    if value is None:
        return ""
    if value != value:
        return ""
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"


def export_rows(numbers: dict, texts: dict, x0: float, x1: float,
                right: float | None = None) -> list[tuple[int, str, str, str]]:
    """The data in a time range as rows of (epoch ms, tag, value, note): for
    each tag, first the value it already held at ``x0`` (stamped ``x0``,
    noted as such), then every change inside the range, all in time order.
    Enough to rebuild exactly what the chart showed."""
    rows: list[tuple[int, str, str, str]] = []
    start_ms = int(round(x0 * 1000))
    for tag, series in numbers.items():
        held = series.value_at(x0)
        if held is not None:
            rows.append((start_ms, tag, text_of(held), "held at the start of the range"))
        t, v = series.arrays()
        idx = series.changes()
        for i in idx:
            if x0 < t[i] <= x1 and (right is None or t[i] <= right):
                rows.append((int(round(t[i] * 1000)), tag, text_of(v[i]), ""))
    for tag, series in texts.items():
        hit = series.at(x0, right)
        if hit is not None:
            rows.append((start_ms, tag, hit[2], "held at the start of the range"))
        for begin, _end, value in series.segments(right, x0, x1):
            if x0 < begin <= x1:
                rows.append((int(round(begin * 1000)), tag, value, ""))
    rows.sort(key=lambda r: (r[0], r[3] == ""))
    return rows
