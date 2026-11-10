"""The window and the headless runner build the same socket monitor from the
same config, and what it hears reaches the log."""

import socket
import threading
import time

from app.engine import logdata
from app.engine.config_manager import ConfigManager
from app.engine.event_log import EventLogger
from app.engine.monitors import build_socket_monitor


def free_port(kind=socket.SOCK_STREAM):
    # Free for the protocol the test will use: a TCP port that is free can be
    # a UDP port something else holds (Windows runners keep many open).
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(path, predicate, send=None, timeout=10.0):
    """Rows of the log at `path` once one matches, sending again meanwhile.

    A fixed sleep before sending raced the monitor's start on a slow CI
    runner: a datagram sent before the bind is simply gone.
    """
    deadline = time.monotonic() + timeout
    rows = []
    while time.monotonic() < deadline:
        if send is not None:
            send()
        time.sleep(0.2)
        try:
            rows = list(logdata.read_rows(str(path)))
        except OSError:
            rows = []
        if any(predicate(r) for r in rows):
            return rows
    return rows


def test_server_mode_logs_what_arrives(tmp_path):
    port = free_port()
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log))
    monitor = build_socket_monitor({"mode": "server", "listen_host": "127.0.0.1",
                                    "listen_port": port, "source_label": "T"}, logger)
    monitor.start()
    # Connect once the listener is up rather than after a guessed sleep.
    deadline = time.monotonic() + 10
    while True:
        try:
            client = socket.create_connection(("127.0.0.1", port), timeout=2)
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)
    with client:
        client.sendall(b"PART;00001;OK")
        rows = wait_for(log, lambda r: r["event_type"] == "SOCK_RECV" and "PART" in r["notes"])
    monitor.stop()
    logger.flush()
    rows = list(logdata.read_rows(str(log)))
    assert any(r["event_type"] == "SOCK_RECV" and "PART" in r["notes"] for r in rows)


def test_headless_runs_the_enabled_socket_monitor_and_stops(tmp_path):
    from app.engine.headless import run
    port = free_port(socket.SOCK_DGRAM)
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

    def send():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.sendto(b"hello", ("127.0.0.1", port))

    try:
        rows = wait_for(tmp_path / "h.csv", lambda r: r["event_type"] == "SOCK_RECV", send)
    finally:
        stop.set()
        thread.join(10)
    assert not thread.is_alive()
    assert any(r["event_type"] == "SOCK_RECV" for r in rows), [
        (r["event_type"], r["notes"]) for r in rows]


def test_a_udp_port_already_taken_is_logged(tmp_path):
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("127.0.0.1", 0))
    port = holder.getsockname()[1]
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log))
    monitor = build_socket_monitor({"mode": "udp", "listen_host": "127.0.0.1",
                                    "listen_port": port, "source_label": "U"}, logger)
    try:
        monitor.start()
        rows = wait_for(log, lambda r: r["event_type"] == "SOCK_ERROR")
    finally:
        monitor.stop()
        holder.close()
    assert any("Bind error" in r["notes"] for r in rows if r["event_type"] == "SOCK_ERROR")
