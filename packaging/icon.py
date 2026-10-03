"""Draws the application icon. Build-time only; nothing here ships.

The family's mark, after File Manager's packaging/icon.py: a dark rounded tile,
light shapes on it, one stroke of the accent. Redline PDF is a sheet with a red
line through it, File Manager two panes with the active one's header in blue,
NetControl a tree of devices with one lit. This is a reading panel -- three
grey rows of tags -- with a Drafting-blue trace stepping across it, which is
what the application is: values, and the moment they changed.

    python packaging/icon.py

Writes packaging/icon.ico and assets/icon.png. Both are committed.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

CANVAS = 1024
SCALE = CANVAS // 256

TILE_TOP = (32, 36, 44)
TILE_BOTTOM = (16, 18, 22)
PANE = (233, 237, 244)
ROW = (168, 176, 189)
ACCENT = (74, 145, 255)

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _px(value: float) -> int:
    return int(round(value * SCALE))


def draw() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    gradient = Image.new("RGB", (1, CANVAS))
    for y in range(CANVAS):
        ratio = y / (CANVAS - 1)
        gradient.putpixel((0, y), tuple(int(round(t + (b - t) * ratio))
                                        for t, b in zip(TILE_TOP, TILE_BOTTOM)))
    gradient = gradient.resize((CANVAS, CANVAS))
    mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CANVAS - 1, CANVAS - 1), radius=_px(58), fill=255)
    image.paste(gradient, (0, 0), mask)
    canvas = ImageDraw.Draw(image)

    # The panel.
    panel = (_px(38), _px(52), CANVAS - _px(38), CANVAS - _px(52))
    canvas.rounded_rectangle(panel, radius=_px(12), fill=PANE)

    # Three tag rows across the top: a name and a value each.
    x0, y0, x1, _ = panel
    pad = _px(14)
    for i in range(3):
        y = y0 + _px(18) + i * _px(20)
        h = _px(7)
        canvas.rounded_rectangle((x0 + pad, y, x0 + pad + _px(58), y + h), radius=h // 2, fill=ROW)
        canvas.rounded_rectangle((x1 - pad - _px(34), y, x1 - pad, y + h), radius=h // 2, fill=ROW)

    # The trace: a step, the shape a logged change makes. One stroke, one colour.
    w = _px(11)
    base = panel[3] - _px(26)
    high = base - _px(46)
    left, right = x0 + pad, x1 - pad
    points = [(left, base), (left + _px(42), base), (left + _px(42), high),
              (left + _px(104), high), (left + _px(104), base - _px(18)), (right, base - _px(18))]
    canvas.line(points, fill=ACCENT, width=w, joint="curve")
    for p in (points[0], points[-1]):
        canvas.ellipse((p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2), fill=ACCENT)
    return image


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    master = draw().resize((256, 256), Image.LANCZOS)
    ico = os.path.join(here, "icon.ico")
    master.save(ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    png = os.path.join(root, "assets", "icon.png")
    os.makedirs(os.path.dirname(png), exist_ok=True)
    master.save(png, format="PNG")
    print(f"wrote {ico}\nwrote {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
