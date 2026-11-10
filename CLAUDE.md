# CLAUDE.md

Guidance for working in this repository.

## What this is

**PLC Monitor** timestamps PLC tag changes and socket messages to the millisecond
in one CSV, so the latency between a PLC changing and a downstream system
reacting can be measured -- in a window, or unattended as a Windows service.
Python + PySide6. Repo: Robbuie/plc-monitor (public, all rights reserved).

It is part of a family -- **File Manager** (PySide6), **NetControl**
(WPF, BOOTP/DHCP and network diagnostics), **Redline PDF** (Electron), **DWG
Viewer** (PyQt6) -- that is meant to read as one suite. The look is a spec, not
a preference: see *The design system*.

**It must stand alone.** It is installed on customer PCs that will never have
NetControl, File Manager or RobControl. Sibling integration (`app/core/siblings.py`) is
found at run time through the siblings' installer keys and every caller has a
plain path for when they are absent.

## Working agreement

- **No emojis** anywhere: UI, code, comments, commits, changelog. `tests/test_theme.py` checks.
- **No colour literals outside `app/theme`.** Also checked.
- Comments explain *why*, in prose.
- Ask before adding a dependency; every one ships in the installer.
- Every user-visible change gets a README changelog entry (bottom of README.md),
  a CHANGELOG.md entry, and a version bump in `app/__init__.py` and `pyproject.toml`.
- Update README.md whenever the display changes or a feature is added: it is
  also the in-app guide (Help > User guide renders it, minus the changelog).
- Claude cannot test against a PLC. `tools/preview.py` renders every page
  offscreen to PNG; use it before saying a layout is right, and say plainly what
  needs trying on real hardware.

## Layout

```
app/
  __main__.py        gui | headless | service (SCM entry); --profile, --config
  paths.py           ProgramData (config, logs, lib) vs LOCALAPPDATA (ui.json); legacy migration
  service.py         PLCMonitorService (pywin32) + sc.exe control, elevated through one UAC prompt
  engine/            no Qt. Drivers, poller, socket monitors, sniffer, logger, config
    monitors.py      THE way a config becomes a driver / socket monitor -- window and service both
    profiles.py      profiles (ProgramData\profiles\*.json), the service pointer, first-run migration
    clock.py         the precise wall clock every row is stamped with
    tagimport.py     GX Works3 .gx3 / L5X / tag CSV / plain list -> tag entries
    tagspec.py       one tag-list entry: address, xN registers, format, name; decoding
    headless.py      the unattended runner (retries the PLC every 15 s)
    logdata.py       reading logs back: trend series, latency pairing
    robdata.py       reading RobControl's trend recording (SQLite, read-only) for the Trend page
    network_discovery.py   connections, gentle port check, read-only PLC handshakes
  core/              no widgets. settings.py (ui.json), updates.py, themeswitch.py, siblings.py
  theme/             tokens.py (copied from File Manager), qss.py (copied), sheet.py (ours)
  ui/                window, titlebar, rail, widgets, appearance, guide, session, pages/,
                     profiles_ui (switcher, details, Manage), tagpicker, tagedit
packaging/           build.py, plcmonitor.spec, installer.iss, icon.py
tools/preview.py     offscreen screenshots with a made-up log
tests/
windows_service.py   source-mode service control (pythonservice)
headless_runner.py   kept for old shortcuts
```

### Rules that keep the window and the service in step

- `app/engine` never imports Qt. The service imports nothing from `app/ui`.
- A monitor is built from config only through `engine/monitors.py`. The window
  saves its fields into the config and then connects, so what was tested is
  exactly what the service will run.
- `plc.enabled` / `socket.enabled` mean "run under the service". The window
  ignores them.
- A profile is an ordinary config file plus a `profile` block (name, site,
  line, notes) that only app/engine/profiles.py writes; ConfigManager.save
  keeps the block that is on disk. The file name is the id and never changes.
  The service runs the profile named in `ProgramData\service.json`; the window
  opens the one last used (ui.json `profile`).
- A tag is a TagSpec (engine/tagspec.py). A grouped one is read with the
  driver's `read_block` -- one request, so one PLC scan -- and decoded in the
  poller; it is logged under `spec.key` (its name, else `D1916 x4`). Never
  read a group's registers one by one and stitch them: that is the torn-read
  bug grouping exists to fix.
- The logger writes on its own thread. Anything that reads the file right
  after logging (tests, mostly) calls `logger.flush()` first.

### The design system

`app/theme/tokens.py` and `app/theme/qss.py` are **copied verbatim from File
Manager** so they can be diffed; change them there first. PLC Monitor's own
additions are marked at the bottom of tokens.py (`EVENTS`, `SERIES_LIGHT`,
`SERIES_DARK`) and in `qss.build` (the `ev_*` tokens). `sheet.py` is this app's
template and adds `accent_ink` (accent text that reads on light themes too).
Widgets get their look from a `role` property and the sheet; nothing sets a
colour on a widget.

Chart colours come from `SERIES_LIGHT`/`SERIES_DARK`, validated for colour
vision; a tag keeps its colour for the session; eight lines maximum.

## Safety -- this touches plant networks

- The port check is **gentle by default**: known PLC ports, 4 connections at a
  time with a pause. Fast (128) is labelled bench-only. Never raise the default.
- One host per check; broadcast addresses are refused.
- Handshakes are reads (ListIdentity, COTP CR, MC read of D0). Nothing here
  writes to a PLC. Adding a write is a change to these rules first.
- Passive sniff transmits nothing.
- Robots are RobControl's: PLC Monitor reads RobControl's recording
  (`robcontrol.db`, opened `mode=ro`) and never sends anything to a robot.
  Faster robot reads belong in RobControl, under its own safety rules.

## Commands

```
pip install -r requirements.txt
python -m app
python -m app headless --profile "Site/Name"     (or --config path\to\config.json)
PLCMON_LIBPLCTAG_DIR=<libplctag release folder> pytest tests/test_libplctag_live.py   # against ab_server
QT_QPA_PLATFORM=offscreen pytest
python tools/preview.py --themes dark,light,control --out-dir previews
python packaging/build.py          # needs Inno Setup 6 for the installer
```

Release: tag `vX.Y.Z` matching `__version__`; `.github/workflows/release.yml`
builds the installer and `latest.json` and publishes them with `gh`.

## Known open questions

- **scapy is GPL-2.0** and is bundled for Passive sniff. NetControl avoided this
  by loading Npcap's `wpcap.dll` directly through ctypes. Doing the same here
  would drop the GPL dependency from the installer.
- NetControl takes no command-line target yet; the handoff puts the address on
  the clipboard. When it learns `--target`, change `siblings.launch` callers.
