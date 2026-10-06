"""Reading RobControl's recording: the signal list, steps, the left edge, and
that it is read-only."""

import os
import sqlite3
from datetime import datetime

import pytest

from app.engine import robdata

# The tables PLC Monitor reads, as RobControl's FleetSchema.cs creates them.
SCHEMA = """
CREATE TABLE Robot (Id INTEGER PRIMARY KEY, Name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    Address TEXT NOT NULL);
CREATE TABLE TrendSignal (Id INTEGER PRIMARY KEY, RobotId INTEGER NOT NULL REFERENCES Robot(Id),
                          Address TEXT NOT NULL, Label TEXT, CreatedUtc TEXT NOT NULL,
                          UNIQUE (RobotId, Address));
CREATE TABLE TrendSample (SignalId INTEGER NOT NULL REFERENCES TrendSignal(Id),
                          UtcMs INTEGER NOT NULL, Value REAL NOT NULL);
"""

T0 = 1_790_000_000_000


def make_db(folder, samples=()):
    path = os.path.join(folder, "robcontrol.db")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("INSERT INTO Robot VALUES (1, 'R2-14', '10.20.1.54'), (2, 'R2-15', '10.20.1.55')")
    db.execute("INSERT INTO TrendSignal VALUES (1, 1, 'R[1]', 'Weld count', 'x'),"
               " (2, 2, 'R[1]', NULL, 'x'), (3, 1, 'DI[3]', NULL, 'x')")
    db.executemany("INSERT INTO TrendSample VALUES (?, ?, ?)", samples)
    db.commit()
    db.close()
    return path


def test_signals_are_listed_by_robot_with_their_newest_sample(isolated_data):
    path = make_db(isolated_data, [(1, T0, 5), (1, T0 + 4000, 6)])
    found = robdata.signals(path)
    assert [s.name for s in found] == ["R2-14 / R[1]  Weld count", "R2-14 / DI[3]", "R2-15 / R[1]"]
    assert found[0].last_ms == T0 + 4000
    assert found[1].last_ms is None
    assert len({s.key for s in found}) == 3


def test_changes_draw_as_steps_and_heartbeats_do_not_add_steps(isolated_data):
    path = make_db(isolated_data, [(1, T0, 5), (1, T0 + 2000, 5), (1, T0 + 4000, 7)])
    signal = robdata.signals(path)[0]
    times, values = robdata.series(path, [signal])[signal.name]
    assert values == [5, 5, 5, 7]
    assert times[2] == times[3] == datetime.fromtimestamp((T0 + 4000) / 1000)


def test_the_value_before_the_range_starts_the_line_at_the_left_edge(isolated_data):
    path = make_db(isolated_data, [(1, T0, 5), (1, T0 + 60_000, 9)])
    signal = robdata.signals(path)[0]
    start = T0 + 30_000
    times, values = robdata.series(path, [signal], start)[signal.name]
    assert times[0] == datetime.fromtimestamp(start / 1000)
    assert values == [5, 5, 9]


def test_the_database_is_never_written(isolated_data):
    path = make_db(isolated_data, [(1, T0, 5)])
    before = os.path.getmtime(path), os.path.getsize(path)
    robdata.series(path, robdata.signals(path))
    assert (os.path.getmtime(path), os.path.getsize(path)) == before
    with pytest.raises(sqlite3.OperationalError):
        robdata._connect(path).execute("DELETE FROM TrendSample")


def test_a_database_without_recordings_says_what_to_do(isolated_data):
    path = os.path.join(isolated_data, "old.db")
    sqlite3.connect(path).execute("CREATE TABLE Robot (Id INTEGER)").connection.close()
    with pytest.raises(robdata.RobDataError, match="RobControl 0.2.0"):
        robdata.signals(path)


def test_default_path_is_robcontrols_folder(isolated_data):
    assert robdata.default_db_path() is None
    os.makedirs(os.path.join(isolated_data, "RobControl"))
    make_db(os.path.join(isolated_data, "RobControl"))
    assert robdata.default_db_path() == os.path.join(isolated_data, "RobControl", "robcontrol.db")


def test_the_trend_page_plots_tags_and_robot_signals_together(isolated_data):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.core.settings import Settings
    from app.ui.session import Session
    from app.ui.window import MainWindow

    os.makedirs(os.path.join(isolated_data, "RobControl"))
    make_db(os.path.join(isolated_data, "RobControl"), [(1, T0, 5), (1, T0 + 4000, 6)])
    window = MainWindow(Session(), Settings(os.path.join(isolated_data, "ui.json")))
    page = window.pages["trend"]
    page.refresh_robot_sources()
    for _ in range(200):
        app.processEvents()
        if page.robot_list.count():
            break
    assert page.robot_list.count() == 3
    page.robot_list.item(0).setCheckState(Qt.Checked)
    assert [s.address for s in page._selected_robots()] == ["R[1]"]
    page._render(robdata.series(robdata.default_db_path(), page._selected_robots()),
                 [page._selected_robots()[0].name], "everything")
    assert len(page._lines) == 1
    window.session.shutdown()
