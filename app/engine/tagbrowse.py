"""Reading a Rockwell controller's own tag list, to pick tags from.

The Import button reads a tag list out of a file somebody exported; this reads
the same thing from the controller itself, so a tag can be chosen without the
project on hand. Controller tags, every program's tags and the I/O module tags
come back with their data types, and a structure -- a UDT, a TIMER, a
module's :I data -- opens out into its members, an array into its elements,
down to the single values the poller can log.

Two ways in, matching the two Rockwell drivers:

  * **pycomm3** uploads the tag database when it connects (the same upload
    the Tags page waits for on Connect); this keeps its result instead of
    polling with it.
  * **libplctag** reads the `@tags` and `Program:<name>.@tags` listings and
    each structure's `@udt/<id>` template.

Both are reads: the CIP Symbol and Template objects are asked to describe
themselves (Get Instance Attribute List, Get Attribute List, Read Template).
Nothing is written to the controller. The listing runs on its own connection
and is closed again as soon as it is done, so it does not disturb a poll that
is running.

Tag descriptions are not part of what a controller will report; an L5X
import is the way to get them.

No Qt: what the browser dialog shows is built here and tested on its own.
"""

from __future__ import annotations

import ctypes
import struct
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

#: CIP atomic type codes, as a symbol or a template member reports them.
ATOMIC = {
    0xC1: "BOOL", 0xC2: "SINT", 0xC3: "INT", 0xC4: "DINT", 0xC5: "LINT",
    0xC6: "USINT", 0xC7: "UINT", 0xC8: "UDINT", 0xC9: "ULINT",
    0xCA: "REAL", 0xCB: "LREAL", 0xD1: "BYTE", 0xD2: "WORD", 0xD3: "DWORD",
    0xD4: "LWORD",
}

TYPE_STRUCT = 0x8000
TYPE_SYSTEM = 0x1000
TYPE_DIMS = 0x6000
TYPE_ID = 0x0FFF

#: Above this many elements an array opens into ranges first, so expanding a
#: DINT[5000] does not put five thousand rows in the tree at once.
PAGE = 200


class BrowseError(Exception):
    """The tag list could not be read. The message is for the person."""


# ---------------------------------------------------------------- the model

@dataclass
class Member:
    name: str
    dtype: str
    struct: "StructDef | None" = None
    length: int = 0           # array length; 0 for a single value


@dataclass
class StructDef:
    name: str
    members: list[Member] = field(default_factory=list)
    #: A Logix STRING (or STRING20 ...): LEN and DATA, read as one text value.
    is_string: bool = False


@dataclass
class TagDef:
    name: str                 # the address as read: Program:Main.Count
    dtype: str
    struct: StructDef | None = None
    dims: tuple[int, ...] = ()
    scope: str = ""           # "", "Program:<name>" or "I/O"


@dataclass
class Database:
    tags: list[TagDef]
    source: str = ""
    programs: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.tags)


def _bool_count(declared: int) -> int:
    """Elements in a BOOL array that reports itself as DWORDs.

    Logix keeps BOOL arrays in 32-bit words and rounds the size up to a
    multiple of 32, so a count that is a multiple of 32 is already in bits
    and anything else is a number of words. Worth checking against a real
    controller -- see the README.
    """
    return declared if declared % 32 == 0 else declared * 32


# ------------------------------------------------------------------- nodes

class Node:
    """One row of the browser tree.

    `kind` is "scope" (Controller, a program, I/O), "array", "range" (a page
    of a long array), "struct" or "value". Only a value can be added: it is
    what the poller reads and logs as one thing. Children are made when
    asked for, so a controller with a thousand UDT instances costs nothing
    until one is opened.
    """

    __slots__ = ("label", "path", "dtype", "kind", "struct", "dims", "_tags",
                 "_lo", "_hi", "_prefix")

    def __init__(self, label: str, path: str, dtype: str, kind: str, *,
                 struct: StructDef | None = None, dims: tuple[int, ...] = (),
                 tags: list[TagDef] | None = None, lo: int = 0, hi: int = 0,
                 prefix: tuple[int, ...] = ()) -> None:
        self.label = label
        self.path = path
        self.dtype = dtype
        self.kind = kind
        self.struct = struct
        self.dims = dims
        self._tags = tags
        self._lo = lo
        self._hi = hi
        self._prefix = prefix

    def __repr__(self) -> str:
        return f"Node({self.kind} {self.path!r} {self.type_label})"

    @property
    def readable(self) -> bool:
        return self.kind == "value"

    @property
    def has_children(self) -> bool:
        return self.kind != "value"

    @property
    def entry_dtype(self) -> str:
        """The data type to read it as: any Logix string type is STRING."""
        if self.struct is not None and self.struct.is_string:
            return "STRING"
        return self.dtype

    @property
    def type_label(self) -> str:
        if self.kind == "scope":
            n = len(self._tags or [])
            return f"{n:,} tag{'s' if n != 1 else ''}"
        if self.kind == "range":
            return ""
        if self.kind == "array":
            remaining = self.dims[len(self._prefix):]
            return f"{self.dtype}[{','.join(str(d) for d in remaining)}]"
        return self.dtype

    def children(self) -> list["Node"]:
        if self.kind == "scope":
            return [tag_node(t) for t in self._tags or []]
        if self.kind == "struct":
            return [member_node(self.path, m) for m in self.struct.members] if self.struct else []
        if self.kind in ("array", "range"):
            return self._elements()
        return []

    def _elements(self) -> list["Node"]:
        depth = len(self._prefix)
        size = self.dims[depth]
        if self.kind == "array" and size > PAGE:
            out = []
            for lo in range(0, size, PAGE):
                hi = min(size, lo + PAGE) - 1
                out.append(Node(f"[{lo}..{hi}]", self.path, self.dtype, "range",
                                struct=self.struct, dims=self.dims, lo=lo, hi=hi,
                                prefix=self._prefix))
            return out
        lo, hi = (self._lo, self._hi) if self.kind == "range" else (0, size - 1)
        base = self.path
        out = []
        for i in range(lo, hi + 1):
            index = self._prefix + (i,)
            label = f"[{','.join(map(str, index))}]"
            if depth + 1 < len(self.dims):
                # A two- or three-dimensional array: Logix reads Tag[1,2],
                # so the inner level keeps the indices so far.
                out.append(Node(label, base, self.dtype, "array", struct=self.struct,
                                dims=self.dims, prefix=index))
            else:
                out.append(_value_or_struct(label, f"{base}{label}", self.dtype, self.struct))
        return out


def _value_or_struct(label: str, path: str, dtype: str, struct_: StructDef | None) -> Node:
    if struct_ is not None and not struct_.is_string:
        return Node(label, path, dtype, "struct", struct=struct_)
    return Node(label, path, dtype, "value", struct=struct_)


def tag_node(tag: TagDef) -> Node:
    label = tag.name.split(".", 1)[1] if tag.scope.startswith("Program:") else tag.name
    if tag.dims:
        return Node(label, tag.name, tag.dtype, "array", struct=tag.struct, dims=tag.dims)
    return _value_or_struct(label, tag.name, tag.dtype, tag.struct)


def member_node(parent: str, m: Member) -> Node:
    path = f"{parent}.{m.name}"
    if m.length:
        return Node(m.name, path, m.dtype, "array", struct=m.struct, dims=(m.length,))
    return _value_or_struct(m.name, path, m.dtype, m.struct)


def tree(db: Database) -> list[Node]:
    """The top of the tree: controller tags, then each program, then I/O."""
    out = []
    controller = [t for t in db.tags if not t.scope]
    out.append(Node("Controller tags", "", "", "scope", tags=controller))
    scopes: dict[str, list[TagDef]] = {}
    for t in db.tags:
        if t.scope:
            scopes.setdefault(t.scope, []).append(t)
    for program in db.programs:
        scopes.setdefault(f"Program:{program}", [])
    for scope in sorted((s for s in scopes if s.startswith("Program:")), key=str.lower):
        out.append(Node(scope, scope, "", "scope", tags=scopes[scope]))
    if scopes.get("I/O"):
        out.append(Node("I/O modules", "", "", "scope", tags=scopes["I/O"]))
    return out


# ------------------------------------------------------------------ search

def search(db: Database, text: str, limit: int = 400) -> tuple[list[Node], bool]:
    """Tags and members whose address or type contains `text`.

    Structures are searched all the way down; arrays are not walked element
    by element (a REAL[10000] would be ten thousand matches for "real").
    An array whose element type has a matching member is returned as the
    array, to open. Returns the matches and whether `limit` cut them short.
    """
    needle = text.strip().lower()
    if not needle:
        return [], False
    found: list[Node] = []
    memo: dict[int, bool] = {}

    def type_has(struct_: StructDef | None, stack: frozenset = frozenset()) -> bool:
        if struct_ is None or struct_.is_string or id(struct_) in stack:
            return False
        key = id(struct_)
        if key not in memo:
            stack = stack | {key}
            memo[key] = any(needle in m.name.lower() or needle in m.dtype.lower()
                            or type_has(m.struct, stack) for m in struct_.members)
        return memo[key]

    def walk(node: Node, depth: int) -> Iterator[Node]:
        hit = needle in node.path.lower() or needle in node.dtype.lower()
        if node.kind == "array":
            if hit or type_has(node.struct):
                yield node
            return
        if hit:
            yield node
        if node.kind == "struct" and depth < 12:
            for child in node.children():
                yield from walk(child, depth + 1)

    for top in tree(db):
        for node in top.children():
            for match in walk(node, 0):
                found.append(match)
                if len(found) >= limit:
                    return found, True
    return found, False


# ------------------------------------------------------- pycomm3's database

def _struct_from_pycomm3(data_type: dict, cache: dict[str, StructDef]) -> StructDef:
    name = str(data_type.get("name", "?"))
    if name in cache:
        return cache[name]
    sdef = StructDef(name, is_string=bool(data_type.get("string")))
    cache[name] = sdef            # before the members: a type can hold itself by reference
    if sdef.is_string:
        return sdef
    internal = data_type.get("internal_tags", {}) or {}
    for member in data_type.get("attributes", []) or []:
        info = internal.get(member) or {}
        dtype = str(info.get("data_type_name", ""))
        sub = None
        if info.get("tag_type") == "struct" and isinstance(info.get("data_type"), dict):
            sub = _struct_from_pycomm3(info["data_type"], cache)
            dtype = sub.name
        length = int(info.get("array", 0) or 0) if dtype != "BOOL" else 0
        if dtype == "DWORD" and length:
            dtype, length = "BOOL", _bool_count(length)
        sdef.members.append(Member(member, dtype, sub, length))
    return sdef


def _scope_of(name: str) -> str:
    if name.startswith("Program:"):
        return name.split(".", 1)[0]
    if ":" in name:
        return "I/O"
    return ""


def from_pycomm3(tags: dict, programs: Iterable[str] = (), source: str = "") -> Database:
    """Turn pycomm3's `LogixDriver.tags` into a Database."""
    cache: dict[str, StructDef] = {}
    out = []
    for name, info in tags.items():
        dtype = str(info.get("data_type_name", ""))
        sdef = None
        if info.get("tag_type") == "struct" and isinstance(info.get("data_type"), dict):
            sdef = _struct_from_pycomm3(info["data_type"], cache)
            dtype = sdef.name
        dim = int(info.get("dim", 0) or 0)
        dims = tuple(int(d) for d in (info.get("dimensions") or [])[:dim] if d)
        if dtype == "DWORD" and dims:
            dtype, dims = "BOOL", (_bool_count(dims[0]),) + dims[1:]
        out.append(TagDef(name, dtype, sdef, dims, _scope_of(name)))
    out.sort(key=lambda t: t.name.lower())
    return Database(out, source, sorted(programs, key=str.lower))


def browse_pycomm3(ip: str, slot: int = 0, source_ip: str = "",
                   progress: Callable[[str], None] | None = None) -> Database:
    """Upload the tag database over pycomm3, on a connection of its own."""
    try:
        from pycomm3 import LogixDriver
    except ImportError as exc:
        raise BrowseError("pycomm3 is not installed.") from exc
    from app.engine.plc_drivers import _bind_to_source_ip

    if progress:
        progress(f"Reading the tag list from {ip}...")
    plc = LogixDriver(f"{ip}/{slot}", init_tags=True, init_program_tags=True)
    try:
        with _bind_to_source_ip(source_ip):
            opened = plc.open()
        if opened is False:
            raise BrowseError("The device took the connection but did not register an "
                              "EtherNet/IP session. Check the address and the CPU slot.")
        programs = list((plc.info or {}).get("programs", {}) or {})
        return from_pycomm3(plc.tags or {}, programs, f"{ip}, slot {slot} (pycomm3)")
    except BrowseError:
        raise
    except Exception as exc:
        raise BrowseError(f"Could not read the tag list from {ip}: {exc}") from exc
    finally:
        try:
            plc.close()
        except Exception:
            pass


# ------------------------------------------------- libplctag's raw listings

@dataclass
class RawTag:
    instance: int
    type: int
    elem_size: int
    dims: tuple[int, int, int]
    name: str

    @property
    def is_struct(self) -> bool:
        return bool(self.type & TYPE_STRUCT)

    @property
    def is_system(self) -> bool:
        return bool(self.type & TYPE_SYSTEM)

    @property
    def num_dims(self) -> int:
        return (self.type & TYPE_DIMS) >> 13

    @property
    def udt_id(self) -> int:
        return self.type & TYPE_ID


@dataclass
class RawField:
    name: str
    meta: int                 # array length, or bit number for a BOOL
    type: int
    offset: int


@dataclass
class RawUdt:
    id: int
    name: str
    size: int
    fields: list[RawField]


def parse_tag_list(buf: bytes) -> list[RawTag]:
    """Decode a `@tags` listing.

    Each entry: instance id (u32), symbol type (u16), element size (u16),
    three array dimensions (u32 each), name length (u16) and the name. The
    layout libplctag's list_tags_logix tool reads.
    """
    out = []
    pos = 0
    while pos + 22 <= len(buf):
        instance, typ, size, d0, d1, d2, n = struct.unpack_from("<IHHIIIH", buf, pos)
        pos += 22
        if pos + n > len(buf):
            raise BrowseError("The tag list the controller sent was cut short.")
        name = buf[pos:pos + n].decode("utf-8", errors="replace")
        pos += n
        out.append(RawTag(instance, typ, size, (d0, d1, d2), name))
    return out


def parse_udt(buf: bytes) -> RawUdt:
    """Decode a `@udt/<id>` template.

    A 14-byte header (id u16, member description size u32, instance size
    u32, member count u16, handle u16), then eight bytes per member (meta
    u16, type u16, offset u32), then NUL-terminated strings: the type's name
    up to a semicolon, then each member's name.
    """
    if len(buf) < 14:
        raise BrowseError("A structure definition the controller sent was cut short.")
    udt_id, _desc, size, count, _handle = struct.unpack_from("<HIIHH", buf, 0)
    pos = 14
    raw = []
    for _ in range(count):
        if pos + 8 > len(buf):
            raise BrowseError("A structure definition the controller sent was cut short.")
        meta, typ, offset = struct.unpack_from("<HHI", buf, pos)
        raw.append((meta, typ, offset))
        pos += 8
    strings = buf[pos:].split(b"\x00")
    strings = [s.decode("utf-8", errors="replace") for s in strings]
    while strings and strings[-1] == "" and len(strings) > count + 1:
        strings.pop()
    first = strings[0] if strings else ""
    if ";" in first:
        name, names = first.split(";", 1)[0], strings[1:]
    elif len(strings) > count:
        # A predefined type (TIMER, COUNTER ...) may put its name first
        # without the semicolon -- pycomm3 handles it the same way.
        name, names = first, strings[1:]
    else:
        name, names = f"UDT_{udt_id:03X}", strings
    names = (names + [""] * count)[:count]
    fields = [RawField(n, m, t, o) for n, (m, t, o) in zip(names, raw)]
    return RawUdt(udt_id, name or f"UDT_{udt_id:03X}", size, fields)


def _hidden(name: str, predefined: bool) -> bool:
    return (not name or name.startswith("ZZZZZZZZZZ") or name.startswith("__")
            or (predefined and name in ("CTL", "Control")))


class _Udts:
    """Structure definitions, fetched once each and shared."""

    def __init__(self, fetch: Callable[[int], RawUdt]) -> None:
        self.fetch = fetch
        self.done: dict[int, StructDef] = {}

    def get(self, udt_id: int) -> StructDef:
        if udt_id in self.done:
            return self.done[udt_id]
        raw = self.fetch(udt_id)
        sdef = StructDef(raw.name)
        self.done[udt_id] = sdef
        predefined = udt_id < 0x100 or udt_id > 0xEFF
        visible = [f for f in raw.fields if not _hidden(f.name, predefined)]
        if ([f.name for f in visible] == ["LEN", "DATA"]
                and ATOMIC.get(visible[1].type & 0xFF) == "SINT" and visible[1].meta):
            sdef.is_string = True
            return sdef
        for f in visible:
            if f.type & TYPE_STRUCT:
                sub = self.get(f.type & TYPE_ID)
                sdef.members.append(Member(f.name, sub.name, sub, f.meta))
                continue
            dtype = ATOMIC.get(f.type & 0xFF, f"0x{f.type:04X}")
            if dtype == "BOOL":
                sdef.members.append(Member(f.name, "BOOL"))
            elif dtype == "DWORD" and f.meta:
                sdef.members.append(Member(f.name, "BOOL", None, _bool_count(f.meta)))
            else:
                sdef.members.append(Member(f.name, dtype, None, f.meta))
        return sdef


def _io_tag(name: str) -> bool:
    return any(x in name for x in (":I", ":O", ":C", ":S"))


def assemble(controller: list[RawTag], programs: dict[str, list[RawTag]],
             fetch_udt: Callable[[int], RawUdt], source: str = "") -> Database:
    """Build a Database from raw listings. `programs` maps a program's name
    (without "Program:") to its listing; `fetch_udt` reads one template."""
    udts = _Udts(fetch_udt)
    out: list[TagDef] = []

    def add(raw: RawTag, scope: str) -> None:
        if raw.is_system or raw.name.startswith("__"):
            return
        name = raw.name
        if ":" in name and not (scope == "" and _io_tag(name)):
            return            # Program:, Routine:, Task:, Map:, Cxn: and friends
        if scope:
            name = f"{scope}.{name}"
        dims = tuple(d for d in raw.dims[:raw.num_dims] if d)
        if raw.is_struct:
            sdef = udts.get(raw.udt_id)
            dtype = sdef.name
        else:
            sdef = None
            dtype = ATOMIC.get(raw.type & 0xFF, f"0x{raw.type & 0xFF:02X}")
            if dtype == "DWORD" and dims:
                dtype, dims = "BOOL", (_bool_count(dims[0]),) + dims[1:]
        out.append(TagDef(name, dtype, sdef, dims, scope or ("I/O" if ":" in name else "")))

    for raw in controller:
        add(raw, "")
    for program, listing in programs.items():
        for raw in listing:
            add(raw, f"Program:{program}")
    out.sort(key=lambda t: t.name.lower())
    return Database(out, source, sorted(programs, key=str.lower))


def program_names(controller: list[RawTag]) -> list[str]:
    return [r.name[len("Program:"):] for r in controller if r.name.startswith("Program:")]


def browse_libplctag(cfg, progress: Callable[[str], None] | None = None) -> Database:
    """Read the listings through plctag.dll. `cfg` is a LibPlcTagConfig."""
    from app.engine.libplctag_driver import (PLCTAG_STATUS_OK, _decode_error,
                                             _load_lib)

    if cfg.protocol != "ab-eip" or cfg.cpu.lower() not in (
            "lgx", "controllogix", "compactlogix", "micro800", "micro800_cip", "omron-njnx"):
        raise BrowseError(
            "This controller does not keep a list of named tags to read: PLC-5, SLC 500 "
            "and MicroLogix address data by file (N7:0, B3:0/1, F8:2). Type those in.")
    lib, err = _load_lib(cfg.dll_path)
    if not lib:
        raise BrowseError(err)

    def attrib(name: str) -> str:
        parts = [f"protocol={cfg.protocol}", f"gateway={cfg.gateway}"]
        if cfg.path:
            parts.append(f"path={cfg.path}")
        parts += [f"cpu={cfg.cpu}", f"name={name}", f"debug={cfg.debug}"]
        return "&".join(parts)

    def read(name: str) -> bytes:
        tid = lib.plc_tag_create(attrib(name).encode("utf-8"), cfg.timeout_ms)
        if tid < 0:
            raise BrowseError(f"Could not open {name}: {_decode_error(lib, tid)}")
        try:
            rc = lib.plc_tag_read(tid, cfg.timeout_ms)
            if rc != PLCTAG_STATUS_OK:
                raise BrowseError(f"Could not read {name}: {_decode_error(lib, rc)}")
            size = lib.plc_tag_get_size(tid)
            if size <= 0:
                return b""
            raw_bytes = getattr(lib, "plc_tag_get_raw_bytes", None)
            if raw_bytes is not None:
                raw_bytes.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
                raw_bytes.restype = ctypes.c_int
                buf = ctypes.create_string_buffer(size)
                if raw_bytes(tid, 0, buf, size) == PLCTAG_STATUS_OK:
                    return buf.raw
            return bytes(lib.plc_tag_get_uint8(tid, i) & 0xFF for i in range(size))
        finally:
            lib.plc_tag_destroy(tid)

    if progress:
        progress(f"Reading the tag list from {cfg.gateway}...")
    controller = parse_tag_list(read("@tags"))
    programs: dict[str, list[RawTag]] = {}
    for program in program_names(controller):
        if progress:
            progress(f"Reading Program:{program}...")
        try:
            programs[program] = parse_tag_list(read(f"Program:{program}.@tags"))
        except BrowseError:
            programs[program] = []      # listed, but its tags could not be read
    return assemble(controller, programs, lambda i: parse_udt(read(f"@udt/{i}")),
                    f"{cfg.gateway} {cfg.path} (libplctag)".replace("  ", " "))
