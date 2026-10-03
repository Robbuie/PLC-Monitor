"""Design tokens. Copied verbatim from File Manager's `app/theme/tokens.py`,
which ports them from Redline PDF's `app.css`.

They are copied rather than re-derived because the family look depends on the
applications using the same numbers. If a grey changes in File Manager, it
changes here; diff the two files rather than tuning one in isolation.

PLC Monitor adds two sets at the bottom, marked as its own -- `EVENTS` and
`SERIES` -- because it paints an event log and a chart rather than a file
listing. Neither touches a theme's greys. They are worth back-porting if
another member of the family ever draws a chart.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Themes. Each restates the greys only. The accent and the density come from
# their own axes and are never named in here.
# --------------------------------------------------------------------------

THEMES: dict[str, dict[str, str]] = {
    # The default: neutral greys, content forward.
    "dark": {
        "bg_0": "#101216",       # app backdrop
        "bg_1": "#171a20",       # chrome: toolbar, tab strip, status bar
        "bg_2": "#1d2128",       # raised chrome: pane background, dialogs
        "bg_3": "#252a33",       # hover
        "bg_4": "#2f3540",       # active, strong borders
        "line": "#2a2f38",
        "line_soft": "#22262d",
        "txt_0": "#e7ebf2",
        "txt_1": "#aab3c0",
        "txt_2": "#79828f",
        "info": "#4aa8ff",
        "good": "#46c98b",
        "warn": "#f2c14e",
        "sel": "#4aa8ff",
    },
    "light": {
        "bg_0": "#eceef2",
        "bg_1": "#f7f8fa",
        "bg_2": "#ffffff",
        "bg_3": "#e8ebf0",
        "bg_4": "#d7dbe2",
        "line": "#d5d9e0",
        "line_soft": "#e3e6eb",
        "txt_0": "#14181f",
        "txt_1": "#4a525e",
        "txt_2": "#7b838f",
        "info": "#4aa8ff",
        "good": "#46c98b",
        "warn": "#f2c14e",
        "sel": "#4aa8ff",
    },
    # Warm light: chrome knocked off pure white, for a long session on a
    # bright monitor.
    "paper": {
        "bg_0": "#ece7dd",
        "bg_1": "#f7f3ea",
        "bg_2": "#fffdf8",
        "bg_3": "#eae4d7",
        "bg_4": "#d9d1c0",
        "line": "#d8d0c0",
        "line_soft": "#e6e0d3",
        "txt_0": "#211d17",
        "txt_1": "#564f43",
        "txt_2": "#857c6c",
        "info": "#4aa8ff",
        "good": "#46c98b",
        "warn": "#f2c14e",
        "sel": "#4aa8ff",
    },
    # Cool, deep chrome.
    "blueprint": {
        "bg_0": "#0a1220",
        "bg_1": "#0f1b2d",
        "bg_2": "#142339",
        "bg_3": "#1b2f4a",
        "bg_4": "#26405f",
        "line": "#21374f",
        "line_soft": "#182b40",
        "txt_0": "#e2ecf8",
        "txt_1": "#9fb4cc",
        "txt_2": "#6d8299",
        "info": "#6cc0ff",
        "good": "#46c98b",
        "warn": "#f2c14e",
        "sel": "#6cc0ff",
    },
    # Not a style, an accessibility target. Every border is a visible line
    # rather than a hint, and the muted grey is lifted until it passes as body
    # text, because in this theme it is being read rather than skimmed.
    "contrast": {
        "bg_0": "#000000",
        "bg_1": "#0a0a0c",
        "bg_2": "#141418",
        "bg_3": "#23232a",
        "bg_4": "#3a3a45",
        "line": "#55555f",
        "line_soft": "#3d3d46",
        "txt_0": "#ffffff",
        "txt_1": "#e4e4ea",
        "txt_2": "#b6b6c0",
        "info": "#7cc4ff",
        "good": "#5ee0a0",
        "warn": "#ffd75e",
        "sel": "#7cc4ff",
    },
    # 0.48: six more, after the mockups of 30 September. These are this
    # application's own rather than ports of Redline PDF's, so they are the
    # place the family look is allowed to grow first; they restate the greys
    # and nothing else, like the five above, so every accent and density works
    # on each of them.
    #
    # True black for an OLED panel or a dim room. The steps between the greys
    # are smaller than Dark's because on black a small step already reads.
    "graphite": {
        "bg_0": "#000000",
        "bg_1": "#070708",
        "bg_2": "#0e0e11",
        "bg_3": "#18181c",
        "bg_4": "#24242a",
        "line": "#1f1f24",
        "line_soft": "#151518",
        "txt_0": "#ededf0",
        "txt_1": "#a6a6ae",
        "txt_2": "#77777f",
        "info": "#5ab0ff",
        "good": "#46c98b",
        "warn": "#f2c14e",
        "sel": "#5ab0ff",
    },
    # After the ISA-101 high-performance HMI screens: calm mid greys, dark
    # text, and the colour left to the things that need somebody -- a share
    # that stopped answering, a failed copy. The greens and ambers are darker
    # than the other themes' because they sit on a light grey.
    "control": {
        "bg_0": "#b7babe",
        "bg_1": "#c6c9cc",
        "bg_2": "#dcdee1",
        "bg_3": "#cdd0d4",
        "bg_4": "#b4b8be",
        "line": "#a9aeb4",
        "line_soft": "#c3c7cc",
        "txt_0": "#14181c",
        "txt_1": "#343a41",
        "txt_2": "#535a63",
        "info": "#2f5fa8",
        "good": "#2b8a55",
        "warn": "#a97a08",
        "sel": "#2f5fa8",
    },
    # Green on black. Pairs with the monospace font in Options and the green
    # accent, though neither is forced: a theme sets greys, not a typeface.
    "phosphor": {
        "bg_0": "#020604",
        "bg_1": "#051009",
        "bg_2": "#08150d",
        "bg_3": "#0e2216",
        "bg_4": "#163222",
        "line": "#13301f",
        "line_soft": "#0d2116",
        "txt_0": "#b4ffc8",
        "txt_1": "#70dc97",
        "txt_2": "#4c9f6b",
        "info": "#39ff88",
        "good": "#39ff88",
        "warn": "#e6d34a",
        "sel": "#39ff88",
    },
    # Warm dark browns, for the end of a long day. Amber is the accent it was
    # drawn with; blue on it works too.
    "dusk": {
        "bg_0": "#14100e",
        "bg_1": "#1b1613",
        "bg_2": "#221c18",
        "bg_3": "#2c2420",
        "bg_4": "#3a302a",
        "line": "#322923",
        "line_soft": "#28211c",
        "txt_0": "#f2e7db",
        "txt_1": "#c3b4a4",
        "txt_2": "#928372",
        "info": "#6fb2ff",
        "good": "#5cc98b",
        "warn": "#f2c14e",
        "sel": "#6fb2ff",
    },
    # A cool light theme with steel-blue greys: brighter than Paper, softer
    # than Light.
    "frost": {
        "bg_0": "#dee5ed",
        "bg_1": "#ecf1f6",
        "bg_2": "#f8fbfd",
        "bg_3": "#e5ecf3",
        "bg_4": "#d1dbe6",
        "line": "#cdd7e2",
        "line_soft": "#e1e8ef",
        "txt_0": "#131f2b",
        "txt_1": "#405066",
        "txt_2": "#67768c",
        "info": "#3b7dd8",
        "good": "#2c9c66",
        "warn": "#b9800e",
        "sel": "#3b7dd8",
    },
    # Black on white like a printed drawing, with the borders made real lines
    # rather than hints. Pairs with square corners and ruled columns.
    "ink": {
        "bg_0": "#ebebe9",
        "bg_1": "#f7f7f5",
        "bg_2": "#ffffff",
        "bg_3": "#efefec",
        "bg_4": "#d4d4d0",
        "line": "#8c8c88",
        "line_soft": "#c6c6c2",
        "txt_0": "#0a0a0a",
        "txt_1": "#2d2d2d",
        "txt_2": "#5a5a5a",
        "info": "#0a58ca",
        "good": "#1d8a4c",
        "warn": "#a8700f",
        "sel": "#0a58ca",
    },
}

THEME_LABELS: dict[str, str] = {
    "dark": "Dark",
    "light": "Light",
    "paper": "Warm paper",
    "blueprint": "Blueprint",
    "contrast": "High contrast",
    "graphite": "Graphite",
    "control": "Control room",
    "phosphor": "Phosphor",
    "dusk": "Dusk",
    "frost": "Frost",
    "ink": "Ink",
}

#: 0.48: which themes are light, for "follow Windows" -- which picks one of
#: each -- and anything else that needs to know without measuring the greys.
LIGHT_THEMES: frozenset[str] = frozenset({"light", "paper", "control", "frost", "ink"})

#: 0.48: the typeface choice. A fourth thing beside the three axes rather than
#: part of a theme, for the axes' reason: a theme that also set a font would
#: be a theme half of whose combinations nobody asked for.
FONTS: dict[str, tuple[str, str]] = {
    "ui": ('"Segoe UI", Inter, system-ui, sans-serif', "Segoe UI"),
    "variable": ('"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif',
                 "Segoe UI Variable"),
    "mono": ('"Cascadia Mono", Consolas, monospace', "Cascadia Mono throughout"),
}

#: 0.48: corner shapes. Square keeps a hint of rounding, because a radius of
#: zero on a selection band makes neighbouring selected rows read as one slab.
CORNERS: dict[str, dict[str, str]] = {
    "round": {"radius": "7px", "radius_sm": "5px", "radius_lg": "12px"},
    "square": {"radius": "2px", "radius_sm": "2px", "radius_lg": "3px"},
}

# --------------------------------------------------------------------------
# Accents. Channel triples, not hex, because every tint is derived from them
# at render time. A literal accent colour written anywhere re-pins that spot to
# one choice and it stops tracking the picker.
# --------------------------------------------------------------------------

ACCENTS: dict[str, tuple[int, int, int]] = {
    "redline": (255, 91, 74),
    "amber": (242, 165, 60),
    "green": (70, 201, 139),
    "cyan": (54, 191, 210),
    "blue": (74, 145, 255),
    "violet": (154, 122, 255),
}

ACCENT_LABELS: dict[str, str] = {
    "redline": "Redline red",
    "amber": "Amber",
    "green": "Field green",
    "cyan": "Cyan",
    "blue": "Drafting blue",
    "violet": "Violet",
}

#: Alpha for each derived tint of the accent.
ACCENT_ALPHA: dict[str, float] = {
    "soft": 0.14,   # fill behind a selected or armed control
    "wash": 0.17,   # fill behind an active row
    "line": 0.45,   # border on an active element
    "glow": 0.80,   # focus ring
}

#: Alpha for each step of the age chip, strongest for the freshest.
#:
#: A semantic set, not an accent tint: how recently a file changed has nothing
#: to do with which colour the picker is on, and tying it to the accent would
#: make "changed today" mean something different in every theme. It derives
#: from the theme's own `good` instead, so it stays legible in all five without
#: a literal being written anywhere.
#: The keys are the step names `app.core.listing.age_step` returns. They are
#: the join between a rule about time and a rule about colour, which live in
#: different layers on purpose -- `tests/test_listing.py` pins that every name
#: one produces has a tint here, because a mismatch draws no chip and raises
#: nothing.
AGE_ALPHA: dict[str, float] = {
    "fresh": 0.22,    # under a day
    "recent": 0.13,   # under a week
    "month": 0.085,   # under a month
}

# --------------------------------------------------------------------------
# Density. The same metrics at three sizes. Row height matters more here than
# anywhere in the other two applications: it is the thing being looked at
# 50,000 times.
# --------------------------------------------------------------------------

DENSITIES: dict[str, dict[str, int | float]] = {
    "compact": {
        "ui_font": 12,
        "toolbar_h": 30,
        "row_h": 20,
        "tab_h": 26,
        "status_h": 22,
        "sidebar_w": 238,
        "button_h": 26,
    },
    "normal": {
        "ui_font": 13,
        "toolbar_h": 34,
        "row_h": 22,
        "tab_h": 30,
        "status_h": 26,
        "sidebar_w": 268,
        "button_h": 30,
    },
    "large": {
        "ui_font": 14.5,
        "toolbar_h": 38,
        "row_h": 26,
        "tab_h": 34,
        "status_h": 30,
        "sidebar_w": 304,
        "button_h": 34,
    },
}

DENSITY_LABELS: dict[str, str] = {
    "compact": "Compact",
    "normal": "Normal",
    "large": "Large",
}

# --------------------------------------------------------------------------
# Shape and type. Not axed; the same in every combination.
# --------------------------------------------------------------------------

#: 0.27: file families, for the badge on a row and the folder's bar. Channel
#: triples like the accents, because the badge's fill and text are derived from
#: them per theme in `qss.build` -- the same hue reads on dark and on paper.
#: These are semantic colours in CLAUDE.md's sense: they say what a file is,
#: so they follow neither the accent nor the theme's hue.
KINDS: dict[str, tuple[int, int, int]] = {
    "logix":   (45, 196, 176),
    "hmi":     (154, 122, 255),
    "cad":     (240, 160, 40),
    "pdf":     (238, 96, 96),
    "sheet":   (70, 190, 110),
    "doc":     (80, 145, 245),
    "image":   (220, 110, 180),
    "archive": (190, 150, 100),
    "code":    (100, 180, 220),
    "program": (150, 160, 175),
    "text":    (140, 148, 160),
    "other":   (125, 133, 145),
    "folder":  (125, 133, 145),
}

SHAPE: dict[str, str] = {
    "radius": "7px",
    "radius_sm": "5px",
    # The pane and the listing. Bigger than a control's on purpose: a
    # container and the buttons inside it drawn at the same corner read as
    # one flat object, which is most of what "blocky" means.
    "radius_lg": "12px",
    "font": '"Segoe UI", Inter, system-ui, sans-serif',
    "mono": '"Cascadia Mono", Consolas, monospace',
}

#: Redline PDF defaults to its own red. Blue is this family's non-markup
#: accent and is what DWG Viewer defaults to.
DEFAULTS: dict[str, str] = {
    "theme": "dark",
    "accent": "blue",
    "density": "normal",
}


#: What a glass window paints under everything else: black at one step of
#: alpha out of 255. Invisible over Mica, and the whole reason it exists is
#: that it is not zero. A translucent Qt window is a layered window, and
#: Windows hands a click on a pixel with no alpha at all to whatever window is
#: behind it -- so every gap between the rail's rows, the empty title bar and
#: the space around the cards was a hole straight through to another program.
GLASS_FLOOR: tuple[int, int, int, int] = (0, 0, 0, 1)


# --------------------------------------------------------------------------
# PLC Monitor's own. Not in File Manager.
# --------------------------------------------------------------------------

#: The event types in the log. Semantic, like KINDS: a PLC change is the same
#: green whichever accent is picked, so the log can be read across a room on
#: any theme. Fill and text are derived per theme in `qss.build` the same way
#: the file badges are.
EVENTS: dict[str, tuple[int, int, int]] = {
    "plc":     (70, 201, 139),    # PLC_CHANGED
    "recv":    (54, 191, 210),    # SOCK_RECV
    "sent":    (242, 165, 60),    # SOCK_SENT
    "connect": (110, 150, 255),   # PLC_CONNECTED, SOCK_CONNECT
    "error":   (229, 83, 75),     # PLC_ERROR, SOCK_ERROR, PLC_DISCONNECTED
    "info":    (125, 133, 145),   # INFO, SOCK_DISCONNECT
}

#: Trend lines, in the order they are handed out -- never cycled; a ninth tag
#: asks the person to pick fewer. Two columns of the same eight hues, one
#: stepped for light grounds and one for dark, run through the colour-vision
#: validator rather than picked by eye. Which column is used follows
#: LIGHT_THEMES. A line's colour follows its tag, not its position: removing
#: one tag from the chart does not repaint the others.
SERIES_LIGHT: tuple[str, ...] = (
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
)
SERIES_DARK: tuple[str, ...] = (
    "#3987e5", "#d95926", "#199e70", "#c98500",
    "#d55181", "#008300", "#9085e9", "#e66767",
)
