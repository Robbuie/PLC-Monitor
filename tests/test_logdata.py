from app.engine import logdata


def row(ms, event, tag="", old="", new="", source="SOCKET", notes=""):
    return {"timestamp_ms": str(ms), "event_type": event, "tag_or_addr": tag,
            "old_value": old, "new_value": new, "source": source, "notes": notes, "raw_hex": ""}


TRIG = logdata.Matcher(kind="plc", tag="Go", value="True")
RESP = logdata.Matcher(kind="socket", events=("SOCK_RECV",))


def test_pairs_each_trigger_with_the_next_response():
    rows = [row(1000, "PLC_CHANGED", "Go", "False", "True"),
            row(1040, "SOCK_RECV"),
            row(2000, "PLC_CHANGED", "Go", "False", "True"),
            row(2075, "SOCK_RECV")]
    result = logdata.measure(rows, TRIG, RESP)
    assert result.answered == [40, 75]
    assert result.unanswered == 0
    assert result.stats()["max"] == 75


def test_a_response_after_the_next_trigger_belongs_to_that_trigger():
    rows = [row(1000, "PLC_CHANGED", "Go", "False", "True"),
            row(1100, "PLC_CHANGED", "Go", "False", "True"),
            row(1130, "SOCK_RECV")]
    result = logdata.measure(rows, TRIG, RESP)
    assert [p.delta_ms for p in result.pairs] == [None, 30]


def test_a_response_outside_the_window_leaves_the_trigger_unanswered():
    rows = [row(1000, "PLC_CHANGED", "Go", "False", "True"), row(9000, "SOCK_RECV")]
    result = logdata.measure(rows, TRIG, RESP, window_ms=5000)
    assert result.unanswered == 1 and result.answered == []


def test_value_filter_and_other_tags_are_ignored():
    rows = [row(1000, "PLC_CHANGED", "Go", "True", "False"),
            row(1010, "PLC_CHANGED", "Other", "0", "1"),
            row(1020, "SOCK_RECV")]
    assert logdata.measure(rows, TRIG, RESP).pairs == []


def test_payload_filter():
    resp = logdata.Matcher(kind="socket", events=("SOCK_RECV",), contains="ack")
    rows = [row(1000, "PLC_CHANGED", "Go", "False", "True"),
            row(1010, "SOCK_RECV", notes="hello"), row(1030, "SOCK_RECV", notes="ACK 1")]
    assert logdata.measure(rows, TRIG, resp).answered == [30]


def test_percentile_matches_linear_interpolation():
    assert logdata.percentile([1, 2, 3, 4], 50) == 2.5
    assert logdata.percentile([10], 95) == 10


def test_series_keeps_the_step():
    rows = [row(1000, "PLC_CHANGED", "Speed", "1", "5")]
    times, values = logdata.series(rows, ["Speed"])["Speed"]
    assert values == [1.0, 5.0] and times[0] == times[1]


def test_numeric_tags_counts_booleans_when_asked():
    rows = [row(1, "PLC_CHANGED", "Bit", "False", "True"), row(2, "PLC_CHANGED", "Name", "a", "b")]
    assert logdata.numeric_tags(rows) == ["Bit"]
    assert logdata.numeric_tags(rows, include_bool=False) == []


def test_reads_a_half_written_last_line(tmp_path):
    path = tmp_path / "log.csv"
    path.write_text("timestamp_ms,event_type\n1,INFO\n2,IN", encoding="utf-8")
    assert len(list(logdata.read_rows(str(path)))) == 2
