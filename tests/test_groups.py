"""Grouped tags: decoding registers into text and numbers, reading them in
one request, decoding older logs that have a row per register, and latency
matched on the value (a VIN)."""

import gzip
import os
import socket
import subprocess
import sys
import time

import pytest

from app.engine import logdata
from app.engine.event_log import EventLogger
from app.engine.plc_poller import PLCPoller
from app.engine.tagspec import SpecError, TagSpec, member_names, parse_shorthand, words_to_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A VIN as a Mitsubishi PLC stores it: "A1234567", low byte first.
VIN_WORDS = [12609, 13106, 13620, 14134]


# ---------------------------------------------------------------- decoding


def test_text_low_byte_first_as_mitsubishi_stores_it():
    assert words_to_text(VIN_WORDS) == "A1234567"
    assert words_to_text([21808, 19278, 20302, 20055]) == "0UNKNOWN"
    assert words_to_text([0x3254, 0, 0, 0]) == "T2"           # NULs end it
    assert words_to_text([0x5432], swap_bytes=True) == "T2"


def test_numbers():
    assert TagSpec("D1", 1, "int16").decode([0xFFFF]) == -1
    assert TagSpec("D1", 1, "uint16").decode([0xFFFF]) == 65535
    assert TagSpec("D1", 2, "int32").decode([0x0000, 0x0001]) == 65536      # low word first
    assert TagSpec("D1", 2, "int32", swap_words=True).decode([0x0001, 0x0000]) == 65536
    assert TagSpec("D1", 2, "uint32").decode([0xFFFF, 0xFFFF]) == 4294967295
    assert TagSpec("D1", 2, "float32").decode([0x0000, 0x4048]) == 3.125
    assert TagSpec("D1", 2, "bcd").decode([0x5678, 0x1234]) == 12345678      # DBCD: low word first
    assert TagSpec("D1", 2, "hex").decode([0xBEEF, 0x0001]) == "BEEF 0001"
    assert TagSpec("D1", 3).decode([1, 0xFFFF, 3]) == "1 -1 3"
    assert TagSpec("D1", 4, "int32").decode([1, 0, 2, 0]) == "1 2"
    with pytest.raises(SpecError):
        TagSpec("D1", 1, "bcd").decode([0x12AB])
    with pytest.raises(SpecError):
        TagSpec("D1", 3, "int32").decode([1, 2, 3])


def test_names_keys_and_storage():
    spec = TagSpec("D1916", 4, "ascii", name="Plant VIN")
    assert spec.key == "Plant VIN" and spec.grouped
    assert spec.describe() == "D1916-D1919 as Text (ASCII, 2 per register)"
    assert TagSpec("D1916", 4).key == "D1916 x4"
    assert not TagSpec("D100").grouped and TagSpec("D100").key == "D100"
    back = TagSpec.from_entry(spec.to_entry())
    assert back == spec
    old = TagSpec.from_entry({"tag": "Motor_Running", "dtype": "BOOL"})
    assert (old.address, old.count, old.fmt, old.dtype) == ("Motor_Running", 1, "", "BOOL")
    assert TagSpec.from_entry({"tag": "D1", "format": "nonsense"}).fmt == ""


def test_member_names():
    assert member_names("D1916", 4) == ["D1916", "D1917", "D1918", "D1919"]
    assert member_names("W0E", 3) == ["W E".replace(" ", ""), "WF", "W10"]
    assert member_names("DB10,INT4", 3) == ["DB10,INT4", "DB10,INT6", "DB10,INT8"]
    assert member_names("Data[16]", 2) == ["Data[16]", "Data[17]"]
    assert member_names("Motor", 2) == []


def test_shorthand():
    assert parse_shorthand("D1916 x4 ascii") == TagSpec("D1916", 4, "ascii")
    assert parse_shorthand("D1916 X4 text") == TagSpec("D1916", 4, "ascii")
    # No space, no group: a Logix tag can be called Box2.
    assert parse_shorthand("Box2") == TagSpec("Box2")
    assert parse_shorthand("D200 int32") == TagSpec("D200", 2, "int32")
    assert parse_shorthand("D200 x2 dint") == TagSpec("D200", 2, "int32")
    assert parse_shorthand("D100") == TagSpec("D100")
    assert parse_shorthand("Program:Main.Counter") == TagSpec("Program:Main.Counter")
    with pytest.raises(SpecError):
        parse_shorthand("D1 x4 colour")


# ----------------------------------------------------------------- polling


class Blocks:
    PLC_TYPE = "Fake"
    ip = "x"

    def __init__(self):
        self.mem = {"D1916": VIN_WORDS, "D100": [7]}
        self.calls = []

    def read_tags(self, tags):
        return {t: (self.mem.get(t, [0])[0], None) for t in tags}

    def read_block(self, address, count):
        self.calls.append((address, count))
        words = self.mem.get(address)
        return (list(words[:count]), None) if words else (None, "no such register")


def test_the_poller_logs_one_decoded_row_per_group(tmp_path):
    drv = Blocks()
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log))
    specs = [TagSpec("D1916", 4, "ascii", name="Plant VIN"), TagSpec("D100")]
    values = {}
    poller = PLCPoller(drv, specs, 100, logger, tag_update_cb=values.__setitem__)
    poller.poll_once("Fake")
    assert values == {"Plant VIN": "A1234567", "D100": 7}
    drv.mem["D1916"] = [21808, 19278, 20302, 20055]
    poller.poll_once("Fake")
    logger.flush()
    rows = [r for r in logdata.read_rows(str(log)) if r["event_type"] == "PLC_CHANGED"]
    assert [(r["tag_or_addr"], r["old_value"], r["new_value"]) for r in rows] == [
        ("Plant VIN", "A1234567", "0UNKNOWN")]
    assert rows[0]["notes"] == "D1916-D1919 as Text (ASCII, 2 per register)"
    assert drv.calls == [("D1916", 4), ("D1916", 4)]           # one request each poll


def test_a_driver_without_blocks_reports_it(tmp_path):
    class Plain:
        PLC_TYPE = "Plain"
        ip = "x"

        def read_tags(self, tags):
            return {t: (1, None) for t in tags}

    values = {}
    poller = PLCPoller(Plain(), [TagSpec("D1", 2, "int32")], 100,
                       EventLogger(str(tmp_path / "l.csv")), tag_update_cb=values.__setitem__)
    poller.poll_once("x")
    assert "cannot read grouped tags" in values["D1 x2"]


def test_a_decode_failure_is_a_read_error(tmp_path):
    drv = Blocks()
    drv.mem["D5"] = [0x12AB]
    values = {}
    poller = PLCPoller(drv, [TagSpec("D5", 1, "bcd")], 100,
                       EventLogger(str(tmp_path / "l.csv")), tag_update_cb=values.__setitem__)
    poller.poll_once("x")
    assert values["D5"].startswith("ERROR: Could not decode")


# ------------------------------------------------- real MC protocol reads


@pytest.fixture(scope="module")
def simulator(tmp_path_factory):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    errors = tmp_path_factory.mktemp("sim") / "stderr.txt"
    with open(errors, "w") as err:
        # stdout stays a pipe-like target with the platform's encoding, the
        # way CI runs it: a character the simulator prints that cp1252 cannot
        # encode used to kill it here on Windows only.
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "mitsubishi_simulator.py"),
                                 str(port)], stdout=subprocess.DEVNULL, stderr=err,
                                env=dict(os.environ, PYTHONIOENCODING="cp1252"))
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail("The simulator exited:\n" + errors.read_text(errors="replace"))
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    time.sleep(0.3)
    if proc.poll() is not None:
        pytest.fail("The simulator exited:\n" + errors.read_text(errors="replace"))
    yield port
    proc.terminate()
    proc.wait(5)


def test_mitsubishi_reads_a_vin_in_one_request(simulator):
    pytest.importorskip("pymcprotocol")
    from app.engine.plc_drivers import MitsubishiDriver
    drv = MitsubishiDriver("127.0.0.1", port=simulator)
    assert drv.connect()[0]
    seen = set()
    deadline = time.time() + 6
    while time.time() < deadline and len(seen) < 2:
        words, err = drv.read_block("D1916", 4)
        assert err is None
        seen.add(words_to_text(words))
        time.sleep(0.2)
    assert any(v.startswith("A123") and len(v) == 8 for v in seen)
    assert drv.read_block("M100", 2)[1].startswith("A grouped tag starts at a word device")
    drv.disconnect()


# ------------------------------------------------- older logs, decoded


def row(ms, tag, old, new):
    return {"timestamp_ms": str(ms), "event_type": "PLC_CHANGED", "tag_or_addr": tag,
            "old_value": str(old), "new_value": str(new), "source": "M@1", "notes": "",
            "raw_hex": "", "window_ms": ""}


def test_register_rows_of_one_poll_become_one_decoded_row():
    unknown = [21808, 19278, 20302, 20055]
    rows = [row(1000, "D1916", 0, unknown[0]), row(1002, "D1917", 0, unknown[1]),
            row(1004, "D1918", 0, unknown[2]), row(1006, "D1919", 0, unknown[3]),
            row(1500, "CN13", 1, 2)]
    # The next vehicle: two registers change in one poll, two stay.
    rows += [row(8000, "D1916", unknown[0], VIN_WORDS[0]),
             row(8003, "D1917", unknown[1], VIN_WORDS[1]),
             row(8005, "D1918", unknown[2], VIN_WORDS[2]),
             row(8007, "D1919", unknown[3], VIN_WORDS[3])]
    spec = TagSpec("D1916", 4, "ascii", name="Plant VIN")
    out = list(logdata.with_groups(rows, [spec]))
    assert [(r["tag_or_addr"], r["old_value"], r["new_value"]) for r in out] == [
        ("Plant VIN", "", "0UNKNOWN"), ("CN13", "1", "2"), ("Plant VIN", "0UNKNOWN", "A1234567")]
    assert out[2]["timestamp_ms"] == "8007"
    assert "decoded from D1916-D1919" in out[2]["notes"]


def test_logs_with_the_group_already_are_left_alone():
    rows = [row(1, "Plant VIN", "a", "b"), row(2, "CN13", 1, 2)]
    spec = TagSpec("D1916", 4, "ascii", name="Plant VIN")
    assert list(logdata.with_groups(rows, [spec])) == rows


def test_latency_matched_on_the_same_vin():
    rows = [row(1000, "Plant VIN", "0UNKNOWN", "T1"), row(1100, "Plant VIN", "T1", "0UNKNOWN"),
            row(2000, "Plant VIN", "0UNKNOWN", "T2"),
            row(2500, "Sent VIN", "", "T0"),          # an earlier vehicle: not T1's answer
            row(3000, "Sent VIN", "T0", "T1"),
            row(4500, "Sent VIN", "T1", "T2")]
    trig = logdata.Matcher(kind="plc", tag="Plant VIN", value="!0UNKNOWN")
    resp = logdata.Matcher(kind="plc", tag="Sent VIN")
    result = logdata.measure(rows, trig, resp, same_value=True, window_ms=10_000)
    assert [(p.trigger_text.split(":")[1].strip(), p.delta_ms) for p in result.pairs] == [
        ("0UNKNOWN to T1", 2000), ("0UNKNOWN to T2", 2500)]
    # Without matching values, T2 is credited with T0's message, and T1
    # with nothing.
    plain = logdata.measure(rows, trig, resp, window_ms=10_000)
    assert [p.delta_ms for p in plain.pairs] == [None, 500]


def test_a_socket_message_carrying_the_vin_answers_it():
    rows = [row(1000, "Plant VIN", "", "A1234567"),
            {"timestamp_ms": "1200", "event_type": "SOCK_SENT", "source": "LINE", "tag_or_addr": "",
             "notes": "", "raw_hex": b"\x02A1234567\x03".hex(), "old_value": "", "new_value": ""}]
    trig = logdata.Matcher(kind="plc", tag="Plant VIN")
    resp = logdata.Matcher(kind="socket")
    assert logdata.measure(rows, trig, resp, same_value=True).answered == [200]


def test_a_day_log_decodes_cleanly(tmp_path):
    """The pattern of a real day log: before 1.5 the rebuilt VIN went through
    mixtures like A1NKNOWN."""
    path = tmp_path / "log.csv.gz"
    lines = ["timestamp_ms,timestamp_human,event_type,source,tag_or_addr,old_value,new_value,raw_hex,notes"]
    ms = 1_000
    prev = [0, 0, 0, 0]
    seq = [[21808, 19278, 20302, 20055], VIN_WORDS, [21808, 19278, 20302, 20055],
           [12609, 13106, 13620, 14646]]
    for words in seq:
        for i, (a, b) in enumerate(zip(prev, words)):
            if a != b:
                lines.append(f"{ms + i * 3},x,PLC_CHANGED,M,D{1916 + i},{a},{b},,")
        prev = words
        ms += 4000
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    spec = TagSpec("D1916", 4, "ascii", name="Plant VIN")
    values = [r["new_value"] for r in logdata.with_groups(logdata.read_rows(str(path)), [spec])]
    assert values == ["0UNKNOWN", "A1234567", "0UNKNOWN", "A1234569"]


# ------------------------------------------------------ pycomm3 blocks


def test_pycomm3_reads_an_array_slice_in_one_call():
    from app.engine.plc_drivers import RockwellDriver

    class Result:
        def __init__(self, value, error=None):
            self.value, self.error = value, error

    class Logix:
        calls = []

        def read(self, tag):
            self.calls.append(tag)
            return Result([12609, 13106, -12, 14134]) if tag == "Data[16]{4}" else None

    drv = RockwellDriver("1.2.3.4")
    drv._plc = Logix()
    assert drv.read_block("Data[16]", 4) == ([12609, 13106, 0xFFF4, 14134], None)
    assert Logix.calls == ["Data[16]{4}"]
    assert drv.read_block("Nope", 2)[1] == "Tag 'Nope' not found"


# ------------------------------------------------------ GX Works3 import


def make_gx3(path):
    """A tiny .gx3 with the same tables GX Works3 writes, holding labels
    shaped like a real line project's (not the real one: the repository is
    public)."""
    import sqlite3
    import zipfile
    db = path.parent / "LabelData.db"
    con = sqlite3.connect(db)
    con.executescript("""
        create table DeviceAssignTbl (LabelID text, RowID int, MELSECDevice text);
        create table ColumnDataTbl (LabelID text, RowID int, ColumnID int, ColumnStrValue text,
                                    ColumnIntValue int);
        create table RowTbl (LabelID text, RowID int, RowNo int, VarType int, BitCount text);
        create table CommentDataTbl (LabelID text, RowID int, ColumnID int, ArrayOffset text,
                                     BitPosition int, CommentStrValue text);
    """)
    labels = [  # row, device, name, size, type code, comment
        (1, "M300", "bRunRefresh", "1", "", "check"),
        (2, "D300", "uOpenErrID", "1", "", ""),
        (3, "D1000", "uSendData", "12", "", "Data out"),
        (4, "D500", "Result", "17", "32", "Result text"),
        (5, "LV:84", "lastVin", "4", "", ""),
        (6, "W1E", "wFlags", "3", "", ""),
    ]
    for row, device, name, size, kind, comment in labels:
        con.execute("insert into DeviceAssignTbl values ('L1', ?, ?)", (row, device))
        for col, text in ((1, "VAR_GLOBAL"), (2, name), (3, kind), (15, comment)):
            con.execute("insert into ColumnDataTbl values ('L1', ?, ?, ?, 0)", (row, col, text))
        con.execute("insert into RowTbl values ('L1', ?, ?, 1, ?)", (row, row, size))
    for offset, text in enumerate(["vin digit 0/1", "vin digit 2/3", "vin digit 4/5",
                                   "vin digit 6/7", "spare + Model digit 0",
                                   "model digit 1 + spare", "Color 0/1", "Color 2/spare"]):
        con.execute("insert into CommentDataTbl values ('L1', 3, 15, ?, 255, ?)",
                    (str(offset), text))
    con.commit()
    con.close()
    with zipfile.ZipFile(path, "w") as z:
        z.write(db, "LabelData.db")
        z.writestr("Config.xml", "<x/>")
    os.remove(db)


def test_gx3_labels_and_suggested_groups(tmp_path):
    from app.engine.tagimport import read
    path = tmp_path / "Line.gx3"
    make_gx3(path)
    entries = {e.label: e for e in read(str(path))}
    assert entries["bRunRefresh"].tag == "M300" and entries["bRunRefresh"].description == "check"
    assert entries["uOpenErrID"].tag == "D300" and entries["uOpenErrID"].count == 1
    vin = entries["uSendData VIN"]
    assert (vin.tag, vin.count, vin.fmt) == ("D1000", 4, "ascii")
    assert vin.spec().describe() == "D1000-D1003 as Text (ASCII, 2 per register)"
    assert (entries["uSendData[4..5] Model"].tag, entries["uSendData[4..5] Model"].count) == ("D1004", 2)
    assert entries["uSendData[6..7] Color"].tag == "D1006"
    assert entries["uSendData[11]"].tag == "D1011"                 # every element too
    assert entries["uSendData[0]"].description == "vin digit 0/1"
    assert (entries["Result"].count, entries["Result"].fmt) == (17, "ascii")   # STRING(32)
    assert "lastVin" not in entries                                 # local label, no device
    assert [entries[f"wFlags[{i}]"].tag for i in range(3)] == ["W1E", "W1F", "W20"]


def test_a_file_that_is_not_a_project(tmp_path):
    import zipfile
    from app.engine.tagimport import TagImportError, read
    path = tmp_path / "x.gx3"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("other.txt", "x")
    with pytest.raises(TagImportError):
        read(str(path))


def test_specs_for_reads_groups_from_the_config():
    from app.engine.monitors import specs_for, tags_for
    plc = {"type": "Mitsubishi (MC Proto)", "tags_per_driver": {"Mitsubishi (MC Proto)": [
        {"tag": "D1916", "count": 4, "format": "ascii", "name": "Plant VIN"},
        {"tag": "D100", "dtype": "DINT"}, "M0"]}}
    specs = specs_for(plc)
    assert [s.key for s in specs] == ["Plant VIN", "D100", "M0"]
    assert specs[0].grouped and not specs[1].grouped
    assert tags_for(plc) == ["Plant VIN", "D100", "M0"]


def _poll(ms, before, after, which):
    """Register rows for one poll that saw only the registers in `which`
    rewritten -- the others were read before the PLC got to them."""
    return [row(ms + i * 3, f"D{1916 + i}", before[i], after[i])
            for i in which if before[i] != after[i]]


UNKNOWN = [21808, 19278, 20302, 20055]


def test_a_read_torn_by_the_plc_rewriting_is_folded_into_the_change():
    # A vehicle leaves: one poll sees the first two registers already
    # 0UNKNOWN's and the last two still the VIN's ("0UNK4567" -> 4567); the
    # next poll, 100 ms later, sees the rest.
    rows = _poll(1000, UNKNOWN, VIN_WORDS, range(4))
    rows += _poll(9000, VIN_WORDS, UNKNOWN, (0, 1))
    rows += _poll(9100, [UNKNOWN[0], UNKNOWN[1], VIN_WORDS[2], VIN_WORDS[3]], UNKNOWN, (2, 3))
    spec = TagSpec("D1916", 4, "digits")
    out = [r for r in logdata.with_groups(rows, [spec])]
    assert [(r["old_value"], r["new_value"]) for r in out] == [("", "1234567"), ("1234567", "")]
    # Stamped at the first sign of the change, and saying what it passed through.
    assert out[1]["timestamp_ms"] == "9003"
    assert "passed through '4567'" in out[1]["notes"]
    # As text, the same.
    text = TagSpec("D1916", 4, "ascii")
    assert [r["new_value"] for r in logdata.with_groups(rows, [text])] == ["A1234567", "0UNKNOWN"]


def test_values_that_last_are_kept_and_rows_stay_in_order():
    rows = _poll(1000, [0] * 4, UNKNOWN, range(4))
    rows += _poll(8000, UNKNOWN, VIN_WORDS, range(4))
    rows.append(row(8200, "CN13", 1, 2))
    rows += _poll(9500, VIN_WORDS, UNKNOWN, range(4))   # 1.5 s later: real
    spec = TagSpec("D1916", 4, "ascii")
    out = list(logdata.with_groups(rows, [spec]))
    assert [r["new_value"] for r in out] == ["0UNKNOWN", "A1234567", "2", "0UNKNOWN"]
    assert not any("passed through" in r["notes"] for r in out)


def test_a_brief_value_that_is_no_mixture_stays():
    # The PLC put a VIN out for 300 ms and cleared it: brief, but every
    # register changed together, so it was really there.
    rows = _poll(1000, [0] * 4, VIN_WORDS, range(4)) + _poll(1300, VIN_WORDS, [0] * 4, range(4))
    rows += _poll(5000, [0] * 4, UNKNOWN, range(4))
    torn = [VIN_WORDS[0], VIN_WORDS[1], UNKNOWN[2], 0]   # not half of either side
    rows += _poll(5100, UNKNOWN, torn, range(4)) + _poll(5200, torn, VIN_WORDS, range(4))
    spec = TagSpec("D1916", 4, "ascii")
    out = list(logdata.with_groups(rows, [spec]))
    assert [r["new_value"] for r in out] == ["A1234567", "", "0UNKNOWN", "A123NO", "A1234567"]
    assert not any("_words" in r for r in out)


def test_a_first_value_of_nothing_is_not_a_row():
    rows = _poll(1000, [0] * 4, UNKNOWN, range(4)) + _poll(8000, UNKNOWN, VIN_WORDS, range(4))
    out = list(logdata.with_groups(rows, [TagSpec("D1916", 4, "digits")]))
    assert [(r["old_value"], r["new_value"]) for r in out] == [("", "1234567")]
