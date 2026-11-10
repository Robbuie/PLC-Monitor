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
    tag: str
    dtype: str = ""
    description: str = ""
    scope: str = ""          # program name for a Rockwell program tag

    @property
    def basic(self) -> bool:
        return self.dtype.upper() in BASIC_TYPES or not self.dtype


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
    if ext == ".l5x" or raw.lstrip()[:5] == b"<?xml":
        entries = _read_l5x(raw)
    else:
        entries = _read_text(_decode(raw))
    seen: set[str] = set()
    unique = []
    for entry in entries:
        if entry.tag and entry.tag not in seen:
            seen.add(entry.tag)
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
