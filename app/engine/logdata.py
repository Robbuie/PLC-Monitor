"""Reading the CSV log back: for the trend chart and for latency.

No Qt here. Everything takes an iterable of row dicts so it can be tested
against a list, and `read_rows` is the only part that touches a file. Both the
live log and its `.csv.gz` archives read the same way.
"""

from __future__ import annotations

import csv
import gzip
import io
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Iterator

PLC_CHANGED = "PLC_CHANGED"
SOCKET_EVENTS = ("SOCK_RECV", "SOCK_SENT")


def open_text(path: str):
    if path.lower().endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace",
                                newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def read_rows(path: str) -> Iterator[dict]:
    """Every row in a log file, as the dicts `csv.DictReader` gives.

    Tolerates the one thing a live log does all the time: a last line that is
    half written because the logger is mid-append.
    """
    with open_text(path) as handle:
        reader = csv.DictReader(handle)
        try:
            for row in reader:
                if row:
                    yield row
        except csv.Error:
            return


def row_ms(row: dict) -> int | None:
    try:
        return int(row.get("timestamp_ms") or "")
    except ValueError:
        text = (row.get("timestamp_human") or "").strip()
        try:
            return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f").timestamp() * 1000)
        except ValueError:
            return None


def _number(text: str) -> float | None:
    text = (text or "").strip()
    if text in ("True", "true"):
        return 1.0
    if text in ("False", "false"):
        return 0.0
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------


def numeric_tags(rows: Iterable[dict], include_bool: bool = True) -> list[str]:
    """Tags that have at least one numeric value. Booleans count when asked:
    a step chart of a bit is often the most useful line on the page."""
    found: set[str] = set()
    for row in rows:
        if row.get("event_type") != PLC_CHANGED:
            continue
        tag = (row.get("tag_or_addr") or "").strip()
        if not tag or tag in found:
            continue
        value = (row.get("new_value") or "").strip()
        if not include_bool and value in ("True", "False", "true", "false"):
            continue
        if _number(value) is not None:
            found.add(tag)
    return sorted(found)


def series(rows: Iterable[dict], tags: list[str], start_ms: int | None = None,
           end_ms: int | None = None) -> dict[str, tuple[list[datetime], list[float]]]:
    """{tag: (times, values)} for the PLC changes of each tag inside the range.

    The value *before* each change is kept as well, as a point at the change's
    own time: a log records changes, not samples, so a line drawn through the
    new values alone slopes between them when the real signal stepped.
    """
    wanted = set(tags)
    out: dict[str, tuple[list[datetime], list[float]]] = {t: ([], []) for t in tags}
    for row in rows:
        if row.get("event_type") != PLC_CHANGED:
            continue
        tag = (row.get("tag_or_addr") or "").strip()
        if tag not in wanted:
            continue
        ms = row_ms(row)
        if ms is None:
            continue
        if start_ms is not None and ms < start_ms:
            continue
        if end_ms is not None and ms > end_ms:
            continue
        new = _number(row.get("new_value", ""))
        if new is None:
            continue
        when = datetime.fromtimestamp(ms / 1000)
        old = _number(row.get("old_value", ""))
        times, values = out[tag]
        if old is not None:
            times.append(when)
            values.append(old)
        times.append(when)
        values.append(new)
    return out


# --------------------------------------------------------------------------
# Latency
# --------------------------------------------------------------------------


@dataclass
class Matcher:
    """Which rows count as the trigger, or as the response.

    `kind` "plc": a PLC_CHANGED row for `tag`, optionally only when the new
    value equals `value`. `kind` "socket": a SOCK_RECV or SOCK_SENT row (or
    both), optionally only from `source` and only when the payload text or hex
    contains `contains`.
    """

    kind: str = "plc"
    tag: str = ""
    value: str = ""
    events: tuple[str, ...] = SOCKET_EVENTS
    source: str = ""
    contains: str = ""

    def matches(self, row: dict) -> bool:
        event = row.get("event_type", "")
        if self.kind == "plc":
            if event != PLC_CHANGED:
                return False
            if self.tag and (row.get("tag_or_addr") or "").strip() != self.tag:
                return False
            if self.value != "" and (row.get("new_value") or "").strip() != self.value:
                return False
            return True
        if event not in self.events:
            return False
        if self.source and (row.get("source") or "") != self.source:
            return False
        if self.contains:
            needle = self.contains.lower()
            hay = ((row.get("notes") or "") + " " + (row.get("raw_hex") or "")).lower()
            if needle not in hay:
                return False
        return True

    def describe(self) -> str:
        if self.kind == "plc":
            what = self.tag or "any tag"
            return f"{what} changes" + (f" to {self.value}" if self.value != "" else "")
        events = "/".join(e.replace("SOCK_", "").lower() for e in self.events)
        text = f"socket {events}"
        if self.source:
            text += f" from {self.source}"
        if self.contains:
            text += f" containing '{self.contains}'"
        return text


@dataclass
class Pair:
    trigger_ms: int
    response_ms: int | None
    trigger_text: str
    response_text: str = ""

    @property
    def delta_ms(self) -> int | None:
        if self.response_ms is None:
            return None
        return self.response_ms - self.trigger_ms


@dataclass
class LatencyResult:
    pairs: list[Pair] = field(default_factory=list)

    @property
    def answered(self) -> list[int]:
        return [p.delta_ms for p in self.pairs if p.delta_ms is not None]

    @property
    def unanswered(self) -> int:
        return sum(1 for p in self.pairs if p.response_ms is None)

    def stats(self) -> dict[str, float]:
        values = sorted(self.answered)
        if not values:
            return {}
        return {
            "count": len(values),
            "min": values[0],
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "p95": percentile(values, 95),
            "max": values[-1],
            "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
        }


def percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank on linear interpolation, the same as numpy's default."""
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * pct / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(sorted_values[low])
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (rank - low)


def _summary(row: dict) -> str:
    event = row.get("event_type", "")
    if event == PLC_CHANGED:
        return f"{row.get('tag_or_addr', '')}: {row.get('old_value', '')} to {row.get('new_value', '')}"
    notes = (row.get("notes") or "").strip()
    if len(notes) > 80:
        notes = notes[:77] + "..."
    return f"{event.replace('SOCK_', '')} {row.get('tag_or_addr', '')}  {notes}".strip()


def measure(rows: Iterable[dict], trigger: Matcher, response: Matcher, *,
            window_ms: int = 5000,
            start_ms: int | None = None, end_ms: int | None = None) -> LatencyResult:
    """Pair every trigger with the first response after it.

    A trigger with no response inside `window_ms` is kept as unanswered rather
    than dropped: a message that never came is the most important result this
    can produce. A response that arrives after the next trigger is credited to
    that later trigger, not the earlier one -- otherwise two quick triggers and
    one reply read as one slow reply.

    Rows are taken in file order, which is time order: the logger appends.
    """
    result = LatencyResult()
    open_pair: Pair | None = None
    for row in rows:
        ms = row_ms(row)
        if ms is None:
            continue
        if start_ms is not None and ms < start_ms:
            continue
        if end_ms is not None and ms > end_ms:
            break
        if open_pair is not None and ms - open_pair.trigger_ms > window_ms:
            open_pair = None
        if open_pair is not None and response.matches(row) and ms >= open_pair.trigger_ms:
            open_pair.response_ms = ms
            open_pair.response_text = _summary(row)
            open_pair = None
            continue
        if trigger.matches(row):
            pair = Pair(trigger_ms=ms, response_ms=None, trigger_text=_summary(row))
            result.pairs.append(pair)
            open_pair = pair
    return result


def sources(rows: Iterable[dict]) -> tuple[list[str], list[str]]:
    """(PLC tags, socket source labels) seen in a log, for the pickers."""
    tags: set[str] = set()
    labels: set[str] = set()
    for row in rows:
        event = row.get("event_type", "")
        if event == PLC_CHANGED:
            tag = (row.get("tag_or_addr") or "").strip()
            if tag:
                tags.add(tag)
        elif event in SOCKET_EVENTS:
            label = (row.get("source") or "").strip()
            if label:
                labels.add(label)
    return sorted(tags), sorted(labels)
