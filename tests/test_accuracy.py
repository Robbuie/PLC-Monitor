"""Timestamps: the clock, the logger, the poller's change window, batched
reads, the sniffer's capture stamps and retransmissions, and pairing rows
that were written out of time order."""

import csv
import glob
import os
import time

import pytest

from app.engine import clock, logdata
from app.engine.event_log import CSV_COLUMNS, EventLogger
from app.engine.plc_drivers import PLCDriver, RockwellDriver
from app.engine.plc_poller import PLCPoller


def rows_of(path):
    return list(logdata.read_rows(str(path)))


# ----------------------------------------------------------------- clock


def test_clock_agrees_with_the_system_clock():
    assert abs(clock.now_ns() - time.time_ns()) < 50_000_000
    assert clock.now_ms() == pytest.approx(time.time() * 1000, abs=50)


def test_precise_windows_clock_is_not_used_off_windows():
    if os.name != "nt":
        assert clock._precise_windows_clock() is None
        assert clock.SOURCE == "time.time_ns"


# ---------------------------------------------------------------- logger


def test_logger_uses_the_given_stamp_and_prints_the_right_millisecond(tmp_path):
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log))
    # .123 exactly -- the value float formatting tends to print as .122
    ns = 1_700_000_000_123_000_000
    logger.log("INFO", "T", ts_ns=ns, notes="x")
    row = rows_of(log)[0]
    assert row["timestamp_ms"] == "1700000000123"
    assert row["timestamp_human"].endswith(".123")
    assert row["window_ms"] == ""


def test_logger_writes_the_window_column(tmp_path):
    log = tmp_path / "log.csv"
    EventLogger(str(log)).log("PLC_CHANGED", "P", "Go", False, True, window_ms=98)
    assert rows_of(log)[0]["window_ms"] == "98"
    with open(log, newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == CSV_COLUMNS


def test_a_log_from_an_older_version_is_archived_not_appended_to(tmp_path):
    log = tmp_path / "plc_monitor_log.csv"
    old_cols = CSV_COLUMNS[:-1]
    log.write_text(",".join(old_cols) + "\n1,2024-01-01 00:00:00.001,INFO,a,,,,,old\n",
                   encoding="utf-8")
    logger = EventLogger(str(log))
    logger.log("INFO", "new", notes="fresh")
    with open(log, newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == CSV_COLUMNS
    assert [r["notes"] for r in rows_of(log)] == ["fresh"]
    # Compression runs on its own thread; it is done when the uncompressed
    # copy is gone.
    deadline = time.time() + 5
    while time.time() < deadline and (not glob.glob(str(tmp_path / "*.csv.gz"))
                                      or len(glob.glob(str(tmp_path / "*.csv"))) > 1):
        time.sleep(0.05)
    archives = glob.glob(str(tmp_path / "*.csv.gz"))
    assert len(archives) == 1
    assert [r["notes"] for r in rows_of(archives[0])] == ["old"]


def test_non_ascii_payload_text_is_written(tmp_path):
    log = tmp_path / "log.csv"
    EventLogger(str(log)).log("SOCK_RECV", "S", notes="Teil gepr\u00fcft \u4e2d")
    assert "\u4e2d" in rows_of(log)[0]["notes"]


# ---------------------------------------------------------------- poller


class FakeDriver(PLCDriver):
    PLC_TYPE = "Fake"
    ip = "1.2.3.4"

    def __init__(self):
        self.values = {}
        self.errors = {}
        self.batches = []

    def read_tag(self, tag):
        if tag in self.errors:
            return None, self.errors[tag]
        return self.values.get(tag), None


class BatchDriver(FakeDriver):
    def read_tags(self, tags):
        self.batches.append(list(tags))
        return {t: self.read_tag(t) for t in tags}


def poller_for(driver, tags, tmp_path):
    logger = EventLogger(str(tmp_path / "log.csv"))
    return PLCPoller(driver, tags, 100, logger), tmp_path / "log.csv"


def test_the_poller_reads_all_tags_in_one_batch(tmp_path):
    drv = BatchDriver()
    drv.values = {"A": 1, "B": 2}
    poller, _ = poller_for(drv, ["A", "B"], tmp_path)
    poller.poll_once("Fake")
    assert drv.batches == [["A", "B"]]


def test_a_change_records_the_window_since_the_previous_read(tmp_path, monkeypatch):
    drv = BatchDriver()
    drv.values = {"Go": False}
    poller, log = poller_for(drv, ["Go"], tmp_path)
    now = [1_700_000_000_000_000_000]
    monkeypatch.setattr(clock, "now_ns", lambda: now[0])
    poller.poll_once("Fake")             # first read: no row
    now[0] += 97_000_000
    poller.poll_once("Fake")             # unchanged
    now[0] += 103_000_000
    drv.values["Go"] = True
    poller.poll_once("Fake")
    changes = [r for r in rows_of(log) if r["event_type"] == "PLC_CHANGED"]
    assert len(changes) == 1
    assert changes[0]["window_ms"] == "103"
    assert changes[0]["timestamp_ms"] == str(now[0] // 1_000_000)


def test_failed_reads_widen_the_window(tmp_path, monkeypatch):
    drv = BatchDriver()
    drv.values = {"Go": 0}
    poller, log = poller_for(drv, ["Go"], tmp_path)
    now = [1_700_000_000_000_000_000]
    monkeypatch.setattr(clock, "now_ns", lambda: now[0])
    poller.poll_once("Fake")
    drv.errors["Go"] = "timeout"
    for _ in range(3):
        now[0] += 100_000_000
        poller.poll_once("Fake")
    del drv.errors["Go"]
    drv.values["Go"] = 1
    now[0] += 100_000_000
    poller.poll_once("Fake")
    change = [r for r in rows_of(log) if r["event_type"] == "PLC_CHANGED"][0]
    assert change["window_ms"] == "400"


def test_tag_by_tag_drivers_stamp_each_tag_as_it_answers(tmp_path, monkeypatch):
    class Slow(FakeDriver):
        def read_tag(self, tag):
            now[0] += 50_000_000     # each read takes 50 ms
            return super().read_tag(tag)

    now = [1_700_000_000_000_000_000]
    monkeypatch.setattr(clock, "now_ns", lambda: now[0])
    drv = Slow()
    drv.values = {"A": 0, "B": 0}
    poller, log = poller_for(drv, ["A", "B"], tmp_path)
    poller.poll_once("Fake")
    drv.values = {"A": 1, "B": 1}
    poller.poll_once("Fake")
    rows = {r["tag_or_addr"]: r for r in rows_of(log) if r["event_type"] == "PLC_CHANGED"}
    assert int(rows["B"]["timestamp_ms"]) - int(rows["A"]["timestamp_ms"]) == 50
    assert rows["A"]["window_ms"] == "100" and rows["B"]["window_ms"] == "100"


def test_a_driver_without_read_tags_still_polls(tmp_path):
    class Bare:
        PLC_TYPE = "Bare"
        ip = "x"

        def read_tag(self, tag):
            return 5, None

    poller, _ = poller_for(Bare(), ["X"], tmp_path)
    poller.poll_once("Bare")
    assert poller._last_values == {"X": 5}


def test_a_string_tag_whose_value_looks_like_the_old_sentinel_still_logs(tmp_path):
    drv = BatchDriver()
    drv.values = {"S": "__UNSET__"}
    poller, log = poller_for(drv, ["S"], tmp_path)
    poller.poll_once("Fake")
    drv.values = {"S": "ok"}
    poller.poll_once("Fake")
    assert any(r["event_type"] == "PLC_CHANGED" for r in rows_of(log))


# ------------------------------------------------------- pycomm3 batching


class Result:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error


class FakeLogix:
    def __init__(self, answers, raise_on_batch=False):
        self.answers, self.raise_on_batch, self.calls = answers, raise_on_batch, []

    def read(self, *tags):
        self.calls.append(tags)
        if len(tags) > 1:
            if self.raise_on_batch:
                raise ValueError("bad tag")
            return [self.answers.get(t) for t in tags]
        return self.answers.get(tags[0])


def rockwell(plc):
    drv = RockwellDriver("1.2.3.4")
    drv._plc = plc
    return drv


def test_pycomm3_reads_all_tags_in_one_call():
    plc = FakeLogix({"A": Result(1), "B": Result(2.5)})
    out = rockwell(plc).read_tags(["A", "B"])
    assert plc.calls == [("A", "B")]
    assert out == {"A": (1, None), "B": (2.5, None)}


def test_pycomm3_reports_a_per_tag_error_without_losing_the_others():
    plc = FakeLogix({"A": Result(1), "B": Result(None, "Tag doesn't exist")})
    out = rockwell(plc).read_tags(["A", "B"])
    assert out["A"] == (1, None)
    assert out["B"][0] is None and "doesn't exist" in out["B"][1]


def test_pycomm3_falls_back_to_one_at_a_time_when_the_batch_raises():
    plc = FakeLogix({"A": Result(1), "B": Result(2)}, raise_on_batch=True)
    out = rockwell(plc).read_tags(["A", "B"])
    assert out["A"][:2] == (1, None) and out["B"][:2] == (2, None)
    assert plc.calls[1:] == [("A",), ("B",)]


def test_pycomm3_not_connected():
    out = RockwellDriver("1.2.3.4").read_tags(["A"])
    assert out["A"] == (None, "Not connected")


# ------------------------------------------------------ libplctag batching


class FakeLib:
    """Reads go pending, then finish after `polls` status checks."""

    def __init__(self, polls=2, fail=(), never=(), empty=(), bad_create=()):
        self.polls, self.fail, self.never = polls, set(fail), set(never)
        self.empty, self.bad_create, self.creates = set(empty), set(bad_create), []
        self.names, self.left, self.destroyed, self.aborted = {}, {}, [], []
        self.next_id = 1

    def plc_tag_create(self, attribs, timeout):
        tid = self.next_id
        self.next_id += 1
        name = attribs.decode().split("name=")[1].split("&")[0]
        self.creates.append(name)
        if name in self.bad_create:
            return -7
        self.names[tid] = name
        return tid

    def plc_tag_read_blocking(self, tid):
        return -7 if self.names[tid] in self.fail else 0

    def plc_tag_status(self, tid):
        if tid not in self.left:
            return 0
        if self.names[tid] in self.never:
            return 1
        self.left[tid] -= 1
        if self.left[tid] <= 0:
            del self.left[tid]
            return -7 if self.names[tid] in self.fail else 0
        return 1

    def plc_tag_read(self, tid, timeout):
        if timeout:
            return self.plc_tag_read_blocking(tid)
        self.left[tid] = self.polls
        return 1


    def plc_tag_get_int32(self, tid, offset):
        return 40 + tid

    def plc_tag_get_size(self, tid):
        return 0 if self.names[tid] in self.empty else 4

    def plc_tag_decode_error(self, code):
        return b"PLCTAG_ERR_BAD_STATUS"

    def plc_tag_abort(self, tid):
        self.aborted.append(tid)
        return 0

    def plc_tag_destroy(self, tid):
        self.destroyed.append(tid)
        return 0


def libplctag(lib, timeout_ms=200):
    from app.engine.libplctag_driver import LibPlcTagConfig, LibPlcTagDriver
    drv = LibPlcTagDriver(LibPlcTagConfig("1.2.3.4", timeout_ms=timeout_ms))
    drv._lib, drv._connected = lib, True
    return drv


def test_libplctag_starts_every_read_before_waiting():
    lib = FakeLib()
    out = libplctag(lib).read_tags(["A", "B", "C"])
    assert {t: v[:2] for t, v in out.items()} == {"A": (41, None), "B": (42, None),
                                                  "C": (43, None)}
    assert all(len(v) == 3 and v[2] for v in out.values())


def test_libplctag_a_failed_tag_is_recreated_next_time():
    lib = FakeLib(fail={"B"})
    drv = libplctag(lib)
    out = drv.read_tags(["A", "B"])
    assert out["A"][:2] == (41, None)
    assert out["B"][0] is None and "BAD_STATUS" in out["B"][1]
    assert "B" not in drv._tag_ids and 2 in lib.destroyed


def test_libplctag_a_read_that_never_finishes_times_out():
    lib = FakeLib(never={"B"})
    drv = libplctag(lib, timeout_ms=50)
    started = time.monotonic()
    out = drv.read_tags(["A", "B"])
    assert time.monotonic() - started < 1
    assert out["A"][:2] == (41, None)
    assert "timed out" in out["B"][1]
    assert lib.aborted == [2]


def test_libplctag_an_empty_reply_is_an_error_not_a_value():
    out = libplctag(FakeLib(empty={"A"})).read_tags(["A", "B"])
    assert out["A"][0] is None and "returned 0 bytes" in out["A"][1]
    assert out["B"][:2] == (42, None)


def test_libplctag_falls_back_to_one_at_a_time_when_packed_reads_hang():
    lib = FakeLib(never={"A", "B"})
    drv = libplctag(lib, timeout_ms=30)
    out = drv.read_tags(["A", "B"])
    assert all("timed out" in v[1] for v in out.values())
    assert drv._batch_ok is False
    lib.never.clear()
    out = drv.read_tags(["A", "B"])          # blocking reads now
    assert out["A"][:2][1] is None and out["B"][:2][1] is None


def test_libplctag_a_tag_that_cannot_be_created_is_not_retried_every_poll():
    lib = FakeLib(bad_create={"Typo"})
    drv = libplctag(lib)
    for _ in range(3):
        out = drv.read_tags(["A", "Typo"])
        assert out["A"][1] is None and "plc_tag_create failed" in out["Typo"][1]
    assert lib.creates.count("Typo") == 1
    drv._create_backoff["Typo"] = 0           # backoff over
    drv.read_tags(["A", "Typo"])
    assert lib.creates.count("Typo") == 2


# ---------------------------------------------------------------- sniffer

scapy = pytest.importorskip("scapy.all")
from scapy.layers.inet import IP, TCP, UDP  # noqa: E402
from scapy.packet import Raw  # noqa: E402

from app.engine.passive_sniffer import PassiveSniffer  # noqa: E402

T0 = time.time()


def pkt(src, dst, sport, dport, flags, seq, data=b"", at=0.0, udp=False):
    if udp:
        p = IP(src=src, dst=dst) / UDP(sport=sport, dport=dport) / Raw(data)
    else:
        p = IP(src=src, dst=dst) / TCP(sport=sport, dport=dport, flags=flags, seq=seq)
        if data:
            p = p / Raw(data)
    p = IP(bytes(p))   # as it would come off the wire
    p.time = T0 + at
    return p


def sniffer(tmp_path):
    log = tmp_path / "log.csv"
    return PassiveSniffer(EventLogger(str(log)), "", None, "S", our_ip="10.0.0.1"), log


CAM, US = "10.0.0.50", "10.0.0.1"


def test_rows_are_stamped_with_the_capture_time(tmp_path):
    sn, log = sniffer(tmp_path)
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 100, b"OK", at=-2.0))
    row = rows_of(log)[0]
    assert row["event_type"] == "SOCK_RECV"
    assert int(row["timestamp_ms"]) == int((T0 - 2.0) * 1000)


def test_udp_is_stamped_with_the_capture_time(tmp_path):
    sn, log = sniffer(tmp_path)
    sn._handle_packet(pkt(US, CAM, 2000, 9000, "", 0, b"hi", at=-1.5, udp=True))
    row = rows_of(log)[0]
    assert row["event_type"] == "SOCK_SENT"
    assert int(row["timestamp_ms"]) == int((T0 - 1.5) * 1000)


def test_an_absurd_capture_time_falls_back_to_now(tmp_path):
    sn, log = sniffer(tmp_path)
    p = pkt(CAM, US, 9000, 2000, "PA", 100, b"OK")
    p.time = 0
    sn._handle_packet(p)
    assert abs(int(rows_of(log)[0]["timestamp_ms"]) - time.time() * 1000) < 5000


def test_a_split_message_is_stamped_by_its_first_segment(tmp_path):
    sn, log = sniffer(tmp_path)
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "A", 100, b"PART;", at=-1.0))
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 105, b"0001", at=-0.9))
    rows = [r for r in rows_of(log) if r["event_type"] == "SOCK_RECV"]
    assert len(rows) == 1
    assert bytes.fromhex(rows[0]["raw_hex"]) == b"PART;0001"
    assert int(rows[0]["timestamp_ms"]) == int((T0 - 1.0) * 1000)


def test_a_retransmission_is_not_logged_twice(tmp_path):
    sn, log = sniffer(tmp_path)
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 100, b"OK1", at=-1.0))
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 100, b"OK1", at=-0.8))
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 103, b"OK2", at=-0.5))
    payloads = [bytes.fromhex(r["raw_hex"]) for r in rows_of(log) if r["event_type"] == "SOCK_RECV"]
    assert payloads == [b"OK1", b"OK2"]
    assert sn.retransmissions == 1


def test_a_retransmission_with_new_data_keeps_only_the_new_part(tmp_path):
    sn, log = sniffer(tmp_path)
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 100, b"AB", at=-1.0))
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 100, b"ABCD", at=-0.5))
    payloads = [bytes.fromhex(r["raw_hex"]) for r in rows_of(log) if r["event_type"] == "SOCK_RECV"]
    assert payloads == [b"AB", b"CD"]


def test_sequence_numbers_wrap(tmp_path):
    sn, log = sniffer(tmp_path)
    top = (1 << 32) - 2
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", top, b"WXYZ", at=-1.0))
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 2, b"next", at=-0.5))
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 2, b"next", at=-0.4))   # repeat
    payloads = [bytes.fromhex(r["raw_hex"]) for r in rows_of(log) if r["event_type"] == "SOCK_RECV"]
    assert payloads == [b"WXYZ", b"next"]


def test_a_new_connection_resets_the_stream(tmp_path):
    sn, log = sniffer(tmp_path)
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 500, b"old", at=-2.0))
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "S", 10, at=-1.5))
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "PA", 11, b"new", at=-1.0))
    payloads = [bytes.fromhex(r["raw_hex"]) for r in rows_of(log) if r["event_type"] == "SOCK_RECV"]
    assert payloads == [b"old", b"new"]


def test_data_waiting_at_fin_is_logged_before_the_disconnect(tmp_path):
    sn, log = sniffer(tmp_path)
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "A", 100, b"tail", at=-1.0))
    sn._handle_packet(pkt(CAM, US, 9000, 2000, "FA", 104, at=-0.9))
    events = [r["event_type"] for r in rows_of(log)]
    assert events == ["SOCK_RECV", "SOCK_DISCONNECT"]


# ------------------------------------------------- pairing out of order


def r(ms, event, tag="", old="", new="", window=""):
    return {"timestamp_ms": str(ms), "event_type": event, "tag_or_addr": tag,
            "old_value": old, "new_value": new, "source": "S", "notes": "", "raw_hex": "",
            "window_ms": window}


TRIG = logdata.Matcher(kind="plc", tag="Go", value="True")
RESP = logdata.Matcher(kind="socket", events=("SOCK_RECV",))


def test_rows_written_out_of_time_order_still_pair():
    # The packet (stamped 1040) was written after the PLC change (1000)
    # that came in while it was being processed -- and before an earlier one.
    rows = [r(1000, "PLC_CHANGED", "Go", "False", "True", "90"),
            r(1100, "PLC_CHANGED", "Go", "True", "False", "100"),
            r(1040, "SOCK_RECV")]
    result = logdata.measure(rows, TRIG, RESP)
    assert result.answered == [40]
    assert result.pairs[0].trigger_window_ms == 90


def test_window_stats():
    rows = [r(1000, "PLC_CHANGED", "Go", "False", "True", "90"), r(1040, "SOCK_RECV"),
            r(2000, "PLC_CHANGED", "Go", "False", "True", "110"), r(2050, "SOCK_RECV"),
            r(3000, "PLC_CHANGED", "Go", "False", "True", "300")]
    stats = logdata.measure(rows, TRIG, RESP).window_stats()
    assert stats == {"median": 100, "max": 110}


def test_logs_without_a_window_column_report_none():
    rows = [{"timestamp_ms": "1000", "event_type": "PLC_CHANGED", "tag_or_addr": "Go",
             "new_value": "True"}, {"timestamp_ms": "1030", "event_type": "SOCK_RECV"}]
    result = logdata.measure(rows, TRIG, RESP)
    assert result.answered == [30] and result.window_stats() == {}


def test_the_latency_page_explains_the_window():
    from app.ui.pages.latency import window_note
    assert window_note({}) == ""
    text = window_note({"median": 100, "max": 140})
    assert "140 ms" in text and "typically within 100 ms" in text
    assert "typically" not in window_note({"median": 100, "max": 100})
