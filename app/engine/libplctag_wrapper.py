"""
libplctag_wrapper.py
A self-contained ctypes wrapper for libplctag.

libplctag is NOT on PyPI — this file replaces that dependency entirely.
It locates plctag.dll (Windows) or libplctag.so (Linux/macOS) from:
  1. Same directory as this file  (bundled with the app)
  2. System PATH / LD_LIBRARY_PATH

Download plctag.dll from:
  https://github.com/libplctag/libplctag/releases
  → libplctag_v2.x.x_windows_x64.zip  →  plctag.dll

Tag attribute string format (all joined with '&'):
  protocol=ab-eip
  gateway=192.168.1.1
  path=1,0              ← backplane port, CPU slot  (e.g. ENB in slot 0, CPU in slot 1 → "1,1")
  cpu=lgx               ← lgx, lgx_pccc, plc, slc, micro800, micro800_cip, omron-njnx
  elem_size=4           ← bytes per element (BOOL=1, INT=2, DINT=4, REAL=4, LINT=8)
  elem_count=1          ← number of array elements to read
  name=MyTag
  debug=1               ← 0=none … 5=chatty
  timeout=5000          ← ms
"""

from __future__ import annotations
import ctypes
import ctypes.util
import os
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Status codes
# ---------------------------------------------------------------------------
PLCTAG_STATUS_OK        =  0
PLCTAG_ERR_NULL_PTR     = -1
PLCTAG_ERR_BAD_DATA     = -10
PLCTAG_ERR_NOT_FOUND    = -17

# Lookup dict for human-readable status
_STATUS_NAMES = {
     0: "PLCTAG_STATUS_OK",
    -1: "PLCTAG_ERR_NULL_PTR",
    -2: "PLCTAG_ERR_BAD_PARAM",
    -3: "PLCTAG_ERR_CREATE",
    -4: "PLCTAG_ERR_NOT_EMPTY",
    -5: "PLCTAG_ERR_OPEN",
    -6: "PLCTAG_ERR_SET",
    -7: "PLCTAG_ERR_WRITE",
    -8: "PLCTAG_ERR_TIMEOUT",
    -9: "PLCTAG_ERR_WINSOCK",
    -10: "PLCTAG_ERR_BAD_DATA",
    -11: "PLCTAG_ERR_ENCODE",
    -12: "PLCTAG_ERR_DECODE",
    -13: "PLCTAG_ERR_UNSUPPORTED",
    -14: "PLCTAG_ERR_TOO_LONG",
    -15: "PLCTAG_ERR_CLOSE",
    -16: "PLCTAG_ERR_NOT_ALLOWED",
    -17: "PLCTAG_ERR_NOT_FOUND",
    -18: "PLCTAG_ERR_ABORT",
    -19: "PLCTAG_ERR_WINSOCK",
    -20: "PLCTAG_ERR_NO_DATA",
    -21: "PLCTAG_ERR_MUTEX_INIT",
    -22: "PLCTAG_ERR_MUTEX_LOCK",
    -23: "PLCTAG_ERR_MUTEX_UNLOCK",
    -24: "PLCTAG_ERR_MUTEX_DESTROY",
    -25: "PLCTAG_ERR_THREAD_CREATE",
    -26: "PLCTAG_ERR_THREAD_JOIN",
    -27: "PLCTAG_ERR_BAD_DEVICE",
    -28: "PLCTAG_ERR_BAD_GATEWAY",
    -29: "PLCTAG_ERR_REMOTE_ERR",
    -30: "PLCTAG_ERR_NOT_IMPLEMENTED",
    -31: "PLCTAG_ERR_NO_MEM",
    -32: "PLCTAG_ERR_IO",
    -33: "PLCTAG_ERR_BAD_STATUS_1",
    -34: "PLCTAG_ERR_BAD_STATUS_2",
    -35: "PLCTAG_ERR_DUPLICATE",
    -36: "PLCTAG_ERR_BAD_REPLY",
    -37: "PLCTAG_ERR_BAD_OPT",
    -38: "PLCTAG_ERR_PARTIAL",
    -39: "PLCTAG_ERR_BAD_CONFIG",
}


def _status_str(code: int) -> str:
    return _STATUS_NAMES.get(code, f"UNKNOWN({code})")


# ---------------------------------------------------------------------------
# DLL loading
# ---------------------------------------------------------------------------

_lib: ctypes.CDLL | None = None
_load_error: str = ""


def _find_dll() -> str | None:
    """The same search the libplctag driver uses, so both find the same file."""
    from app.engine.libplctag_driver import find_dll
    return find_dll()


def load_library() -> tuple[bool, str]:
    """Load the native library. Returns (ok, error_message)."""
    global _lib, _load_error
    if _lib is not None:
        return True, ""

    path = _find_dll()
    if not path:
        msg = (
            "plctag.dll not found.\n\n"
            "Download from:\n"
            "  https://github.com/libplctag/libplctag/releases\n"
            "  (libplctag_vX.X.X_windows_x64.zip, then plctag.dll inside it)\n\n"
            "Or use Download on the Tags page, which puts it where this looks."
        )
        _load_error = msg
        return False, msg

    try:
        lib = ctypes.CDLL(path)

        # Declare function signatures for type safety
        lib.plc_tag_create.argtypes  = [ctypes.c_char_p, ctypes.c_int]
        lib.plc_tag_create.restype   = ctypes.c_int32

        lib.plc_tag_destroy.argtypes = [ctypes.c_int32]
        lib.plc_tag_destroy.restype  = ctypes.c_int

        lib.plc_tag_read.argtypes    = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_read.restype     = ctypes.c_int

        lib.plc_tag_status.argtypes  = [ctypes.c_int32]
        lib.plc_tag_status.restype   = ctypes.c_int

        lib.plc_tag_get_int8.argtypes   = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_int8.restype    = ctypes.c_int8
        lib.plc_tag_get_uint8.argtypes  = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_uint8.restype   = ctypes.c_uint8
        lib.plc_tag_get_int16.argtypes  = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_int16.restype   = ctypes.c_int16
        lib.plc_tag_get_uint16.argtypes = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_uint16.restype  = ctypes.c_uint16
        lib.plc_tag_get_int32.argtypes  = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_int32.restype   = ctypes.c_int32
        lib.plc_tag_get_uint32.argtypes = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_uint32.restype  = ctypes.c_uint32
        lib.plc_tag_get_int64.argtypes  = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_int64.restype   = ctypes.c_int64
        lib.plc_tag_get_float32.argtypes = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_float32.restype  = ctypes.c_float
        lib.plc_tag_get_float64.argtypes = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_float64.restype  = ctypes.c_double

        # String tag (AB Logix ANSI string)
        lib.plc_tag_get_string.argtypes  = [ctypes.c_int32, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        lib.plc_tag_get_string.restype   = ctypes.c_int
        lib.plc_tag_get_string_length.argtypes = [ctypes.c_int32, ctypes.c_int]
        lib.plc_tag_get_string_length.restype  = ctypes.c_int

        lib.plc_tag_decode_error.argtypes = [ctypes.c_int]
        lib.plc_tag_decode_error.restype  = ctypes.c_char_p

        lib.plc_tag_get_size.argtypes = [ctypes.c_int32]
        lib.plc_tag_get_size.restype  = ctypes.c_int

        _lib = lib
        return True, ""

    except Exception as e:
        _load_error = f"Failed to load plctag library from {path}: {e}"
        return False, _load_error


def get_lib() -> ctypes.CDLL | None:
    return _lib


def decode_error(code: int) -> str:
    if _lib:
        try:
            msg = _lib.plc_tag_decode_error(code)
            if msg:
                return msg.decode("utf-8", errors="replace")
        except Exception:
            pass
    return _status_str(code)


# ---------------------------------------------------------------------------
# Data type helpers
# ---------------------------------------------------------------------------

# Maps our user-facing type name → (elem_size_bytes, read_fn_name)
DATA_TYPES: dict[str, tuple[int, str]] = {
    "BOOL":    (1, "get_uint8"),     # stored as 1-byte in Logix
    "SINT":    (1, "get_int8"),
    "USINT":   (1, "get_uint8"),
    "INT":     (2, "get_int16"),
    "UINT":    (2, "get_uint16"),
    "DINT":    (4, "get_int32"),
    "UDINT":   (4, "get_uint32"),
    "LINT":    (8, "get_int64"),
    "REAL":    (4, "get_float32"),
    "LREAL":   (8, "get_float64"),
    "STRING":  (0, "get_string"),    # variable; elem_size determined at runtime
    "WORD":    (2, "get_uint16"),
    "DWORD":   (4, "get_uint32"),
    "LWORD":   (8, "get_int64"),
    "TIMER":   (12, "get_int32"),    # simplified: read CV (DINT at offset 8)
    "COUNTER": (12, "get_int32"),
}

CPU_TYPES = [
    "lgx",           # ControlLogix / CompactLogix (most common)
    "lgx_pccc",      # Older ControlLogix using PCCC
    "micro800",      # Micro800 series
    "micro800_cip",  # Micro800 with CIP
    "plc",           # PLC/5
    "plc5",          # PLC/5 explicit
    "slc",           # SLC 500
    "slc500",        # SLC 500 explicit
    "micrologix",    # MicroLogix
    "micrologix1100",
    "omron-njnx",    # Omron NX/NJ
    "modbus-tcp",    # Modbus TCP (any vendor)
]

PROTOCOLS = ["ab-eip", "ab-pccc", "modbus-tcp"]


def build_tag_attribs(
    gateway: str,
    path: str,          # CIP path e.g. "1,0"
    cpu: str,           # cpu type e.g. "lgx"
    name: str,          # tag name
    data_type: str,     # one of DATA_TYPES
    elem_count: int,
    protocol: str = "ab-eip",
    debug: int = 0,
    timeout: int = 5000,
) -> str:
    type_info = DATA_TYPES.get(data_type.upper(), (4, "get_int32"))
    elem_size = type_info[0] if type_info[0] > 0 else 88  # AB string default

    parts = [
        f"protocol={protocol}",
        f"gateway={gateway}",
    ]
    if path:
        parts.append(f"path={path}")
    parts += [
        f"cpu={cpu}",
        f"elem_size={elem_size}",
        f"elem_count={elem_count}",
        f"name={name}",
        f"debug={debug}",
        f"timeout={timeout}",
    ]
    return "&".join(parts)


def read_tag_value(
    lib: ctypes.CDLL,
    tag_id: int,
    data_type: str,
    elem_index: int = 0,
) -> tuple[Any, str | None]:
    """Read a single value from an open tag. Returns (value, error_or_None)."""
    dt = data_type.upper()
    type_info = DATA_TYPES.get(dt)
    if not type_info:
        return None, f"Unknown data type: {data_type}"

    elem_size, fn_name = type_info
    offset = elem_index * elem_size

    try:
        if dt == "BOOL":
            raw = lib.plc_tag_get_uint8(tag_id, offset)
            return bool(raw & 0xFF), None
        elif fn_name == "get_int8":
            return lib.plc_tag_get_int8(tag_id, offset), None
        elif fn_name == "get_uint8":
            return lib.plc_tag_get_uint8(tag_id, offset), None
        elif fn_name == "get_int16":
            return lib.plc_tag_get_int16(tag_id, offset), None
        elif fn_name == "get_uint16":
            return lib.plc_tag_get_uint16(tag_id, offset), None
        elif fn_name == "get_int32":
            return lib.plc_tag_get_int32(tag_id, offset), None
        elif fn_name == "get_uint32":
            return lib.plc_tag_get_uint32(tag_id, offset), None
        elif fn_name == "get_int64":
            return lib.plc_tag_get_int64(tag_id, offset), None
        elif fn_name == "get_float32":
            return round(lib.plc_tag_get_float32(tag_id, offset), 6), None
        elif fn_name == "get_float64":
            return round(lib.plc_tag_get_float64(tag_id, offset), 10), None
        elif fn_name == "get_string":
            str_len = lib.plc_tag_get_string_length(tag_id, offset)
            if str_len < 0:
                return None, f"get_string_length error: {decode_error(str_len)}"
            buf = ctypes.create_string_buffer(str_len + 1)
            rc = lib.plc_tag_get_string(tag_id, offset, buf, str_len + 1)
            if rc < PLCTAG_STATUS_OK:
                return None, f"get_string error: {decode_error(rc)}"
            return buf.value.decode("utf-8", errors="replace"), None
        else:
            return None, f"No read function for {dt}"
    except Exception as e:
        return None, str(e)
