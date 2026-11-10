"""What one line in the tag list means: an address, and how to read it.

Most tags are one value read as the driver gives it -- `D100`, `Motor_Running`.
A **grouped tag** spans several registers read in one request and decoded
together: `D1916 x4` as ASCII is the 8-character VIN a Mitsubishi PLC keeps
two characters per register; `D200 x2` as INT32 is a double word. Reading the
registers in one request matters: four separate reads can straddle a PLC scan
and come back half old and half new.

A spec is stored as the tag list's dict (`tags_per_driver` in the config):

    {"tag": "D1916", "count": 4, "format": "ascii", "name": "Plant VIN",
     "swap_bytes": false, "swap_words": false, "dtype": "DINT"}

Only `tag` is required; older configs have `tag` and `dtype` only.

No Qt here.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

#: Format id -> label for the UI. "" means as the driver returns it.
FORMATS: dict[str, str] = {
    "": "As read",
    "ascii": "Text (ASCII, 2 per register)",
    "digits": "Number in text (the digits of the ASCII)",
    "int16": "INT16 (signed)",
    "uint16": "UINT16 (unsigned)",
    "int32": "INT32 (2 registers)",
    "uint32": "UINT32 (2 registers)",
    "float32": "REAL (2 registers)",
    "bcd": "BCD",
    "hex": "Hex",
}

#: Registers one value of each format takes.
WIDTH = {"int32": 2, "uint32": 2, "float32": 2}

#: Mitsubishi devices numbered in hex rather than decimal.
_HEX_DEVICES = {"X", "Y", "B", "W", "SB", "SW", "DX", "DY"}


class SpecError(ValueError):
    pass


@dataclass
class TagSpec:
    address: str
    count: int = 1
    fmt: str = ""
    name: str = ""
    swap_bytes: bool = False
    swap_words: bool = False
    dtype: str = "DINT"

    # ------------------------------------------------------------ identity

    @property
    def grouped(self) -> bool:
        """Read as a block of registers and decoded here, rather than as one
        value the driver decodes itself."""
        return self.count > 1 or bool(self.fmt)

    @property
    def key(self) -> str:
        """What the log and the Live log call it: the name if it has one."""
        if self.name:
            return self.name
        if self.count > 1:
            return f"{self.address} x{self.count}"
        return self.address

    def describe(self) -> str:
        """`D1916-D1919 as Text (ASCII, 2 per register)`, for notes and tips."""
        span = self.address
        members = member_names(self.address, self.count)
        if self.count > 1:
            span = f"{members[0]}-{members[-1]}" if members else f"{self.address} x{self.count}"
        if not self.fmt:
            return span
        extra = []
        if self.swap_bytes:
            extra.append("bytes swapped")
        if self.swap_words:
            extra.append("words swapped")
        tail = f" ({', '.join(extra)})" if extra else ""
        return f"{span} as {FORMATS.get(self.fmt, self.fmt)}{tail}"

    # -------------------------------------------------------------- storage

    def to_entry(self) -> dict:
        entry = {"tag": self.address, "dtype": self.dtype}
        if self.count != 1:
            entry["count"] = self.count
        if self.fmt:
            entry["format"] = self.fmt
        if self.name:
            entry["name"] = self.name
        if self.swap_bytes:
            entry["swap_bytes"] = True
        if self.swap_words:
            entry["swap_words"] = True
        return entry

    @classmethod
    def from_entry(cls, entry) -> "TagSpec":
        if isinstance(entry, str):
            return cls(address=entry.strip())
        try:
            count = max(1, min(int(entry.get("count", 1) or 1), 960))
        except (TypeError, ValueError):
            count = 1
        fmt = str(entry.get("format") or "")
        return cls(address=str(entry.get("tag", "")).strip(), count=count,
                   fmt=fmt if fmt in FORMATS else "",
                   name=str(entry.get("name") or "").strip(),
                   swap_bytes=bool(entry.get("swap_bytes")),
                   swap_words=bool(entry.get("swap_words")),
                   dtype=str(entry.get("dtype") or "DINT"))

    # -------------------------------------------------------------- decoding

    def decode(self, words: list[int]) -> object:
        """The registers read for this tag, as one value."""
        words = [int(w) & 0xFFFF for w in words]
        fmt = self.fmt
        if fmt == "ascii":
            return words_to_text(words, swap_bytes=self.swap_bytes)
        if fmt == "digits":
            return text_to_number(words_to_text(words, swap_bytes=self.swap_bytes))
        if fmt == "hex":
            return " ".join(f"{w:04X}" for w in words)
        if fmt == "bcd":
            ordered = list(reversed(words)) if not self.swap_words else list(words)
            digits = "".join(f"{w:04X}" for w in ordered)
            if not digits.isdigit():
                raise SpecError(f"not BCD: {digits}")
            return int(digits)
        if fmt in ("int16", "uint16", ""):
            # "As read" on a group: each register as a signed 16-bit number,
            # the way a single D register reads.
            signed = fmt != "uint16"
            values = [w - 0x10000 if signed and w & 0x8000 else w for w in words]
            return values[0] if len(values) == 1 else " ".join(str(v) for v in values)
        width = WIDTH.get(fmt)
        if width:
            if len(words) % width:
                raise SpecError(f"{FORMATS[fmt]} needs a multiple of {width} registers")
            out = []
            for i in range(0, len(words), width):
                pair = words[i:i + width]
                # Mitsubishi and most others keep the low word first.
                low, high = (pair[1], pair[0]) if self.swap_words else (pair[0], pair[1])
                raw = struct.pack("<HH", low, high)
                code = {"int32": "<i", "uint32": "<I", "float32": "<f"}[fmt]
                value = struct.unpack(code, raw)[0]
                if fmt == "float32":
                    value = round(value, 6)
                out.append(value)
            return out[0] if len(out) == 1 else " ".join(str(v) for v in out)
        raise SpecError(f"unknown format {fmt!r}")


def words_to_text(words: list[int], swap_bytes: bool = False) -> str:
    """Registers holding two characters each, low byte first (Mitsubishi's
    order; `swap_bytes` for a PLC that puts the first character high).
    NULs end or pad the text and are dropped; anything unprintable shows as ?."""
    chars = []
    for w in words:
        low, high = w & 0xFF, (w >> 8) & 0xFF
        chars.extend((high, low) if swap_bytes else (low, high))
    text = "".join("" if c == 0 else (chr(c) if 32 <= c < 127 else "?") for c in chars)
    return text.rstrip()


def text_to_number(text: str):
    """The number written in a piece of text: "A1234567" -> 1234567, the
    vehicle's sequence number in its VIN. The longest run of digits is
    taken; text with no run of at least two (a placeholder like "0UNKNOWN")
    gives "" -- no value -- rather than a 0 that would plot as a dive to
    zero between vehicles."""
    runs = re.findall(r"\d+", text or "")
    if not runs:
        return ""
    best = max(runs, key=len)
    return int(best) if len(best) >= 2 else ""


# ------------------------------------------------------------- addresses


_MITSU = re.compile(r"^([A-Z]+)([0-9A-F]+)$")
_S7 = re.compile(r"^DB(\d+),(INT|WORD|DINT|REAL|BYTE)(\d+)$")
_ARRAY = re.compile(r"^(.*)\[(\d+)\]$")


def member_names(address: str, count: int) -> list[str]:
    """The single-register tags a group covers, as an older log names them:
    D1916 x4 -> D1916, D1917, D1918, D1919. Used to decode logs made before
    the group existed. Empty when the address form is not one it knows."""
    text = (address or "").strip()
    if count < 1:
        return []
    up = text.upper()
    m = _S7.match(up)
    if m:
        db, kind, offset = int(m.group(1)), m.group(2), int(m.group(3))
        size = {"BYTE": 1, "INT": 2, "WORD": 2, "DINT": 4, "REAL": 4}[kind]
        return [f"DB{db},{kind}{offset + i * size}" for i in range(count)]
    m = _ARRAY.match(text)
    if m:
        return [f"{m.group(1)}[{int(m.group(2)) + i}]" for i in range(count)]
    m = _MITSU.match(up)
    if m:
        prefix, number = m.group(1), m.group(2)
        if prefix in _HEX_DEVICES:
            start = int(number, 16)
            return [f"{prefix}{start + i:X}" for i in range(count)]
        if number.isdigit():
            start = int(number)
            return [f"{prefix}{start + i}" for i in range(count)]
    return [text] if count == 1 else []


_SHORT = re.compile(r"^\s*(\S+)(?:\s+[xX*](\d+))?(?:\s+(\w+))?\s*$")


def parse_shorthand(text: str) -> TagSpec:
    """What the Add box accepts: `D1916`, `D1916 x4`, `D1916 x4 ascii`,
    `D200 x2 int32`. A format name alone (`D100 bcd`) reads one register."""
    m = _SHORT.match(text or "")
    if not m:
        raise SpecError("Type an address, optionally followed by xN and a format.")
    address, count, fmt = m.group(1), m.group(2), (m.group(3) or "").lower()
    if fmt and fmt not in FORMATS:
        aliases = {"text": "ascii", "string": "ascii", "str": "ascii", "dint": "int32",
                   "number": "digits", "num": "digits",
                   "real": "float32", "float": "float32", "int": "int16", "word": "uint16",
                   "dword": "uint32", "udint": "uint32"}
        if fmt not in aliases:
            raise SpecError(f"Unknown format '{fmt}'. Use one of: "
                            + ", ".join(f for f in FORMATS if f))
        fmt = aliases[fmt]
    count_n = int(count) if count else WIDTH.get(fmt, 1)
    if count_n < 1 or count_n > 960:
        raise SpecError("A group is 1 to 960 registers.")
    return TagSpec(address=address, count=count_n, fmt=fmt)


def specs_from(entries) -> list[TagSpec]:
    specs = []
    for entry in entries or []:
        spec = TagSpec.from_entry(entry)
        if spec.address:
            specs.append(spec)
    return specs
