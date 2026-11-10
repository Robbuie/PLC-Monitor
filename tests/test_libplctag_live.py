"""End to end against libplctag's own PLC simulator, `ab_server`.

Skipped unless PLCMON_LIBPLCTAG_DIR names a folder holding the libplctag
library and `ab_server` from a libplctag release zip (any platform). It
exercises the real library: tag creation, the batched read, the fall-back for
a device that refuses packed requests (ab_server does), and the poller
logging a change written to the simulator.
"""

import ctypes
import os
import socket
import subprocess
import sys
import time

import pytest

from app.engine import logdata
from app.engine.event_log import EventLogger
from app.engine.plc_poller import PLCPoller

DIR = os.environ.get("PLCMON_LIBPLCTAG_DIR", "")
pytestmark = pytest.mark.skipif(not DIR, reason="PLCMON_LIBPLCTAG_DIR not set")


def _lib_path():
    for name in ("plctag.dll", "libplctag.so", "libplctag.dylib"):
        if os.path.exists(os.path.join(DIR, name)):
            return os.path.join(DIR, name)
    pytest.skip("no libplctag library in PLCMON_LIBPLCTAG_DIR")


@pytest.fixture(scope="module")
def server():
    exe = os.path.join(DIR, "ab_server.exe" if sys.platform == "win32" else "ab_server")
    proc = subprocess.Popen([exe, "--plc=ControlLogix", "--path=1,0", "--tag=Go:DINT[1]",
                             "--tag=Speed:REAL[1]", "--tag=Count:DINT[1]"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", 44818), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield
    proc.terminate()
    proc.wait(5)


def driver():
    from app.engine.libplctag_driver import LibPlcTagConfig, LibPlcTagDriver
    cfg = LibPlcTagConfig("127.0.0.1", path="1,0", timeout_ms=800, dll_path=_lib_path())
    drv = LibPlcTagDriver(cfg, tag_types={"Speed": "REAL"})
    assert drv.connect() == (True, "")
    return drv


def write_dint(drv, tag, value):
    lib = drv._lib
    lib.plc_tag_set_int32.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int32]
    lib.plc_tag_write.argtypes = [ctypes.c_int32, ctypes.c_int]
    tid, err = drv._get_or_create(tag)
    assert not err
    lib.plc_tag_set_int32(tid, 0, value)
    assert lib.plc_tag_write(tid, 800) == 0


def test_reads_settle_into_working_values(server):
    drv = driver()
    for _ in range(3):
        out = drv.read_tags(["Go", "Speed", "Count"])
    assert all(v[1] is None for v in out.values()), out
    assert isinstance(out["Speed"][0], float)
    drv.disconnect()


def test_the_poller_logs_a_change_with_its_window(server, tmp_path):
    drv = driver()
    log = tmp_path / "log.csv"
    poller = PLCPoller(drv, ["Go", "Count"], 50, EventLogger(str(log)))
    for _ in range(3):
        poller.poll_once("sim")
    writer = driver()
    write_dint(writer, "Go", 1)
    time.sleep(0.05)
    poller.poll_once("sim")
    changes = [r for r in logdata.read_rows(str(log)) if r["event_type"] == "PLC_CHANGED"]
    assert [(c["tag_or_addr"], c["old_value"], c["new_value"]) for c in changes] == [("Go", "0", "1")]
    assert int(changes[0]["window_ms"]) >= 40
    writer.disconnect()
    drv.disconnect()
