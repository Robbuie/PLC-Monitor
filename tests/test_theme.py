"""The design system's guards, after NetControl's ThemeTests."""

import os
import re

from app.theme import qss, sheet
from app.theme.tokens import ACCENTS, DENSITIES, THEMES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def test_no_colour_literals_in_the_ui():
    """A literal colour is a spot that stops following the theme."""
    offenders = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "app", "ui")):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as handle:
                    for number, line in enumerate(handle, 1):
                        if HEX.search(line) and not line.strip().startswith("#"):
                            offenders.append(f"{path}:{number}")
    assert offenders == []


def test_every_combination_renders():
    for theme in THEMES:
        for accent in ACCENTS:
            for density in DENSITIES:
                tokens = sheet._with_ink(qss.build(theme, accent, density))
                tokens.update(arrow_png="a.png", check_png="c.png")
                qss.render(sheet.TEMPLATE, tokens)


def test_no_emoji_anywhere_in_the_app():
    pattern = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿]")
    offenders = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "app")):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(base, name), encoding="utf-8") as handle:
                    if pattern.search(handle.read()):
                        offenders.append(name)
    assert offenders == []


def test_tokens_match_file_manager_where_copied():
    """The greys are the family's; a theme tuned here alone drifts."""
    assert THEMES["dark"]["bg_0"] == "#101216"
    assert ACCENTS["blue"] == (74, 145, 255)
