"""
find_plc_port.py
Quick port discovery helper — finds PLC/automation software listening on this machine
or any host, with special knowledge of Mitsubishi and other common PLC ports.

Usage:
    python find_plc_port.py                   # scans localhost
    python find_plc_port.py 192.168.1.50      # scans a remote host
    python find_plc_port.py 192.168.1.50 --all  # full port scan (slow)
"""

import socket
import sys
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ---------------------------------------------------------------------------
# Well-known PLC / automation ports
# ---------------------------------------------------------------------------

KNOWN_PORTS: dict[int, str] = {
    # Mitsubishi
    5006:  "Mitsubishi MC Protocol (3E frame)  ← most common",
    5007:  "Mitsubishi MC Protocol (4E frame)",
    5510:  "Mitsubishi MC Protocol (1E frame)",
    4999:  "Mitsubishi MC Protocol (alt)",
    # Rockwell / Allen-Bradley
    44818: "EtherNet/IP  (Rockwell ControlLogix/CompactLogix)",
    2222:  "EtherNet/IP  (UDP, device identification)",
    # Siemens
    102:   "Siemens S7 ISO-TSAP",
    4840:  "OPC UA",
    # Modbus
    502:   "Modbus TCP",
    503:   "Modbus TCP (alt)",
    # Other automation
    9600:  "Omron FINS",
    9999:  "Omron FINS (alt)",
    20000: "DNP3",
    47808: "BACnet",
    1883:  "MQTT",
    18245: "GE SRTP",
    789:   "Red Lion",
    # Common software ports
    1433:  "SQL Server",
    5432:  "PostgreSQL",
    8080:  "HTTP Alt / Web HMI",
    80:    "HTTP",
    443:   "HTTPS",
}

# Ports to always check, regardless of scan mode
PRIORITY_PORTS = [
    5006, 5007, 5510, 4999,         # Mitsubishi
    44818, 2222,                    # Rockwell
    102, 4840,                      # Siemens / OPC UA
    502, 503,                       # Modbus
    9600,                           # Omron
]

TIMEOUT = 0.5


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def probe_tcp(host: str, port: int, source_ip: str = "") -> bool:
    """Connect to host:port. source_ip pins the outbound NIC (multi-NIC fix)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT)
            if source_ip:
                s.bind((source_ip, 0))
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def try_mitsubishi_handshake(host: str, port: int, source_ip: str = "") -> str | None:
    """
    Send a minimal MC Protocol 3E batch-read request for D0 x1 and check the response.
    Returns a description string if the response looks like MC Protocol, else None.
    source_ip: if set, bind socket to this local IP before connecting (multi-NIC fix).
    """
    # 3E binary frame: read D0 x1
    # Subheader(2BE) + net(1) + PC(1) + IO(2LE) + sta(1) + datalen(2LE) + timer(2LE)
    # + cmd(2LE=0x0401) + sub(2LE=0x0000) + devnum(3LE) + devcode(1=0xA8=D) + points(2LE=1)
    frame = (
        b'\x50\x00'          # subheader (big-endian)
        b'\x00'              # network
        b'\xFF'              # PC
        b'\xFF\x03'          # dest I/O
        b'\x00'              # station
        b'\x0C\x00'          # data length = 12
        b'\x00\x00'          # monitoring timer
        b'\x01\x04'          # command 0x0401 (little-endian)
        b'\x00\x00'          # subcommand 0x0000 (word)
        b'\x00\x00\x00'      # device number 0
        b'\xA8'              # device code D
        b'\x01\x00'          # 1 point
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            if source_ip:
                s.bind((source_ip, 0))
            s.connect((host, port))
            s.sendall(frame)
            resp = s.recv(256)
            if len(resp) >= 9 and resp[0:2] == b'\xD0\x00':
                end_code = struct.unpack("<H", resp[9:11])[0]
                if end_code == 0x0000:
                    return "Mitsubishi MC Protocol 3E — responded to test read of D0, end_code=OK"
                else:
                    return f"Mitsubishi MC Protocol 3E — responded, end_code=0x{end_code:04X} (connected but read error)"
    except Exception:
        pass
    return None


def scan_host(host: str, full_scan: bool = False):
    print(f"\n{'='*64}")
    print(f"  PLC Port Discovery")
    print(f"  Host:  {host}")
    print(f"  Mode:  {'Full scan 1-65535' if full_scan else 'Priority PLC ports + common range 1-10000'}")
    print(f"  Time:  {ts()}")
    print(f"{'='*64}\n")

    # Resolve hostname
    try:
        ip = socket.gethostbyname(host)
        if ip != host:
            print(f"  Resolved {host} → {ip}\n")
    except Exception as e:
        print(f"  ERROR: Cannot resolve {host}: {e}")
        return

    ports_to_scan = PRIORITY_PORTS[:]
    if full_scan:
        ports_to_scan = list(range(1, 65536))
    else:
        # Add common port range
        ports_to_scan += list(range(1, 10001))
        ports_to_scan = sorted(set(ports_to_scan))

    print(f"  Scanning {len(ports_to_scan)} ports with {TIMEOUT}s timeout...\n")

    open_ports: list[int] = []
    lock = threading.Lock()
    done = [0]
    total = len(ports_to_scan)

    def probe(port):
        result = probe_tcp(ip, port)
        with lock:
            done[0] += 1
            if done[0] % 1000 == 0 or done[0] == total:
                pct = 100 * done[0] // total
                print(f"  ... {done[0]}/{total} ({pct}%)", end="\r")
            if result:
                open_ports.append(port)
        return result

    with ThreadPoolExecutor(max_workers=256) as ex:
        futures = {ex.submit(probe, p): p for p in ports_to_scan}
        for f in as_completed(futures):
            pass

    print(f"\n\n  {'─'*56}")
    print(f"  Open ports found: {len(open_ports)}")
    print(f"  {'─'*56}\n")

    if not open_ports:
        print("  No open ports found.")
        print("  Check that the PLC software is running and the firewall allows connections.\n")
        return

    mitsubishi_confirmed = []

    for port in sorted(open_ports):
        known = KNOWN_PORTS.get(port, "")
        marker = " ◄◄" if port in KNOWN_PORTS else ""
        print(f"  Port {port:5d}  {known or '(unknown service)'}{marker}")

        # For likely Mitsubishi ports, attempt a real handshake
        if port in (5006, 5007, 5510, 4999) or (not known and port > 1024):
            mitsub = try_mitsubishi_handshake(ip, port)
            if mitsub:
                print(f"            ✓ {mitsub}")
                mitsubishi_confirmed.append(port)

    print(f"\n  {'─'*56}")
    if mitsubishi_confirmed:
        print(f"\n  ✓ CONFIRMED Mitsubishi MC Protocol on port(s): {', '.join(map(str, mitsubishi_confirmed))}")
        print(f"\n  In PLC Monitor, use:")
        print(f"    PLC Type:  Mitsubishi (MC Proto)")
        print(f"    IP:        {host}")
        print(f"    Port:      {mitsubishi_confirmed[0]}")
    else:
        print(f"\n  No confirmed Mitsubishi MC Protocol response.")
        if open_ports:
            print(f"  Try these ports manually in PLC Monitor: {sorted(open_ports)[:10]}")

    print(f"\n  Scan complete at {ts()}")
    print(f"{'='*64}\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--all" in sys.argv

    host = args[0] if args else "127.0.0.1"
    scan_host(host, full_scan=full)
    input("\nPress Enter to exit...")
