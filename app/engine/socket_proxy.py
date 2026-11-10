"""
socket_proxy.py
Two-mode socket monitor:

  PROXY mode   – App sits between sender and real receiver.
                 Sender ──► our_port ──► forward_host:forward_port
                 All bytes both directions are timestamped and logged.

  SERVER mode  – App just listens on a port and logs whatever arrives.
                 Useful when you only have access to the receiving side.
"""

from __future__ import annotations
import socket
import threading
import time
from typing import Callable
from app.engine.event_log import EventLogger, EV_SOCK_RECV, EV_SOCK_SENT, EV_SOCK_CONN, EV_SOCK_DISC, EV_SOCK_ERROR

# How long to wait for data on a socket before looping (seconds)
RECV_TIMEOUT = 0.2
BUFFER_SIZE  = 4096


class SocketMonitorBase:
    def __init__(self, logger: EventLogger, status_cb: Callable[[str], None] | None = None):
        self.logger = logger
        self.status_cb = status_cb or (lambda msg: None)
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._server_sock: socket.socket | None = None

    def stop(self):
        self._stop_event.set()
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        for t in self._threads:
            t.join(timeout=3)
        self._threads.clear()
        self._stop_event.clear()

    def _status(self, msg: str):
        self.status_cb(msg)

    def _try_decode(self, data: bytes) -> str:
        """Best-effort decode to printable string."""
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return data.hex()


# ---------------------------------------------------------------------------
# Proxy Mode
# ---------------------------------------------------------------------------

class SocketProxy(SocketMonitorBase):
    """
    Transparent proxy: intercepts traffic between a sender and a receiver.

    Configure sender software to send to (listen_host, listen_port) instead of
    the real destination.  The proxy forwards to (forward_host, forward_port)
    and timestamps every byte in both directions.
    """

    def __init__(self, logger: EventLogger,
                 listen_host: str, listen_port: int,
                 forward_host: str, forward_port: int,
                 source_label: str = "PROXY",
                 status_cb: Callable[[str], None] | None = None):
        super().__init__(logger, status_cb)
        self.listen_host  = listen_host
        self.listen_port  = listen_port
        self.forward_host = forward_host
        self.forward_port = forward_port
        self.source_label = source_label

    def start(self):
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        self._threads.append(t)

    def _accept_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock = srv
            try:
                srv.bind((self.listen_host, self.listen_port))
                srv.listen(10)
                srv.settimeout(RECV_TIMEOUT)
                self._status(f"[Proxy] Listening on {self.listen_host}:{self.listen_port} → {self.forward_host}:{self.forward_port}")
            except Exception as e:
                self._status(f"[Proxy] Bind error: {e}")
                self.logger.log(EV_SOCK_ERROR, self.source_label, notes=f"Bind error: {e}")
                return

            while not self._stop_event.is_set():
                try:
                    client_sock, addr = srv.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break

                self.logger.log(EV_SOCK_CONN, self.source_label, tag_or_addr=str(addr),
                                notes="Sender connected")
                self._status(f"[Proxy] Connection from {addr}")

                t = threading.Thread(target=self._handle_client,
                                     args=(client_sock, addr), daemon=True)
                t.start()
                self._threads.append(t)

        self._status("[Proxy] Listener stopped.")

    def _handle_client(self, client_sock: socket.socket, client_addr):
        try:
            fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            fwd_sock.connect((self.forward_host, self.forward_port))
        except Exception as e:
            self._status(f"[Proxy] Cannot connect to {self.forward_host}:{self.forward_port}: {e}")
            self.logger.log(EV_SOCK_ERROR, self.source_label, notes=f"Forward connect fail: {e}")
            client_sock.close()
            return

        client_sock.settimeout(RECV_TIMEOUT)
        fwd_sock.settimeout(RECV_TIMEOUT)

        def relay(src, dst, direction: str):
            event = EV_SOCK_RECV if direction == "recv" else EV_SOCK_SENT
            while not self._stop_event.is_set():
                try:
                    data = src.recv(BUFFER_SIZE)
                    if not data:
                        break
                    ts_label = f"{self.forward_host}:{self.forward_port}" if direction == "recv" else str(client_addr)
                    self.logger.log(event, self.source_label,
                                    tag_or_addr=ts_label,
                                    raw_data=data,
                                    notes=f"{len(data)} bytes | {self._try_decode(data)[:120]}")
                    dst.sendall(data)
                except socket.timeout:
                    continue
                except Exception as e:
                    self.logger.log(EV_SOCK_ERROR, self.source_label, notes=str(e))
                    break

        t1 = threading.Thread(target=relay, args=(client_sock, fwd_sock, "recv"), daemon=True)
        t2 = threading.Thread(target=relay, args=(fwd_sock, client_sock, "sent"), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

        self.logger.log(EV_SOCK_DISC, self.source_label, tag_or_addr=str(client_addr), notes="Session ended")
        self._status(f"[Proxy] Session closed: {client_addr}")

        for s in (client_sock, fwd_sock):
            try:
                s.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Server (listen-only) Mode — TCP
# ---------------------------------------------------------------------------

class SocketServer(SocketMonitorBase):
    """
    Simple TCP server — logs everything that arrives.
    No forwarding; the app IS the endpoint.
    Useful when the sending software can be directed to send to us.
    """

    def __init__(self, logger: EventLogger,
                 listen_host: str, listen_port: int,
                 source_label: str = "SERVER",
                 status_cb: Callable[[str], None] | None = None):
        super().__init__(logger, status_cb)
        self.listen_host  = listen_host
        self.listen_port  = listen_port
        self.source_label = source_label

    def start(self):
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        self._threads.append(t)

    def _accept_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock = srv
            try:
                srv.bind((self.listen_host, self.listen_port))
                srv.listen(10)
                srv.settimeout(RECV_TIMEOUT)
                self._status(f"[Server] Listening on {self.listen_host}:{self.listen_port}")
            except Exception as e:
                self._status(f"[Server] Bind error: {e}")
                self.logger.log(EV_SOCK_ERROR, self.source_label, notes=f"Bind error: {e}")
                return

            while not self._stop_event.is_set():
                try:
                    client_sock, addr = srv.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break
                self.logger.log(EV_SOCK_CONN, self.source_label, tag_or_addr=str(addr))
                self._status(f"[Server] Connection from {addr}")
                t = threading.Thread(target=self._handle_client,
                                     args=(client_sock, addr), daemon=True)
                t.start()
                self._threads.append(t)

    def _handle_client(self, client_sock: socket.socket, addr):
        client_sock.settimeout(RECV_TIMEOUT)
        while not self._stop_event.is_set():
            try:
                data = client_sock.recv(BUFFER_SIZE)
                if not data:
                    break
                self.logger.log(EV_SOCK_RECV, self.source_label,
                                tag_or_addr=str(addr),
                                raw_data=data,
                                notes=f"{len(data)} bytes | {self._try_decode(data)[:120]}")
            except socket.timeout:
                continue
            except Exception:
                break

        self.logger.log(EV_SOCK_DISC, self.source_label, tag_or_addr=str(addr))
        try:
            client_sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# UDP Monitor
# ---------------------------------------------------------------------------

class UDPMonitor(SocketMonitorBase):
    """Logs all UDP datagrams arriving on a port."""

    def __init__(self, logger: EventLogger,
                 listen_host: str, listen_port: int,
                 source_label: str = "UDP",
                 status_cb: Callable[[str], None] | None = None):
        super().__init__(logger, status_cb)
        self.listen_host  = listen_host
        self.listen_port  = listen_port
        self.source_label = source_label

    def start(self):
        t = threading.Thread(target=self._recv_loop, daemon=True)
        t.start()
        self._threads.append(t)

    def _recv_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            self._server_sock = sock
            try:
                sock.bind((self.listen_host, self.listen_port))
                sock.settimeout(RECV_TIMEOUT)
                self._status(f"[UDP] Listening on {self.listen_host}:{self.listen_port}")
            except Exception as e:
                self._status(f"[UDP] Bind error: {e}")
                # Into the log as well, like Proxy and Server: under the
                # service the status line goes nowhere a person looks, and a
                # port already taken left a monitor that silently heard nothing.
                self.logger.log(EV_SOCK_ERROR, self.source_label,
                                notes=f"Bind error on {self.listen_host}:{self.listen_port}: {e}")
                return

            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(BUFFER_SIZE)
                    self.logger.log(EV_SOCK_RECV, self.source_label,
                                    tag_or_addr=str(addr),
                                    raw_data=data,
                                    notes=f"{len(data)} bytes | {self._try_decode(data)[:120]}")
                except socket.timeout:
                    continue
                except ConnectionResetError:
                    # Windows reports an ICMP "port unreachable" as WSAECONNRESET
                    # on the next recvfrom of a UDP socket. It says nothing about
                    # this socket's ability to receive; carry on listening.
                    continue
                except Exception as e:
                    if not self._stop_event.is_set():
                        self.logger.log(EV_SOCK_ERROR, self.source_label, notes=str(e))
                    break
