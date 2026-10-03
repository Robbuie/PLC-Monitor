"""One person's preferences: how the window looks and where it was left.

Separate from the monitor config on purpose. That file is site configuration
the service also reads, it lives in `%PROGRAMDATA%`, and nothing about a theme
belongs in it. This one is `%LOCALAPPDATA%\\PLC Monitor\\ui.json`, the same
split NetControl makes between its settings and its appearance file.

Keys are dotted strings so `themeswitch.current` -- copied from File Manager
-- can read them with the names it already uses.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from app import paths

DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "accent": "blue",
    "density": "normal",
    "font": "ui",
    "corners": "round",
    # "off" uses `theme`; "windows" follows Windows' light/dark setting and
    # picks between `theme.light` and `theme.dark`. Schedule is supported by
    # themeswitch but not offered: a monitor left running overnight on a
    # control-room PC should not change colour at 19:00 by itself.
    "theme.follow": "off",
    "theme.light": "light",
    "theme.dark": "dark",
    "theme.day_from": 7,
    "theme.night_from": 19,
    "window.geometry": "",
    "window.maximized": False,
    "window.native_frame": False,
    "page": "tags",
    "updates.check": True,
    "trend.include_bool": True,
}


class Settings:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or paths.ui_settings_path()
        self._data: dict[str, Any] = {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self._data = loaded
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str) -> Any:
        if key in self._data:
            return self._data[key]
        return DEFAULTS.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def save(self) -> None:
        folder = os.path.dirname(self.path)
        try:
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".ui-", suffix=".json", dir=folder)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass  # a preference that did not save is not worth an error box
