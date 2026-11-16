"""Trend past Rockwell: event markers, math pens, flags, compare, cycles."""

import os
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

T0 = 1_800_000_000.0


def settle(app, seconds=0.1):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def wait(app, cond, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline and not cond():
        app.processEvents()
        time.sleep(0.02)
    return cond()


@pytest.fixture
def page(isolated_data):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.core.settings import Settings
    from app.ui.session import Session
    from app.ui.window import MainWindow
    session = Session()
    win = MainWindow(session, Settings(os.path.join(isolated_data, "ui.json")))
    win.resize(1400, 900)
    win.show()
    win.show_page("trend")
    tr = win.pages["trend"]
    tr.app = app
    tr.window = win
    tr.range.setCurrentIndex(tr.range.findData(None))
    yield tr
    tr.live.setChecked(False)
    session.shutdown()
    win.close()
    win.deleteLater()
    session.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_events_strip_and_a_click_opens_the_row(page):
    from app.engine import trendmath
    from app.engine.trendview import StepSeries
    rows = [{"timestamp_ms": str(int((T0 + 5) * 1000)), "event_type": "SOCK_RECV",
             "tag_or_addr": "10.0.0.5:9000", "notes": "ACK"},
            {"timestamp_ms": str(int((T0 + 7) * 1000)), "event_type": "SOCK_ERROR",
             "tag_or_addr": "", "notes": "reset"}]
    events = [trendmath.event_of(r) for r in rows]
    data = {"Speed": StepSeries([T0, T0 + 10], [1, 2]), "__events__": events}
    page._render(data, ["Speed"], "test")
    settle(page.app)
    chart = page.chart
    assert chart.events is not None and chart.events.count() == 2
    assert chart.plots[-1]._kind == "events"
    text = chart.readout_text(T0 + 5, chart.plots[-1])
    assert "10.0.0.5:9000" in text and "ACK" in text
    seen = []
    page.session.showInLog.connect(lambda path, ms, tag, value: seen.append((ms, tag)))
    chart.eventClicked.emit(events[0])
    assert seen == [(int((T0 + 5) * 1000), "10.0.0.5:9000")]
    # Cursors snap to events too.
    chart.show_range(T0, T0 + 20, record=False)
    page.cursor_btn.setChecked(True)
    chart.place_cursor("A", T0 + 7.05)
    assert chart.cursors["A"] == pytest.approx(T0 + 7)


def test_math_pen_flags_and_stepping_through_them(page):
    from app.engine import trendprefs
    from app.engine.trendview import StepSeries
    cfg = page.session.cfg.as_dict()
    trendprefs.save_math(cfg, "Gap", {"op": "diff", "a": "A", "b": "B"})
    trendprefs.set_pen(cfg, "Gap", {"high": 5})
    a = StepSeries([T0, T0 + 10, T0 + 20, T0 + 30], [10, 20, 10, 20])
    b = StepSeries([T0], [10])
    data = {"A": a, "B": b, "__math__": trendprefs.math_pens(cfg)}
    page._render(data, ["Gap"], "test")
    settle(page.app)
    line = page._lines[0]
    assert line["tag"] == "Gap" and line["series"].value_at(T0 + 15) == 10
    assert [(f.kind, f.start) for f in line["flags"]] == [("high", T0 + 10), ("high", T0 + 30)]
    page.chart.show_range(T0, T0 + 5, record=False)
    page._next_flag("Gap")
    lo, hi = page.chart.view_range()
    assert lo <= T0 + 10 <= hi
    assert "flag 1 of 2" in page.chart_status.text()
    from app.ui.trendpens import FLAGS
    assert page.pen_table.item(0, FLAGS).text() in ("1", "2")


def test_math_pen_dialog_saves_into_the_list(page, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    from app.engine import trendprefs
    from app.ui.pages.trend import KIND_ROLE, TAG_ROLE
    for tag in ("A", "B"):
        item = QListWidgetItem(tag)
        item.setData(TAG_ROLE, tag)
        item.setData(KIND_ROLE, "number")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Unchecked)
        page.tag_list.addItem(item)
    from app.ui import trendtools
    monkeypatch.setattr(trendtools.MathDialog, "exec", lambda self: (
        self.name.setText("Gap"), self.a.setCurrentText("A"), self.b.setCurrentText("B"),
        True)[-1])
    page._new_math()
    assert "Gap" in trendprefs.math_pens(page.session.cfg.as_dict())
    kinds = page._kinds()
    assert kinds["Gap"] == "math" and "Gap" in page._selected()


def test_compare_draws_yesterday_dashed(page, tmp_path):
    log = page.session.logger
    now = time.time()
    # Yesterday is in another file (the logger would rotate on a row stamped
    # yesterday): an archive, as it would be.
    yesterday = tmp_path / "yesterday.csv"
    lines = ["timestamp_ms,timestamp_human,event_type,source,tag_or_addr,old_value,new_value"]
    for k in range(5):
        log.log("PLC_CHANGED", "PLC", "Speed", k, k + 1, ts_ns=int((now - 3600 + k * 600) * 1e9))
        ms = int((now - 86400 - 3600 + k * 600) * 1000)
        lines.append(f"{ms},x,PLC_CHANGED,PLC,Speed,{100 + k},{101 + k}")
    yesterday.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.flush()
    page.refresh_sources()
    assert wait(page.app, lambda: page.tag_list.count() == 1)
    page.range.setCurrentIndex(page.range.findData(240))    # the last four hours
    page.plot()
    assert wait(page.app, lambda: not page._loading and page._lines)
    page._compare = (str(yesterday), 86400.0, "a day earlier")
    page._load_compare()
    assert wait(page.app, lambda: page._lines[0].get("ghost") is not None)
    ghost = page._lines[0]["ghost"]
    assert ghost.value_at(now - 3600 + 1) == 101             # yesterday's value, shifted
    assert "a day earlier" in page.chart.readout_text(now - 3600 + 1, page.chart.plots[0])
    assert "compared with a day earlier" in page.chart_status.text()


def test_cycles_view_finds_the_late_cycle_and_jumps_to_it(page):
    from app.engine.trendview import StepSeries
    bt, bv, vt, vv = [], [], [], []
    for k in range(12):
        start = T0 + k * 100
        late = 3.0 if k == 7 else 1.0
        bt += [start, start + 5]
        bv += [1, 0]
        vt += [start - 1, start + late, start + 10]
        vv += [0, 100, 0]
    data = {"Cycle": StepSeries(bt, bv), "Value": StepSeries(vt, vv)}
    page._render(data, ["Cycle", "Value"], "test")
    settle(page.app)
    page.view.set_value("cycles")
    page._view_changed("cycles")
    assert page.views.currentIndex() == 1 and not page.cursor_btn.isVisible()
    cv = page.cycle_view
    cv.trigger.setCurrentIndex(cv.trigger.findText("Cycle"))
    cv.edge.setCurrentIndex(cv.edge.findData("rising"))
    cv.before.setValue(1)
    cv.after.setValue(8)
    cv.run()
    assert cv.result is not None and len(cv.result) == 11       # the first rise is the start
    worst = cv.table.item(0, 0)
    from PySide6.QtCore import Qt
    assert cv.result.triggers[worst.data(Qt.UserRole)] == pytest.approx(T0 + 700)
    cv._jump_picked()
    assert page.views.currentIndex() == 0
    lo, hi = page.chart.view_range()
    assert lo < T0 + 700 < hi and hi - lo < 20


def test_live_events_and_math_keep_up(page):
    from app.engine import trendprefs
    page.session.logger.log("PLC_CHANGED", "PLC", "A", 0, 10)
    page.session.logger.log("PLC_CHANGED", "PLC", "B", 0, 4)
    page.session.logger.flush()
    trendprefs.save_math(page.session.cfg.as_dict(), "Gap", {"op": "diff", "a": "A", "b": "B"})
    page.refresh_sources()
    assert wait(page.app, lambda: page.tag_list.count() == 3)
    from PySide6.QtCore import Qt
    for i in range(page.tag_list.count()):
        item = page.tag_list.item(i)
        item.setCheckState(Qt.Checked if item.text().startswith("Gap") else Qt.Unchecked)
    page.show_events.setChecked(True)
    page.range.setCurrentIndex(page.range.findData(5))
    page.live.setChecked(True)
    assert wait(page.app, lambda: not page._loading and page._lines)
    gap = page._lines[0]
    assert gap["series"].value_at(time.time() + 1) == 6
    ms = int(time.time() * 1000)
    page.session.events.emit([
        {"timestamp_ms": ms, "event_type": "PLC_CHANGED", "tag_or_addr": "A",
         "old_value": "10", "new_value": "30"},
        {"timestamp_ms": ms, "event_type": "SOCK_RECV", "tag_or_addr": "x", "notes": "hi"}])
    assert page.chart.events.count() == 1
    assert wait(page.app, lambda: gap["series"].value_at(time.time() + 1) == 26, 5)
