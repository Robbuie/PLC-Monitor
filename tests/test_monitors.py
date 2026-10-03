"""The window and the headless runner build the same socket monitor from the
same config, and what it hears reaches the log."""

import socket
import threading
import time

from app.engine import logdata
from app.engine.config_manager import ConfigManager
from app.engine.event_log import EventLogger
from app.engine.monitors import build_socket_monitor


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_server_mode_logs_what_arrives(tmp_path):
    port = free_port()
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log))
    monitor = build_socket_monitor({"mode": "server", "listen_host": "127.0.0.1",
                                    "listen_port": port, "source_label": "T"}, logger)
    monitor.start()
    time.sleep(0.3)
    with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
        client.sendall(b"PART;00001;OK")
        time.sleep(0.4)
    monitor.stop()
    rows = list(logdata.read_rows(str(log)))
    assert any(r["event_type"] == "SOCK_RECV" and "PART" in r["notes"] for r in rows)


def test_headless_runs_the_enabled_socket_monitor_and_stops(tmp_path):
    from app.engine.headless import run
    port = free_port()
    cfg = ConfigManager(str(tmp_path / "cfg.json"))
    cfg.set("logging", "log_path", str(tmp_path / "h.csv"))
    cfg.set("socket", "enabled", True)
    cfg.set("socket", "mode", "udp")
    cfg.set("socket", "listen_host", "127.0.0.1")
    cfg.set("socket", "listen_port", port)
    cfg.save()
    stop = threading.Event()
    thread = threading.Thread(target=run, args=(cfg.path, stop))
    thread.start()
    time.sleep(0.6)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.sendto(b"hello", ("127.0.0.1", port))
    time.sleep(0.5)
    stop.set()
    thread.join(5)
    assert not thread.is_alive()
    rows = list(logdata.read_rows(str(tmp_path / "h.csv")))
    assert any(r["event_type"] == "SOCK_RECV" for r in rows)
