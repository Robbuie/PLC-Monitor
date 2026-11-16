"""Trend's measuring tools: cursors that snap to changes, the value bar,
lanes for bits, fixed and shared scales, view history, saved layouts and the
CSV export."""

import csv
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
    tr.range.setCurrentIndex(tr.range.findData(None))
    yield tr
    session.shutdown()
    win.close()
    win.deleteLater()
    session.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def data():
    from app.engine.trendview import StepSeries, TextSeries
    # Speed steps up at +10 s and +40 s; Ready (a bit) rises at +20 s and
    # falls at +20.212 s -- a 212 ms pulse.
    speed = StepSeries([T0, T0 + 10, T0 + 10, T0 + 40, T0 + 40], [5, 5, 50, 50, 20])
    ready = StepSeries([T0, T0 + 20, T0 + 20.212], [0, 1, 0])
    vin = TextSeries([T0, T0 + 30], ["A1", "A2"])
    return {"Speed": speed, "Ready": ready, "__text__": {"VIN": vin}}


def draw(page, arrange="stacked"):
    page.arrange.set_value(arrange)
    page._render(data(), ["Speed", "Ready", "VIN"], "test")
    page.chart.show_range(T0, T0 + 60, record=False)
    settle(page.app)


def test_bits_are_lanes_and_numbers_are_charts(page):
    draw(page)
    kinds = {line["tag"]: line["lane"] for line in page._lines}
    assert kinds == {"Speed": False, "Ready": True}
    assert [p._kind for p in page.chart.plots] == ["chart", "bit", "text"]


def test_cursors_snap_to_edges_and_measure_between_them(page):
    draw(page)
    page.cursor_btn.setChecked(True)
    assert page.chart.cursors is not None
    chart = page.chart
    # Dropped near the rising and falling edges of Ready, they land on them.
    chart.place_cursor("A", T0 + 20.05)
    chart.place_cursor("B", T0 + 20.3)
    assert chart.cursors["A"] == pytest.approx(T0 + 20)
    assert chart.cursors["B"] == pytest.approx(T0 + 20.212)
    assert "212 ms" in page.cursor_info.text()
    page._update_stats()
    table = page.pen_table
    rows = {table.pens[r]["tag"]: r for r in range(table.rowCount())}
    from app.ui.trendpens import A, B, CHANGES, DELTA
    assert table.item(rows["Speed"], A).text() == "50"
    assert table.item(rows["Ready"], A).text() == "1"
    assert table.item(rows["Ready"], B).text() == "0"
    assert table.item(rows["Ready"], DELTA).text() == "-1"
    assert table.item(rows["Ready"], CHANGES).text() == "1"
    # Dragging a cursor's line on any chart snaps it and moves it on all.
    which, line, _plot = next(c for c in chart._cursor_items if c[0] == "B" and c[2] is
                              chart.plots[-1])
    line.setValue(T0 + 30.1)
    assert chart.cursors["B"] == pytest.approx(T0 + 30)           # VIN's change
    assert all(item.value() == pytest.approx(T0 + 30)
               for w, item, _p in chart._cursor_items if w == "B")
    # A click moves the nearer cursor.
    chart.place_cursor("A", T0 + 1, snap=False)
    assert chart.cursors["A"] == pytest.approx(T0 + 1)
    page.cursor_btn.setChecked(False)
    assert chart.cursors is None and table.isColumnHidden(A)


def test_value_bar_reads_the_pointer_and_the_view(page):
    draw(page)
    page._hovered(T0 + 15)
    from app.ui.trendpens import AVG, MAX, MIN, POINTER
    table = page.pen_table
    assert table.horizontalHeaderItem(POINTER).text() == "At pointer"
    assert table.item(0, POINTER).text() == "50"
    page._hovered(None)
    assert table.horizontalHeaderItem(POINTER).text() == "Latest"
    page.chart.show_range(T0, T0 + 20, record=False)
    page._update_stats()
    assert table.item(0, MIN).text() == "5" and table.item(0, MAX).text() == "50"
    assert float(table.item(0, AVG).text()) == pytest.approx(27.5)


def test_same_scale_and_fixed_scale(page):
    from app.engine import trendprefs
    from app.engine.trendview import StepSeries
    page.arrange.set_value("same")
    big = StepSeries([T0, T0 + 30], [100, 900])
    small = StepSeries([T0, T0 + 30], [1, 3])
    page._render({"Big": big, "Small": small}, ["Big", "Small"], "test")
    settle(page.app)
    ranges = [p.getViewBox().viewRange()[1] for p in page.chart.plots]
    assert ranges[0] == pytest.approx(ranges[1])
    assert ranges[1][1] > 800                     # Small drawn on Big's scale
    trendprefs.set_pen(page.session.cfg.as_dict(), "Small", {"scale": [0, 10]})
    page._rerender()
    settle(page.app)
    low, high = page.chart.plots[1].getViewBox().viewRange()[1]
    assert low < 0.5 and 9.5 < high < 11


def test_back_and_forward_step_through_views(page):
    draw(page)
    chart = page.chart
    first = chart.view_range()
    chart.show_range(T0 + 10, T0 + 20)            # a person's step (Find, Home)
    second = chart.view_range()
    assert page.view_back.isEnabled()
    page._nav("back")
    assert chart.view_range() == pytest.approx(first)
    assert page.view_forward.isEnabled()
    page._nav("forward")
    assert chart.view_range() == pytest.approx(second)
    # A drag settles into one step.
    chart.plots[0].getViewBox().setXRange(T0 + 30, T0 + 35, padding=0)
    chart.plots[0].getViewBox().setXRange(T0 + 31, T0 + 36, padding=0)
    settle(page.app, 0.7)
    page._nav("back")
    assert chart.view_range() == pytest.approx(second)


def test_pen_settings_show_on_the_chart_and_table(page):
    from app.engine import trendprefs
    trendprefs.set_pen(page.session.cfg.as_dict(), "Speed", {"units": "mm/s",
                                                               "description": "Conveyor 3"})
    trendprefs.set_pen(page.session.cfg.as_dict(), "Ready", {"draw": "line"})
    draw(page)
    assert {line["tag"]: line["lane"] for line in page._lines}["Ready"] is False
    assert page.pen_table.item(0, 0).text() == "Speed  (mm/s)"
    assert "mm/s" in page.chart.readout_text(T0 + 15, page.chart.plots[0])


def test_layouts_save_and_load(page, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    from app.engine import trendprefs
    from app.ui.pages.trend import KIND_ROLE, TAG_ROLE
    for tag in ("Speed", "Ready", "Other"):
        item = QListWidgetItem(tag)
        item.setData(TAG_ROLE, tag)
        item.setData(KIND_ROLE, "number")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if tag != "Other" else Qt.Unchecked)
        page.tag_list.addItem(item)
    page.arrange.set_value("shared")
    page.cursor_btn.setChecked(True)
    from app.ui.pages import trend as trend_module
    monkeypatch.setattr(trend_module.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Press  handshake", True)))
    page._save_layout()
    from app.engine.config_manager import ConfigManager
    saved = trendprefs.layouts(ConfigManager(page.session.cfg.path).as_dict())
    assert saved["Press handshake"]["tags"] == ["Speed", "Ready"]
    assert saved["Press handshake"]["arrange"] == "shared"
    # Change everything, then load it back.
    for i in range(3):
        page.tag_list.item(i).setCheckState(Qt.Checked if i == 2 else Qt.Unchecked)
    page.arrange.set_value("stacked")
    page.cursor_btn.setChecked(False)
    monkeypatch.setattr(page, "plot", lambda: None)
    page.load_layout("Press handshake")
    assert page._selected() == ["Speed", "Ready"]
    assert page.arrange.value() == "shared" and page.cursor_btn.isChecked()


def test_csv_export_of_what_is_in_view(page, tmp_path):
    draw(page)
    out = tmp_path / "view.csv"
    count = page.write_csv(str(out), T0 + 15, T0 + 35)
    with open(out, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert count == len(rows)
    held = [r for r in rows if r["note"]]
    assert {r["tag"] for r in held} == {"Speed", "Ready", "VIN"}
    changes = [(r["tag"], r["value"]) for r in rows if not r["note"]]
    assert changes == [("Ready", "1"), ("Ready", "0"), ("VIN", "A2")]
    assert rows[0]["timestamp_human"].endswith(".000")
