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


def row_window(row: dict) -> int | None:
    try:
        return int(row.get("window_ms") or "")
    except (TypeError, ValueError):
        return None


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
    value equals `value` (or, written `!value`, is anything but it). `kind` "socket": a SOCK_RECV or SOCK_SENT row (or
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
            if self.value != "":
                new = (row.get("new_value") or "").strip()
                # "!0UNKNOWN": any value but that one -- a VIN register that
                # shows a placeholder between vehicles.
                if self.value.startswith("!"):
                    if new == self.value[1:].strip():
                        return False
                elif new != self.value:
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
    #: For a PLC-change trigger: how long before its stamp the change could
    #: really have happened (the poll window). The true latency is between
    #: delta_ms and delta_ms + trigger_window_ms. None when not known.
    trigger_window_ms: int | None = None

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

    def windows(self) -> list[int]:
        """The poll windows of the answered triggers that have one."""
        return [p.trigger_window_ms for p in self.pairs
                if p.delta_ms is not None and p.trigger_window_ms is not None]

    def window_stats(self) -> dict[str, float]:
        """How uncertain the trigger stamps were: the median and largest poll
        window. Empty when the log does not record windows (before 1.2, or a
        socket trigger)."""
        values = sorted(self.windows())
        if not values:
            return {}
        return {"median": statistics.median(values), "max": values[-1]}

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
            start_ms: int | None = None, end_ms: int | None = None,
            same_value: bool = False) -> LatencyResult:
    """Pair every trigger with the first response after it.

    A trigger with no response inside `window_ms` is kept as unanswered rather
    than dropped: a message that never came is the most important result this
    can produce. A response that arrives after the next trigger is credited to
    that later trigger, not the earlier one -- otherwise two quick triggers and
    one reply read as one slow reply.

    Rows are put in time order first. The logger appends, but rows can be
    stamped with an earlier time than the one before them -- a captured
    packet carries the time the adapter saw it, and is written a moment
    later, possibly after a PLC change seen in between.
    """
    # Only rows that can matter are kept: a long log holds hundreds of
    # thousands of rows, and pairing needs only the triggers and responses.
    stamped = []
    for index, row in enumerate(rows):
        is_trigger, is_response = trigger.matches(row), response.matches(row)
        if not (is_trigger or is_response):
            continue
        ms = row_ms(row)
        if ms is None:
            continue
        if start_ms is not None and ms < start_ms:
            continue
        if end_ms is not None and ms > end_ms:
            continue
        stamped.append((ms, index, is_trigger, is_response, row))
    stamped.sort(key=lambda item: (item[0], item[1]))
    if same_value:
        return _measure_by_value(stamped, window_ms)

    result = LatencyResult()
    open_pair: Pair | None = None
    for ms, _index, is_trigger, is_response, row in stamped:
        if open_pair is not None and ms - open_pair.trigger_ms > window_ms:
            open_pair = None
        if open_pair is not None and is_response and ms >= open_pair.trigger_ms:
            open_pair.response_ms = ms
            open_pair.response_text = _summary(row)
            open_pair = None
            continue
        if is_trigger:
            pair = Pair(trigger_ms=ms, response_ms=None, trigger_text=_summary(row),
                        trigger_window_ms=row_window(row) if row.get("event_type") == PLC_CHANGED
                        else None)
            result.pairs.append(pair)
            open_pair = pair
    return result


def carries(row: dict, value: str) -> bool:
    """Whether a row carries `value`: a tag that changed to it (or to text
    containing it), or a socket message whose payload holds it as text."""
    if not value:
        return False
    if row.get("event_type") == PLC_CHANGED:
        return value in (row.get("new_value") or "")
    if value in (row.get("notes") or ""):
        return True
    return value.encode("utf-8", "replace").hex() in (row.get("raw_hex") or "").lower()


def _measure_by_value(stamped: list, window_ms: int) -> LatencyResult:
    """Pair each trigger with the first later response carrying the SAME
    value -- vehicle A1234567 arriving in one register and leaving in
    another, or in a socket message. Triggers can overlap (several vehicles
    in flight); each waits for its own value, up to `window_ms`, and a
    response answers the oldest trigger still waiting for its value."""
    result = LatencyResult()
    waiting: list[tuple[Pair, str]] = []
    for ms, _index, is_trigger, is_response, row in stamped:
        waiting = [(p, v) for p, v in waiting if ms - p.trigger_ms <= window_ms]
        if is_response and waiting:
            for i, (pair, value) in enumerate(waiting):
                if ms >= pair.trigger_ms and carries(row, value):
                    pair.response_ms = ms
                    pair.response_text = _summary(row)
                    del waiting[i]
                    break
        if is_trigger:
            value = (row.get("new_value") or "").strip()
            if not value:
                continue
            pair = Pair(trigger_ms=ms, response_ms=None, trigger_text=_summary(row),
                        trigger_window_ms=row_window(row) if row.get("event_type") == PLC_CHANGED
                        else None)
            result.pairs.append(pair)
            waiting.append((pair, value))
    return result


# --------------------------------------------------------------------------
# Grouped tags in logs made before the group existed
# --------------------------------------------------------------------------


def with_groups(rows: Iterable[dict], specs, gap_ms: int = 50) -> Iterator[dict]:
    """Decode grouped tags in a log that recorded their registers one by one.

    A log written before `D1916 x4 as Text` was set up has a row per register
    -- D1916, D1917, ... -- and rebuilding the text row by row shows mixtures
    (`A1NKNOWN`) as each register's row arrives. The rows of one poll are
    written within a few milliseconds of each other, so registers changing
    within `gap_ms` are taken together: one decoded row per poll, named as
    the group is, in place of the register rows. A group's registers start
    from the old value on their first row (the first read of a tag is not
    logged as a change).

    Rows of anything else pass through untouched, and a log that already has
    the group's own rows is left alone.
    """
    from app.engine.tagspec import member_names

    groups = []
    owner: dict[str, int] = {}
    for spec in specs or []:
        if not spec.grouped:
            continue
        members = member_names(spec.address, spec.count)
        if len(members) != spec.count or any(m in owner for m in members):
            continue
        state = {"spec": spec, "members": members, "words": {}, "pending": None,
                 "last": None, "first_ms": None, "window": None, "source": ""}
        for m in members:
            owner[m] = len(groups)
        groups.append(state)
    if not groups:
        yield from rows
        return

    def flush(g):
        if g["pending"] is None:
            return None
        ms = g["pending"]
        g["pending"] = None
        if len(g["words"]) < len(g["members"]):
            return None
        try:
            value = g["spec"].decode([g["words"][m] for m in g["members"]])
        except Exception:
            return None
        text = str(value)
        if text == g["last"]:
            return None
        old, g["last"] = g["last"], text
        if old is None:
            # The value before this poll is known only if every register's
            # old value was: the first decoded value has no "from".
            old = ""
        spec = g["spec"]
        return {"timestamp_ms": str(ms),
                "timestamp_human": _human(ms),
                "event_type": PLC_CHANGED, "source": g["source"], "tag_or_addr": spec.key,
                "old_value": old, "new_value": text, "raw_hex": "",
                "notes": f"decoded from {spec.describe()}",
                "window_ms": "" if g["window"] is None else str(g["window"])}

    for row in rows:
        ms = row_ms(row)
        due = []
        if ms is not None:
            for g in groups:
                if g["pending"] is not None and ms - g["pending"] > gap_ms:
                    out = flush(g)
                    if out:
                        due.append(out)
        yield from due
        tag = (row.get("tag_or_addr") or "").strip()
        index = owner.get(tag)
        if index is None or row.get("event_type") != PLC_CHANGED or ms is None:
            yield row
            continue
        g = groups[index]
        try:
            new = int(float(row.get("new_value") or ""))
        except ValueError:
            yield row
            continue
        if tag not in g["words"]:
            try:
                g["words"][tag] = int(float(row.get("old_value") or ""))
            except ValueError:
                pass
            if g["last"] is None and len(g["words"]) == len(g["members"]) and g["pending"] is None:
                # Every register's starting value is known: that is the
                # value the first change is "from".
                try:
                    g["last"] = str(g["spec"].decode([g["words"][m] for m in g["members"]]))
                except Exception:
                    pass
        g["words"][tag] = new
        g["source"] = row.get("source", "")
        if g["pending"] is None:
            g["window"] = row_window(row)
        g["pending"] = ms
    for g in groups:
        out = flush(g)
        if out:
            yield out


def _human(ms: int) -> str:
    when = datetime.fromtimestamp(ms // 1000)
    return f"{when:%Y-%m-%d %H:%M:%S}.{ms % 1000:03d}"


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
