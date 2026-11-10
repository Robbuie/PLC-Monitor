"""Profiles: one saved setup per site, line or PLC, and which one runs where.

A profile is an ordinary config file -- the same JSON the window and the
service have always read -- kept in `%PROGRAMDATA%\\PLC Monitor\\profiles\\`
with a small `profile` block on top that names it:

    {"profile": {"name": "Press PLC", "site": "Toledo", "line": "Line 3",
                 "notes": "..."}, "plc": {...}, "socket": {...}, ...}

The file name is the profile's id. It is made once from the site and name and
never changes, so renaming a profile cannot break the service's pointer to it
or a shortcut that names it.

Each profile logs into its own folder under `logs\\` (chosen when it is
created), so a Latency run on one machine never picks up another's events.

Which profile the **service** runs is a separate pointer,
`%PROGRAMDATA%\\PLC Monitor\\service.json`, because it is site state rather
than one person's choice. Which one the **window** opens with is a personal
preference and lives in ui.json (see app/core/settings.py).

No Qt here: the service uses it.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass

from app import paths

PROFILE_DIR = "profiles"
SERVICE_POINTER = "service.json"
EXPORT_EXT = ".plcmon"
EXPORT_MARK = "plcmonitor_profile"
DEFAULT_NAME = "Default"

#: Settings that belong to one PC rather than to the setup: which network
#: adapter, this PC's own address, where its log and libplctag DLL are. An
#: exported profile leaves them out, so it opens cleanly on another PC.
MACHINE_FIELDS = (
    ("plc", "source_ip"),
    ("plc", "libplctag", "dll_path"),
    ("socket", "sniff", "iface"),
    ("socket", "sniff", "our_ip"),
    ("logging", "log_path"),
    ("service", "config_path"),
)


class ProfileError(ValueError):
    """Something a person should be told, in words they can act on."""


@dataclass
class Profile:
    id: str
    path: str
    name: str
    site: str = ""
    line: str = ""
    notes: str = ""
    modified: float = 0.0

    @property
    def label(self) -> str:
        """"Site / Name", or just the name for a profile with no site."""
        return f"{self.site} / {self.name}" if self.site else self.name


# --------------------------------------------------------------- locations


def profiles_dir() -> str:
    return os.path.join(paths.data_dir(), PROFILE_DIR)


def _pointer_path() -> str:
    return os.path.join(paths.data_dir(), SERVICE_POINTER)


def _safe(text: str) -> str:
    """Text made safe for a Windows file or folder name."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    return text[:80]


def _slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", text.lower()).strip("-")
    return text[:60] or "profile"


def log_folder_for(site: str, name: str) -> str:
    """The folder a new profile logs into. Readable, since people open it."""
    base = _safe(f"{site} - {name}" if site else name) or "Profile"
    folder = os.path.join(paths.log_dir(), base)
    n = 2
    while os.path.exists(folder) and _folder_in_use(folder):
        folder = os.path.join(paths.log_dir(), f"{base} ({n})")
        n += 1
    return folder


def _folder_in_use(folder: str) -> bool:
    """Whether another profile already logs into `folder`."""
    target = os.path.normcase(os.path.abspath(folder))
    for profile in list_profiles():
        data = _read(profile.path) or {}
        log_path = (data.get("logging") or {}).get("log_path") or ""
        if log_path and os.path.normcase(os.path.dirname(os.path.abspath(log_path))) == target:
            return True
    return False


# ------------------------------------------------------------------ reading


def _read(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: str, data: dict) -> None:
    """Atomically, like ConfigManager.save: the service may read it at boot."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".profile-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def info(path: str) -> Profile | None:
    """The profile stored at `path`, or None if it is not a readable one."""
    data = _read(path)
    if data is None:
        return None
    meta = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        modified = os.path.getmtime(path)
    except OSError:
        modified = 0.0
    return Profile(id=stem, path=os.path.abspath(path),
                   name=str(meta.get("name") or stem), site=str(meta.get("site") or ""),
                   line=str(meta.get("line") or ""), notes=str(meta.get("notes") or ""),
                   modified=modified)


def list_profiles() -> list[Profile]:
    """Every profile, grouped by site (no site last), then by name."""
    folder = profiles_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = []
    for name in names:
        if name.lower().endswith(".json") and not name.startswith("."):
            profile = info(os.path.join(folder, name))
            if profile is not None:
                found.append(profile)
    found.sort(key=lambda p: (p.site == "", p.site.lower(), p.name.lower()))
    return found


def get(profile_id: str | None) -> Profile | None:
    if not profile_id:
        return None
    path = os.path.join(profiles_dir(), f"{profile_id}.json")
    return info(path) if os.path.isfile(path) else None


def is_profile_path(path: str) -> bool:
    folder = os.path.normcase(os.path.abspath(profiles_dir()))
    return os.path.normcase(os.path.dirname(os.path.abspath(path))) == folder


def find(text: str) -> Profile | None:
    """A profile by id, by "Site/Name" (spaces around the slash optional), or
    by name alone when only one profile has it. Case does not matter."""
    wanted = (text or "").strip()
    if not wanted:
        return None
    every = list_profiles()
    low = wanted.lower()
    for p in every:
        if p.id.lower() == low:
            return p
    if "/" in wanted:
        site, _, name = (part.strip().lower() for part in wanted.partition("/"))
        for p in every:
            if p.site.lower() == site and p.name.lower() == name:
                return p
    by_name = [p for p in every if p.name.lower() == low or p.label.lower() == low]
    return by_name[0] if len(by_name) == 1 else None


def _label_taken(site: str, name: str, ignore_id: str = "") -> bool:
    site, name = site.strip().lower(), name.strip().lower()
    return any(p.site.lower() == site and p.name.lower() == name and p.id != ignore_id
               for p in list_profiles())


def _unique_label(site: str, name: str) -> str:
    """`name`, or `name (2)`, ... -- whichever is free at this site."""
    if not _label_taken(site, name):
        return name
    n = 2
    while _label_taken(site, f"{name} ({n})"):
        n += 1
    return f"{name} ({n})"


# ------------------------------------------------------------------ writing


def _new_id(site: str, name: str) -> str:
    base = _slug(f"{site}-{name}" if site else name)
    candidate, n = base, 2
    while os.path.exists(os.path.join(profiles_dir(), f"{candidate}.json")):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ProfileError("A profile needs a name.")
    if len(name) > 80:
        raise ProfileError("Keep the name under 80 characters.")
    return name


def create(name: str, site: str = "", line: str = "", notes: str = "",
           base: dict | None = None, log_path: str | None = None) -> Profile:
    """Make a new profile, from `base` (a config dict) or the defaults.

    It gets its own log folder unless `log_path` says otherwise (the migrated
    Default keeps the log it already had).
    """
    from app.engine.config_manager import DEFAULT_CONFIG, _deep_merge

    name, site = _check_name(name), (site or "").strip()
    if _label_taken(site, name):
        raise ProfileError(f"There is already a profile called {site + ' / ' if site else ''}{name}.")
    data = _deep_merge(copy.deepcopy(DEFAULT_CONFIG), copy.deepcopy(base or {}))
    profile_id = _new_id(site, name)
    path = os.path.join(profiles_dir(), f"{profile_id}.json")
    data["profile"] = {"name": name, "site": site, "line": (line or "").strip(),
                       "notes": notes or "", "created": time.strftime("%Y-%m-%d %H:%M")}
    data.setdefault("logging", {})["log_path"] = log_path or os.path.join(
        log_folder_for(site, name), paths.LOG_NAME)
    data.setdefault("service", {})["config_path"] = os.path.abspath(path)
    paths.ensure_data_dir()
    _write(path, data)
    return info(path)


def update_details(profile_id: str, name: str, site: str = "", line: str = "",
                   notes: str = "") -> Profile:
    """Rename a profile or change its site, line or notes. The id, the file
    and the log folder stay as they are."""
    profile = get(profile_id)
    if profile is None:
        raise ProfileError("That profile no longer exists.")
    name, site = _check_name(name), (site or "").strip()
    if _label_taken(site, name, ignore_id=profile_id):
        raise ProfileError(f"There is already a profile called {site + ' / ' if site else ''}{name}.")
    data = _read(profile.path) or {}
    meta = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    meta.update({"name": name, "site": site, "line": (line or "").strip(), "notes": notes or ""})
    data["profile"] = meta
    _write(profile.path, data)
    return info(profile.path)


def duplicate(profile_id: str, name: str, site: str = "", line: str = "",
              notes: str | None = None, data: dict | None = None) -> Profile:
    """A copy with everything but its name, details and log folder.

    `data` is the config to copy when it is not the saved one -- the window
    passes what is on screen, so a copy made mid-edit has the edits without
    the original being saved behind the person's back.
    """
    source = get(profile_id)
    if source is None:
        raise ProfileError("That profile no longer exists.")
    data = copy.deepcopy(data) if data is not None else (_read(source.path) or {})
    data.pop("profile", None)
    data.get("logging", {}).pop("log_path", None)
    return create(name, site, line, source.notes if notes is None else notes, base=data)


def delete(profile_id: str) -> None:
    """Remove a profile. Its logs stay where they are."""
    profile = get(profile_id)
    if profile is None:
        return
    if service_profile_id() == profile_id:
        raise ProfileError("The service runs this profile. Choose another one for it on the "
                           "Service page first.")
    os.remove(profile.path)


def export(profile_id: str, dest: str, data: dict | None = None) -> str:
    """Write a profile to a .plcmon file for another PC, without the
    settings that only make sense on this one. `data`, when given, is the
    config to write instead of the saved one (what is on screen)."""
    profile = get(profile_id)
    if profile is None:
        raise ProfileError("That profile no longer exists.")
    saved = _read(profile.path) or {}
    data = copy.deepcopy(data if data is not None else saved)
    data["profile"] = copy.deepcopy(saved.get("profile") or {})
    for keys in MACHINE_FIELDS:
        node = data
        for key in keys[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(keys[-1], None)
    for section in ("plc", "socket"):
        # Whether the service runs a monitor is decided on the PC it runs on.
        if isinstance(data.get(section), dict):
            data[section]["enabled"] = False
    data[EXPORT_MARK] = 1
    data["exported"] = time.strftime("%Y-%m-%d %H:%M")
    if not dest.lower().endswith(EXPORT_EXT):
        dest += EXPORT_EXT
    with open(dest, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    return dest


def import_file(src: str) -> Profile:
    """Make a profile from a .plcmon export, or from any PLC Monitor config
    file (an old plc_monitor_config.json from another PC, say)."""
    data = _read(src)
    if data is None:
        raise ProfileError("That file is not a PLC Monitor profile (it is not readable JSON).")
    if not any(isinstance(data.get(k), dict) for k in ("plc", "socket")):
        raise ProfileError("That file is not a PLC Monitor profile (it has no PLC or socket "
                           "settings).")
    meta = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    name = str(meta.get("name") or os.path.splitext(os.path.basename(src))[0]).strip()
    site = str(meta.get("site") or "").strip()
    for key in ("profile", EXPORT_MARK, "exported"):
        data.pop(key, None)
    data.get("logging", {}).pop("log_path", None)
    data.get("service", {}).pop("config_path", None)
    return create(_unique_label(site, name), site, str(meta.get("line") or ""),
                  str(meta.get("notes") or ""), base=data)


# ------------------------------------------------- the service's profile


def service_profile_id() -> str | None:
    data = _read(_pointer_path()) or {}
    value = data.get("profile")
    return str(value) if value else None


def set_service_profile(profile_id: str) -> None:
    if get(profile_id) is None:
        raise ProfileError("That profile no longer exists.")
    paths.ensure_data_dir()
    data = _read(_pointer_path()) or {}
    data["profile"] = profile_id
    _write(_pointer_path(), data)


# ----------------------------------------------------------- start-up


def ensure_default() -> Profile:
    """Make sure at least one profile exists, and return the first.

    The first time 1.4 starts, the single config every earlier version used
    becomes the profile "Default" -- settings, tags and log file as they were
    -- and the service is pointed at it, so an installed service carries on
    exactly as before. The old file is left where it was.
    """
    existing = list_profiles()
    if existing:
        return existing[0]
    paths.migrate_legacy_config()
    legacy = os.path.join(paths.data_dir(), paths.CONFIG_NAME)
    base = _read(legacy) if os.path.isfile(legacy) else None
    log_path = None
    if base is not None:
        old_log = (base.get("logging") or {}).get("log_path") or ""
        if old_log and not os.path.isabs(old_log):
            old_log = os.path.join(paths.log_dir(), os.path.basename(old_log))
        log_path = old_log or paths.default_log_path()
    profile = create(DEFAULT_NAME, base=base, log_path=log_path)
    if service_profile_id() is None:
        set_service_profile(profile.id)
    return profile


def service_config_path() -> str:
    """The config the service and `headless` run with no --config given.

    PLC_MONITOR_CONFIG still wins (pre-1.0 service installs set it); then the
    service pointer; then the first profile, made from the old config if
    there is none yet.
    """
    override = os.environ.get(paths.CONFIG_ENV)
    if override:
        return override
    pointed = get(service_profile_id())
    if pointed is not None:
        return pointed.path
    return ensure_default().path


def resolve(text: str) -> str:
    """A --profile argument to a config path: a profile id or name, or a
    path to a config file."""
    if text and os.path.isfile(text):
        return os.path.abspath(text)
    profile = find(text)
    if profile is None:
        names = ", ".join(p.label for p in list_profiles()) or "none yet"
        raise ProfileError(f"No profile called '{text}'. Profiles: {names}.")
    return profile.path


def startup_path(last_id: str | None) -> str:
    """The profile the window opens with: the one last used here, else the
    service's, else the first."""
    for candidate in (get(last_id), get(service_profile_id())):
        if candidate is not None:
            return candidate.path
    return ensure_default().path
