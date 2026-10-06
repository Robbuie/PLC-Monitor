"""Reading RobControl's trend recordings, so robot signals can sit on the same
chart as PLC tags.

RobControl (the sibling tool for FANUC robots) records registers, I/O and
system variables into its own SQLite database. Reading the robots is
RobControl's job and stays there: it carries the safety rules for talking to a
controller (classified KCL, one request at a time, a 2 s floor). PLC Monitor
only *reads the recording*, never a robot.

Why the database and not an export: it is live. RobControl keeps writing while
this reads, so the Trend page's 30 s refresh shows both sides as they happen.

  * Opened read-only (``mode=ro``) and closed after every read. The database
    is in WAL mode, so a reader never blocks RobControl's writes.
  * The schema is RobControl's, from ``FleetSchema.cs``: ``Robot``,
    ``TrendSignal (Id, RobotId, Address, Label)`` and
    ``TrendSample (SignalId, UtcMs, Value)``. ``UtcMs`` is Unix epoch
    milliseconds, the same clock as this application's ``timestamp_ms``.
  * RobControl stores a value when it changes, plus a heartbeat every 60 s.
    So, like a PLC Monitor log, each change is drawn as a step.

No Qt here, like the rest of app/engine.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime

DB_NAME = "robcontrol.db"

# One signal never draws more than this many samples. Matches RobControl's own
# chart limit; at one change every 2 s it is over four days of a busy signal.
SAMPLE_LIMIT = 200_000


class RobDataError(Exception):
    """A RobControl database that could not be read, with a message a person can act on."""


@dataclass(frozen=True)
class RobotSignal:
    id: int
    robot: str
    address: str
    label: str = ""
    last_ms: int | None = None
    path: str = ""

    @property
    def name(self) -> str:
        """How the signal is named on the chart and in the list. The robot
        comes first so two robots' R[1] can never be confused, and nothing here
        can be mistaken for a PLC tag name."""
        text = f"{self.robot} / {self.address}"
        return f"{text}  {self.label}" if self.label else text

    @property
    def key(self) -> str:
        """Identity across reloads: which database and which signal."""
        return f"{self.path}#{self.id}"


def default_db_path() -> str | None:
    """RobControl's database on this PC for this user, if there is one.

    RobControl keeps it in %LOCALAPPDATA%\\RobControl. A PLC Monitor running
    elevated (for Passive sniff) is still the same user and finds the same
    folder.
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    path = os.path.join(base, "RobControl", DB_NAME)
    return path if os.path.isfile(path) else None


def _connect(path: str) -> sqlite3.Connection:
    if not os.path.isfile(path):
        raise RobDataError(f"{path} does not exist.")
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=3)
        connection.execute("SELECT 1 FROM TrendSample LIMIT 1")
    except sqlite3.OperationalError as exc:
        text = str(exc)
        if "no such table" in text:
            raise RobDataError(
                f"{os.path.basename(path)} has no trend recordings. RobControl 0.2.0 or later "
                "records them: open a robot's Trends tab and start recording.") from exc
        raise RobDataError(f"Could not open {path} read-only: {text}") from exc
    except sqlite3.DatabaseError as exc:
        raise RobDataError(f"{path} is not a RobControl database ({exc}).") from exc
    return connection


def signals(path: str) -> list[RobotSignal]:
    """Every signal RobControl has been asked to record, by robot then address,
    with the time of its newest sample (None if it has never been recorded)."""
    connection = _connect(path)
    try:
        rows = connection.execute(
            """
            SELECT s.Id, r.Name, s.Address, COALESCE(s.Label, ''),
                   (SELECT MAX(UtcMs) FROM TrendSample WHERE SignalId = s.Id)
            FROM TrendSignal s JOIN Robot r ON r.Id = s.RobotId
            ORDER BY r.Name COLLATE NOCASE, s.Id
            """).fetchall()
    except sqlite3.Error as exc:
        raise RobDataError(f"Could not read the signal list: {exc}") from exc
    finally:
        connection.close()
    return [RobotSignal(int(i), str(robot), str(address), str(label),
                        int(last) if last is not None else None, path)
            for i, robot, address, label, last in rows]


def series(path: str, wanted: list[RobotSignal], start_ms: int | None = None,
           end_ms: int | None = None) -> dict[str, tuple[list[datetime], list[float]]]:
    """{signal.name: (times, values)}, drawn as steps like logdata.series.

    The last sample before ``start_ms`` is included at the left edge of the
    range, so a register that has not changed for hours still draws a line
    across the chart rather than nothing.
    """
    out: dict[str, tuple[list[datetime], list[float]]] = {s.name: ([], []) for s in wanted}
    if not wanted:
        return out
    low = start_ms if start_ms is not None else -(2 ** 62)
    high = end_ms if end_ms is not None else 2 ** 62
    connection = _connect(path)
    try:
        for signal in wanted:
            rows = connection.execute(
                """
                SELECT UtcMs, Value FROM (
                    SELECT UtcMs, Value FROM TrendSample
                    WHERE SignalId = ? AND UtcMs < ? ORDER BY UtcMs DESC LIMIT 1)
                UNION ALL
                SELECT UtcMs, Value FROM (
                    SELECT UtcMs, Value FROM TrendSample
                    WHERE SignalId = ? AND UtcMs >= ? AND UtcMs <= ? ORDER BY UtcMs LIMIT ?)
                ORDER BY UtcMs
                """, (signal.id, low, signal.id, low, high, SAMPLE_LIMIT)).fetchall()
            times, values = out[signal.name]
            previous = None
            for ms, value in rows:
                if value is None:
                    continue
                ms = max(int(ms), low) if start_ms is not None else int(ms)
                when = datetime.fromtimestamp(ms / 1000)
                value = float(value)
                if previous is not None and value != previous:
                    times.append(when)
                    values.append(previous)
                times.append(when)
                values.append(value)
                previous = value
    except sqlite3.Error as exc:
        raise RobDataError(f"Could not read the samples: {exc}") from exc
    finally:
        connection.close()
    return out
