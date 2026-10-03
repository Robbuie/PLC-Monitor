"""Builds the token set for one (theme, accent, density) combination.

This is the stand-in for what CSS gives Redline PDF for free. `app.css`
derives every tint of the accent with `rgba(var(--accent-rgb), a)` and
`color-mix()`, so its picker sets one property and the whole sheet follows.
QSS has neither, so the derivation happens here in Python and the results are
written into the generated stylesheet as literals.

The consequence is that the sheet is regenerated and re-applied whenever any
axis changes. That is the whole mechanism — there is no partial update path,
and adding one would reintroduce exactly the half-updated chrome this avoids.
"""

from __future__ import annotations

from app.theme.tokens import (
    ACCENT_ALPHA,
    ACCENTS,
    AGE_ALPHA,
    CORNERS,
    FONTS,
    KINDS,
    DEFAULTS,
    DENSITIES,
    EVENTS,
    SHAPE,
    THEMES,
)

RGB = tuple[int, int, int]


def _clamp(v: float) -> int:
    return max(0, min(255, round(v)))


def mix(colour: RGB, other: RGB, weight: float) -> str:
    """Mix `colour` with `other`, `weight` being the share of `colour`.

    The equivalent of `color-mix(in srgb, a W%, b)`. sRGB, not linear, because
    that is what the CSS uses and the point is to match it.
    """
    return rgb(tuple(  # type: ignore[arg-type]
        _clamp(c * weight + o * (1 - weight)) for c, o in zip(colour, other)
    ))


def rgb(colour: RGB) -> str:
    return "#%02x%02x%02x" % colour


def unhex(value: str) -> RGB:
    """`#1d2128` back to channels, for mixing against a theme's own greys."""
    text = value.lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def rgba(colour: RGB, alpha: float) -> str:
    """QSS understands `rgba(r, g, b, a)` with a float alpha."""
    r, g, b = colour
    return f"rgba({r}, {g}, {b}, {alpha:g})"


_BLACK: RGB = (0, 0, 0)
_WHITE: RGB = (255, 255, 255)
_LIFT: RGB = (255, 176, 102)


def build(
    theme: str | None = None,
    accent: str | None = None,
    density: str | None = None,
    backdrop: str = "solid",
    accent_rgb: RGB | None = None,
    font: str | None = None,
    corners: str | None = None,
) -> dict[str, str]:
    """Return every token for one combination, ready to substitute into QSS.

    `font` and `corners` (0.48) are a key of `FONTS` and of `CORNERS`. Like
    the backdrop they are not axes of the design system -- neither changes a
    colour -- and an unknown or missing one is the shipped look.

    `accent_rgb` (0.35) is a triple that stands in for the named accent when
    it comes from Windows or the wallpaper. Every tint is derived from it the
    same way, so a custom accent is still one colour and nothing else.

    `backdrop` is not a fourth axis of the design system -- it does not change
    a single grey. It says whether the window's own background is painted at
    all: "glass" leaves it transparent so Windows' Mica shows through behind
    the chrome, and the panes keep their raised grey on top of it.

    An unknown name falls back to the default rather than raising: a settings
    file carrying a theme from a later version should not stop the application
    starting.
    """
    theme_name = theme if theme in THEMES else DEFAULTS["theme"]
    accent_name = accent if accent in ACCENTS else DEFAULTS["accent"]
    density_name = density if density in DENSITIES else DEFAULTS["density"]

    out: dict[str, str] = {}
    out.update(THEMES[theme_name])
    out.update(SHAPE)
    out.update(CORNERS.get(corners or "round", CORNERS["round"]))
    if font in FONTS:
        out["font"] = FONTS[font][0]

    a = ACCENTS[accent_name]
    if accent_rgb is not None and len(accent_rgb) == 3:
        a = tuple(_clamp(c) for c in accent_rgb)  # type: ignore[assignment]
        accent_name = "custom"
    out["accent"] = rgb(a)
    out["accent_dim"] = mix(a, _BLACK, 0.70)
    out["accent_text"] = mix(a, _WHITE, 0.74)
    out["accent_lift"] = mix(a, _LIFT, 0.62)
    for name, alpha in ACCENT_ALPHA.items():
        out[f"accent_{name}"] = rgba(a, alpha)

    # The selected row needs an *opaque* tint as well as the translucent one.
    # Qt paints item selection from the palette, and a palette colour has no
    # alpha to give, so the wash has to be pre-mixed against the surface it
    # sits on. Two of them: the focused pane reads stronger than the other, so
    # that with two panes on screen it is obvious which selection is live.
    surface = unhex(out["bg_2"])
    out["accent_row"] = mix(a, surface, 0.26)
    out["accent_row_idle"] = mix(a, surface, 0.13)

    # The age chip. Derived from the theme's own `good` rather than from the
    # accent, for the reason AGE_ALPHA gives -- and derived rather than listed
    # per theme so a new theme gets a working chip from its greens alone.
    #
    # The text is `good` pulled most of the way to the theme's primary text
    # colour, which is what makes one rule work on a dark ground and a light
    # one: on dark it lightens, on paper it darkens, and it stays the same hue.
    good = unhex(out["good"])
    ink = unhex(out["txt_0"])
    for name, alpha in AGE_ALPHA.items():
        out[f"age_{name}"] = rgba(good, alpha)
    out["age_text"] = mix(good, ink, 0.70)
    # 0.34: the recency glow -- a wash under a row changed today and the halo
    # round its lit edge. Same green, same reason.
    out["age_row"] = rgba(good, 0.07)
    out["age_glow"] = rgba(good, 0.30)
    # 0.35: the drafting grid behind the Blueprint theme, in the theme's own
    # info blue. Two weights, a line every cell and a heavier one every fifth,
    # which is what makes it read as graph paper rather than as a texture.
    info = unhex(out["info"])
    # 0.39: stronger, now that it sits behind the rows rather than in the gaps
    # between panes. Still well under the text: the listing is read through it.
    out["grid_minor"] = rgba(info, 0.10)
    # 0.47: where a column ends. Before this nothing marked it, so finding the
    # spot to drag meant hunting along the header for a cursor change.
    # Derived from the theme's own greys -- muted text pulled most of the way
    # to the pane -- so the divider is visible on every theme and never as
    # loud as the text it separates. `rule_soft` is the same line through the
    # rows, faint enough to read the listing through; `band` is the shade on
    # every other column for the banded style.
    muted = unhex(out["txt_2"])
    out["rule"] = mix(surface, muted, 0.55)
    out["rule_soft"] = rgba(muted, 0.16)
    out["band"] = rgba(unhex(out["txt_1"]), 0.06)
    # 0.47: the size bar, now drawn *behind* the figure for the full height of
    # the row rather than as a line under the digits that ran into them. Low
    # enough that the number stays the strongest thing in the cell on every
    # theme; a counted folder's bar keeps the accent, as it did.
    out["size_fill"] = rgba(unhex(out["txt_1"]), 0.15)
    out["size_fill_dir"] = rgba(a, 0.20)
    # 0.49: striped rows (off by default), and the chip behind a date in a
    # day folder's name -- the neutral one, and today's in the accent.
    out["stripe"] = rgba(unhex(out["txt_1"]), 0.04)
    out["date_chip"] = rgba(unhex(out["txt_1"]), 0.14)
    out["date_today"] = rgba(a, 0.30)
    out["grid_major"] = rgba(info, 0.20)

    for key, value in DENSITIES[density_name].items():
        out[key] = f"{value:g}px" if key == "ui_font" else f"{int(value)}px"

    # The column headers. Derived from the density's own size rather than
    # listed as a fourth number per density, so a header can never end up
    # larger than the rows it is labelling.
    ui = float(DENSITIES[density_name]["ui_font"])
    out["head_font"] = f"{max(9.0, ui - 2.5):g}px"

    # File families. The bar is the hue itself; the badge is the hue at a low
    # alpha behind text pulled most of the way to the theme's ink, the rule the
    # age chip uses, so one triple works on every theme.
    for name, hue in KINDS.items():
        out[f"kind_{name}"] = rgb(hue)
        out[f"kind_{name}_fill"] = rgba(hue, 0.17)
        out[f"kind_{name}_text"] = mix(hue, ink, 0.62)

    # PLC Monitor: the event badges in the log, by the same rule as the file
    # kinds above -- hue for a bar, low-alpha fill behind text pulled to ink.
    for name, hue in EVENTS.items():
        out[f"ev_{name}"] = rgb(hue)
        out[f"ev_{name}_fill"] = rgba(hue, 0.16)
        out[f"ev_{name}_text"] = mix(hue, ink, 0.55)

    # What sits directly on the window: the gaps around the cards, the title
    # bar, the rail and the status line. One token so glass and solid differ
    # in exactly one place.
    glass = backdrop in ("glass", "frosted")
    out["backdrop"] = "transparent" if glass else out["bg_0"]
    out["backdrop_name"] = "glass" if glass else "solid"
    # 0.50: the pane card's own ground. Solid everywhere except the frosted
    # glass look, where the card lets the desktop through -- at an alpha that
    # keeps text on it well clear of the wallpaper behind. 0.50.1: the card
    # only, never the listing inside it: a see-through listing cannot scroll
    # by moving pixels, so every step of a scroll and every hover redrew the
    # whole window through the glass.
    surface_rgb = unhex(out["bg_2"])
    out["pane_bg"] = rgba(surface_rgb, 0.78) if backdrop == "frosted" else out["bg_2"]
    # Windows' own close-button red. A semantic colour that has to agree with
    # every other window on the screen, so it follows neither theme nor accent.
    out["close_hover"] = "#c42b1c"
    # 0.37: a share that is not answering. Semantic, like the close button's
    # red: it has to mean "down" whatever the accent is, so it follows neither.
    out["down"] = "#e5534b"
    # 0.38: the six label colours. Semantic -- a red label means what its
    # owner decided red means -- so they follow neither theme nor accent.
    for number, colour in enumerate(("#e5534b", "#e8913a", "#e6c547",
                                     "#46c98b", "#4a91ff", "#a07cff"), start=1):
        out[f"label_{number}"] = colour
    out["close_press"] = "#b22a1b"
    # 0.49: tags that sit on the pane in the warn amber and the down red, with
    # text pulled towards the theme's ink so they read on light and dark.
    for name in ("warn", "down"):
        hue = unhex(out[name])
        out[f"{name}_soft"] = rgba(hue, 0.20)
        out[f"{name}_text"] = mix(hue, ink, 0.55)

    out["theme_name"] = theme_name
    out["accent_name"] = accent_name
    out["density_name"] = density_name
    return out


def render(template: str, tokens: dict[str, str]) -> str:
    """Substitute `{token}` placeholders in a QSS template.

    Raises on an unknown placeholder rather than leaving it in the sheet, where
    it would silently produce an unstyled widget.
    """
    try:
        return template.format(**tokens)
    except KeyError as exc:  # pragma: no cover - a template bug, not a state
        raise KeyError(f"unknown token in QSS template: {exc.args[0]}") from exc
