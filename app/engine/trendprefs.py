"""The Trend page's saved settings, kept in the profile's own config.

They live in the profile rather than in ui.json because they describe the
site, not the person: a tag's units and sensible scale, and the sets of tags
worth looking at together ("Press handshake", "Oven zone 2"). So they travel
with the profile when it is exported to a .plcmon and are there for whoever
opens it on the plant PC. The service never reads them.

The block, under the config's "trend" key::

    {"pens":    {tag: {"description": str, "units": str,
                       "scale": [low, high] | None, "draw": "auto"|"line"|"lane",
                       "high": float | None, "low": float | None,
                       "stuck_s": float | None, "jump": float | None}},
     "math":    {name: {"op": "diff"|"sum"|"product"|"ratio"|"rate"|"duration",
                        "a": tag, "b": tag, "window_s": float, "per": "s"|"min",
                        "value": str}},
     "layouts": {name: {"tags": [...], "robots": [...], "arrange": str,
                        "range": minutes | None, "cursors": bool,
                        "events": bool, "pairs": bool}}}

No Qt here: the page passes ConfigManager's dict in and saves it after.
"""

from __future__ import annotations

import copy
import math

DRAW_CHOICES = ("auto", "line", "lane")
ARRANGE_CHOICES = ("stacked", "same", "shared")

DEFAULT_PEN = {"description": "", "units": "", "scale": None, "draw": "auto",
               "high": None, "low": None, "stuck_s": None, "jump": None}
#: The pen settings that are flag rules (engine/trendmath.flags).
RULES = ("high", "low", "stuck_s", "jump")
MATH_OPS = ("diff", "sum", "product", "ratio", "rate", "duration")


def _read(config: dict, key: str) -> dict:
    """A part of the block, for reading: never adds anything to the config,
    or the window would see a change nobody made and ask to save it."""
    block = config.get("trend")
    part = block.get(key) if isinstance(block, dict) else None
    return part if isinstance(part, dict) else {}


def _block(config: dict) -> dict:
    block = config.get("trend")
    if not isinstance(block, dict):
        block = {}
        config["trend"] = block
    for key in ("pens", "layouts", "math"):
        if not isinstance(block.get(key), dict):
            block[key] = {}
    return block


def pen(config: dict, tag: str) -> dict:
    """A tag's settings, with defaults for anything not set. A copy."""
    stored = _read(config, "pens").get(tag)
    out = dict(DEFAULT_PEN)
    if isinstance(stored, dict):
        out.update({k: stored[k] for k in DEFAULT_PEN if k in stored})
    out["scale"] = clean_scale(out.get("scale"))
    if out["draw"] not in DRAW_CHOICES:
        out["draw"] = "auto"
    out["description"] = str(out.get("description") or "")
    out["units"] = str(out.get("units") or "")
    for key in RULES:
        out[key] = _number(out.get(key))
    for key in ("stuck_s", "jump"):
        if out[key] is not None and out[key] <= 0:
            out[key] = None
    return out


def _number(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def rules(settings: dict) -> dict:
    """The flag rules of a pen's settings, or {} when it has none."""
    found = {k: settings.get(k) for k in RULES if settings.get(k) is not None}
    return found


def clean_scale(scale) -> list[float] | None:
    """[low, high] when both are numbers and low < high, else None (automatic)."""
    try:
        low, high = float(scale[0]), float(scale[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not (math.isfinite(low) and math.isfinite(high)) or low >= high:
        return None
    return [low, high]


def set_pen(config: dict, tag: str, settings: dict) -> dict:
    """Store a tag's settings. All defaults removes the entry, so the
    config only holds what somebody actually set."""
    merged = dict(DEFAULT_PEN)
    merged.update({k: settings[k] for k in DEFAULT_PEN if k in settings})
    merged["scale"] = clean_scale(merged.get("scale"))
    if merged["draw"] not in DRAW_CHOICES:
        merged["draw"] = "auto"
    merged["description"] = str(merged["description"] or "").strip()
    merged["units"] = str(merged["units"] or "").strip()
    for key in RULES:
        merged[key] = _number(merged.get(key))
    for key in ("stuck_s", "jump"):
        if merged[key] is not None and merged[key] <= 0:
            merged[key] = None
    if merged == DEFAULT_PEN:
        if tag in _read(config, "pens"):
            _block(config)["pens"].pop(tag, None)
        return merged
    _block(config)["pens"][tag] = merged
    return merged


def layouts(config: dict) -> dict[str, dict]:
    """Saved layouts by name, sorted by name. Copies."""
    stored = _read(config, "layouts")
    return {name: clean_layout(stored[name])
            for name in sorted(stored, key=str.lower) if isinstance(stored[name], dict)}


def clean_layout(layout: dict) -> dict:
    rng = layout.get("range")
    if rng is not None:
        try:
            rng = float(rng)
        except (TypeError, ValueError):
            rng = None
    arrange = layout.get("arrange")
    return {
        "tags": [str(t) for t in layout.get("tags") or [] if str(t)],
        "robots": [str(r) for r in layout.get("robots") or [] if str(r)],
        "arrange": arrange if arrange in ARRANGE_CHOICES else "stacked",
        "range": rng,
        "cursors": bool(layout.get("cursors", False)),
        "events": bool(layout.get("events", False)),
        "pairs": bool(layout.get("pairs", False)),
    }


def save_layout(config: dict, name: str, layout: dict) -> str:
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("A layout needs a name.")
    _block(config)["layouts"][name] = clean_layout(copy.deepcopy(layout))
    return name


def delete_layout(config: dict, name: str) -> bool:
    return name in _read(config, "layouts") and \
        _block(config)["layouts"].pop(name, None) is not None


def math_pens(config: dict) -> dict[str, dict]:
    """Math pens by name, sorted by name. Copies."""
    stored = _read(config, "math")
    return {name: clean_math(stored[name])
            for name in sorted(stored, key=str.lower) if isinstance(stored[name], dict)}


def clean_math(spec: dict) -> dict:
    op = spec.get("op") if spec.get("op") in MATH_OPS else "diff"
    window = _number(spec.get("window_s"))
    return {"op": op, "a": str(spec.get("a") or ""), "b": str(spec.get("b") or ""),
            "window_s": window if window and window > 0 else 60.0,
            "per": "min" if spec.get("per") == "min" else "s",
            "value": str(spec.get("value") if spec.get("value") is not None else "").strip()}


def save_math(config: dict, name: str, spec: dict) -> str:
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("A math pen needs a name.")
    spec = clean_math(spec)
    if not spec["a"]:
        raise ValueError("Choose the tag it is worked out from.")
    _block(config)["math"][name] = spec
    return name


def delete_math(config: dict, name: str) -> bool:
    return name in _read(config, "math") and _block(config)["math"].pop(name, None) is not None
