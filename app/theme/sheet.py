"""PLC Monitor's QSS template, and the one function that applies it.

Same mechanism as File Manager's `app/theme/sheet.py`: the template is the
whole stylesheet, written once with `{token}` placeholders and rendered by
substitution, so there is no colour literal in it and must never be (the test
in tests/test_theme.py reads every file under app/ui for one). Braces are
doubled because the render is a `str.format`.

Where File Manager's sheet is about one listing in two panes, this one is about
forms and tables on cards: a card is the raised grey on the window's backdrop,
a field is a step darker than the card it sits on, and the accent is spent on
the one control per card that starts something. Status is carried by the
semantic colours -- good, warn, down -- never by the accent, because "running"
must look the same whichever accent somebody picked.

Two things QSS cannot draw are done here in Python instead: the combo box's
arrow and the check box's tick, which QSS will only take as image files. They
are drawn from the glyph set into a cache folder, in the theme's own colours,
each time the sheet is rendered.
"""

from __future__ import annotations

import os

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app.theme import qss
from app.theme.tokens import DEFAULTS

TEMPLATE = """
QWidget {{
    background: transparent;
    color: {txt_0};
    font-family: {font};
    font-size: {ui_font};
    selection-background-color: {accent_soft};
    selection-color: {accent_ink};
}}
QMainWindow, QWidget[role="window"] {{ background: {bg_0}; }}
QDialog {{ background: {bg_0}; }}
QToolTip {{
    background: {bg_2};
    color: {txt_0};
    border: 1px solid {line};
    border-radius: {radius_sm};
    padding: 5px 8px;
}}

/* ------------------------------------------------------------ title bar */

QWidget[role="titlebar"] {{ background: {bg_0}; }}
QToolButton[role="mark"] {{
    background: {accent};
    border: none;
    border-radius: {radius};
    min-width: 24px; max-width: 24px;
    min-height: 24px; max-height: 24px;
    padding: 0px;
}}
QLabel[role="apptitle"] {{ color: {txt_1}; font-weight: 600; padding: 0px 10px 0px 8px; }}
QMenuBar {{ background: transparent; border: none; padding: 0px; }}
QMenuBar::item {{
    padding: 5px 10px;
    background: transparent;
    color: {txt_1};
    border-radius: {radius};
}}
QMenuBar::item:selected {{ background: {bg_3}; color: {txt_0}; }}
QMenuBar::item:pressed {{ background: {accent_soft}; color: {accent_ink}; }}
QMenu {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius};
    padding: 5px;
}}
QMenu::item {{ padding: 6px 26px 6px 22px; border-radius: {radius_sm}; }}
QMenu::item:selected {{ background: {accent_soft}; color: {accent_ink}; }}
QMenu::item:disabled {{ color: {txt_2}; }}
QMenu::separator {{ height: 1px; background: {line_soft}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}
QToolButton[role="caption"] {{
    background: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
}}
QToolButton[role="caption"]:hover {{ background: {bg_3}; }}
QToolButton[role="caption"]:pressed {{ background: {bg_4}; }}
QToolButton[role="caption"][kind="close"]:hover {{ background: {close_hover}; }}
QToolButton[role="caption"][kind="close"]:pressed {{ background: {close_press}; }}

/* The open profile, beside the menus. Reads as a label until hovered; the
   caret is a glyph, so the style's own menu arrow is hidden. */
QToolButton[role="profile"] {{
    background: transparent;
    border: 1px solid {line_soft};
    border-radius: {radius};
    color: {txt_0};
    font-weight: 600;
    padding: 2px 8px;
    margin-left: 10px;
}}
QToolButton[role="profile"]:hover {{ background: {bg_3}; }}
QToolButton[role="profile"]:pressed {{ background: {bg_4}; }}
QToolButton[role="profile"]::menu-indicator {{ image: none; width: 0px; }}

/* The chips in the title bar: what is running, readable from across the room.
   Semantic colours only. */
QLabel[role="chip"] {{
    border-radius: 10px;
    padding: 1px 10px;
    min-height: 18px;
    max-height: 20px;
    font-size: {head_font};
    font-weight: 600;
    background: {bg_2};
    color: {txt_2};
    border: 1px solid {line_soft};
}}
QLabel[role="chip"][tone="good"] {{ background: {age_fresh}; color: {age_text}; border: 1px solid transparent; }}
QLabel[role="chip"][tone="warn"] {{ background: {warn_soft}; color: {warn_text}; border: 1px solid transparent; }}
QLabel[role="chip"][tone="down"] {{ background: {down_soft}; color: {down_text}; border: 1px solid transparent; }}
QLabel[role="chip"][tone="info"] {{ background: {accent_soft}; color: {accent_ink}; border: 1px solid transparent; }}

/* ----------------------------------------------------------------- rail */

QFrame[role="rail"] {{ background: {bg_0}; border: none; }}
QLabel[role="railhead"] {{
    color: {txt_2};
    font-size: {head_font};
    font-weight: 600;
    padding: 12px 10px 4px 12px;
}}
QPushButton[role="railrow"] {{
    background: transparent;
    border: none;
    border-radius: {radius};
    color: {txt_1};
    padding: 6px 10px;
    text-align: left;
    min-height: 22px;
}}
QPushButton[role="railrow"]:hover {{ background: {bg_3}; color: {txt_0}; }}
QPushButton[role="railrow"]:focus {{ border: none; }}
QPushButton[role="railrow"]:pressed {{ background: {bg_4}; }}
QPushButton[role="railrow"][state="current"] {{
    background: {accent_soft};
    color: {accent_ink};
    font-weight: 600;
}}
QFrame[role="railstatus"] {{
    background: {bg_1};
    border: 1px solid {line_soft};
    border-radius: {radius_lg};
}}
QLabel[role="railstat"] {{ color: {txt_1}; font-size: {head_font}; padding: 1px 0px; }}
QLabel[role="railstatvalue"] {{ color: {txt_0}; font-size: {head_font}; font-family: {mono}; }}

/* ----------------------------------------------------------------- cards */

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QWidget[role="page"] {{ background: {bg_0}; }}
QFrame[role="card"] {{
    background: {bg_2};
    border: 1px solid {line_soft};
    border-radius: {radius_lg};
}}
QFrame[role="card"] QLabel {{ background: transparent; }}
QLabel[role="cardtitle"] {{
    color: {txt_2};
    font-size: {head_font};
    font-weight: 700;
}}
QLabel[role="pagetitle"] {{ font-size: 18px; font-weight: 600; color: {txt_0}; }}
QLabel[role="pagesub"] {{ color: {txt_2}; }}
QLabel[role="hint"] {{ color: {txt_2}; }}
QLabel[role="mono"] {{ font-family: {mono}; color: {txt_1}; }}
QLabel[role="fieldlabel"] {{ color: {txt_1}; }}
QLabel[role="big"] {{ font-size: 22px; font-weight: 600; font-family: {mono}; color: {txt_0}; }}
QLabel[role="bigcaption"] {{ color: {txt_2}; font-size: {head_font}; font-weight: 600; }}
QLabel[role="link"] {{ color: {accent_ink}; }}
QLabel[tone="good"] {{ color: {good}; }}
QLabel[tone="warn"] {{ color: {warn}; }}
QLabel[tone="down"] {{ color: {down}; }}

QFrame[role="banner"] {{
    background: {warn_soft};
    border: 1px solid transparent;
    border-radius: {radius};
}}
QFrame[role="banner"][tone="down"] {{ background: {down_soft}; }}
QFrame[role="banner"][tone="info"] {{ background: {accent_soft}; }}
QFrame[role="banner"] QLabel {{ background: transparent; color: {txt_0}; }}

QFrame[role="rule"] {{ background: {line_soft}; border: none; max-height: 1px; min-height: 1px; }}

/* --------------------------------------------------------------- buttons */

QPushButton, QToolButton {{
    background: {bg_3};
    border: 1px solid {line};
    border-radius: {radius};
    color: {txt_0};
    padding: 3px 12px;
    min-height: {button_h};
}}
QPushButton:hover, QToolButton:hover {{ background: {bg_4}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {bg_4}; border: 1px solid {accent_line}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {txt_2}; background: {bg_2}; border: 1px solid {line_soft}; }}
QPushButton:focus, QToolButton:focus {{ border: 1px solid {accent_line}; }}
QPushButton::menu-indicator {{ width: 0px; }}

/* The one button per card that starts something. */
QPushButton[role="primary"] {{
    background: {accent};
    border: 1px solid {accent};
    color: {bg_0};
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{ background: {accent_lift}; border: 1px solid {accent_lift}; }}
QPushButton[role="primary"]:disabled {{ background: {bg_3}; color: {txt_2}; border: 1px solid {line_soft}; }}
/* Stop, Remove: the down red as an outline, never a fill -- a red fill is a
   button that looks like it is already in an error state. */
QPushButton[role="danger"] {{
    background: transparent;
    border: 1px solid {down};
    color: {down_text};
}}
QPushButton[role="danger"]:hover {{ background: {down_soft}; }}
QPushButton[role="danger"]:disabled {{ border: 1px solid {line_soft}; color: {txt_2}; }}
QPushButton[role="quiet"], QToolButton[role="quiet"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {txt_1};
}}
QPushButton[role="quiet"]:hover, QToolButton[role="quiet"]:hover {{ background: {bg_3}; color: {txt_0}; }}
QToolButton[role="nav"] {{
    background: transparent;
    border: none;
    border-radius: {radius};
    padding: 0px;
    min-width: {button_h};
    max-width: {button_h};
}}
QToolButton[role="nav"]:hover {{ background: {bg_3}; }}
QToolButton[role="nav"]:pressed {{ background: {bg_4}; }}
QToolButton[role="nav"]:checked {{ background: {accent_soft}; }}

/* A row of mutually exclusive choices, drawn as one control. */
QWidget[role="segments"] {{
    background: {bg_1};
    border: 1px solid {line_soft};
    border-radius: {radius};
}}
QPushButton[role="segment"] {{
    background: transparent;
    border: none;
    border-radius: {radius_sm};
    color: {txt_1};
    padding: 3px 12px;
    margin: 2px;
    min-height: 0px;
}}
QPushButton[role="segment"]:focus {{ border: none; }}
QPushButton[role="segment"]:hover {{ background: {bg_3}; color: {txt_0}; }}
QPushButton[role="segment"]:checked {{
    background: {accent_soft};
    color: {accent_ink};
    font-weight: 600;
}}

/* ---------------------------------------------------------------- fields */

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit {{
    background: {bg_1};
    border: 1px solid {line_soft};
    border-radius: {radius};
    color: {txt_0};
    padding: 3px 8px;
    min-height: {button_h};
    selection-background-color: {accent};
    selection-color: {bg_0};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QDateTimeEdit:hover {{ border: 1px solid {line}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus {{
    border: 1px solid {accent_line};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {txt_2}; background: {bg_2}; }}
QLineEdit[role="mono"] {{ font-family: {mono}; }}
QLineEdit[role="search"] {{ border-left: 3px solid {accent}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button, QDateTimeEdit::up-button, QDateTimeEdit::down-button {{
    width: 0px; border: none;
}}
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
}}
QComboBox::down-arrow {{ image: url("{arrow_png}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius_sm};
    color: {txt_0};
    outline: none;
    padding: 4px;
    selection-background-color: {accent_soft};
    selection-color: {accent_ink};
}}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QListView::indicator, QTableView::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {line};
    border-radius: 4px;
    background: {bg_1};
}}
QCheckBox::indicator:hover {{ border: 1px solid {accent_line}; }}
QCheckBox::indicator:checked, QListView::indicator:checked, QTableView::indicator:checked {{
    background: {accent};
    border: 1px solid {accent};
    image: url("{check_png}");
}}
QCheckBox:disabled {{ color: {txt_2}; }}

QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {bg_1};
    border: 1px solid {line_soft};
    border-radius: {radius};
    color: {txt_1};
    padding: 6px 8px;
}}
QPlainTextEdit[role="console"] {{ font-family: {mono}; color: {txt_1}; }}
QTextBrowser[role="guide"] {{ background: {bg_2}; border: none; padding: 10px 18px; color: {txt_0}; }}

QProgressBar {{
    background: {bg_1};
    border: none;
    border-radius: 3px;
    max-height: 6px;
    min-height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

/* ---------------------------------------------------------------- tables */

QTableView, QTreeView, QListView, QListWidget {{
    background: {bg_2};
    alternate-background-color: {stripe};
    color: {txt_0};
    border: none;
    gridline-color: transparent;
    outline: none;
    selection-background-color: {accent_row};
    selection-color: {txt_0};
}}
QTableView::item, QTreeView::item {{ padding: 0px 8px; border: none; }}
QListView::item, QListWidget::item {{ padding: 3px 6px; border-radius: {radius_sm}; }}
QListView::item:hover, QListWidget::item:hover {{ background: {bg_3}; }}
QListView::item:selected, QListWidget::item:selected {{ background: {accent_row}; color: {txt_0}; }}
QTableView::item:hover {{ background: transparent; }}
/* Trees (the Profiles list): a selected row is highlighted across, branch
   included, and rows get the list's breathing room. */
QTreeView::item {{ padding: 3px 6px; }}
QTreeView::item:selected, QTreeView::branch:selected {{ background: {accent_row}; color: {txt_0}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: transparent;
    color: {txt_2};
    border: none;
    border-bottom: 1px solid {line_soft};
    padding: 6px 8px;
    font-size: {head_font};
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {txt_0}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {bg_4};
    border-radius: 3px;
    min-height: 28px;
    min-width: 28px;
}}
QScrollBar::handle:hover {{ background: {txt_2}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {accent_line}; }}

/* ------------------------------------------------------------ status bar */

QFrame[role="statusbar"] {{ background: {bg_0}; border: none; }}
QLabel[role="status"] {{ color: {txt_2}; padding: 0px 4px; }}
QLabel[role="statusright"] {{ color: {txt_2}; font-family: {mono}; font-size: {head_font}; }}
"""

_current: dict[str, str] = {}


def _cache_dir() -> str:
    from app import paths
    folder = os.path.join(paths.user_dir(), "cache", "theme")
    os.makedirs(folder, exist_ok=True)
    return folder


def _glyph_png(name: str, colour: str, size: int = 16) -> str:
    """Draw one glyph to a PNG and return a path QSS will take."""
    from app.ui import glyphs
    path = os.path.join(_cache_dir(), f"{name}-{colour.lstrip('#')}-{size}.png")
    if not os.path.exists(path):
        pix = glyphs.icon(name, colour=colour, muted=colour, size=size, ratio=2.0).pixmap(size * 2, size * 2)
        pix.save(path, "PNG")
    return path.replace("\\", "/")


def tokens() -> dict[str, str]:
    """The tokens in use, plus `accent_ink` (see `render`)."""
    return _current or _with_ink(qss.build(DEFAULTS["theme"], DEFAULTS["accent"], DEFAULTS["density"]))


def _with_ink(built: dict[str, str]) -> dict[str, str]:
    from app.theme.tokens import LIGHT_THEMES
    a = qss.unhex(built["accent"])
    light = built.get("theme_name") in LIGHT_THEMES
    built["accent_ink"] = qss.mix(a, (0, 0, 0), 0.62) if light else built["accent_text"]
    return built


def render(tokens_: dict[str, str]) -> str:
    # Text on an accent tint. File Manager's `accent_text` is the accent pulled
    # towards white, which reads on a dark ground and vanishes on a light one;
    # on the light themes it is pulled towards black instead.
    extra = _with_ink(dict(tokens_))
    extra["arrow_png"] = _glyph_png("sort_down", tokens_["txt_1"], 14)
    extra["check_png"] = _glyph_png("check", tokens_["bg_0"], 14)
    return qss.render(TEMPLATE, extra)


def apply(app: QApplication, theme: str, accent: str, density: str,
          font: str | None = None, corners: str | None = None) -> dict[str, str]:
    """Render and apply the sheet for one combination. Returns the tokens."""
    global _current
    built = _with_ink(qss.build(theme, accent, density, font=font, corners=corners))
    _current = built
    palette = app.palette()
    for group in (QPalette.Active, QPalette.Inactive):
        palette.setColor(group, QPalette.Highlight, QColor(built["accent_row"]))
        palette.setColor(group, QPalette.HighlightedText, QColor(built["txt_0"]))
    palette.setColor(QPalette.Window, QColor(built["bg_0"]))
    palette.setColor(QPalette.Base, QColor(built["bg_2"]))
    palette.setColor(QPalette.AlternateBase, QColor(built["bg_1"]))
    palette.setColor(QPalette.Text, QColor(built["txt_0"]))
    palette.setColor(QPalette.WindowText, QColor(built["txt_0"]))
    palette.setColor(QPalette.ButtonText, QColor(built["txt_0"]))
    palette.setColor(QPalette.PlaceholderText, QColor(built["txt_2"]))
    palette.setColor(QPalette.ToolTipBase, QColor(built["bg_2"]))
    palette.setColor(QPalette.ToolTipText, QColor(built["txt_0"]))
    app.setPalette(palette)
    app.setStyleSheet(render(built))
    return built
