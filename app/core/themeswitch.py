"""Which theme is on screen right now, when it is allowed to change by itself.

0.48. Two ways for the theme to follow something other than the picker:
Windows' own light or dark setting, and the time of day. Both reduce to the
same question -- given the settings and the moment, which theme -- and that is
a pure function here, so the part that decides is tested without a clock or a
registry. The window asks it on a timer and re-renders only when the answer
changes, which is what makes a check every minute cost nothing.

The registry read is the one call that is not pure. It is a value in the
current user's hive, local and instant, the same kind of read `winframe` makes
for the transparency setting -- not a filesystem call, and nothing on the
network. Anywhere but Windows it answers None, and None means "keep the theme
the picker says".
"""

from __future__ import annotations

import sys

from app.theme.tokens import LIGHT_THEMES, THEMES

FOLLOW_OFF = "off"
FOLLOW_WINDOWS = "windows"
FOLLOW_SCHEDULE = "schedule"


def pick(follow: str, fixed: str, light: str, dark: str, *,
         windows_light: bool | None, hour: int,
         day_from: int = 7, night_from: int = 19) -> str:
    """The theme to draw with.

    `fixed` is the picker's theme and the answer whenever following is off or
    cannot be worked out. A light or dark choice that names no theme (a
    settings file from a later version) falls back to `fixed` rather than to a
    theme nobody chose.
    """
    if fixed not in THEMES:
        fixed = "dark"
    light = light if light in THEMES else fixed
    dark = dark if dark in THEMES else fixed
    if follow == FOLLOW_WINDOWS:
        if windows_light is None:
            return fixed
        return light if windows_light else dark
    if follow == FOLLOW_SCHEDULE:
        if day_from == night_from:
            return fixed
        if day_from < night_from:
            daytime = day_from <= hour < night_from
        else:
            # Day starting after it ends: somebody on nights, where "day"
            # runs over midnight.
            daytime = hour >= day_from or hour < night_from
        return light if daytime else dark
    return fixed


def is_light(theme: str) -> bool:
    return theme in LIGHT_THEMES


def windows_light() -> bool | None:
    """Whether Windows is set to light mode for apps, or None if unknown."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return bool(value)
    except OSError:
        return None


def current(config, *, hour: int | None = None,
            windows: bool | None | str = "ask") -> str:
    """`pick` from a settings object, asking Windows and the clock itself
    unless told. The one place the setting names are read."""
    import time

    if hour is None:
        hour = time.localtime().tm_hour
    follow = str(config.get("theme.follow"))
    if windows == "ask":
        windows = windows_light() if follow == FOLLOW_WINDOWS else None
    return pick(follow, str(config.get("theme")), str(config.get("theme.light")),
                str(config.get("theme.dark")), windows_light=windows, hour=hour,
                day_from=int(config.get("theme.day_from")),
                night_from=int(config.get("theme.night_from")))
