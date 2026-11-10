"""Reading tag lists out of files, so nobody types two hundred tag names.

Understands:

  * **Rockwell L5X** -- a Studio 5000 / RSLogix 5000 project or tag export
    (File > Save As > .L5X). Controller tags and every program's tags, with
    their data types and descriptions; program tags come back as
    `Program:<name>.<tag>`, the form pycomm3 and libplctag read.
  * **Rockwell tag CSV** -- Studio 5000's Tools > Export > Tags and Logic
    Comments. The `remark` lines are skipped, TAG and ALIAS rows kept.
  * **Any other CSV or text list** -- a column headed Tag, Name, Address,
    Device or Symbol (the first column when there is no header), with an
    optional Type / Data type and Description / Comment. That covers a
    Siemens or Mitsubishi list kept in a spreadsheet, or one tag per line.

  * **Mitsubishi GX Works3 project** (.gx3) -- the global labels that sit on
    a device (D, W, R, ZR, M, X, Y ...), named as in the project, with their
    comments. A word array comes in one register per element, named
    `label[i]` with the element's comment, plus suggested grouped tags where
    the element comments say consecutive registers hold one thing ("vin
    digit 0/1", "vin digit 2/3" ... becomes `uSendData VIN`, D1000 x4 as
    text).

Nothing here talks to a PLC; it only reads the file a person picked.
No Qt: tested on its own.
"""

from __future__ import annotations

import csv
import io
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

#: Data types the poller can log as one value each. A UDT, an array or a
#: string-ish structure reads as a dict or list -- loggable as text, but not
#: something a trend or a latency trigger can use -- so the picker offers to
#: hide them.
BASIC_TYPES = {"BOOL", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT",
               "REAL", "LREAL", "STRING", "WORD", "DWORD", "LWORD", "BYTE", "TIMER.ACC",
               "COUNTER.ACC"}

TAG_HEADERS = ("tag", "tag name", "tagname", "name", "address", "device", "device name",
               "symbol", "item")
TYPE_HEADERS = ("type", "data type", "datatype", "data_type", "dtype")
DESC_HEADERS = ("description", "comment", "comments", "desc")


@dataclass
class TagEntry:
    tag: str                 # the address: D1000, Program:Main.Count
    dtype: str = ""
    description: str = ""
    scope: str = ""          # program name for a Rockwell program tag
    # A GX Works3 label carries a name and may span registers.
    name: str = ""
    count: int = 1
    fmt: str = ""

    @property
    def basic(self) -> bool:
        return self.count > 1 or self.dtype.upper() in BASIC_TYPES or not self.dtype

    @property
    def label(self) -> str:
        """What the picker lists it as: its name if it has one."""
        return self.name or self.tag

    @property
    def kind(self) -> str:
        """The picker's second column: the data type, or for a named or
        grouped entry where it lives and how it is read."""
        if self.name or self.count > 1 or self.fmt:
            return self.spec().describe()
        return self.dtype

    def spec(self, dtype: str = "DINT"):
        from app.engine.tagspec import TagSpec
        return TagSpec(address=self.tag, count=self.count, fmt=self.fmt, name=self.name,
                       dtype=dtype)


class TagImportError(ValueError):
    pass


def read(path: str) -> list[TagEntry]:
    """Every tag in the file, in file order, without duplicates."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise TagImportError(f"Could not read the file: {exc}") from exc
    if ext == ".gx3" or raw[:4] == b"PK\x03\x04":
        entries = _read_gx3(path)
    elif ext == ".l5x" or raw.lstrip()[:5] == b"<?xml":
        entries = _read_l5x(raw)
    else:
        entries = _read_text(_decode(raw))
    seen: set[tuple] = set()
    unique = []
    for entry in entries:
        key = (entry.tag, entry.count, entry.fmt)
        if entry.tag and key not in seen:
            seen.add(key)
            unique.append(entry)
    if not unique:
        raise TagImportError("No tags found in that file.")
    return unique


def _decode(raw: bytes) -> str:
    # Studio 5000 writes UTF-8 with a BOM on current versions and the
    # Windows code page on older ones; try them in that order.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ------------------------------------------------------------------ L5X


def _read_l5x(raw: bytes) -> list[TagEntry]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise TagImportError(f"That is not a readable L5X file: {exc}") from exc
    controller = root.find("Controller") if root.tag != "Controller" else root
    if controller is None:
        raise TagImportError("That XML file has no Controller in it -- is it an L5X export?")
    entries = [_l5x_tag(tag, "") for tag in controller.findall("./Tags/Tag")]
    for program in controller.findall("./Programs/Program"):
        name = program.get("Name", "")
        entries += [_l5x_tag(tag, name) for tag in program.findall("./Tags/Tag")]
    return entries


def _l5x_tag(element, program: str) -> TagEntry:
    name = element.get("Name", "")
    dtype = element.get("DataType", "") or ""
    if element.get("TagType") == "Alias" and not dtype:
        dtype = "alias"
    dims = (element.get("Dimensions") or "").strip()
    if dims and dims != "0":
        dtype = f"{dtype}[{dims.replace(' ', ',')}]"
    desc = element.findtext("./Description") or ""
    full = f"Program:{program}.{name}" if program else name
    return TagEntry(tag=full, dtype=dtype, description=" ".join(desc.split()), scope=program)


# ------------------------------------------------------------------ CSV


def _read_text(text: str) -> list[TagEntry]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    # Studio 5000's export: remark lines, then TYPE,SCOPE,NAME,DESCRIPTION,DATATYPE,...
    for i, line in enumerate(lines):
        head = [c.strip().upper() for c in next(csv.reader([line]))]
        if head[:3] == ["TYPE", "SCOPE", "NAME"]:
            return _read_rockwell_csv(lines[i:])
    dialect = _sniff(lines[0])
    rows = list(csv.reader(io.StringIO("\n".join(lines)), dialect))
    header = [c.strip().lower() for c in rows[0]]
    tag_col = _column(header, TAG_HEADERS)
    if tag_col is None:
        # No header: one tag per line, or the tag in the first column.
        return [TagEntry(tag=r[0].strip()) for r in rows if r and r[0].strip()]
    type_col, desc_col = _column(header, TYPE_HEADERS), _column(header, DESC_HEADERS)
    out = []
    for r in rows[1:]:
        if len(r) <= tag_col or not r[tag_col].strip():
            continue
        out.append(TagEntry(tag=r[tag_col].strip(),
                            dtype=r[type_col].strip() if type_col is not None and len(r) > type_col else "",
                            description=r[desc_col].strip() if desc_col is not None and len(r) > desc_col else ""))
    return out


def _sniff(first_line: str):
    for delimiter in (",", ";", "\t"):
        if delimiter in first_line:
            class Dialect(csv.excel):
                pass
            Dialect.delimiter = delimiter
            return Dialect
    return csv.excel


def _column(header: list[str], names) -> int | None:
    for name in names:
        if name in header:
            return header.index(name)
    return None


def _read_rockwell_csv(lines: list[str]) -> list[TagEntry]:
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    header = [c.strip().upper() for c in rows[0]]
    col = {name: header.index(name) for name in ("TYPE", "SCOPE", "NAME", "DESCRIPTION", "DATATYPE")
           if name in header}
    out = []
    for r in rows[1:]:
        def cell(name):
            i = col.get(name)
            return r[i].strip() if i is not None and i < len(r) else ""
        kind = cell("TYPE").upper()
        if kind not in ("TAG", "ALIAS"):
            continue
        scope, name = cell("SCOPE"), cell("NAME")
        if not name:
            continue
        dtype = cell("DATATYPE") or ("alias" if kind == "ALIAS" else "")
        out.append(TagEntry(tag=f"Program:{scope}.{name}" if scope else name, dtype=dtype,
                            description=" ".join(cell("DESCRIPTION").split()), scope=scope))
    return out


# ------------------------------------------------------------ GX Works3

#: Devices whose registers are words, and the ones numbered in hex.
_WORD_DEVICES = {"D", "W", "R", "ZR", "SD", "SW"}
_BIT_DEVICES = {"M", "X", "Y", "B", "L", "F", "SM", "SB", "V"}
_HEX = {"X", "Y", "B", "W", "SB", "SW"}
#: Comment words that say nothing about WHAT a register holds.
_NOISE = {"digit", "digits", "ascii", "dec", "spare", "no", "number", "numbre", "byte",
          "char", "chars", "low", "high", "hi", "lo"}
#: A run with one of these in its comments is characters, two per register.
_TEXT_HINTS = ("ascii", "digit", "vin", "char", "text", "string", "/")


def _read_gx3(path: str) -> list[TagEntry]:
    """Global labels from a GX Works3 project.

    A .gx3 is a zip; the labels are in LabelData.db, an SQLite database
    inside it. Read with the standard library only, from a temporary copy
    (sqlite needs a file), and never written.
    """
    import shutil
    import sqlite3
    import tempfile
    import zipfile

    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise TagImportError(f"That is not a GX Works3 project: {exc}") from exc
    names = {n.split("\\")[-1].split("/")[-1]: n for n in archive.namelist()}
    if "LabelData.db" not in names:
        raise TagImportError("That project has no label data (LabelData.db). Is it a GX Works3 "
                             "project?")
    folder = tempfile.mkdtemp(prefix="plcmon-gx3-")
    try:
        target = os.path.join(folder, "LabelData.db")
        with archive.open(names["LabelData.db"]) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            return _gx3_labels(con)
        except sqlite3.Error as exc:
            raise TagImportError(f"Could not read the project's labels: {exc}") from exc
        finally:
            con.close()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _gx3_labels(con) -> list[TagEntry]:
    import re

    def column(label_id, row_id):
        return dict(con.execute("select ColumnID, ColumnStrValue from ColumnDataTbl "
                                "where LabelID=? and RowID=?", (label_id, row_id)))

    out: list[TagEntry] = []
    rows = con.execute("select LabelID, RowID, MELSECDevice from DeviceAssignTbl "
                       "where MELSECDevice is not null and MELSECDevice <> '' "
                       "order by LabelID, RowID").fetchall()
    for label_id, row_id, device in rows:
        m = re.match(r"^([A-Z]+)([0-9A-F]+)$", (device or "").upper())
        if not m:
            continue                        # LV:84 (local), U3\G100, Z0 ...
        prefix, number = m.group(1), m.group(2)
        if prefix not in _WORD_DEVICES and prefix not in _BIT_DEVICES:
            continue
        cols = column(label_id, row_id)
        if (cols.get(1) or "") != "VAR_GLOBAL":
            continue
        name = (cols.get(2) or "").strip()
        comment = " ".join((cols.get(15) or "").split())
        size_row = con.execute("select BitCount from RowTbl where LabelID=? and RowID=?",
                               (label_id, row_id)).fetchone()
        try:
            size = max(1, int(size_row[0])) if size_row else 1
        except (TypeError, ValueError):
            size = 1
        is_string = (cols.get(3) or "").isdigit() and prefix in _WORD_DEVICES
        if prefix in _BIT_DEVICES or size == 1:
            out.append(TagEntry(tag=device.upper(), name=name, description=comment,
                                dtype="BOOL" if prefix in _BIT_DEVICES else "WORD"))
            continue
        if is_string:
            # STRING(n): the label's registers hold the text.
            out.append(TagEntry(tag=device.upper(), name=name, description=comment,
                                count=size, fmt="ascii", dtype="STRING"))
            continue
        start = int(number, 16) if prefix in _HEX else int(number)
        element = {}
        for offset, text in con.execute(
                "select ArrayOffset, CommentStrValue from CommentDataTbl "
                "where LabelID=? and RowID=? and BitPosition=255", (label_id, row_id)):
            try:
                element[int(offset)] = " ".join((text or "").split())
            except (TypeError, ValueError):
                pass

        def address(i):
            return f"{prefix}{start + i:X}" if prefix in _HEX else f"{prefix}{start + i}"

        out += _suggest_groups(name, size, element, address)
        for i in range(size):
            out.append(TagEntry(tag=address(i), name=f"{name}[{i}]",
                                description=element.get(i) or comment, dtype="WORD"))
    return out


def _stem(comment: str) -> str:
    import re
    words = re.findall(r"[a-z]+", comment.lower())
    return " ".join(w for w in words if w not in _NOISE)


def _suggest_groups(label: str, size: int, element: dict, address) -> list[TagEntry]:
    """Runs of consecutive commented elements that describe one thing:
    "vin digit 0/1", "vin digit 2/3", ... share the stem "vin"."""
    groups = []
    offsets = sorted(o for o in element if 0 <= o < size)
    run: list[int] = []

    def close():
        if len(run) >= 2:
            stem = _stem(element[run[0]])
            texts = " ".join(element[o].lower() for o in run)
            fmt = "ascii" if any(h in texts for h in _TEXT_HINTS) else ""
            title = stem.upper() if len(stem) <= 3 else stem.title()
            groups.append(TagEntry(
                tag=address(run[0]), count=len(run), fmt=fmt,
                name=(f"{label} {title}" if run[0] == 0
                      else f"{label}[{run[0]}..{run[-1]}] {title}"),
                description="; ".join(element[o] for o in run), dtype="WORD"))

    for o in offsets:
        stem = _stem(element[o])
        if run and o == run[-1] + 1 and stem and stem == _stem(element[run[-1]]):
            run.append(o)
        else:
            close()
            run = [o] if stem else []
    close()
    return groups
