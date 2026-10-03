"""
network_discovery.py
Active connection viewer (netstat + process names), port scanner,
and PLC handshake verification for Rockwell, Siemens, and Mitsubishi.
Requires: pip install psutil
"""

from __future__ import annotations
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


# ---------------------------------------------------------------------------
# PLC handshake verification
# ---------------------------------------------------------------------------

PLC_VERIFY_TIMEOUT = 2.0   # seconds per attempt

def verify_mitsubishi(host: str, port: int, source_ip: str = "") -> tuple[bool, str]:
    """
    Send a minimal MC Protocol 3E batch-read of D0 x1.
    Returns (confirmed, description).
    source_ip: if set, bind socket to this local IP (multi-NIC fix).
    """
    frame = (
        b'\x50\x00'          # subheader big-endian 0x5000
        b'\x00'              # network no
        b'\xFF'              # PC no
        b'\xFF\x03'          # dest I/O
        b'\x00'              # station
        b'\x0C\x00'          # data length = 12
        b'\x00\x00'          # monitoring timer
        b'\x01\x04'          # cmd 0x0401 LE
        b'\x00\x00'          # subcommand 0x0000 word
        b'\x00\x00\x00'      # device number 0
        b'\xA8'              # device code D
        b'\x01\x00'          # 1 point
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(PLC_VERIFY_TIMEOUT)
            if source_ip:
                s.bind((source_ip, 0))
            s.connect((host, port))
            s.sendall(frame)
            resp = s.recv(256)
        if len(resp) >= 11 and resp[0:2] == b'\xD0\x00':
            end_code = struct.unpack("<H", resp[9:11])[0]
            d0 = struct.unpack("<H", resp[11:13])[0] if len(resp) >= 13 else 0
            if end_code == 0x0000:
                return True, f"Mitsubishi MC Proto 3E  (D0={d0}, end=OK)"
            return True, f"Mitsubishi MC Proto 3E  (end=0x{end_code:04X}, device responded)"
    except Exception:
        pass
    return False, ""


def verify_rockwell(host: str, port: int, source_ip: str = "") -> tuple[bool, str]:
    """
    Send EtherNet/IP List Identity (0x0063) and parse response.
    Returns (confirmed, description).
    source_ip: if set, bind socket to this local IP (multi-NIC fix).
    """
    frame = (
        b'\x63\x00'                              # command List Identity
        b'\x00\x00'                              # length
        b'\x00\x00\x00\x00'                      # session
        b'\x00\x00\x00\x00'                      # status
        b'\x00\x00\x00\x00\x00\x00\x00\x00'     # sender context
        b'\x00\x00\x00\x00'                      # options
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(PLC_VERIFY_TIMEOUT)
            if source_ip:
                s.bind((source_ip, 0))
            s.connect((host, port))
            s.sendall(frame)
            resp = s.recv(512)
        if len(resp) >= 12:
            cmd    = struct.unpack("<H", resp[0:2])[0]
            status = struct.unpack("<I", resp[8:12])[0]
            if cmd == 0x0063 and status == 0:
                # Extract product name — length-prefixed string somewhere in payload
                name = ""
                try:
                    payload = resp[24:]
                    for i in range(len(payload) - 1):
                        slen = payload[i]
                        if 4 <= slen <= 64 and i + 1 + slen <= len(payload):
                            candidate = payload[i+1:i+1+slen]
                            if all(32 <= b < 127 for b in candidate):
                                name = candidate.decode("ascii")
                                break
                except Exception:
                    pass
                desc = f"Rockwell EtherNet/IP"
                if name:
                    desc += f"  (product='{name}')"
                return True, desc
    except Exception:
        pass
    return False, ""


def verify_siemens(host: str, port: int, source_ip: str = "") -> tuple[bool, str]:
    """
    Send ISO-TSAP COTP Connection Request and check for CC (Connection Confirm).
    Returns (confirmed, description).
    source_ip: if set, bind socket to this local IP (multi-NIC fix).
    """
    frame = bytes([
        0x03, 0x00, 0x00, 0x16,       # TPKT: v3, len 22
        0x11, 0xE0, 0x00, 0x00,       # COTP CR, dst ref=0
        0x00, 0x01, 0x00,             # src ref=1, class=0
        0xC0, 0x01, 0x0A,             # tpdu-size parameter
        0xC1, 0x02, 0x01, 0x00,       # src TSAP
        0xC2, 0x02, 0x01, 0x02,       # dst TSAP (slot 2)
    ])
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(PLC_VERIFY_TIMEOUT)
            if source_ip:
                s.bind((source_ip, 0))
            s.connect((host, port))
            s.sendall(frame)
            resp = s.recv(256)
        if resp and resp[0] == 0x03 and len(resp) >= 5:
            cotp_code = resp[4]
            if cotp_code == 0xD0:
                return True, "Siemens S7 ISO-TSAP  (COTP Connection Confirm)"
            if cotp_code == 0x70:
                return True, "Siemens S7 ISO-TSAP  (TPKT DT Data — device active)"
    except Exception:
        pass
    return False, ""


# Ports that have known PLC verifiers
# Each entry: port → [(verify_fn, display_name, driver_key), ...]
PORT_VERIFIERS: dict[int, list[tuple]] = {
    44818: [(verify_rockwell,   "Rockwell (pycomm3)")],
    2222:  [(verify_rockwell,   "Rockwell (pycomm3)")],
    102:   [(verify_siemens,    "Siemens (snap7)")],
    5006:  [(verify_mitsubishi, "Mitsubishi (MC Proto)")],
    5007:  [(verify_mitsubishi, "Mitsubishi (MC Proto)")],
    5510:  [(verify_mitsubishi, "Mitsubishi (MC Proto)")],
    4999:  [(verify_mitsubishi, "Mitsubishi (MC Proto)")],
}

# Ports that auto-trigger verification when found open
AUTO_VERIFY_PORTS = set(PORT_VERIFIERS.keys())

_ALL_VERIFIERS = [
    (verify_rockwell,   "Rockwell (pycomm3)"),
    (verify_siemens,    "Siemens (snap7)"),
    (verify_mitsubishi, "Mitsubishi (MC Proto)"),
]


def verify_port(host: str, port: int, source_ip: str = "") -> tuple[bool, str, str]:
    """
    Try PLC handshake verifiers for this port.
    For known PLC ports, tries the expected verifier first.
    For unknown ports, tries all three.
    Returns (confirmed, plc_driver_key, description).
    source_ip: if set, bind outbound sockets to this local IP (multi-NIC fix).
    """
    verifiers = PORT_VERIFIERS.get(port, _ALL_VERIFIERS)
    for fn, driver_key in verifiers:
        ok, desc = fn(host, port, source_ip)
        if ok:
            return True, driver_key, desc
    return False, "", ""


# ---------------------------------------------------------------------------
# Active connections
# ---------------------------------------------------------------------------

def get_connections() -> list[dict]:
    """
    Returns all active TCP/UDP connections with process name.
    Falls back to a basic socket-level list if psutil isn't available.
    """
    rows = []

    if not HAS_PSUTIL:
        return [{"error": "psutil not installed — run: pip install psutil"}]

    # Build pid → name map once
    pid_names: dict[int, str] = {}
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid_names[proc.info["pid"]] = proc.info["name"]
            except Exception:
                pass
    except Exception:
        pass

    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        # On Windows, non-admin users can't see all connections
        try:
            conns = psutil.net_connections(kind="inet4")
        except Exception:
            return [{"error": "Access denied — try running as Administrator for full results"}]

    for c in conns:
        laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
        pid   = c.pid or 0
        rows.append({
            "proto":    "TCP" if c.type == socket.SOCK_STREAM else "UDP",
            "local":    laddr,
            "remote":   raddr,
            "state":    c.status or "",
            "pid":      str(pid) if pid else "",
            "process":  pid_names.get(pid, "") if pid else "",
            "lport":    str(c.laddr.port) if c.laddr else "",
            "rhost":    c.raddr.ip  if c.raddr else "",
            "rport":    str(c.raddr.port) if c.raddr else "",
        })

    # Sort: ESTABLISHED first, then by process name
    order = {"ESTABLISHED": 0, "LISTEN": 1, "TIME_WAIT": 2, "CLOSE_WAIT": 3}
    rows.sort(key=lambda r: (order.get(r["state"], 9), r["process"].lower()))
    return rows


# ---------------------------------------------------------------------------
# Port scanner
# ---------------------------------------------------------------------------

#: How hard a scan pushes. Industrial devices have thin TCP stacks and some
#: fall over under an aggressive scan, so the default is the gentle one and the
#: fast one says where it belongs. The same rule NetControl keeps: gentle by
#: default, anything faster opt-in and labelled. (connections at once, pause
#: in seconds before each connect)
SCAN_PROFILES: dict[str, tuple[int, float]] = {
    "gentle": (4, 0.05),
    "normal": (32, 0.0),
    "fast":   (128, 0.0),
}

SCAN_PROFILE_LABELS: dict[str, str] = {
    "gentle": "Gentle - 4 at a time (safe on a live line)",
    "normal": "Normal - 32 at a time",
    "fast":   "Fast - 128 at a time (bench only)",
}

#: The ports worth asking a plant device about, which is most of what anybody
#: scans for here. A full range is still there for when this list misses.
PLC_PORTS: list[int] = sorted({
    21, 22, 23, 80, 102, 443, 502, 503, 789, 1883, 2222, 4000, 4840, 4999,
    5006, 5007, 5510, 8080, 9600, 18245, 20000, 44818, 47808,
})


def scan_ports(
    host: str,
    port_start: int = 1,
    port_end: int = 0,
    timeout: float = 0.4,
    max_workers: int = 4,
    progress_cb: Callable[[int, int], None] | None = None,
    result_cb: Callable[[int, str], None] | None = None,
    stop_event: threading.Event | None = None,
    source_ip: str = "",
    ports: list[int] | None = None,
    pause: float = 0.05,
) -> list[dict]:
    """
    TCP connect scan across [port_start, port_end].

    progress_cb(scanned, total) — called periodically
    result_cb(port, banner)     — called immediately when an open port is found
    stop_event                  — set to cancel mid-scan
    source_ip                   — if set, bind outbound sockets to this local IP

    Returns list of {"port": int, "banner": str} for open ports.
    """
    try:
        host_ip = socket.gethostbyname(host)
    except socket.gaierror as e:
        return [{"port": 0, "banner": f"DNS error: {e}"}]

    ports = list(ports) if ports else list(range(port_start, port_end + 1))
    total = len(ports)
    open_ports: list[dict] = []
    scanned = 0
    lock = threading.Lock()

    def probe(port: int) -> tuple[int, str] | None:
        if stop_event and stop_event.is_set():
            return None
        if pause > 0:
            time.sleep(pause)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if source_ip:
                    s.bind((source_ip, 0))
                if s.connect_ex((host_ip, port)) == 0:
                    # Grab banner (best effort)
                    banner = ""
                    try:
                        s.settimeout(0.3)
                        data = s.recv(256)
                        banner = data.decode("utf-8", errors="replace").strip()[:80]
                    except Exception:
                        pass
                    return port, banner
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(probe, p): p for p in ports}
        for future in as_completed(futures):
            if stop_event and stop_event.is_set():
                break
            result = future.result()
            with lock:
                scanned += 1
                if result is not None:
                    port, banner = result
                    entry = {"port": port, "banner": banner}
                    open_ports.append(entry)
                    if result_cb:
                        result_cb(port, banner)
                if progress_cb and (scanned % 20 == 0 or scanned == total):
                    progress_cb(scanned, total)

    open_ports.sort(key=lambda r: r["port"])
    return open_ports


# ---------------------------------------------------------------------------
# Common port name lookup
# ---------------------------------------------------------------------------

WELL_KNOWN_PORTS: dict[int, str] = {
    20: "FTP data", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP",
    53: "DNS", 80: "HTTP", 102: "Siemens S7 (ISO-TSAP)", 110: "POP3",
    135: "RPC", 139: "NetBIOS", 143: "IMAP", 161: "SNMP", 443: "HTTPS",
    445: "SMB", 502: "Modbus TCP", 503: "Modbus TCP (alt)", 789: "Red Lion",
    1433: "SQL Server", 1494: "Citrix", 1883: "MQTT", 2000: "Cisco SCCP",
    2222: "EtherNet/IP (UDP)", 4840: "OPC UA", 5006: "Mitsubishi MC Proto",
    5007: "Mitsubishi MC Proto (alt)", 5900: "VNC", 8080: "HTTP Alt",
    8883: "MQTT TLS", 9600: "Omron FINS", 18245: "GE SRTP",
    20000: "DNP3", 44818: "EtherNet/IP / Rockwell", 47808: "BACnet",
    4000: "Emerson / ROC", 4999: "Mitsubishi MC Proto (alt)",
    5510: "Mitsubishi MC Proto (1E)",
}

def port_label(port: int) -> str:
    return WELL_KNOWN_PORTS.get(port, "")
