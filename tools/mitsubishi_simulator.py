"""
mitsubishi_simulator.py
Mitsubishi MC Protocol 3E Frame Simulator (Binary mode)

Correctly implements the 3E binary frame format as used by pymcprotocol:

  REQUEST:
    [0:2]   Subheader  0x5000  (BIG endian → 0x50 0x00)
    [2]     Network No
    [3]     PC No
    [4:6]   Dest I/O   (little endian)
    [6]     Dest Station
    [7:9]   Data length (little endian) = 2(timer) + len(cmd data)
    [9:11]  CPU monitoring timer (little endian)
    [11:13] Command    (little endian, 0x0401 = batch read)
    [13:15] Subcommand (0x0000 word / 0x0001 bit for Q/L/iQ-L)
    [15:18] Device number (3 bytes, little endian)
    [18]    Device code byte
    [19:21] Number of points (2 bytes, little endian)

  RESPONSE:
    [0:2]   Subheader  0xD000  (BIG endian → 0xD0 0x00)
    [2]     Network No
    [3]     PC No
    [4:6]   Dest I/O
    [6]     Dest Station
    [7:9]   Data length (little endian)
    [9:11]  End code   (0x0000 = OK, little endian)
    [11:]   Response data

Usage:
  python mitsubishi_simulator.py [port]    default: 5006

In PLC Monitor:
  PLC Type:  Mitsubishi (MC Proto)
  IP:        127.0.0.1
  Port:      5006  (or whatever you chose)

Test tags:
  D100  counter (increments every 5s)
  D101  temperature x10 (sine wave 200-300)
  D102  speed RPM (sine wave 1400-1500)
  D200  fault code (non-zero briefly every 60s)
  M100  motor running (stays ON)
  M101  conveyor (toggles every 8s)
  M200  alarm (pulses every 30s)
  Y0    output (toggles every 3s)
  X0    input (follows Y0)
"""

import socket
import struct
import threading
import time
import math
import sys
import random
from datetime import datetime

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5006

# ---------------------------------------------------------------------------
# Device code map  (matches pymcprotocol mcprotocolconst.py exactly)
# ---------------------------------------------------------------------------
# code → (is_bit, mem_key)
DEVICE_CODES: dict[int, tuple[bool, str]] = {
    0x91: (True,  "SM"),
    0xA9: (False, "SD"),
    0x9C: (True,  "X"),
    0x9D: (True,  "Y"),
    0x90: (True,  "M"),
    0x92: (True,  "L"),
    0x93: (True,  "F"),
    0x94: (True,  "V"),
    0xA0: (True,  "B"),
    0xA8: (False, "D"),
    0xB4: (False, "W"),
    # Timers:  TS=contact(bit), TC=coil(bit), TN=current value(word)
    0xC1: (True,  "TS"),
    0xC0: (True,  "TC"),
    0xC2: (False, "TN"),
    # Retentive timers
    0xC7: (True,  "STS"),
    0xC6: (True,  "STC"),
    0xC8: (False, "STN"),
    # Counters: CC=contact/coil(bit), CN=current value(word), CS=set value(word)
    0xC3: (True,  "CC"),
    0xC5: (False, "CN"),
    0xC4: (False, "CS"),
    0xA1: (True,  "SB"),
    0xB5: (False, "SW"),
    0xA2: (True,  "DX"),
    0xA3: (True,  "DY"),
    0xAF: (False, "R"),
    0xB0: (False, "ZR"),
}

# Reverse: name → code (for display)
DEVICE_NAMES: dict[str, int] = {v[1]: k for k, v in DEVICE_CODES.items()}

# ---------------------------------------------------------------------------
# Simulated memory
# ---------------------------------------------------------------------------
WORD_MEM: dict[str, list[int]] = {k: [0]*8192 for k in
    ["D","W","TN","CN","CS","STN","SD","R","ZR","SW"]}
BIT_MEM: dict[str, list[int]]  = {k: [0]*8192 for k in
    ["M","X","Y","B","L","F","V","SM","SB","DX","DY","TC","TS","CC","STS","STC"]}

def _init():
    WORD_MEM["D"][100] = 1234
    WORD_MEM["D"][101] = 235
    WORD_MEM["D"][102] = 1450
    WORD_MEM["D"][103] = 3
    WORD_MEM["D"][200] = 0
    for i in range(10):
        WORD_MEM["D"][500+i] = i*100
    # D300: a word with individual bits that rotate — good for testing D300.0, D300.1, etc.
    WORD_MEM["D"][300] = 0b0000000010101010
    # Timer current values
    WORD_MEM["TN"][0] = 150
    WORD_MEM["TN"][1] = 0
    # Counter current values
    WORD_MEM["CN"][0] = 0     # CC0 current value — will increment
    WORD_MEM["CN"][1] = 500   # CC1 — static preset value
    WORD_MEM["CS"][0] = 1000  # CC0 set value (preset)
    WORD_MEM["CS"][1] = 500
    # Bits
    BIT_MEM["SM"][400] = 1
    BIT_MEM["M"][100]  = 1
    BIT_MEM["M"][101]  = 0
    BIT_MEM["M"][200]  = 0
    BIT_MEM["Y"][0]    = 1
    BIT_MEM["X"][0]    = 1
    BIT_MEM["CC"][0]   = 0    # counter 0 contact (OFF until count reaches set value)
    BIT_MEM["TC"][0]   = 0    # timer 0 contact

_init()
_lock = threading.Lock()
_t0   = time.time()

def _sim_loop():
    while True:
        time.sleep(0.5)
        t = time.time() - _t0
        with _lock:
            WORD_MEM["D"][100] = int(t/5) % 65536
            WORD_MEM["D"][101] = int(250 + 50*math.sin(t/10))
            WORD_MEM["D"][102] = int(1450 + 50*math.sin(t/7))
            BIT_MEM["M"][101]  = 1 if int(t/8)%2==0 else 0
            BIT_MEM["M"][200]  = 1 if int(t)%30==0  else 0
            BIT_MEM["Y"][0]    = 1 if int(t/3)%2==0 else 0
            BIT_MEM["X"][0]    = BIT_MEM["Y"][0]
            WORD_MEM["D"][200] = (0x0101 if random.random()<0.5 else 0x0203) if int(t)%60==45 else 0
            # D300: rotate bits left every 2s — individual D300.N reads will toggle
            rot = int(t / 2) % 16
            WORD_MEM["D"][300] = ((0b10101010 << rot) | (0b10101010 >> (16 - rot))) & 0xFFFF
            cn0 = int(t/2) % WORD_MEM["CS"][0]
            WORD_MEM["CN"][0] = cn0
            BIT_MEM["CC"][0]  = 1 if cn0 >= WORD_MEM["CS"][0]-1 else 0
            # Timer TN0: counts 0-200 in a 20s cycle
            WORD_MEM["TN"][0] = int((t % 20) / 20 * 200)
            BIT_MEM["TC"][0]  = 1 if WORD_MEM["TN"][0] >= 150 else 0

threading.Thread(target=_sim_loop, daemon=True).start()

# ---------------------------------------------------------------------------
# Frame constants
# ---------------------------------------------------------------------------
REQ_SUBHEADER  = b'\x50\x00'   # big-endian 0x5000
RESP_SUBHEADER = b'\xD0\x00'   # big-endian 0xD000
CMD_BATCH_READ = 0x0401
SUB_WORD       = 0x0000
SUB_BIT        = 0x0001        # Q/L/QnA/iQ-L bit read subcommand

def _ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------
def _make_response(payload: bytes, end_code: int = 0x0000) -> bytes:
    """
    Build a 3E binary response frame.
    Header = subheader(2) + net(1) + pc(1) + io(2) + sta(1) + datalen(2)
    Then end_code(2) + payload
    """
    body    = struct.pack("<H", end_code) + payload
    header  = (
        RESP_SUBHEADER
        + b'\x00'           # network
        + b'\xFF'           # PC
        + struct.pack("<H", 0xFF03)   # dest I/O
        + b'\x00'           # station
        + struct.pack("<H", len(body))
    )
    return header + body

def _err(code: int) -> bytes:
    return _make_response(b"", code)

# ---------------------------------------------------------------------------
# Request parser
# ---------------------------------------------------------------------------
MIN_FRAME = 21   # minimum valid 3E batch read frame length

def _process_frame(data: bytes) -> bytes | None:
    """Parse one complete 3E request frame and return the response."""

    # Validate subheader (bytes 0-1, big endian)
    if data[0:2] != REQ_SUBHEADER:
        print(f"  {_ts()}  BAD subheader: {data[0:2].hex()}")
        return _err(0xC059)

    if len(data) < 9:
        return None  # need more

    data_len = struct.unpack("<H", data[7:9])[0]
    frame_len = 9 + data_len
    if len(data) < frame_len:
        return None  # wait for rest

    # timer at [9:11]
    # command at [11:13]
    # subcommand at [13:15]
    if frame_len < 15:
        return _err(0xC059)

    command    = struct.unpack("<H", data[11:13])[0]
    subcommand = struct.unpack("<H", data[13:15])[0]

    if command != CMD_BATCH_READ:
        print(f"  {_ts()}  Unsupported command: 0x{command:04X}")
        return _err(0xC059)

    # Batch read payload: device_number(3) + device_code(1) + num_points(2)
    if frame_len < 21:
        return _err(0xC059)

    dev_num    = struct.unpack("<I", data[15:18] + b'\x00')[0]
    dev_code   = data[18]
    num_points = struct.unpack("<H", data[19:21])[0]

    if dev_code not in DEVICE_CODES:
        print(f"  {_ts()}  Unknown device code: 0x{dev_code:02X}")
        return _err(0x4004)

    is_bit, mem_key = DEVICE_CODES[dev_code]
    dev_name_str = mem_key

    print(f"  {_ts()}  READ {dev_name_str}{dev_num} x{num_points}  {'(bit)' if is_bit else '(word)'}")

    mem = BIT_MEM.get(mem_key) if is_bit else WORD_MEM.get(mem_key)
    if mem is None:
        return _err(0x4004)

    if dev_num + num_points > len(mem):
        print(f"  {_ts()}  Address out of range: {dev_name_str}{dev_num}+{num_points}")
        return _err(0x4003)

    with _lock:
        values = list(mem[dev_num: dev_num + num_points])

    # Encode response data
    if subcommand == SUB_BIT:
        # Bit response: 2 bits per byte (low nibble = first point)
        resp = bytearray()
        for i in range(0, len(values), 2):
            lo = values[i] & 0x01
            hi = (values[i+1] & 0x01) if i+1 < len(values) else 0
            resp.append((hi << 4) | lo)
        return _make_response(bytes(resp))
    else:
        # Word response: 2 bytes per point, little endian
        resp = bytearray()
        for v in values:
            resp += struct.pack("<H", v & 0xFFFF)
        return _make_response(bytes(resp))

# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------
def _handle_client(conn: socket.socket, addr):
    print(f"  {_ts()}  Client connected: {addr}")
    buf = b""
    conn.settimeout(60)
    try:
        while True:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk

            # Drain complete frames from buffer
            while True:
                if len(buf) < 9:
                    break
                data_len  = struct.unpack("<H", buf[7:9])[0]
                frame_len = 9 + data_len
                if len(buf) < frame_len:
                    break

                frame = buf[:frame_len]
                buf   = buf[frame_len:]

                resp = _process_frame(frame)
                if resp:
                    conn.sendall(resp)

    except ConnectionResetError:
        pass
    except Exception as e:
        if "timed out" not in str(e).lower():
            print(f"  {_ts()}  Client error ({addr}): {e}")
    finally:
        conn.close()
        print(f"  {_ts()}  Client disconnected: {addr}")


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(5)
    srv.settimeout(1)

    print()
    print("=" * 60)
    print("  Mitsubishi MC Protocol 3E Simulator")
    print("=" * 60)
    print(f"  Listening on 0.0.0.0:{PORT}")
    print()
    print("  Connect PLC Monitor with:")
    print(f"    Type:  Mitsubishi (MC Proto)")
    print(f"    IP:    127.0.0.1    Port: {PORT}")
    print()
    print("  Standard test tags:")
    print("    D100   counter  (increments every 5s)")
    print("    D101   temp x10 (sine wave 200-300)")
    print("    D102   RPM      (sine wave 1400-1500)")
    print("    D200   fault    (non-zero every 60s)")
    print("    M100   motor    (stays ON)")
    print("    M101   conveyor (toggles every 8s)")
    print("    Y0     output   (toggles every 3s)")
    print("    X0     input    (follows Y0)")
    print()
    print("  Counter/Timer tags (these were the crash culprits):")
    print("    CN0    counter current value (0→999, resets)")
    print("    CS0    counter set value     (1000, fixed)")
    print("    CC0    counter contact       (bit, ON near rollover)")
    print("    TN0    timer current value   (0-200, 20s cycle)")
    print("    TC0    timer contact         (bit, ON when TN0>=150)")
    print()
    print("  Bit-of-word tags (D300 has rotating bit pattern):")
    print("    D300     full word value (rotating)")
    print("    D300.0   bit 0 of D300   (toggles independently)")
    print("    D300.1   bit 1 of D300")
    print("    D300.7   bit 7 of D300")
    print()
    print("  To find the port of a real Mitsubishi program:")
    print(f"    python find_plc_port.py 127.0.0.1")
    print()
    print("  Press Ctrl-C to stop.")
    print("=" * 60)
    print()

    try:
        while True:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=_handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n  Simulator stopped.")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
