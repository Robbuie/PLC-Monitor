"""The Trend chart's drawing maths: steps, min/max decimation, text spans."""

import math
from datetime import datetime

import numpy as np

from app.engine import logdata, trendview
from app.engine.trendview import StepSeries, TextSeries


def test_few_changes_draw_as_steps_to_the_right_edge():
    s = StepSeries([10, 20, 30], [1, 5, 2])
    xs, ys = s.view(0, 100, buckets=500, right=40)
    assert list(xs) == [10, 20, 20, 30, 30, 40]
    assert list(ys) == [1, 1, 5, 5, 2, 2]


def test_the_value_in_force_at_the_left_edge_is_drawn():
    s = StepSeries([10, 20, 30], [1, 5, 2])
    xs, ys = s.view(25, 100, buckets=500, right=40)
    assert xs[0] == 20 and ys[0] == 5          # 5 has held since 20


def test_a_one_ms_spike_survives_decimation():
    # Eight hours at 10 changes a second, flat at 0 except one 1 ms spike.
    n = 8 * 3600 * 10
    t = np.arange(n) / 10.0
    v = np.zeros(n)
    spike_at = n // 3
    t = np.insert(t, spike_at + 1, t[spike_at] + 0.001)
    v = np.insert(v, spike_at + 1, 0.0)
    v[spike_at] = 99.0
    s = StepSeries(t, v)
    xs, ys = s.view(0, t[-1], buckets=1000, right=t[-1])
    assert len(xs) <= 4 * 1000 + 1               # bounded by the pixels, not the data
    assert np.nanmax(ys) == 99.0
    # And it is drawn at the right moment, within one bucket.
    where = xs[np.nanargmax(ys)]
    assert abs(where - t[spike_at]) <= t[-1] / 1000


def test_decimated_strokes_cover_the_held_value_too():
    # A bucket with one change from 7 to 3 must span 3..7, not just 3.
    t = np.concatenate(([0.0], np.linspace(1, 100, 5000)))
    v = np.concatenate(([7.0], np.full(5000, 3.0)))
    s = StepSeries(t, v)
    xs, ys = s.view(0.5, 100, buckets=10, right=100)
    first = ys[:4]
    assert min(first) == 3.0 and max(first) == 7.0


def test_gaps_stay_gaps_and_nothing_is_drawn_after_the_right_edge():
    s = StepSeries([0, 10, 20], [1, math.nan, 2])
    xs, ys = s.view(0, 30, buckets=100, right=25)
    assert math.isnan(ys[2]) and math.isnan(ys[3])
    assert xs[-1] == 25
    big = StepSeries(np.arange(10000.0), np.ones(10000))
    xs, ys = big.view(0, 20000, buckets=100, right=9999)
    assert np.all(np.isnan(ys[xs > 10000]))


def test_value_at_and_live_appends():
    s = StepSeries([10, 20], [1, 2])
    assert s.value_at(5) is None and s.value_at(10) == 1 and s.value_at(25) == 2
    assert not s.append(15, 9)                   # older than the last: already read
    assert not s.append(20, 2)                   # the same change again
    assert s.append(30, 4) and s.value_at(31) == 4
    assert len(s) == 3


def test_text_spans_from_logdata_and_live():
    rows = [
        {"event_type": "PLC_CHANGED", "tag_or_addr": "VIN", "timestamp_ms": "1000",
         "old_value": "A1", "new_value": "A2"},
        {"event_type": "PLC_CHANGED", "tag_or_addr": "VIN", "timestamp_ms": "3000",
         "old_value": "A2", "new_value": ""},
        {"event_type": "PLC_CHANGED", "tag_or_addr": "VIN", "timestamp_ms": "4000",
         "old_value": "", "new_value": "A3"},
    ]
    spans = logdata.text_series(rows, ["VIN"])["VIN"]
    series = TextSeries.from_spans(spans, left=0.0)
    segs = series.segments(right=10.0)
    assert [v for _b, _e, v in segs] == ["A1", "A2", "", "A3"]
    assert segs[0][0] == 0.0 and segs[-1][1] == 10.0
    assert series.at(2.0, right=10.0)[2] == "A2"
    assert series.append(5.0, "A4")
    assert [v for _b, _e, v in series.segments(x0=4.5, x1=9.0, right=10.0)] == ["A3", "A4"]


def test_number_matches_mark_each_arrival_once():
    s = StepSeries([1, 1, 2, 3, 4], [5, 3206300, 3206300, 7, 3206300])
    found = trendview.number_matches(s, "3206300")
    assert [t for t, _ in found] == [1, 4]
    assert found[0][1] == "3206300"


def test_datetimes_convert_to_epoch_seconds():
    when = datetime(2026, 11, 16, 8, 0, 0, 123000)
    s = StepSeries.from_datetimes([when], [1.0])
    assert abs(s.first_time - when.timestamp()) < 1e-6


def test_a_long_live_session_keeps_the_newest_changes(monkeypatch):
    monkeypatch.setattr(trendview, "MAX_POINTS", 1024)
    s = StepSeries()
    for i in range(5000):
        assert s.append(float(i), float(i))
    assert len(s) <= 1024
    assert s.last_time == 4999.0 and s.value_at(4999.5) == 4999.0
    t, _v = s.arrays()
    assert np.all(np.diff(t) > 0)


# ------------------------------------------------------------ Phase 1


def test_changes_skip_the_old_value_rows():
    # A log row draws the old value then the new at the same moment.
    s = StepSeries([0, 10, 10, 20, 20], [1, 1, 2, 2, 3])
    assert list(s.change_times()) == [0, 10, 20]
    assert s.nearest_change(10.4, within=0.5) == 10
    assert s.nearest_change(15, within=0.5) is None


def test_stats_are_time_weighted_and_count_changes_in_range():
    # 0 for 90 s, then 10 for 10 s: a plain mean of rows would say 5.
    s = StepSeries([0, 90], [0, 10])
    st = s.stats(0, 100, right=100)
    assert st["min"] == 0 and st["max"] == 10
    assert abs(st["avg"] - 1.0) < 1e-9
    assert st["changes"] == 1
    # Only part of the first level is in range: it counts from x0.
    st = s.stats(80, 100, right=100)
    assert abs(st["avg"] - 5.0) < 1e-9 and st["changes"] == 1
    # Nothing counts after the right edge.
    st = s.stats(0, 1000, right=100)
    assert abs(st["avg"] - 1.0) < 1e-9


def test_stats_ignore_gaps():
    s = StepSeries([0, 10, 20], [4, math.nan, 6])
    st = s.stats(0, 30, right=30)
    assert st["min"] == 4 and st["max"] == 6 and abs(st["avg"] - 5) < 1e-9


def test_boolean_detection():
    assert StepSeries([0, 1, 2], [0, 1, math.nan]).is_boolean()
    assert not StepSeries([0, 1], [0, 2]).is_boolean()
    assert not StepSeries().is_boolean()


def test_text_series_changes_and_snapping():
    t = TextSeries([0, 10, 20], ["A", "B", "C"])
    assert t.changes_between(5, 25) == 2
    assert t.nearest_change(9.8, within=0.5) == 10
    assert t.nearest_change(15, within=0.5) is None


def test_export_rows_rebuild_what_was_shown():
    nums = {"Speed": StepSeries([0, 5, 5, 15], [1, 1, 2, 3])}
    texts = {"VIN": TextSeries([0, 8], ["A1", "A2"])}
    rows = trendview.export_rows(nums, texts, 4, 12, right=20)
    assert rows[0][0] == 4000 and rows[0][3].startswith("held")
    body = [(ms, tag, value) for ms, tag, value, note in rows if not note]
    assert body == [(5000, "Speed", "2"), (8000, "VIN", "A2")]


def test_pen_settings_and_layouts_in_the_profile_config():
    from app.engine import trendprefs
    cfg = {"plc": {}}
    assert trendprefs.pen(cfg, "Speed") == trendprefs.DEFAULT_PEN
    trendprefs.set_pen(cfg, "Speed", {"units": " mm/s ", "scale": ["0", "1500"]})
    assert trendprefs.pen(cfg, "Speed")["units"] == "mm/s"
    assert trendprefs.pen(cfg, "Speed")["scale"] == [0.0, 1500.0]
    trendprefs.set_pen(cfg, "Speed", {"scale": [5, 1]})        # low >= high: automatic
    assert "Speed" not in cfg["trend"]["pens"]                 # all defaults: not stored
    name = trendprefs.save_layout(cfg, "  Press   handshake ", {
        "tags": ["A", "B"], "robots": ["R2 / R[1]"], "arrange": "nonsense", "range": "5"})
    assert name == "Press handshake"
    saved = trendprefs.layouts(cfg)[name]
    assert saved["arrange"] == "stacked" and saved["range"] == 5.0 and saved["tags"] == ["A", "B"]
    assert trendprefs.delete_layout(cfg, name) and not trendprefs.layouts(cfg)
