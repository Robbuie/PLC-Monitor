"""The Trend page's analysis, past drawing what was logged. No Qt.

* **Math pens** -- a pen worked out from others: the difference, sum,
  product or ratio of two tags, the rate of a counter, or how long each state
  of a tag lasted. They are step series like any other, so they draw, snap,
  export and measure the same way.
* **Flags** -- the chart pointing things out: a value over its high limit or
  under its low one, a tag that has not changed for longer than it should, a
  counter that jumped by more than one step (missed pulses) or went backwards.
* **Triggers and cycles** -- every moment a tag rose, fell or took a value,
  and each pen's values around those moments laid on one relative time axis,
  so a hundred machine cycles can be drawn over each other. The band most
  cycles stay inside (the envelope) and how far each cycle strays outside it
  pick out the odd one.
* **Events** -- socket messages and connection events from the log, and
  latency pairs (trigger to response), as markers on the time axis.

Times are epoch seconds, as in engine/trendview.py.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np

from app.engine import logdata
from app.engine.trendview import StepSeries, TextSeries, duration, text_of

# --------------------------------------------------------------------------
# Math pens
# --------------------------------------------------------------------------

#: What a math pen can be: (key, what the dialog says, needs a second pen).
MATH_OPS = [
    ("diff", "A minus B", True),
    ("sum", "A plus B", True),
    ("product", "A times B", True),
    ("ratio", "A divided by B", True),
    ("rate", "Rate of A (change per second or minute)", False),
    ("duration", "How long each state of A lasted (seconds)", False),
]
NEEDS_B = {key for key, _text, needs in MATH_OPS if needs}


def _compress(t: np.ndarray, v: np.ndarray) -> StepSeries:
    """Only the points where the value changes: a math pen evaluated at
    every change of its sources repeats itself between its own changes."""
    if not len(t):
        return StepSeries()
    same = np.zeros(len(v), dtype=bool)
    same[1:] = (v[1:] == v[:-1]) | (np.isnan(v[1:]) & np.isnan(v[:-1]))
    keep = ~same
    return StepSeries(t[keep], v[keep])


def _held(series: StepSeries, times: np.ndarray) -> np.ndarray:
    """Each series' value held at each of ``times`` (NaN before its first)."""
    t, v = series.arrays()
    if not len(t):
        return np.full(len(times), np.nan)
    i = np.searchsorted(t, times, side="right") - 1
    out = v[np.clip(i, 0, None)].astype(float)
    out[i < 0] = np.nan
    return out


def combine(a: StepSeries, b: StepSeries, op: str) -> StepSeries:
    """A op B, held between changes of either: worked out at every moment
    either source changed, so a step in either is a step in the result."""
    ta, _ = a.arrays()
    tb, _ = b.arrays()
    times = np.union1d(ta, tb)
    if not len(times):
        return StepSeries()
    va, vb = _held(a, times), _held(b, times)
    with np.errstate(divide="ignore", invalid="ignore"):
        if op == "diff":
            out = va - vb
        elif op == "sum":
            out = va + vb
        elif op == "product":
            out = va * vb
        elif op == "ratio":
            out = np.where(vb == 0, np.nan, va / np.where(vb == 0, 1, vb))
        else:
            raise ValueError(f"Unknown operation {op!r}")
    return _compress(times, out)


def rate(series: StepSeries, window_s: float, per_s: float = 1.0) -> StepSeries:
    """How fast a value climbs: its change over the last ``window_s``
    seconds, per ``per_s`` seconds (60 for per minute). Worked out wherever
    the window's edges cross a change, so it is exact for a step series."""
    t, _v = series.arrays()
    if not len(t) or window_s <= 0:
        return StepSeries()
    times = np.union1d(t, t + window_s)
    now = _held(series, times)
    then = _held(series, times - window_s)
    return _compress(times, (now - then) / window_s * per_s)


def state_durations(series: StepSeries, of_value: float | None = None) -> StepSeries:
    """At the end of each state, how long it lasted, in seconds -- how long
    Ready was high each time, how long each part sat in the station. With
    ``of_value``, only the states holding that value count."""
    t, v = series.arrays()
    idx = series.changes()
    if len(idx) < 2:
        return StepSeries()
    starts, ends = t[idx[:-1]], t[idx[1:]]
    values = v[idx[:-1]]
    lasted = ends - starts
    if of_value is not None:
        keep = values == of_value
        ends, lasted = ends[keep], lasted[keep]
    return StepSeries(ends, lasted)


def compute_math(spec: dict, sources: dict[str, StepSeries]) -> StepSeries | None:
    """A math pen from its spec (engine/trendprefs: op, a, b, window_s,
    per, value) and the loaded series it names. None when a source is missing."""
    a = sources.get(spec.get("a", ""))
    if a is None:
        return None
    op = spec.get("op", "diff")
    if op in NEEDS_B:
        b = sources.get(spec.get("b", ""))
        return None if b is None else combine(a, b, op)
    if op == "rate":
        per = 60.0 if spec.get("per") == "min" else 1.0
        return rate(a, float(spec.get("window_s") or 60), per)
    if op == "duration":
        value = spec.get("value")
        return state_durations(a, None if value in (None, "") else float(value))
    return None


def describe_math(spec: dict) -> str:
    a, b = spec.get("a", "?"), spec.get("b", "?")
    op = spec.get("op")
    if op in ("diff", "sum", "product", "ratio"):
        sign = {"diff": "-", "sum": "+", "product": "x", "ratio": "/"}[op]
        return f"{a} {sign} {b}"
    if op == "rate":
        per = "min" if spec.get("per") == "min" else "s"
        return f"rate of {a} per {per} over {text_of(float(spec.get('window_s') or 60))} s"
    if op == "duration":
        value = spec.get("value")
        return f"time {a} was {value}" if value not in (None, "") else f"time in each state of {a}"
    return "?"


# --------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------


@dataclass
class Flag:
    kind: str           # "high" | "low" | "stuck" | "jump" | "back"
    start: float
    end: float
    text: str


FLAG_KINDS = {"high": "over the high limit", "low": "under the low limit",
              "stuck": "no change", "jump": "counter jumped", "back": "counter went back"}


def flags(series: StepSeries, rules: dict, right: float | None = None,
          limit: int = 5000) -> list[Flag]:
    """What a pen's rules point out (engine/trendprefs: high, low, stuck_s,
    jump). ``right`` is where the last value ends (now, live)."""
    t, v = series.arrays()
    idx = series.changes()
    if not len(idx):
        return []
    end_of_data = float(t[-1]) if right is None else max(float(right), float(t[-1]))
    starts = t[idx]
    ends = np.append(t[idx[1:]], end_of_data)
    values = v[idx]
    out: list[Flag] = []

    def spans(mask: np.ndarray, kind: str, describe) -> None:
        # Consecutive states that break the rule are one flag.
        i = 0
        n = len(mask)
        while i < n and len(out) < limit:
            if not mask[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append(Flag(kind, float(starts[i]), float(ends[j]),
                            describe(values[i:j + 1], ends[j] - starts[i])))
            i = j + 1

    high, low = rules.get("high"), rules.get("low")
    with np.errstate(invalid="ignore"):
        if high is not None:
            spans(values > high, "high",
                  lambda vals, d: f"over {text_of(high)} for {duration(d)} "
                                  f"(peak {text_of(float(np.nanmax(vals)))})")
        if low is not None:
            spans(values < low, "low",
                  lambda vals, d: f"under {text_of(low)} for {duration(d)} "
                                  f"(lowest {text_of(float(np.nanmin(vals)))})")
    stuck = rules.get("stuck_s")
    if stuck:
        lasted = ends - starts
        for k in np.nonzero(lasted > stuck)[0][:limit]:
            out.append(Flag("stuck", float(starts[k] + stuck), float(ends[k]),
                            f"no change for {duration(float(lasted[k]))} "
                            f"(at {text_of(float(values[k]))})"))
    jump = rules.get("jump")
    if jump is not None and len(values) > 1:
        steps = np.diff(values)
        for k in np.nonzero(steps > jump)[0][:limit]:
            when = float(starts[k + 1])
            out.append(Flag("jump", when, when,
                            f"jumped by {text_of(float(steps[k]))}: {text_of(float(values[k]))} "
                            f"to {text_of(float(values[k + 1]))} (missed pulses?)"))
        for k in np.nonzero(steps < 0)[0][:limit]:
            when = float(starts[k + 1])
            out.append(Flag("back", when, when,
                            f"went back from {text_of(float(values[k]))} to "
                            f"{text_of(float(values[k + 1]))}"))
    out.sort(key=lambda f: f.start)
    return out


# --------------------------------------------------------------------------
# Triggers and cycles
# --------------------------------------------------------------------------

EDGES = [("rising", "rises"), ("falling", "falls"), ("equals", "changes to"),
         ("any", "changes")]


def trigger_times(series, edge: str, value: str = "") -> np.ndarray:
    """Every moment ``series`` rose, fell, changed, or changed to ``value``.
    A text series only knows "any" and "equals"."""
    if isinstance(series, TextSeries):
        begins = np.asarray(series.begins, dtype=float)
        if not len(begins):
            return begins
        if edge == "equals":
            hits = np.asarray([val == value for val in series.values], dtype=bool)
            hits[0] = False             # the value already held when the range began
            return begins[hits]
        return begins[1:]
    t, v = series.arrays()
    idx = series.changes()
    if len(idx) < 2:
        return np.empty(0)
    prev, cur = v[idx[:-1]], v[idx[1:]]
    times = t[idx[1:]]
    with np.errstate(invalid="ignore"):
        if edge == "rising":
            mask = cur > prev
        elif edge == "falling":
            mask = cur < prev
        elif edge == "equals":
            try:
                target = float(value)
            except ValueError:
                return np.empty(0)
            mask = cur == target
        else:
            mask = np.ones(len(cur), dtype=bool)
    return times[mask]


@dataclass
class Cycles:
    """Every pen around every trigger, on one relative time axis."""
    triggers: np.ndarray
    before: float
    after: float
    grid: np.ndarray                                    # seconds from the trigger
    samples: dict[str, np.ndarray] = field(default_factory=dict)   # pen -> cycles x grid
    low: dict[str, np.ndarray] = field(default_factory=dict)
    mid: dict[str, np.ndarray] = field(default_factory=dict)
    high: dict[str, np.ndarray] = field(default_factory=dict)
    deviation: np.ndarray = field(default_factory=lambda: np.empty(0))
    good: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=bool))

    def __len__(self) -> int:
        return len(self.triggers)

    def ranked(self) -> list[int]:
        """Cycle indices, the one furthest outside the envelope first."""
        return [int(i) for i in np.argsort(-np.nan_to_num(self.deviation, nan=-1.0),
                                           kind="stable")]


def cycles(triggers: np.ndarray, pens: dict[str, StepSeries], before: float, after: float,
           points: int = 400, good: np.ndarray | None = None,
           right: float | None = None, max_cycles: int = 2000) -> Cycles:
    """Sample each pen's held value on a grid from ``before`` seconds ahead
    of each trigger to ``after`` seconds past it.

    The envelope -- the band from the 5th to the 95th percentile at each
    moment of the cycle, and the median -- is taken over the ``good`` cycles
    (all of them by default; with under ten, the lowest to the highest). A
    cycle's deviation is how far, on average across the cycle and summed over
    the pens, it strays outside that band, as a share of the pen's whole
    range: 0 is a cycle inside the envelope throughout."""
    triggers = np.asarray(triggers, dtype=float)[-max_cycles:]
    grid = np.linspace(-abs(before), abs(after), max(points, 2))
    result = Cycles(triggers, abs(before), abs(after), grid)
    n = len(triggers)
    # A cycle the log ends in the middle of is not a short cycle: it is left
    # out of the band and the ranking.
    complete = np.ones(n, dtype=bool) if right is None else triggers + abs(after) <= right
    good = complete.copy() if good is None or len(good) != n else \
        (np.asarray(good, bool) & complete)
    result.good = good
    result.deviation = np.zeros(n)
    if not n:
        return result
    at = triggers[:, None] + grid[None, :]
    for name, series in pens.items():
        t, v = series.arrays()
        if not len(t):
            continue
        i = np.searchsorted(t, at, side="right") - 1
        m = v[np.clip(i, 0, None)].astype(float)
        m[i < 0] = np.nan
        if right is not None:
            m[at > right] = np.nan
        result.samples[name] = m
        ref = m[good] if good.any() else m
        with np.errstate(all="ignore"), warnings.catch_warnings():
            # All-NaN columns (before a pen's first value) are expected.
            warnings.simplefilter("ignore", RuntimeWarning)
            if len(ref) >= 10:
                low, mid, high = np.nanpercentile(ref, [5, 50, 95], axis=0)
            else:
                low, mid, high = (np.nanmin(ref, axis=0), np.nanmedian(ref, axis=0),
                                  np.nanmax(ref, axis=0))
            result.low[name], result.mid[name], result.high[name] = low, mid, high
            spread = np.nanmax(high) - np.nanmin(low)
            if not np.isfinite(spread) or spread <= 0:
                top = np.nanmax(np.abs(m))
                spread = max(float(top), 1.0) if np.isfinite(top) else 1.0
            outside = np.maximum(low[None, :] - m, 0) + np.maximum(m - high[None, :], 0)
            score = np.nanmean(outside, axis=1) / spread
        result.deviation += np.nan_to_num(score, nan=0.0)
    result.deviation[~complete] = np.nan
    return result


# --------------------------------------------------------------------------
# Events on the time axis
# --------------------------------------------------------------------------

#: Event lanes, top to bottom. "Status" is connections, disconnections and errors.
EVENT_LANES = [("recv", "Received"), ("sent", "Sent"), ("conn", "Status"),
               ("pair", "Latency")]
_EVENT_LANE = {"SOCK_RECV": "recv", "SOCK_SENT": "sent",
               "SOCK_CONNECT": "conn", "SOCK_DISCONNECT": "conn", "SOCK_ERROR": "conn",
               "PLC_CONNECTED": "conn", "PLC_DISCONNECTED": "conn", "PLC_ERROR": "conn"}


@dataclass
class Event:
    t: float
    lane: str           # "recv" | "sent" | "conn" | "pair"
    event: str          # the log's event_type ("PAIR" for a latency pair)
    tag: str            # tag_or_addr, for finding the row in the Live log
    text: str
    end: float | None = None        # a latency pair's response, None if unanswered
    error: bool = False


def is_event_row(row: dict) -> bool:
    return row.get("event_type") in _EVENT_LANE


def event_of(row: dict) -> Event | None:
    """A log row as a marker, or None for a row that is not one (a PLC
    change -- those are the lines)."""
    kind = row.get("event_type", "")
    lane = _EVENT_LANE.get(kind)
    if lane is None:
        return None
    ms = logdata.row_ms(row)
    if ms is None:
        return None
    notes = str(row.get("notes") or "").strip()
    if len(notes) > 120:
        notes = notes[:117] + "..."
    address = str(row.get("tag_or_addr") or "").strip()
    text = f"{kind.replace('SOCK_', '').replace('PLC_', 'PLC ').lower()}"
    if address:
        text += f"  {address}"
    if notes:
        text += f"  {notes}"
    return Event(ms / 1000, lane, kind, address, text, error=kind.endswith("ERROR"))


def pair_events(result: "logdata.LatencyResult", tag: str = "") -> list[Event]:
    """Latency pairs as spans from trigger to response: 'PLC set Ready, MES
    answered 212 ms later'. An unanswered trigger is a marker of its own.
    ``tag`` is the trigger's tag, so a click finds its row in the Live log."""
    out = []
    for p in result.pairs:
        if p.response_ms is None:
            out.append(Event(p.trigger_ms / 1000, "pair", "PAIR", tag,
                             f"{p.trigger_text}: no response", None, error=True))
        else:
            out.append(Event(p.trigger_ms / 1000, "pair", "PAIR", tag,
                             f"{duration(p.delta_ms / 1000)}: {p.trigger_text} -> "
                             f"{p.response_text}", p.response_ms / 1000))
    return out
