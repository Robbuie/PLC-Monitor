"""Text tags on Trend, Find on Trend and in the Live log, opening a log file
in the Live log, and the number inside a VIN."""

import math
import os
import time

import pytest

from app.engine import logdata
from app.engine.tagspec import TagSpec, text_to_number

A1 = [12609, 13106, 13620, 14134]          # "A1234567", low byte first
A3 = [12609, 13106, 13620, 14646]          # "A1234569"
UNKNOWN = [21808, 19278, 20302, 20055]     # "0UNKNOWN"


def row(ms, tag, old, new, event="PLC_CHANGED"):
    return {"timestamp_ms": str(ms), "timestamp_human": "", "event_type": event,
            "tag_or_addr": tag, "old_value": str(old), "new_value": str(new), "source": "M",
            "notes": "", "raw_hex": "", "window_ms": ""}


# ------------------------------------------------------------- engine


def test_the_number_in_a_vin():
    assert text_to_number("A1234567") == 1234567
    assert text_to_number("B7654321 x") == 7654321
    assert text_to_number("0UNKNOWN") == ""          # no run of two digits: no value
    assert text_to_number("") == ""
    assert TagSpec("D1916", 4, "digits").decode(A1) == 1234567
    assert TagSpec("D1916", 4, "digits").decode(UNKNOWN) == ""


def test_trend_tags_says_which_are_text():
    rows = [row(1, "Count", 1, 2), row(2, "VIN", "", "A1234567"), row(3, "Bit", "False", "True"),
            row(4, "VIN no.", "", "1234567"), row(5, "VIN no.", "1234567", "")]
    assert logdata.trend_tags(rows) == [("Bit", "number"), ("Count", "number"),
                                        ("VIN", "text"), ("VIN no.", "number")]


def test_text_series_keeps_the_value_before_the_first_change():
    rows = [row(1000, "VIN", "0UNKNOWN", "A1234567"), row(5000, "VIN", "A1234567", "0UNKNOWN"),
            row(2000, "Other", "a", "b")]
    spans = logdata.text_series(rows, ["VIN"])["VIN"]
    assert [v for _t, v in spans] == ["0UNKNOWN", "A1234567", "0UNKNOWN"]
    assert spans[0][0] == spans[1][0]


def test_a_blank_number_is_a_gap_in_the_line():
    rows = [row(1000, "N", "", "1234567"), row(2000, "N", "1234567", ""),
            row(3000, "N", "", "1234569")]
    times, values = logdata.series(rows, ["N"])["N"]
    assert values[0] == 1234567 and math.isnan(values[2]) and values[-1] == 1234569


def test_two_groups_can_share_registers():
    rows = [row(1000 + i, f"D{1916 + i}", 0, w) for i, w in enumerate(A1)]
    specs = [TagSpec("D1916", 4, "ascii", name="VIN"), TagSpec("D1916", 4, "digits", name="No.")]
    out = {r["tag_or_addr"]: r["new_value"] for r in logdata.with_groups(rows, specs)}
    assert out == {"VIN": "A1234567", "No.": "1234567"}


# ----------------------------------------------------------------- UI

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def window(isolated_data, tmp_path):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.core.settings import Settings
    from app.engine import profiles
    from app.engine.config_manager import ConfigManager
    from app.ui.session import Session
    from app.ui.window import MainWindow
    p = profiles.create("Line")
    cfg = ConfigManager(p.path)
    cfg.set("plc", "type", "Mitsubishi (MC Proto)")
    cfg.set("plc", "tags_per_driver", {"Mitsubishi (MC Proto)": [
        {"tag": "D1916", "count": 4, "format": "ascii", "name": "Plant VIN"},
        {"tag": "D1916", "count": 4, "format": "digits", "name": "Plant VIN no."}]})
    cfg.save()
    # A log from before the group existed: a row per register.
    lines = ["timestamp_ms,timestamp_human,event_type,source,tag_or_addr,old_value,new_value,raw_hex,notes"]
    base = 1_800_000_000_000
    prev = [0] * 4
    for k, words in enumerate([UNKNOWN, A1, UNKNOWN, A3, UNKNOWN]):
        ms = base + k * 60_000
        for i, (a, b) in enumerate(zip(prev, words)):
            if a != b:
                lines.append(f"{ms + i},x,PLC_CHANGED,M,D{1916 + i},{a},{b},,")
        lines.append(f"{ms + 500},x,PLC_CHANGED,M,CN13,{k},{k + 1},,")
        prev = words
    log = tmp_path / "old.csv"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    session = Session(p.path)
    win = MainWindow(session, Settings(os.path.join(isolated_data, "ui.json")))
    win.resize(1400, 900)
    win.show()
    win.app, win.log_file = app, str(log)
    yield win
    session.shutdown()
    win.close()
    win.deleteLater()
    session.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def wait(app, cond, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline and not cond():
        app.processEvents()
        time.sleep(0.02)
    return cond()


def test_trend_draws_text_tags_and_finds_a_vehicle(window):
    from PySide6.QtCore import Qt
    from app.ui.pages.trend import KIND_ROLE, TAG_ROLE
    app, tr = window.app, window.pages["trend"]
    window.show_page("trend")
    tr._extra_files.append(window.log_file)
    tr.refresh_sources()
    tr.source.setCurrentIndex(tr.source.findData(window.log_file))
    assert wait(app, lambda: tr.tag_list.count() == 3)
    kinds = {tr.tag_list.item(i).data(TAG_ROLE): tr.tag_list.item(i).data(KIND_ROLE)
             for i in range(3)}
    assert kinds == {"CN13": "number", "Plant VIN": "text", "Plant VIN no.": "number"}
    tr.range.setCurrentIndex(tr.range.findData(None))
    for i in range(3):
        tr.tag_list.item(i).setCheckState(Qt.Checked)
    tr.plot()
    assert wait(app, lambda: not tr._loading and tr._lanes)
    lane = tr._lanes[0]
    assert [v for _b, _f, v in lane["segments"]] == ["0UNKNOWN", "A1234567", "0UNKNOWN",
                                                     "A1234569", "0UNKNOWN"]
    tr.finder.setText("1234569")
    app.processEvents()
    assert {(tag, str(v)) for _w, tag, v in tr._matches} == {
        ("Plant VIN", "A1234569"), ("Plant VIN no.", "1234569")}
    tr._step(1)
    assert tr.find_log.isEnabled() and "A1234569" in tr.chart_status.text() or \
        "1234569" in tr.chart_status.text()
    shown = [t.get_text() for t in lane["labels"]]
    assert "A1234569" in shown                          # labelled once zoomed in

    # Show in log: the Live log opens the same file, at that change.
    tr._match_at = next(i for i, m in enumerate(tr._matches) if m[1] == "Plant VIN") - 1
    tr._step(1)
    tr._show_in_log()
    lg = window.pages["log"]
    assert wait(app, lambda: lg.viewing == window.log_file and lg._found_at >= 0)
    selected = lg.table.selectionModel().selectedRows()
    assert len(selected) == 1
    picked = lg.model.rows[lg.proxy.mapToSource(selected[0]).row()]
    assert (picked["tag_or_addr"], picked["new_value"]) == ("Plant VIN", "A1234569")
    assert window.stack.currentWidget() is lg


def test_the_live_log_opens_a_file_and_finds_with_context(window):
    app, lg = window.app, window.pages["log"]
    window.show_page("log")
    lg.view_file(window.log_file)
    assert wait(app, lambda: lg.viewing == window.log_file)
    tags = [r["tag_or_addr"] for r in lg.model.rows]
    assert "Plant VIN" in tags and "D1916" not in tags     # decoded, registers folded
    assert lg.banner.isVisible() and "old.csv" in lg.banner.text.text()
    total = lg.proxy.rowCount()
    lg.finder.setText("1234567")
    assert "2 matches" in lg.find_count.text() or lg.find_count.text().startswith("4 ")
    lg._step(1)
    assert lg.proxy.rowCount() == total                     # Find hides nothing
    assert lg.find_count.text().startswith("1 of")
    first = lg.model.rows[lg._found[0]]
    assert "1234567" in first["new_value"] + first["old_value"]
    # Filter narrows; Find then looks only in what is shown.
    lg.kind.setCurrentIndex(lg.kind.findData("ERR"))
    assert lg.find_count.text() == "No match"
    lg.kind.setCurrentIndex(0)
    lg.back_to_live()
    assert wait(app, lambda: lg.viewing is None and not lg.banner.isVisible())
