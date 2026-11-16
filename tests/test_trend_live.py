"""Trend in Live mode: fed from the rows the monitor logs, scrolling with now,
and stopping when somebody moves the chart."""

import os
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def wait(app, cond, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline and not cond():
        app.processEvents()
        time.sleep(0.02)
    return cond()


def row(tag, old, new, ms=None):
    ms = int(time.time() * 1000) if ms is None else ms
    return {"timestamp_ms": ms, "timestamp_human": "x", "event_type": "PLC_CHANGED",
            "source": "PLC", "tag_or_addr": tag, "old_value": str(old), "new_value": str(new),
            "raw_hex": "", "notes": "", "window_ms": ""}


@pytest.fixture
def page(isolated_data):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.core.settings import Settings
    from app.ui.session import Session
    from app.ui.window import MainWindow
    session = Session()
    # History in the live log: Speed for the last minute, a VIN.
    now = int(time.time() * 1000)
    for k in range(6):
        session.logger.log("PLC_CHANGED", "PLC", "Speed", k, k + 1, ts_ns=(now - 60_000 + k * 10_000)
                           * 1_000_000)
    session.logger.log("PLC_CHANGED", "PLC", "VIN", "A1", "A2", ts_ns=(now - 30_000) * 1_000_000)
    session.logger.flush()
    win = MainWindow(session, Settings(os.path.join(isolated_data, "ui.json")))
    win.resize(1400, 900)
    win.show()
    win.show_page("trend")
    tr = win.pages["trend"]
    tr.app = app
    tr.refresh_sources()
    assert wait(app, lambda: tr.tag_list.count() == 2)
    yield tr
    tr.live.setChecked(False)
    session.shutdown()
    win.close()
    win.deleteLater()
    session.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_live_follows_new_rows_and_now(page):
    app = page.app
    page.range.setCurrentIndex(page.range.findData(5))
    page.live.setChecked(True)
    assert wait(app, lambda: not page._loading and len(page._lines) == 1 and page._lanes)
    speed = page._lines[0]["series"]
    assert len(speed) >= 6
    # Rows the monitor logs now arrive through Session.events, not the file.
    page.session.events.emit([row("Speed", 6, 42), row("VIN", "A2", "A9"),
                              row("Other", 1, 2)])
    assert speed.value_at(time.time() + 1) == 42
    assert page._lanes[0]["series"].values[-1] == "A9"
    # The right edge keeps up with the clock and the view follows it.
    before = page.chart.right_edge
    time.sleep(0.6)
    assert wait(app, lambda: page.chart.right_edge > before + 0.4)
    lo, hi = page.chart.view_range()
    assert abs(hi - time.time()) < 1.5 and abs((hi - lo) - 300) < 1
    assert "following" in page.chart_status.text() or page.chart_status.text() == ""


def test_moving_the_chart_stops_following_and_home_resumes(page):
    app = page.app
    page.live.setChecked(True)
    assert wait(app, lambda: not page._loading and page._lines)
    lo, hi = page.chart.view_range()
    page.chart.plots[0].getViewBox().sigRangeChangedManually.emit([True, False])
    assert not page._following
    page.chart.show_range(lo - 100, hi - 100)
    time.sleep(0.6)
    app.processEvents()
    assert page.chart.view_range()[1] < time.time() - 50      # stayed where it was put
    page._nav("home")
    assert page._following
    assert abs(page.chart.view_range()[1] - time.time()) < 1.5


def test_live_turns_off_when_an_archive_is_picked(page, tmp_path):
    page.live.setChecked(True)
    archive = tmp_path / "old.csv"
    archive.write_text("timestamp_ms,event_type,tag_or_addr,old_value,new_value\n")
    page._extra_files.append(str(archive))
    page.refresh_sources()
    page.source.setCurrentIndex(page.source.findData(str(archive)))
    assert not page.live.isChecked()
