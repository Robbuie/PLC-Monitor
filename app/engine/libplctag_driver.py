"""
libplctag_driver.py
All libplctag functionality in one place:
  - LibPlcTagConfig  config builder  
  - find_dll()       locate plctag.dll on disk
  - download_dll()   fetch latest plctag.dll from GitHub releases
  - LibPlcTagDriver  driver class used by plc_drivers.py
  - Constants        CPU_TYPES, PROTOCOLS, DATA_TYPES, DATA_TYPE_MAP, PATH_HELP
"""

from __future__ import annotations
import ctypes
import ctypes.util
import json
import os
import sys
import urllib.request
import zipfile
import io
import threading
import time
from typing import Any, Callable

from app.engine import clock
from app.engine.plc_drivers import PLCDriver

CPU_TYPES = [
    "lgx",            # ControlLogix / CompactLogix (most common)
    "lgx_pccc",       # Older ControlLogix (PCCC bridge)
    "micro800",       # Micro800 series
    "micro800_cip",   # Micro800 CIP variant
    "plc",            # PLC/5
    "plc5",           # PLC/5 explicit
    "slc",            # SLC 500
    "slc500",         # SLC 500 explicit
    "micrologix",     # MicroLogix 1100/1400
    "micrologix1100",
    "omron-njnx",     # Omron NX/NJ
    "modbus-tcp",
]

PROTOCOLS = ["ab-eip", "ab-pccc", "modbus-tcp"]

DATA_TYPE_MAP: dict[str, tuple[int, str]] = {
    "BOOL":    (1,  "get_uint8"),
    "SINT":    (1,  "get_int8"),
    "USINT":   (1,  "get_uint8"),
    "INT":     (2,  "get_int16"),
    "UINT":    (2,  "get_uint16"),
    "DINT":    (4,  "get_int32"),
    "UDINT":   (4,  "get_uint32"),
    "LINT":    (8,  "get_int64"),
    "REAL":    (4,  "get_float32"),
    "LREAL":   (8,  "get_float64"),
    "STRING":  (88, "get_string"),
    "WORD":    (2,  "get_uint16"),
    "DWORD":   (4,  "get_uint32"),
    "LWORD":   (8,  "get_int64"),
}

DATA_TYPES = list(DATA_TYPE_MAP.keys())

PATH_HELP = (
    "libplctag CIP Path Field\n"
    "\n"
    "Format:  <backplane_port>,<cpu_slot>\n\n"
    "Examples:\n"
    '  "1,0"  Backplane port 1, CPU in slot 0  (most common CLX)\n'
    '  "1,1"  CPU in slot 1  (your case: ENB in slot 0, CPU in slot 1)\n'
    '  "1,2"  CPU in slot 2\n'
    '  ""     Leave empty for direct-connect MicroLogix / Micro800\n\n'
    "For bridged paths (DHRIO, etc.):\n"
    '  "5,0,2,1,0"  DH+ port 5, bridge slot 0, node 2, backplane port 1, CPU slot 0\n\n'
    "Tip: Start with \"1,0\". If that fails, try \"1,1\" or leave empty."
)

_RELEASES_API = "https://api.github.com/repos/libplctag/libplctag/releases/latest"
_HERE = os.path.dirname(os.path.abspath(__file__))
PLCTAG_STATUS_OK = 0
PLCTAG_STATUS_PENDING = 1
#: Seconds before a tag that could not be created is tried again.
CREATE_RETRY_S = 10.0
_lib: ctypes.CDLL | None = None
_lib_lock = threading.Lock()


class LibPlcTagConfig:
    def __init__(self, gateway, path="1,0", cpu="lgx", protocol="ab-eip",
                 data_type="DINT", elem_count=1, timeout_ms=5000, debug=0, dll_path=""):
        self.gateway    = gateway
        self.path       = path
        self.cpu        = cpu
        self.protocol   = protocol
        self.data_type  = data_type.upper()
        self.elem_count = elem_count
        self.timeout_ms = timeout_ms
        self.debug      = debug
        self.dll_path   = dll_path

    @property
    def elem_size(self):
        return DATA_TYPE_MAP.get(self.data_type, (4, ""))[0]

    def build_attrib(self, tag_name: str, data_type: str | None = None) -> str:
        size = DATA_TYPE_MAP.get((data_type or self.data_type).upper(), (4, ""))[0]
        parts = [f"protocol={self.protocol}", f"gateway={self.gateway}"]
        if self.path:
            parts.append(f"path={self.path}")
        parts += [
            f"cpu={self.cpu}",
            f"elem_size={size}",
            f"elem_count={self.elem_count}",
            f"name={tag_name}",
            f"debug={self.debug}",
            f"timeout={self.timeout_ms}",
        ]
        return "&".join(parts)


def find_dll() -> str | None:
    if sys.platform == "win32":
        names = ["plctag.dll", "libplctag.dll"]
    elif sys.platform == "darwin":
        names = ["libplctag.dylib", "libplctag.2.dylib"]
    else:
        names = ["libplctag.so", "libplctag.so.2"]
    for folder in _search_dirs():
        for name in names:
            p = os.path.join(folder, name)
            if os.path.exists(p):
                return p
    for name in names:
        found = ctypes.util.find_library(name.split(".")[0])
        if found:
            return found
    return None


def _search_dirs() -> list[str]:
    """Where a plctag.dll may have been put, most deliberate first.

    The data folder is where Download writes, because an installed copy's own
    folder is replaced whole by every update. The executable's folder and this
    module's folder are kept for a DLL somebody dropped in by hand.
    """
    from app import paths
    dirs = [paths.lib_dir()]
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    dirs += [_HERE, os.getcwd()]
    return dirs


def download_dll(dest_dir=None, progress_cb=None) -> str | None:
    def status(msg):
        if progress_cb:
            progress_cb(msg)
    if dest_dir is None:
        from app import paths
        dest_dir = paths.lib_dir()
    try:
        os.makedirs(dest_dir, exist_ok=True)
        status("Fetching latest release info from GitHub...")
        req = urllib.request.Request(_RELEASES_API, headers={"User-Agent": "PLCMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            release = json.loads(resp.read())
        tag_name = release.get("tag_name", "?")
        status(f"Latest release: {tag_name}")
        assets = release.get("assets", [])
        if sys.platform == "win32":
            candidates = [a for a in assets if "windows" in a["name"].lower() and "x64" in a["name"].lower()]
            if not candidates:
                candidates = [a for a in assets if "windows" in a["name"].lower()]
        else:
            candidates = [a for a in assets if sys.platform in a["name"].lower()]
        if not candidates:
            status(f"No asset found for {sys.platform} in {tag_name}")
            return None
        asset = candidates[0]
        status(f"Downloading {asset['name']}...")
        with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as resp:
            data = resp.read()
        status("Extracting...")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            dll_names = [n for n in zf.namelist() if n.lower().endswith(".dll") and "plctag" in n.lower()]
            if not dll_names:
                dll_names = [n for n in zf.namelist() if n.lower().endswith(".dll")]
            if not dll_names:
                status("No DLL found in ZIP")
                return None
            dll_name = next((n for n in dll_names if "x64" in n.lower() or "64" in n.lower()), dll_names[0])
            dest_path = os.path.join(dest_dir, os.path.basename(dll_name))
            with zf.open(dll_name) as src, open(dest_path, "wb") as dst:
                dst.write(src.read())
        status(f"Saved to {dest_path}")
        return dest_path
    except Exception as e:
        status(f"Download error: {e}")
        return None


def _load_lib(dll_path=""):
    global _lib
    with _lib_lock:
        if _lib is not None:
            return _lib, ""
        path = dll_path or find_dll()
        if not path:
            return None, (
                "plctag.dll not found.\n\n"
                "Use Download on the Tags page to fetch it automatically,\n"
                "or place plctag.dll in the application's lib folder.\n\n"
                "Manual download:\n"
                "  https://github.com/libplctag/libplctag/releases\n"
                "  libplctag_vX.X.X_windows_x64.zip, then plctag.dll inside it"
            )
        try:
            lib = ctypes.CDLL(path)
            lib.plc_tag_create.argtypes  = [ctypes.c_char_p, ctypes.c_int]
            lib.plc_tag_create.restype   = ctypes.c_int32
            lib.plc_tag_destroy.argtypes = [ctypes.c_int32]
            lib.plc_tag_destroy.restype  = ctypes.c_int
            lib.plc_tag_read.argtypes    = [ctypes.c_int32, ctypes.c_int]
            lib.plc_tag_read.restype     = ctypes.c_int
            lib.plc_tag_status.argtypes  = [ctypes.c_int32]
            lib.plc_tag_status.restype   = ctypes.c_int
            lib.plc_tag_decode_error.argtypes = [ctypes.c_int]
            lib.plc_tag_decode_error.restype  = ctypes.c_char_p
            # Optional: only the batched read uses it, to cancel a read that
            # ran past its timeout. Very old builds may lack it.
            if hasattr(lib, "plc_tag_abort"):
                lib.plc_tag_abort.argtypes = [ctypes.c_int32]
                lib.plc_tag_abort.restype  = ctypes.c_int
            lib.plc_tag_get_size.argtypes = [ctypes.c_int32]
            lib.plc_tag_get_size.restype  = ctypes.c_int
            for fname, rtype in [
                ("get_int8", ctypes.c_int8), ("get_uint8", ctypes.c_uint8),
                ("get_int16", ctypes.c_int16), ("get_uint16", ctypes.c_uint16),
                ("get_int32", ctypes.c_int32), ("get_uint32", ctypes.c_uint32),
                ("get_int64", ctypes.c_int64),
                ("get_float32", ctypes.c_float), ("get_float64", ctypes.c_double),
            ]:
                fn = getattr(lib, f"plc_tag_{fname}")
                fn.argtypes = [ctypes.c_int32, ctypes.c_int]
                fn.restype  = rtype
            lib.plc_tag_get_string.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            lib.plc_tag_get_string.restype  = ctypes.c_int
            lib.plc_tag_get_string_length.argtypes = [ctypes.c_int32, ctypes.c_int]
            lib.plc_tag_get_string_length.restype  = ctypes.c_int
            _lib = lib
            return lib, ""
        except Exception as e:
            return None, f"Failed to load {path}: {e}"


def _decode_error(lib, code):
    try:
        b = lib.plc_tag_decode_error(code)
        return b.decode("utf-8", errors="replace") if b else f"error({code})"
    except Exception:
        return f"error({code})"


def _read_value(lib, tag_id, data_type, offset=0):
    dt = data_type.upper()
    info = DATA_TYPE_MAP.get(dt)
    if not info:
        return None, f"Unknown data type: {data_type}"
    need, fn = info
    try:
        # The getters return an error code *as the value* when the tag holds
        # fewer bytes than asked for (-21, PLCTAG_ERR_NO_DATA, for an empty
        # reply), which would be logged as a real reading. Check the size
        # first. Strings vary in length and report their own errors.
        if fn != "get_string":
            size = lib.plc_tag_get_size(tag_id)
            if size < offset + need:
                return None, (f"The PLC returned {max(size, 0)} bytes for a {dt} "
                              f"({need} needed). Check the tag name and its data type.")
        if fn == "get_uint8":
            v = lib.plc_tag_get_uint8(tag_id, offset)
            return (bool(v) if dt == "BOOL" else v), None
        elif fn == "get_int8":    return lib.plc_tag_get_int8(tag_id, offset), None
        elif fn == "get_int16":   return lib.plc_tag_get_int16(tag_id, offset), None
        elif fn == "get_uint16":  return lib.plc_tag_get_uint16(tag_id, offset), None
        elif fn == "get_int32":   return lib.plc_tag_get_int32(tag_id, offset), None
        elif fn == "get_uint32":  return lib.plc_tag_get_uint32(tag_id, offset), None
        elif fn == "get_int64":   return lib.plc_tag_get_int64(tag_id, offset), None
        elif fn == "get_float32": return round(lib.plc_tag_get_float32(tag_id, offset), 6), None
        elif fn == "get_float64": return round(lib.plc_tag_get_float64(tag_id, offset), 10), None
        elif fn == "get_string":
            slen = lib.plc_tag_get_string_length(tag_id, offset)
            if slen < 0:
                return None, f"string_length error: {_decode_error(lib, slen)}"
            buf = ctypes.create_string_buffer(slen + 1)
            rc = lib.plc_tag_get_string(tag_id, offset, buf, slen + 1)
            if rc < 0:
                return None, f"get_string error: {_decode_error(lib, rc)}"
            return buf.value.decode("utf-8", errors="replace"), None
        return None, f"No reader for {dt}"
    except Exception as e:
        return None, str(e)


class LibPlcTagDriver(PLCDriver):
    PLC_TYPE = "Rockwell (libplctag)"

    def __init__(self, cfg: LibPlcTagConfig, tag_types: dict[str, str] | None = None):
        self.cfg = cfg
        # Per-tag data types. Before 1.0 the window stored one per tag but the
        # driver read every tag as the session's type, so a REAL in a list of
        # DINTs came back as a meaningless integer.
        self.tag_types = {k: v.upper() for k, v in (tag_types or {}).items() if v}
        self.ip  = cfg.gateway
        self._lib = None
        self._tag_ids: dict[str, int] = {}
        self._connected = False
        # Cleared when the device turns out not to accept packed reads.
        self._batch_ok = True
        # tag -> monotonic time before which a failed create is not retried.
        self._create_backoff: dict[str, float] = {}
        self._create_errors: dict[str, str] = {}

    def connect(self):
        lib, err = _load_lib(self.cfg.dll_path)
        if not lib:
            return False, err
        self._lib = lib
        self._connected = True
        return True, ""

    def disconnect(self):
        if self._lib:
            for tid in list(self._tag_ids.values()):
                try: self._lib.plc_tag_destroy(tid)
                except Exception: pass
        self._tag_ids.clear()
        self._connected = False

    def _get_or_create(self, tag_name):
        if tag_name in self._tag_ids:
            return self._tag_ids[tag_name], None
        # A tag that could not be created (a typo, a tag deleted from the
        # program) is not retried on every poll: creating blocks for up to
        # the timeout, and doing that each poll stalled every other tag with
        # it. It is tried again after CREATE_RETRY_S.
        retry_at = self._create_backoff.get(tag_name)
        if retry_at is not None and time.monotonic() < retry_at:
            return -1, self._create_errors.get(tag_name, "Tag could not be created")
        attribs = self.cfg.build_attrib(tag_name, self._type_of(tag_name))
        tid = self._lib.plc_tag_create(attribs.encode("utf-8"), self.cfg.timeout_ms)
        if tid < 0:
            return -1, self._create_failed(
                tag_name, f"plc_tag_create failed: {_decode_error(self._lib, tid)}\n\nAttributes:\n{attribs}")
        status = self._lib.plc_tag_status(tid)
        if status != PLCTAG_STATUS_OK:
            self._lib.plc_tag_destroy(tid)
            return -1, self._create_failed(
                tag_name, f"Tag init error: {_decode_error(self._lib, status)}\n\nAttributes:\n{attribs}")
        self._create_backoff.pop(tag_name, None)
        self._tag_ids[tag_name] = tid
        return tid, None

    def _create_failed(self, tag_name: str, message: str) -> str:
        self._create_backoff[tag_name] = time.monotonic() + CREATE_RETRY_S
        self._create_errors[tag_name] = message
        return message

    def read_tag(self, tag):
        if not self._connected or not self._lib:
            return None, "Not connected"
        tid, err = self._get_or_create(tag)
        if err:
            return None, err
        rc = self._lib.plc_tag_read(tid, self.cfg.timeout_ms)
        if rc != PLCTAG_STATUS_OK:
            self._forget(tid, tag)
            return None, f"Read error: {_decode_error(self._lib, rc)}"
        return _read_value(self._lib, tid, self._type_of(tag))

    def read_tags(self, tags):
        """Start every read at once, then collect them.

        libplctag reads asynchronously when given a timeout of 0, and packs
        reads that are outstanding to the same PLC at the same time into one
        request. So starting them all before waiting on any turns N round
        trips into about one -- the same thing pycomm3 does with read(*tags).
        Each tag is stamped when its own read is seen to finish.
        """
        if not self._connected or not self._lib:
            return {t: (None, "Not connected") for t in tags}
        if not self._batch_ok or len(tags) == 1:
            return super().read_tags(tags)
        lib = self._lib
        out: dict[str, tuple] = {}
        pending: dict[str, int] = {}
        # Every handle is made before any read starts. Creating a tag reads
        # it once, and a create issued while other reads are outstanding gets
        # packed in with them -- so on a device that refuses packing, the
        # creates would fail too and the tags would never come back.
        handles: dict[str, int] = {}
        for tag in dict.fromkeys(tags):
            tid, err = self._get_or_create(tag)
            if err:
                out[tag] = (None, err)
            else:
                handles[tag] = tid
        for tag, tid in handles.items():
            rc = lib.plc_tag_read(tid, 0)
            if rc == PLCTAG_STATUS_PENDING:
                pending[tag] = tid
            else:
                out[tag] = self._finish(tag, tid, rc)

        deadline = time.monotonic() + max(1, self.cfg.timeout_ms) / 1000
        while pending:
            for tag, tid in list(pending.items()):
                status = lib.plc_tag_status(tid)
                if status != PLCTAG_STATUS_PENDING:
                    out[tag] = self._finish(tag, tid, status)
                    del pending[tag]
            if not pending:
                break
            if time.monotonic() >= deadline:
                timed_out = len(pending)
                for tag, tid in pending.items():
                    self._forget(tid, tag)
                    out[tag] = (None, f"Read timed out after {self.cfg.timeout_ms} ms")
                if timed_out > 1 and not any(v[1] is None for v in out.values()):
                    # Every read of the batch hung. That is the signature of
                    # a device that refuses packed requests (some non-Logix
                    # EtherNet/IP targets; libplctag's own test server too):
                    # the packed request fails and the reads never return.
                    # Read one at a time from now on -- slower, but it works.
                    self._batch_ok = False
                break
            # A millisecond between checks: the stamp is taken when a read is
            # seen to be done, so this is the resolution of the stamp.
            time.sleep(0.001)
        return out

    def _finish(self, tag: str, tid: int, rc: int) -> tuple:
        seen = clock.now_ns()
        if rc != PLCTAG_STATUS_OK:
            self._forget(tid, tag)
            return (None, f"Read error: {_decode_error(self._lib, rc)}", seen)
        value, err = _read_value(self._lib, tid, self._type_of(tag))
        return (value, err, seen)

    def _forget(self, tid: int, tag: str) -> None:
        """Drop a tag handle after a failure so the next poll makes a new one."""
        self._tag_ids.pop(tag, None)
        try:
            if hasattr(self._lib, "plc_tag_abort"):
                self._lib.plc_tag_abort(tid)
            self._lib.plc_tag_destroy(tid)
        except Exception:
            pass

    def _type_of(self, tag: str) -> str:
        return self.tag_types.get(tag, self.cfg.data_type)

    def is_connected(self):
        return self._connected
