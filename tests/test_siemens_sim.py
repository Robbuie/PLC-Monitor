"""The Siemens driver against python-snap7's own S7 server: every tag form
in the guide, read off a real S7 connection."""

import socket

import pytest

snap7 = pytest.importorskip("snap7")

from app.engine.plc_drivers import SiemensDriver  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def plc():
    from snap7 import util
    from snap7.server import Server
    try:
        from snap7.type import SrvArea
    except ImportError:  # python-snap7 1.x
        from snap7.types import srvAreaDB as _db  # noqa: F401
        pytest.skip("this test is written for python-snap7 2.x/3.x")
    db = bytearray(32)
    db[0] = 0b101
    util.set_real(db, 4, 3.5)
    util.set_dint(db, 8, -123456)
    util.set_int(db, 12, -5)
    db[14] = 200
    db[16], db[17] = 1, 2
    mk, pe, pa = bytearray(4), bytearray(4), bytearray(4)
    mk[1] = 0b10
    pe[0] = 0b1
    pa[2] = 0b1000_0000
    server = Server()
    server.register_area(SrvArea.DB, 10, db)
    server.register_area(SrvArea.MK, 0, mk)
    server.register_area(SrvArea.PE, 0, pe)
    server.register_area(SrvArea.PA, 0, pa)
    port = free_port()
    server.start(tcp_port=port)
    driver = SiemensDriver("127.0.0.1", rack=0, slot=1, port=port)
    ok, err = driver.connect()
    assert ok, err
    yield driver
    driver.disconnect()
    server.stop()


@pytest.mark.parametrize("tag, value", [
    ("DB10,X0.0", True), ("DB10,X0.1", False), ("DB10,X0.2", True),
    ("DB10,REAL4", 3.5), ("DB10,DINT8", -123456), ("DB10,INT12", -5),
    ("DB10,BYTE14", 200), ("DB10,WORD16", 258),
    ("M1.1", True), ("M1.0", False), ("I0.0", True), ("Q2.7", True), ("Q2.6", False),
])
def test_every_tag_form_reads(plc, tag, value):
    got, err = plc.read_tag(tag)
    assert err is None
    assert got == pytest.approx(value) if isinstance(value, float) else got == value


def test_batch_read_gives_every_tag_its_own_stamp(plc):
    out = plc.read_tags(["DB10,INT12", "M1.1"])
    assert out["DB10,INT12"][:2] == (-5, None)
    assert out["M1.1"][:2] == (True, None)
    assert out["DB10,INT12"][2] <= out["M1.1"][2]


def test_a_bad_tag_is_an_error_not_a_crash(plc):
    value, err = plc.read_tag("DB10,FOO2")
    assert value is None and err


def test_the_poller_reconnects_when_the_s7_server_comes_back(tmp_path, monkeypatch):
    """Server stopped and started again on the same port: the poller notices,
    reconnects, and logs the change that happened meanwhile."""
    import time

    from snap7.server import Server
    from snap7.type import SrvArea

    from app.engine import logdata
    from app.engine import plc_poller as pm
    from app.engine.event_log import EventLogger

    monkeypatch.setattr(pm, "RECONNECT_AFTER_S", 0.3)
    monkeypatch.setattr(pm, "RECONNECT_BACKOFF_S", (0.2,))
    port = free_port()

    def serve(value):
        db = bytearray(4)
        db[1] = value
        server = Server()
        server.register_area(SrvArea.DB, 1, db)
        server.start(tcp_port=port)
        return server

    server = serve(5)
    driver = SiemensDriver("127.0.0.1", rack=0, slot=1, port=port)
    assert driver.connect()[0]
    logger = EventLogger(str(tmp_path / "log.csv"))
    poller = pm.PLCPoller(driver, ["DB1,INT0"], 50, logger)
    poller.start()
    time.sleep(0.4)
    server.stop()
    time.sleep(1.0)
    server = serve(9)
    deadline = time.time() + 15
    while time.time() < deadline and not poller.reconnects:
        time.sleep(0.1)
    time.sleep(0.3)
    poller.stop()
    driver.disconnect()
    server.stop()
    logger.flush()
    assert poller.reconnects == 1
    changes = [(r["old_value"], r["new_value"]) for r in logdata.read_rows(str(tmp_path / "log.csv"))
               if r["event_type"] == "PLC_CHANGED"]
    assert changes == [("5", "9")]


def test_a_word_block_reads_in_one_request(plc):
    """DB10,WORD12 x2 -> the two words at offsets 12 and 14, high byte first."""
    words, err = plc.read_block("DB10,WORD12", 2)
    assert err is None
    assert words == [0xFFFB, 200 << 8]          # INT -5 at 12; byte 200 at 14
    from app.engine.tagspec import TagSpec
    assert TagSpec("DB10,INT12", 1, "int16").decode(words[:1]) == -5
    assert plc.read_block("M1.1", 2)[1].startswith("A grouped Siemens tag")
