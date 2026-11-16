"""Math pens, flags, triggers and cycles, and events for the Trend chart."""

import math

import numpy as np
import pytest

from app.engine import logdata, trendmath
from app.engine.trendview import StepSeries, TextSeries


def test_difference_steps_wherever_either_source_does():
    a = StepSeries([0, 10], [5, 7])
    b = StepSeries([5, 15], [1, 4])
    d = trendmath.combine(a, b, "diff")
    assert d.value_at(2) != d.value_at(2)          # NaN: B has no value yet
    assert d.value_at(6) == 4 and d.value_at(12) == 6 and d.value_at(20) == 3


def test_ratio_by_zero_is_a_gap():
    r = trendmath.combine(StepSeries([0], [5]), StepSeries([0, 10], [0, 2]), "ratio")
    assert math.isnan(r.value_at(5)) and r.value_at(11) == 2.5


def test_rate_of_a_counter_per_minute():
    # A counter going up by one every 6 s: 10 a minute.
    t = np.arange(0, 600, 6.0)
    c = StepSeries(t, np.arange(len(t), dtype=float))
    r = trendmath.rate(c, window_s=60, per_s=60)
    assert r.value_at(300) == pytest.approx(10)
    assert r.value_at(30) != r.value_at(30)        # the first minute has no "then"


def test_how_long_each_state_lasted():
    ready = StepSeries([0, 10, 10.25, 30, 31], [0, 1, 0, 1, 0])
    every = trendmath.state_durations(ready)
    assert every.value_at(10.3) == pytest.approx(0.25)
    on = trendmath.state_durations(ready, of_value=1)
    assert list(on.arrays()[1]) == pytest.approx([0.25, 1.0])


def test_compute_math_from_a_spec():
    src = {"A": StepSeries([0], [10]), "B": StepSeries([0], [4])}
    assert trendmath.compute_math({"op": "diff", "a": "A", "b": "B"}, src).value_at(1) == 6
    assert trendmath.compute_math({"op": "diff", "a": "A", "b": "missing"}, src) is None
    assert "A - B" == trendmath.describe_math({"op": "diff", "a": "A", "b": "B"})


def test_flags_limits_stuck_and_counters():
    s = StepSeries([0, 10, 20, 30, 100], [5, 50, 60, 5, 6])
    found = trendmath.flags(s, {"high": 40, "low": None, "stuck_s": 30}, right=200)
    kinds = [(f.kind, f.start, f.end) for f in found]
    assert ("high", 10, 30) in kinds                 # two states over, one flag
    assert ("stuck", 60, 100) in kinds               # 5 held 70 s, flagged after 30
    assert ("stuck", 130, 200) in kinds              # still stuck at the right edge
    counter = StepSeries([0, 1, 2, 3, 4], [1, 2, 9, 10, 0])
    found = trendmath.flags(counter, {"jump": 1})
    assert [(f.kind, f.start) for f in found] == [("jump", 2), ("back", 4)]
    assert "missed pulses" in found[0].text


def test_triggers_on_edges_and_values():
    bit = StepSeries([0, 5, 5, 6, 9, 9], [0, 0, 1, 0, 0, 1])
    assert list(trendmath.trigger_times(bit, "rising")) == [5, 9]
    assert list(trendmath.trigger_times(bit, "falling")) == [6]
    assert list(trendmath.trigger_times(bit, "equals", "1")) == [5, 9]
    vin = TextSeries([0, 10, 20], ["A1", "A2", "A1"])
    assert list(trendmath.trigger_times(vin, "equals", "A1")) == [20]
    assert list(trendmath.trigger_times(vin, "any")) == [10, 20]


def test_cycles_overlay_and_the_odd_one_out():
    # Twelve cycles: a bit rises, and a value follows 1 s later. In one the
    # value is 3 s late.
    t, v, bt, bv = [], [], [], []
    for k in range(12):
        start = k * 100.0
        late = 3.0 if k == 7 else 1.0
        bt += [start, start + 5]
        bv += [1, 0]
        t += [start - 1, start + late, start + 10]
        v += [0, 100, 0]
    bit = StepSeries(bt, bv)
    val = StepSeries(t, v)
    triggers = trendmath.trigger_times(bit, "rising")
    assert len(triggers) == 11                       # the first rise is the start value
    triggers = np.concatenate(([0.0], triggers))
    cyc = trendmath.cycles(triggers, {"Value": val}, before=1, after=8, points=200)
    assert cyc.samples["Value"].shape == (12, 200)
    assert cyc.ranked()[0] == 7
    assert cyc.deviation[7] > 5 * np.median(cyc.deviation)
    # The envelope's median is the usual shape: 100 from 1 s on.
    mid = cyc.mid["Value"]
    assert mid[np.searchsorted(cyc.grid, 2.0)] == 100


def test_events_and_latency_pairs_as_markers():
    rows = [
        {"timestamp_ms": "1000", "event_type": "SOCK_RECV", "tag_or_addr": "10.0.0.5:9000",
         "notes": "ACK 42"},
        {"timestamp_ms": "1500", "event_type": "PLC_CHANGED", "tag_or_addr": "Ready",
         "old_value": "False", "new_value": "True"},
        {"timestamp_ms": "1712", "event_type": "SOCK_SENT", "tag_or_addr": "10.0.0.5:9000",
         "notes": "GO"},
        {"timestamp_ms": "1800", "event_type": "SOCK_ERROR", "tag_or_addr": "", "notes": "reset"},
    ]
    events = [e for e in map(trendmath.event_of, rows) if e]
    assert [(e.t, e.lane) for e in events] == [(1.0, "recv"), (1.712, "sent"), (1.8, "conn")]
    assert events[2].error and "reset" in events[2].text
    result = logdata.measure(rows, logdata.Matcher("plc", "Ready", "True"),
                             logdata.Matcher("socket", events=("SOCK_SENT",)))
    pairs = trendmath.pair_events(result)
    assert len(pairs) == 1 and pairs[0].end == pytest.approx(1.712)
    assert pairs[0].text.startswith("212 ms")


def test_rules_and_math_pens_in_the_profile():
    from app.engine import trendprefs
    cfg = {}
    trendprefs.set_pen(cfg, "Temp", {"high": "80", "low": "", "stuck_s": "-5"})
    pen = trendprefs.pen(cfg, "Temp")
    assert pen["high"] == 80 and pen["low"] is None and pen["stuck_s"] is None
    assert trendprefs.rules(pen) == {"high": 80}
    name = trendprefs.save_math(cfg, " Gap ", {"op": "diff", "a": "A", "b": "B"})
    assert trendprefs.math_pens(cfg)[name]["op"] == "diff"
    with pytest.raises(ValueError):
        trendprefs.save_math(cfg, "x", {"op": "rate"})
    assert trendprefs.delete_math(cfg, "Gap") and not trendprefs.math_pens(cfg)


def test_soft_theme_tokens_read_as_colours():
    pytest.importorskip("pyqtgraph")
    from app.ui.trendchart import colour
    c = colour("down_soft")
    assert c.isValid() and c.red() > 200 and 0.1 < c.alphaF() < 0.5


def test_reading_settings_never_changes_the_config():
    """The window compares the config with a snapshot to see unsaved edits:
    a read that added an empty "trend" block asked to save on every switch."""
    from app.engine import trendprefs
    cfg = {"plc": {}}
    trendprefs.pen(cfg, "X")
    trendprefs.layouts(cfg)
    trendprefs.math_pens(cfg)
    trendprefs.set_pen(cfg, "X", {})
    trendprefs.delete_math(cfg, "nope")
    assert cfg == {"plc": {}}
