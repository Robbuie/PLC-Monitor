import socket
import threading

from app.engine import network_discovery as nd


def test_gentle_is_the_default_pace():
    assert nd.scan_ports.__defaults__ is not None
    workers, _pause = nd.SCAN_PROFILES["gentle"]
    assert workers <= 4


def test_scans_only_the_ports_it_is_given():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    found = []
    nd.scan_ports("127.0.0.1", ports=[port, 1], timeout=0.3, pause=0,
                  result_cb=lambda p, b: found.append(p))
    server.close()
    assert found == [port]


def test_stop_event_stops_it():
    stop = threading.Event()
    stop.set()
    assert nd.scan_ports("127.0.0.1", ports=list(range(1, 200)), stop_event=stop, pause=0) == []
