"""
plc_drivers.py
PLC communication drivers for:
  - Rockwell (pycomm3) - recommended for ControlLogix/CompactLogix
  - Rockwell (libplctag) - via ctypes, supports more PLC families
  - Siemens S7 (python-snap7)
  - Mitsubishi (pymcprotocol)

Each driver exposes:
  connect()         -> (bool, error_str)
  disconnect()
  read_tag(tag)     -> (value, error_str | None)
  read_tags(tags)   -> {tag: (value, error_str | None[, seen_ns])}
  read_block(address, count) -> (list of 16-bit register values | None, error_str | None)
  is_connected()    -> bool
"""

from __future__ import annotations
import contextlib
import importlib
import socket as _socket_module
from typing import Any

from app.engine import clock


# ---------------------------------------------------------------------------
# Multi-NIC helper
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _bind_to_source_ip(source_ip: str):
    """
    Forces all new TCP sockets opened inside this context to bind to
    source_ip before connecting, ensuring outbound packets use the correct
    NIC when multiple network interfaces are active.

    Replaces socket.socket in the socket module with a subclass whose
    connect() calls bind() first. Restored on exit regardless of exceptions.
    No-op when source_ip is empty.
    """
    if not source_ip:
        yield
        return

    _orig = _socket_module.socket

    class _BoundSocket(_orig):
        def connect(self, address):
            try:
                self.bind((source_ip, 0))
            except OSError:
                pass  # already bound or bind unsupported — let connect() try anyway
            super().connect(address)

    _socket_module.socket = _BoundSocket
    try:
        yield
    finally:
        _socket_module.socket = _orig


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class PLCDriver:
    PLC_TYPE = "Base"

    def connect(self) -> tuple[bool, str]:
        raise NotImplementedError

    def disconnect(self):
        pass

    def read_tag(self, tag: str) -> tuple[Any, str | None]:
        raise NotImplementedError

    def read_tags(self, tags: list[str]) -> dict[str, tuple]:
        """Read several tags. The default reads them one at a time and stamps
        each as its answer arrives; a driver that can batch overrides this."""
        out: dict[str, tuple] = {}
        for tag in tags:
            value, err = self.read_tag(tag)
            out[tag] = (value, err, clock.now_ns())
        return out

    def read_block(self, address: str, count: int) -> tuple[list[int] | None, str | None]:
        """`count` consecutive 16-bit registers from `address`, in ONE request
        so they all come from the same PLC scan -- the point of a grouped
        tag. A driver that cannot do that says so rather than reading them
        one by one and handing back a mixture."""
        return None, f"{self.PLC_TYPE} cannot read grouped tags."

    def is_connected(self) -> bool:
        return False

    @staticmethod
    def available() -> tuple[bool, str]:
        return False, "Not implemented"


# ---------------------------------------------------------------------------
# Rockwell / Allen-Bradley  (pycomm3)
# ---------------------------------------------------------------------------

class RockwellDriver(PLCDriver):
    """
    pycomm3-based driver for ControlLogix / CompactLogix.
    slot = the chassis slot the CPU sits in (not the ENB/comm module slot).
    e.g. if ENB is slot 0 and CPU is slot 1, set slot=1.
    """
    PLC_TYPE = "Rockwell (pycomm3)"

    def __init__(self, ip: str, slot: int = 0, port: int = 44818, source_ip: str = ""):
        self.ip = ip
        self.slot = slot
        self.port = port
        self.source_ip = source_ip
        self._plc = None

    @staticmethod
    def available() -> tuple[bool, str]:
        try:
            importlib.import_module("pycomm3")
            return True, ""
        except ImportError:
            return False, "pycomm3 not installed — run: pip install pycomm3"

    def connect(self) -> tuple[bool, str]:
        try:
            from pycomm3 import LogixDriver
            # init_tags=True reads the PLC tag database — required for UDTs and
            # program-scoped tags. Adds ~1-3s to connect time but is essential.
            path = f"{self.ip}/{self.slot}"
            self._plc = LogixDriver(path, init_tags=True)
            with _bind_to_source_ip(self.source_ip):
                opened = self._plc.open()
            # pycomm3 reports a refused session by returning False, not by
            # raising. Before 1.2 that was taken as success: the chip went
            # green and every read failed with a message about Forward Open.
            if opened is False:
                self.disconnect()
                return False, ("The device took the connection but did not register an "
                               "EtherNet/IP session. Check the IP address, and that it is a "
                               "ControlLogix/CompactLogix.")
            return True, ""
        except Exception as e:
            self._plc = None
            return False, str(e)

    def disconnect(self):
        if self._plc:
            try:
                self._plc.close()
            except Exception:
                pass
            self._plc = None

    def read_tag(self, tag: str) -> tuple[Any, str | None]:
        if not self._plc:
            return None, "Not connected"
        try:
            result = self._plc.read(tag)
            if result is None:
                return None, (
                    f"Tag '{tag}' not found. Tips:\n"
                    f"  • Slot must be the CPU slot, not the ENB/comm module slot\n"
                    f"  • UDT member: MyStruct.MemberName\n"
                    f"  • Program-scoped: Program:MainProgram.TagName\n"
                    f"  • Array element: MyArray[0]\n"
                    f"  • Tag names are case-sensitive"
                )
            if result.error:
                return None, f"{result.error}  (tag: {tag})"
            return result.value, None
        except Exception as e:
            return None, str(e)

    def read_tags(self, tags: list[str]) -> dict[str, tuple]:
        """All tags in one call: pycomm3 packs them into as few
        multi-service requests as fit, so ten tags cost one round trip, not
        ten. If the batch as a whole fails (a malformed name can make pycomm3
        raise rather than report), fall back to one at a time, so one bad tag
        costs speed rather than every other tag's value."""
        if not self._plc:
            return {t: (None, "Not connected") for t in tags}
        if len(tags) == 1:
            return {tags[0]: self.read_tag(tags[0])}
        try:
            results = self._plc.read(*tags)
        except Exception:
            return super().read_tags(tags)
        if not isinstance(results, list) or len(results) != len(tags):
            return super().read_tags(tags)
        out: dict[str, tuple] = {}
        for tag, result in zip(tags, results):
            if result is None:
                out[tag] = self.read_tag(tag)
            elif result.error:
                out[tag] = (None, f"{result.error}  (tag: {tag})")
            else:
                out[tag] = (result.value, None)
        return out

    def read_block(self, address: str, count: int):
        """An array slice, `Data[16]` x4, in one request: pycomm3's
        `Data[16]{4}` element-count syntax. INT/SINT/DINT elements come back
        as their low 16 bits each; a grouped tag on a Logix PLC is for INT
        arrays holding text or split numbers, as other vendors' PLCs send them."""
        if not self._plc:
            return None, "Not connected"
        try:
            result = self._plc.read(f"{address}{{{count}}}" if count > 1 else address)
        except Exception as e:
            return None, str(e)
        if result is None:
            return None, f"Tag '{address}' not found"
        if result.error:
            return None, f"{result.error}  (tag: {address})"
        values = result.value if isinstance(result.value, list) else [result.value]
        try:
            words = [int(v) & 0xFFFF for v in values]
        except (TypeError, ValueError):
            return None, f"'{address}' is not an array of integers"
        if len(words) != count:
            return None, f"Asked for {count} elements of '{address}', got {len(words)}"
        return words, None

    def is_connected(self) -> bool:
        return self._plc is not None


# ---------------------------------------------------------------------------
# Rockwell / Allen-Bradley  (libplctag — ctypes, no PyPI package needed)
# ---------------------------------------------------------------------------

class RockwellLibplctagDriver(PLCDriver):
    """
    Rockwell driver using libplctag via our own ctypes wrapper.
    Requires plctag.dll (Windows) or libplctag.so (Linux) placed next to
    the application, or on the system PATH.

    build.bat will download plctag.dll automatically from GitHub releases.

    Key config fields:
      cip_path   CIP backplane path e.g. "1,0" = port 1, slot 0
                   "1,1" if CPU is in slot 1 (ENB in slot 0)
                   ""    for direct-connect MicroLogix/Micro800
      cpu        lgx, micro800, plc, slc, micrologix, omron-njnx ...
      data_type  BOOL, SINT, INT, DINT, REAL, LREAL, STRING ...
      protocol   ab-eip (default), ab-pccc, modbus-tcp
      debug      0 (none) ... 5 (very chatty) — writes to stdout
      timeout    milliseconds for each operation
    """
    PLC_TYPE = "Rockwell (libplctag)"

    def __init__(self, ip: str, cip_path: str = "1,0", cpu: str = "lgx",
                 protocol: str = "ab-eip", data_type: str = "DINT",
                 elem_count: int = 1, timeout: int = 5000, debug: int = 0,
                 # These accept the old positional args from create_driver without error:
                 port: int = 44818, slot: int = 0):
        self.ip         = ip
        self.cip_path   = cip_path
        self.cpu        = cpu
        self.protocol   = protocol
        self.data_type  = data_type
        self.elem_count = elem_count
        self.timeout    = timeout
        self.debug      = debug
        self._connected = False
        self._tag_ids: dict[str, int] = {}
        self._lib = None

    @staticmethod
    def available() -> tuple[bool, str]:
        from app.engine.libplctag_wrapper import load_library
        return load_library()

    def connect(self) -> tuple[bool, str]:
        from app.engine.libplctag_wrapper import load_library, get_lib
        ok, msg = load_library()
        if not ok:
            return False, msg
        self._lib = get_lib()
        self._connected = True
        return True, ""

    def disconnect(self):
        if self._lib:
            for tag_id in list(self._tag_ids.values()):
                try:
                    self._lib.plc_tag_destroy(tag_id)
                except Exception:
                    pass
        self._tag_ids.clear()
        self._connected = False
        self._lib = None

    def _get_or_create_tag(self, tag_name: str) -> tuple[int, str | None]:
        from app.engine.libplctag_wrapper import build_tag_attribs, PLCTAG_STATUS_OK, decode_error

        if tag_name in self._tag_ids:
            return self._tag_ids[tag_name], None

        attribs = build_tag_attribs(
            gateway=self.ip,
            path=self.cip_path,
            cpu=self.cpu,
            name=tag_name,
            data_type=self.data_type,
            elem_count=self.elem_count,
            protocol=self.protocol,
            debug=self.debug,
            timeout=self.timeout,
        )

        tag_id = self._lib.plc_tag_create(attribs.encode("utf-8"), self.timeout)
        if tag_id < 0:
            return -1, f"plc_tag_create failed: {decode_error(tag_id)}\nAttribs: {attribs}"

        status = self._lib.plc_tag_status(tag_id)
        if status != PLCTAG_STATUS_OK:
            self._lib.plc_tag_destroy(tag_id)
            return -1, f"Tag init error: {decode_error(status)}\nAttribs: {attribs}"

        self._tag_ids[tag_name] = tag_id
        return tag_id, None

    def read_tag(self, tag: str) -> tuple[Any, str | None]:
        from app.engine.libplctag_wrapper import read_tag_value, PLCTAG_STATUS_OK, decode_error

        if not self._connected or not self._lib:
            return None, "Not connected"

        tag_id, err = self._get_or_create_tag(tag)
        if err:
            return None, err

        rc = self._lib.plc_tag_read(tag_id, self.timeout)
        if rc != PLCTAG_STATUS_OK:
            self._tag_ids.pop(tag, None)  # force recreate next poll
            return None, f"Read error: {decode_error(rc)}"

        val, err = read_tag_value(self._lib, tag_id, self.data_type)
        return val, err

    def is_connected(self) -> bool:
        return self._connected


# ---------------------------------------------------------------------------
# Siemens S7  (python-snap7)
# ---------------------------------------------------------------------------

def _s7_areas():
    """snap7's area enum, wherever this version keeps it.

    python-snap7 1.x has `snap7.types.Areas`; 2.x renamed the module to
    `snap7.type`; 3.x (pure Python, what pip installs today) keeps
    `snap7.type.Areas` and adds `snap7.Area`. Before 1.2 only the 1.x name was
    tried, so on a current install every M, I and Q tag failed to read.
    """
    for name in ("snap7.type", "snap7.types"):
        try:
            areas = getattr(importlib.import_module(name), "Areas", None)
        except ImportError:
            continue
        if areas is not None:
            return areas
    import snap7
    return snap7.Area


class SiemensDriver(PLCDriver):
    """
    Siemens S7-300/400/1200/1500 via python-snap7.
    Tag format: "DB10,REAL4"  →  DB 10, offset 4, type REAL
                "DB10,X0.0"  →  DB 10, byte 0, bit 0 (BOOL)
                "M0.0"       →  Merker bit 0.0
                "Q0.0"       →  Output bit 0.0
                "I0.0"       →  Input bit 0.0
    """
    PLC_TYPE = "Siemens (snap7)"

    def __init__(self, ip: str, rack: int = 0, slot: int = 1, port: int = 102):
        self.ip = ip
        self.rack = rack
        self.slot = slot
        self.port = port
        self._client = None

    @staticmethod
    def available() -> tuple[bool, str]:
        try:
            importlib.import_module("snap7")
            return True, ""
        except ImportError:
            return False, "python-snap7 not installed — run: pip install python-snap7"

    def connect(self) -> tuple[bool, str]:
        try:
            import snap7
            self._client = snap7.client.Client()
            self._client.connect(self.ip, self.rack, self.slot, self.port)
            return True, ""
        except Exception as e:
            self._client = None
            return False, str(e)

    def disconnect(self):
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def read_tag(self, tag: str) -> tuple[Any, str | None]:
        if not self._client:
            return None, "Not connected"
        try:
            import snap7
            from snap7 import util as s7util
            tag = tag.strip().upper()

            if tag.startswith("DB"):
                parts = tag[2:].split(",")
                db_num = int(parts[0])
                type_offset = parts[1]
                if type_offset.startswith("X"):
                    byte_off, bit = map(int, type_offset[1:].split("."))
                    data = self._client.db_read(db_num, byte_off, 1)
                    return s7util.get_bool(data, 0, bit), None
                elif type_offset.startswith("REAL"):
                    offset = int(type_offset[4:])
                    data = self._client.db_read(db_num, offset, 4)
                    return s7util.get_real(data, 0), None
                elif type_offset.startswith("DINT"):
                    offset = int(type_offset[4:])
                    data = self._client.db_read(db_num, offset, 4)
                    return s7util.get_dint(data, 0), None
                elif type_offset.startswith("INT"):
                    offset = int(type_offset[3:])
                    data = self._client.db_read(db_num, offset, 2)
                    return s7util.get_int(data, 0), None
                elif type_offset.startswith("BYTE"):
                    offset = int(type_offset[4:])
                    data = self._client.db_read(db_num, offset, 1)
                    return data[0], None
                elif type_offset.startswith("WORD"):
                    offset = int(type_offset[4:])
                    data = self._client.db_read(db_num, offset, 2)
                    return int.from_bytes(data, "big"), None
                else:
                    return None, f"Unknown type in tag: {tag}"

            elif tag.startswith("M") or tag.startswith("I") or tag.startswith("Q"):
                area_char = tag[0]
                areas = _s7_areas()
                area_map = {"M": areas.MK, "I": areas.PE, "Q": areas.PA}
                byte_off, bit = map(int, tag[1:].split("."))
                data = self._client.read_area(area_map[area_char], 0, byte_off, 1)
                return s7util.get_bool(data, 0, bit), None
            else:
                return None, f"Cannot parse tag: {tag}"

        except Exception as e:
            return None, str(e)

    def read_block(self, address: str, count: int):
        """`DB10,WORD4` (or INT) x count: 2*count bytes of the DB in one
        request. Siemens stores words high byte first."""
        if not self._client:
            return None, "Not connected"
        import re as _re
        m = _re.match(r"^DB(\d+),(?:WORD|INT)(\d+)$", address.strip().upper())
        if not m:
            return None, ("A grouped Siemens tag starts at a word in a DB: DB10,WORD4 or "
                          "DB10,INT4.")
        try:
            data = self._client.db_read(int(m.group(1)), int(m.group(2)), 2 * count)
        except Exception as e:
            return None, str(e)
        data = bytes(data)
        if len(data) < 2 * count:
            return None, f"The PLC returned {len(data)} bytes, {2 * count} needed"
        return [int.from_bytes(data[i:i + 2], "big") for i in range(0, 2 * count, 2)], None

    def is_connected(self) -> bool:
        if self._client is None:
            return False
        try:
            return self._client.get_connected()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Mitsubishi  (pymcprotocol)
# ---------------------------------------------------------------------------

class MitsubishiDriver(PLCDriver):
    """
    Mitsubishi iQ-R/Q/L/FX via MC Protocol (3E/1E frame).
    Tag format: "D100", "M100", "Y0", "X0", "B0"
    """
    PLC_TYPE = "Mitsubishi (MC Proto)"

    def __init__(self, ip: str, port: int = 5006, plc_type: str = "Q",
                 frame: str = "3E", slot: int = 0, source_ip: str = ""):
        self.ip = ip
        self.port = port
        self.plc_type = plc_type
        self.frame = frame
        self.source_ip = source_ip
        self._comm = None

    @staticmethod
    def available() -> tuple[bool, str]:
        try:
            importlib.import_module("pymcprotocol")
            return True, ""
        except ImportError:
            return False, "pymcprotocol not installed — run: pip install pymcprotocol"

    def connect(self) -> tuple[bool, str]:
        try:
            import pymcprotocol
            self._comm = pymcprotocol.Type3E() if self.frame == "3E" else pymcprotocol.Type1E()
            with _bind_to_source_ip(self.source_ip):
                self._comm.connect(self.ip, self.port)
            return True, ""
        except Exception as e:
            self._comm = None
            return False, str(e)

    def disconnect(self):
        if self._comm:
            try:
                self._comm.close()
            except Exception:
                pass
            self._comm = None

    def read_tag(self, tag: str) -> tuple[Any, str | None]:
        if not self._comm:
            return None, "Not connected"
        try:
            tag_upper = tag.strip().upper()

            # ── Bit-within-word notation: D100.1, W0F.3, etc. ──────────────
            # MC Protocol cannot address bits within word devices directly.
            # We read the whole word and extract the bit in software.
            bit_index: int | None = None
            if "." in tag_upper:
                parts = tag_upper.rsplit(".", 1)
                tag_upper = parts[0]
                try:
                    bit_index = int(parts[1])
                    if not (0 <= bit_index <= 15):
                        return None, (
                            f"Bit index {bit_index} out of range — "
                            f"Mitsubishi word devices have 16 bits (0-15).\n"
                            f"Example: D100.0 = bit 0 (LSB), D100.15 = bit 15 (MSB)"
                        )
                except ValueError:
                    return None, f"Invalid bit index in tag '{tag}' — expected integer after dot (e.g. D100.5)"

            import re as _re
            m = _re.match(r'^([A-Z]+)', tag_upper)
            if not m:
                return None, f"Invalid tag name: '{tag}' — must start with a device letter (D, M, Y, X, CC, CN, CS, TN...)"
            prefix = m.group(1)

            # Bare 'C' is not a valid Mitsubishi device
            if prefix == "C":
                num = tag_upper[1:]
                return None, (
                    f"'C' is not a valid Mitsubishi device name.\n"
                    f"Counter sub-elements:\n"
                    f"  CC{num}    = counter contact/coil (bit)\n"
                    f"  CN{num}    = counter current value (word)\n"
                    f"  CS{num}    = counter set value (word)\n"
                    f"  CN{num}.5  = bit 5 of counter current value\n"
                    f"Similarly for timers: TC (contact), TN (current), TS (set)"
                )

            if prefix == "T":
                num = tag_upper[1:]
                return None, (
                    f"'T' is not a valid Mitsubishi device name.\n"
                    f"Timer sub-elements:\n"
                    f"  TC{num}    = timer contact (bit)\n"
                    f"  TN{num}    = timer current value (word)\n"
                    f"  TS{num}    = timer set value (word)\n"
                    f"  TN{num}.5  = bit 5 of timer current value"
                )

            # Bit devices — use batchread_bitunits (dot notation not valid on these)
            BIT_DEVICES = {"X", "Y", "M", "L", "F", "V", "B", "S", "SB", "SM",
                           "DX", "DY", "TC", "CC", "TS", "STC", "STS"}
            # Word devices — use batchread_wordunits
            WORD_DEVICES = {"D", "W", "R", "ZR", "SD", "SW",
                            "TN", "CN", "CS", "STN"}

            if prefix in BIT_DEVICES:
                if bit_index is not None:
                    return None, (
                        f"'{prefix}' is already a bit device — dot notation is not needed.\n"
                        f"Use '{tag_upper}' directly (no .{bit_index})."
                    )
                vals = self._comm.batchread_bitunits(headdevice=tag_upper, readsize=1)
                return bool(vals[0]), None

            elif prefix in WORD_DEVICES:
                vals = self._comm.batchread_wordunits(headdevice=tag_upper, readsize=1)
                word_val = vals[0]
                if bit_index is not None:
                    # Extract specific bit from word value
                    return bool((word_val >> bit_index) & 1), None
                return word_val, None

            else:
                # Unknown prefix — try as word
                try:
                    vals = self._comm.batchread_wordunits(headdevice=tag_upper, readsize=1)
                    word_val = vals[0]
                    if bit_index is not None:
                        return bool((word_val >> bit_index) & 1), None
                    return word_val, None
                except Exception as inner:
                    return None, (
                        f"Unknown device prefix '{prefix}' for tag '{tag}'.\n"
                        f"Word devices:  D, W, R, ZR, SD, SW, TN, CN, CS\n"
                        f"Bit devices:   M, X, Y, B, L, F, SM, SB, TC, CC, TS, DX, DY\n"
                        f"Bit of word:   D100.5, TN0.3, CN10.0 (reads word, extracts bit)\n"
                        f"Error: {inner}"
                    )
        except Exception as e:
            return None, f"Read error for '{tag}': {e}"

    #: Word devices a block can start at. A bit device read as words packs
    #: 16 bits per word, which is not what a grouped tag means.
    WORD_DEVICES = {"D", "W", "R", "ZR", "SD", "SW", "TN", "CN", "CS", "STN"}

    def read_block(self, address: str, count: int):
        """`count` word devices from `address` with one batch read (3E frame:
        up to 960 words), so a VIN in D1916-D1919 cannot come back half
        from one scan and half from the next."""
        if not self._comm:
            return None, "Not connected"
        import re as _re
        head = address.strip().upper()
        m = _re.match(r"^([A-Z]+)[0-9A-F]+$", head)
        if not m or m.group(1) not in self.WORD_DEVICES:
            return None, (f"A grouped tag starts at a word device (D, W, R, ZR, SD...), "
                          f"not '{address}'.")
        if not 1 <= count <= 960:
            return None, "A grouped tag is 1 to 960 registers."
        try:
            values = self._comm.batchread_wordunits(headdevice=head, readsize=count)
        except Exception as e:
            return None, f"Read error for '{address}' x{count}: {e}"
        if len(values) != count:
            return None, f"Asked for {count} registers at {address}, got {len(values)}"
        return [int(v) & 0xFFFF for v in values], None

    def is_connected(self) -> bool:
        return self._comm is not None


# ---------------------------------------------------------------------------
# Driver registry and factory
# ---------------------------------------------------------------------------

DRIVER_MAP = {
    "Rockwell (pycomm3)":   RockwellDriver,
    "Rockwell (libplctag)": RockwellLibplctagDriver,
    "Siemens (snap7)":      SiemensDriver,
    "Mitsubishi (MC Proto)":MitsubishiDriver,
}

DEFAULT_PORTS = {
    "Rockwell (pycomm3)":   44818,
    "Rockwell (libplctag)": 44818,
    "Siemens (snap7)":      102,
    "Mitsubishi (MC Proto)": 5006,
}

DEFAULT_SLOTS = {
    "Rockwell (pycomm3)":   0,
    "Rockwell (libplctag)": 0,
    "Siemens (snap7)":      1,
    "Mitsubishi (MC Proto)": 0,
}

TAG_EXAMPLES = {
    "Rockwell (pycomm3)":   "Motor_Running  |  Conveyor_Speed  |  Program:Main.Counter  |  MyUDT.Member",
    "Rockwell (libplctag)": "Motor_Running  |  Conveyor_Speed  |  MyArray[0]  |  MyUDT.Member",
    "Siemens (snap7)":      "DB10,X0.0  |  DB10,REAL4  |  DB10,DINT4  |  M0.0  |  Q0.0  |  "
                            "DB10,WORD20 x4 ascii (4 words as text)",
    "Mitsubishi (MC Proto)": "D100  |  M200  |  Y0  |  CN0  |  D100.5 (bit 5)  |  "
                             "D1916 x4 ascii (4 registers as text)  |  D200 int32",
}


def create_driver(plc_type_name: str, ip: str, source_ip: str = "", **kwargs) -> PLCDriver:
    cls = DRIVER_MAP[plc_type_name]
    port = kwargs.pop("port", DEFAULT_PORTS[plc_type_name])
    slot = kwargs.pop("slot", DEFAULT_SLOTS[plc_type_name])
    rack = kwargs.pop("rack", 0)

    if cls is RockwellDriver:
        return cls(ip=ip, slot=slot, port=port, source_ip=source_ip)
    elif cls is RockwellLibplctagDriver:
        # The GUI passes a pre-built LibPlcTagConfig as 'cfg'
        cfg = kwargs.pop("cfg", None)
        tag_types = kwargs.pop("tag_types", None)
        if cfg is not None:
            from app.engine.libplctag_driver import LibPlcTagDriver
            return LibPlcTagDriver(cfg=cfg, tag_types=tag_types)
        # Fallback: build config from individual kwargs
        from app.engine.libplctag_driver import LibPlcTagConfig, LibPlcTagDriver
        config = LibPlcTagConfig(
            gateway   = ip,
            path      = kwargs.pop("cip_path", f"1,{slot}"),
            cpu       = kwargs.pop("cpu", "lgx"),
            protocol  = kwargs.pop("protocol", "ab-eip"),
            data_type = kwargs.pop("data_type", "DINT"),
            elem_count= kwargs.pop("elem_count", 1),
            timeout_ms= kwargs.pop("timeout", 5000),
            debug     = kwargs.pop("debug", 0),
        )
        return LibPlcTagDriver(cfg=config)
    elif cls is SiemensDriver:
        return cls(ip=ip, rack=rack, slot=slot, port=port)
    elif cls is MitsubishiDriver:
        plc_sub_type = kwargs.pop("plc_sub_type", "Q")
        frame = kwargs.pop("frame", "3E")
        return cls(ip=ip, port=port, plc_type=plc_sub_type, frame=frame, slot=slot,
                   source_ip=source_ip)
    return cls(ip=ip)
