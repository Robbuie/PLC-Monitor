"""
passive_sniffer.py
Passively captures TCP/UDP packets matching a host+port filter using scapy.

This is completely non-intrusive — it only READS packets off the wire/NIC.
The existing connection between PLC and PC is not touched at all.

Requirements:
  pip install scapy
  Windows: also install Npcap from https://npcap.com  (free, WinPcap compatible)
  Linux:   run as root, or set CAP_NET_RAW capability

How it works:
  - Opens a raw packet capture on the selected network interface
  - Filters by IP and/or port (BPF filter string, same as Wireshark)
  - Reassembles TCP stream data per flow and logs each payload delivery
    with a millisecond timestamp
  - UDP datagrams are logged individually
"""

from __future__ import annotations
import threading
import socket
from typing import Callable
from app.engine.event_log import EventLogger, EV_SOCK_RECV, EV_SOCK_SENT, EV_SOCK_CONN, EV_SOCK_DISC, EV_SOCK_ERROR, EV_INFO

try:
    from scapy.all import sniff, get_if_list, get_if_addr, conf as scapy_conf
    from scapy.layers.inet import IP, TCP, UDP
    HAS_SCAPY = True
except ImportError:
    HAS_SCAPY = False


def scapy_available() -> tuple[bool, str]:
    if not HAS_SCAPY:
        return False, (
            "scapy not installed.\n"
            "Run:  pip install scapy\n"
            "Windows also needs Npcap:  https://npcap.com  (free)"
        )
    return True, ""


def list_interfaces() -> list[dict]:
    """
    Returns network interfaces with friendly names, IPs and up/down status.
    Uses psutil for human-readable names (e.g. "Ethernet", "Wi-Fi").
    Falls back to scapy's raw names if psutil is unavailable.

    Each dict has:
      display  – shown in the dropdown, e.g. "Ethernet  —  192.168.1.50  [UP]"
      name     – the adapter name to pass to scapy sniff(iface=...)
      ip       – primary IPv4 address or ""
      is_up    – bool
    """
    results = []

    # ── Preferred: psutil gives human-readable Windows adapter names ──
    try:
        import psutil, socket as _sock
        addrs  = psutil.net_if_addrs()
        stats  = psutil.net_if_stats()

        for name, addr_list in addrs.items():
            ipv4 = ""
            for addr in addr_list:
                if addr.family == _sock.AF_INET and not addr.address.startswith("169.254"):
                    ipv4 = addr.address
                    break

            stat  = stats.get(name)
            is_up = stat.isup if stat else False

            # Skip purely internal/loopback adapters unless nothing else found
            if name.lower() in ("loopback pseudo-interface 1",) and ipv4 == "127.0.0.1":
                continue

            status = "UP" if is_up else "down"
            if ipv4:
                display = f"{name}  —  {ipv4}  [{status}]"
            else:
                display = f"{name}  [{status}]"

            results.append({"display": display, "name": name,
                             "ip": ipv4, "is_up": is_up})

        # Sort: UP first, then alphabetical
        results.sort(key=lambda r: (not r["is_up"], r["name"].lower()))
        if results:
            return results
    except Exception:
        pass

    # ── Fallback: scapy's own interface list ──
    if not HAS_SCAPY:
        return []
    for iface in get_if_list():
        try:
            ip = get_if_addr(iface)
        except Exception:
            ip = ""
        results.append({"display": f"{iface}  ({ip})" if ip else iface,
                         "name": iface, "ip": ip, "is_up": True})
    return results


class PassiveSniffer:
    """
    Captures packets matching a BPF filter string on a chosen interface.
    Completely passive — does not intercept, redirect, or modify traffic.

    Typical filter examples:
      "host 192.168.1.50 and port 9000"       # both directions, one host+port
      "host 192.168.1.50 and tcp port 9000"   # TCP only
      "port 9000"                             # any host, specific port
      "host 192.168.1.50"                     # all traffic to/from that host
    """

    def __init__(self,
                 logger: EventLogger,
                 bpf_filter: str,
                 iface: str | None,
                 source_label: str = "PASSIVE",
                 our_ip: str = "",
                 status_cb: Callable[[str], None] | None = None):
        self.logger       = logger
        self.bpf_filter   = bpf_filter
        self.iface        = iface          # None = scapy default (usually all)
        self.source_label = source_label
        self.our_ip       = our_ip         # helps determine send vs recv direction
        self.status_cb    = status_cb or (lambda m: None)

        self._stop_event  = threading.Event()
        self._thread: threading.Thread | None = None

        # TCP stream reassembly: (src_ip, src_port, dst_ip, dst_port) -> bytes
        self._tcp_buffers: dict[tuple, bytes] = {}

    def start(self):
        ok, msg = scapy_available()
        if not ok:
            self.logger.log(EV_SOCK_ERROR, self.source_label, notes=msg)
            self.status_cb(f"[Passive] {msg}")
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sniff_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=4)
            self._thread = None
        self._tcp_buffers.clear()

    # ------------------------------------------------------------------

    def _sniff_loop(self):
        self.status_cb(
            f"[Passive] Capturing on {'default iface' if not self.iface else self.iface}"
            f"  filter: {self.bpf_filter}"
        )
        self.logger.log(EV_INFO, self.source_label,
                        notes=f"Passive capture started | filter: {self.bpf_filter} | iface: {self.iface or 'default'}")
        # AsyncSniffer rather than sniff(stop_filter=...): a stop filter is
        # only consulted when a packet arrives, so on a quiet line Stop waited
        # for traffic that might never come and the thread outlived the join.
        try:
            from scapy.all import AsyncSniffer
            sniffer = AsyncSniffer(
                iface=self.iface if self.iface else None,
                filter=self.bpf_filter,
                prn=self._handle_packet,
                store=False,
            )
            sniffer.start()
            # The capture runs on scapy's own thread, so a bad filter or a
            # missing Npcap surfaces there rather than here. Watch for it.
            while not self._stop_event.wait(0.5):
                failure = getattr(sniffer, "exception", None)
                if failure is not None or not getattr(sniffer, "running", True):
                    raise RuntimeError(str(failure or "capture ended unexpectedly"))
            try:
                sniffer.stop()
            except Exception:
                pass
        except Exception as e:
            err = str(e)
            self.logger.log(EV_SOCK_ERROR, self.source_label, notes=f"Sniff error: {err}")
            self.status_cb(f"[Passive] Error: {err}")
            return

        self.logger.log(EV_INFO, self.source_label, notes="Passive capture stopped")
        self.status_cb("[Passive] Capture stopped.")

    def _handle_packet(self, pkt):
        if self._stop_event.is_set():
            return

        if not pkt.haslayer(IP):
            return

        ip_layer  = pkt[IP]
        src_ip    = ip_layer.src
        dst_ip    = ip_layer.dst

        # Determine direction relative to our_ip
        if self.our_ip:
            direction = EV_SOCK_RECV if dst_ip == self.our_ip else EV_SOCK_SENT
        else:
            direction = EV_SOCK_RECV   # unknown — just call it recv

        # ── TCP ──────────────────────────────────────────────────────
        if pkt.haslayer(TCP):
            tcp = pkt[TCP]
            src_port = tcp.sport
            dst_port = tcp.dport
            flags    = tcp.flags
            flow_key = (src_ip, src_port, dst_ip, dst_port)
            addr_str = f"{src_ip}:{src_port} → {dst_ip}:{dst_port}"

            # SYN (new connection)
            if "S" in str(flags) and "A" not in str(flags):
                self.logger.log(EV_SOCK_CONN, self.source_label,
                                tag_or_addr=addr_str,
                                notes="TCP SYN (new connection)")

            # FIN / RST (connection end)
            elif "F" in str(flags) or "R" in str(flags):
                self._tcp_buffers.pop(flow_key, None)
                self.logger.log(EV_SOCK_DISC, self.source_label,
                                tag_or_addr=addr_str,
                                notes="TCP FIN/RST")

            # Data payload
            payload = bytes(tcp.payload)
            if payload:
                self._tcp_buffers[flow_key] = self._tcp_buffers.get(flow_key, b"") + payload
                buf = self._tcp_buffers[flow_key]

                # Flush buffer once it stops growing (we log per-packet here;
                # for a real stream reassembler you'd wait for PSH flag or timeout)
                if "P" in str(flags) or len(buf) >= 4096:
                    decoded = _try_decode(buf)
                    self.logger.log(
                        direction,
                        self.source_label,
                        tag_or_addr=addr_str,
                        raw_data=buf,
                        notes=f"{len(buf)} bytes | {decoded[:140]}",
                    )
                    self._tcp_buffers[flow_key] = b""

        # ── UDP ──────────────────────────────────────────────────────
        elif pkt.haslayer(UDP):
            udp      = pkt[UDP]
            src_port = udp.sport
            dst_port = udp.dport
            payload  = bytes(udp.payload)
            addr_str = f"{src_ip}:{src_port} → {dst_ip}:{dst_port}"

            if payload:
                decoded = _try_decode(payload)
                self.logger.log(
                    direction,
                    self.source_label,
                    tag_or_addr=addr_str,
                    raw_data=payload,
                    notes=f"{len(payload)} bytes | {decoded[:140]}",
                )


def _try_decode(data: bytes) -> str:
    try:
        return data.decode("utf-8", errors="replace").replace("\n", "↵").replace("\r", "")
    except Exception:
        return data.hex()


def build_bpf_filter(host: str = "", port: str = "", proto: str = "any") -> str:
    """Helper to build a valid BPF filter string from GUI inputs."""
    parts = []
    if host:
        parts.append(f"host {host}")
    if port:
        proto_prefix = "" if proto == "any" else f"{proto.lower()} "
        parts.append(f"{proto_prefix}port {port}")
    return " and ".join(parts) if parts else ""
