"""
config_manager.py
Persist the monitor configuration to a JSON file.

This is site configuration -- what to poll, what to sniff, where to log -- and
it is shared by the window, the headless runner and the Windows service. How
the window looks is not in here; that is one person's preference and lives in
`ui.json` beside their profile (see app/paths.py).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from app import paths

DEFAULT_CONFIG: dict[str, Any] = {
    "plc": {
        "type":       "Rockwell (pycomm3)",
        "ip":         "192.168.1.1",
        "port":       44818,
        "slot":       0,
        "rack":       0,
        "tags":       [],
        "tags_per_driver": {},
        "poll_ms":    100,
        # Whether the service and the headless runner poll the PLC. The window
        # ignores it -- there a person presses Connect. Before 1.0 nothing ever
        # set this, so an installed service started, logged that it had
        # started, and polled nothing.
        "enabled":    False,
        "source_ip":  "",
        "plc_sub_type": "Q",
        "frame":      "3E",
        "libplctag": {
            "path":       "1,0",
            "cpu":        "lgx",
            "protocol":   "ab-eip",
            "data_type":  "DINT",
            "timeout_ms": 5000,
            "debug":      0,
            "dll_path":   "",
        },
    },
    "socket": {
        "mode":         "passive",  # "passive" | "proxy" | "server" | "udp"
        "protocol":     "tcp",
        "listen_host":  "0.0.0.0",
        "listen_port":  9000,
        "forward_host": "192.168.1.100",
        "forward_port": 9001,
        "source_label": "SOCKET",
        "enabled":      False,
        "sniff": {
            "iface":  "",
            "host":   "",
            "port":   "",
            "proto":  "any",
            "our_ip": "",
            "bpf":    "",
        },
    },
    "logging": {
        "log_path":     "",          # empty means paths.default_log_path()
        "max_gui_rows": 5000,
        "max_mb":       50,
        "max_rotated":  30,
        "rotate_daily": True,
    },
    "service": {
        "name":         "PLCMonitorService",
        "display_name": "PLC Monitor Service",
        "description":  "Monitors PLC tags and socket traffic for latency analysis.",
        "config_path":  "",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class ConfigManager:
    def __init__(self, path: str | None = None):
        self.path = path or paths.config_path()
        self._data = _deep_merge({}, DEFAULT_CONFIG)
        if os.path.exists(self.path):
            self.load()
        self._normalise()

    def load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            self._data = _deep_merge(DEFAULT_CONFIG, loaded)

    def _normalise(self):
        """Make the log path absolute.

        A relative path is resolved against whatever folder the process was
        started in, which for a service is System32. Pre-1.0 configs carried
        "plc_monitor_log.csv", so a relative one is taken to mean the file of
        that name in the site log folder.
        """
        log_path = str(self._data["logging"].get("log_path") or "")
        if not log_path:
            self._data["logging"]["log_path"] = paths.default_log_path()
        elif not os.path.isabs(log_path):
            self._data["logging"]["log_path"] = os.path.join(
                paths.log_dir(), os.path.basename(log_path))

    def save(self):
        """Write atomically: a crash halfway through must not leave the
        service a config it cannot parse at the next boot."""
        self._data["service"]["config_path"] = os.path.abspath(self.path)
        folder = os.path.dirname(os.path.abspath(self.path))
        if folder == paths.data_dir():
            paths.ensure_data_dir()
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def get(self, *keys) -> Any:
        node = self._data
        for k in keys:
            node = node[k]
        return node

    def set(self, *keys_and_value):
        *keys, value = keys_and_value
        node = self._data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    @property
    def plc(self): return self._data["plc"]
    @property
    def socket(self): return self._data["socket"]
    @property
    def logging(self): return self._data["logging"]
    @property
    def service(self): return self._data["service"]

    def as_dict(self): return self._data
