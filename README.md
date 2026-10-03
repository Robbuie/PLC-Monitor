# PLC Monitor

Timestamps PLC tag changes and socket messages to the millisecond, in one log,
so you can measure how long the downstream system takes to react. Built to
answer one question -- *how many milliseconds between the PLC changing and the
message going out?* -- and to keep answering it unattended as a Windows service.

It stands on its own: install it on a customer PC and nothing else is needed
(Npcap only if you use Passive sniff). When NetControl or File Manager are also
installed, PLC Monitor offers to hand work to them.

---

## Install

Run `PLCMonitor-Setup-<version>.exe`. It installs for the current user, with no
administrator prompt, and checks GitHub for updates when it starts (silently
if the PC has no internet).

- **Passive sniff** needs [Npcap](https://npcap.com) (free; tick *WinPcap
  API-compatible mode*) and PLC Monitor run as administrator. The Socket page
  has a *Restart as administrator* button.
- **libplctag** needs `plctag.dll`: press **Download** on the Tags page.

The configuration and the logs live in `C:\ProgramData\PLC Monitor\`, shared
by the window and the service. Uninstalling leaves them there.

---

## The window

A rail down the left groups the pages: **Monitor** (Tags, Socket, Live log),
**Analyse** (Trend, Latency) and **Set up** (Network, Service). The title bar
holds the menus and two chips -- *PLC* and *Socket* -- that turn green while
they are running, and a third while the service is logging in the background.

Keyboard: `F5` connect the PLC, `F6` start the socket monitor, `Ctrl+.` stop
both, `Ctrl+1`...`Ctrl+7` jump to a page, `Ctrl+S` save the config, `F1` this
guide.

**View > Appearance** sets the theme, accent and density -- the same choices
as the author's other applications. **View > Use the Windows title bar**
swaps the custom title bar for Windows' own (useful over Remote Desktop).

### Tags

Choose the **PLC family** first; the fields follow it.

| Family | Driver | Notes |
|---|---|---|
| Rockwell ControlLogix / CompactLogix | pycomm3 | The usual choice. *CPU slot* is the controller's slot, not the Ethernet module's. |
| Rockwell, other families | libplctag | Needs `plctag.dll` (Download). Each tag has its own data type in the table. |
| Siemens S7-300/400/1200/1500 | snap7 | S7-300 is usually slot 2, 1200/1500 slot 1. |
| Mitsubishi iQ-R / Q / L / FX | MC Protocol | Choose the series and frame. |

Add tags in the box above the table, then **Connect**. Each value updates on
every poll and flashes when it changes; *Changes* counts them. Every change is
written to the log with its old and new value. Double-click a tag to rename it.
Each family keeps its own tag list, so switching back and forth loses nothing.

**Adapter** matters when the PC is on more than one network: pick the one on
the PLC's subnet. **Unattended** decides whether the service polls these tags.

The external-link button beside the IP address opens NetControl (when
installed) with the address on the clipboard.

### Socket

| Mode | What it does | Changes anything? |
|---|---|---|
| **Passive sniff** | Reads packets off the adapter. Sees both directions. | No. Recommended. |
| **Proxy** | Sits between sender and receiver and forwards. | Yes: the sender must be pointed at this PC. |
| **Server** | Listens on a TCP port and logs what arrives. | Yes: the sender must be pointed here. |
| **UDP listener** | Logs datagrams on a port. | Yes. |

For Passive sniff, *Filter host* and *Filter port* build the capture filter;
edit **BPF filter** directly for anything Wireshark's capture syntax can say.
*Our IP* labels each message as received or sent.

### Live log

Every event as it is written, with the event type as a coloured badge (green
PLC change, cyan received, amber sent, red error). Filter by kind or search for
a tag, address, value or payload. The table holds the last few thousand rows;
the CSV has all of them.

**Log files** shows where the CSV is and handles rotation: at a size limit
(default 50 MB), at midnight, or now. Rotated files are compressed to
`.csv.gz` (10-20 times smaller) and the oldest is deleted past the count you
keep. **Folder** opens the log folder -- in File Manager when it is installed.

### Trend

Numeric tag values over time from the live log, an archive, or any PLC Monitor
log opened with **Open file...** (a log from another PC works). Booleans plot
as 0 and 1. **One per tag** gives each tag its own chart on a shared time axis,
so tags with very different ranges stay readable; **Together** puts them on one
axis. Hover a line for the exact time and value. Up to eight tags; each keeps
its colour for the session. **Refresh every 30 s** turns it into a live display.

### Latency

The measurement this application exists for. Choose what **starts the clock**
(usually a tag changing, optionally only to a given value such as `True`) and
what **stops it** (usually the next received socket message, optionally only
from one monitor or only when the payload contains some text). **Measure**
pairs every occurrence in the chosen log and shows:

- the fastest, median, mean, 95th percentile and slowest response;
- how many triggers got **no response** within the *Give up after* window;
- every pair, and a histogram with the 95th percentile marked.

A response that arrives after the *next* trigger is credited to that trigger,
not the earlier one. **Export CSV** saves every pair for a report.

### Network

**Connections on this PC** lists every open TCP and UDP socket with the
program that owns it -- the quickest way to find the port the receiving
software uses. Right-click a row to send its address to the Tags or Socket page.

**Port check** asks one device which ports answer. By default it checks the
known PLC and industrial ports, four at a time with a short pause, which is
safe on a running line. *Range* checks any span; *Fast* opens 128 connections
at once and belongs on a bench only -- some PLC and drive Ethernet cards drop
off the network under that. Known PLC ports get one read-only identity request
(EtherNet/IP ListIdentity, S7 connection request, MC Protocol read of D0), and
*Answered as* says what replied. Right-click a result to monitor that device.

When NetControl is installed, **Open in NetControl** hands the device over for
ping, a protocol-verified service check and link diagnostics.

### Service

Runs the monitors as a Windows service: starts at boot, needs nobody logged in,
writes to the same log, and restarts itself a minute after a crash.

1. Set up the Tags and Socket pages and test them in the window.
2. Turn on **Unattended** for the monitors the service should run.
3. **Install** (Windows asks for administrator rights), then **Start**.

*What it will run* says exactly what the service will do with the saved config.
If the PLC does not answer at boot the service keeps retrying every 15 seconds.
Its own messages go to `service.log` beside the event log. **Run headless here**
runs the same thing in a console window instead.

While the service is running, connecting from the window as well logs every
change twice; stop the service first, or just watch the Live log.

---

## The log file

`C:\ProgramData\PLC Monitor\logs\plc_monitor_log.csv` (change it with
**Move...** on the Live log page).

| Column | Contents |
|---|---|
| `timestamp_ms` | Unix epoch milliseconds -- use this for arithmetic |
| `timestamp_human` | `2026-01-15 10:30:45.123` |
| `event_type` | `PLC_CHANGED`, `PLC_ERROR`, `PLC_CONNECTED`, `SOCK_RECV`, `SOCK_SENT`, `SOCK_CONNECT`, `SOCK_ERROR`, `INFO` |
| `source` | `Rockwell (pycomm3)@192.168.1.1`, the socket monitor's label, ... |
| `tag_or_addr` | Tag name, or `source:port > dest:port` |
| `old_value` / `new_value` | For tag changes |
| `raw_hex` | Socket payload as hex |
| `notes` | Decoded payload text, or error detail |

## Tag formats

**Rockwell:** `Motor_Running`, `Program:Main.Counter`, `MyUDT.Member`, `MyArray[0]`

**Siemens:** `DB10,X0.0` (bit), `DB10,REAL4`, `DB10,DINT4`, `M0.0`, `Q0.0`, `I0.0`

**Mitsubishi:** `D100`, `D100.5` (bit 5 of D100), `M200`, `X0`, `Y0`, `B0`,
`TN0`/`TC0` (timer value/contact), `CN0`/`CC0` (counter value/contact)

## Troubleshooting

**Cannot connect (pycomm3).** *CPU slot* is the controller's slot, not the
Ethernet module's -- count from 0. Ping the PLC; port 44818 must be open.

**Passive sniff sees nothing.** Npcap installed? Running as administrator?
The right adapter? The traffic must actually pass this adapter: a mirrored
switch port, a hub, or this PC being one end. Try clearing *Filter port*.

**"Address already in use."** Something else has that port. The Network page's
connection list shows what.

**The service starts but logs nothing.** Check *What it will run* on the Service
page -- a monitor needs **Unattended** on. Then `service.log`, and Event Viewer
under Windows Logs > Application.

---

## Building from source

Python 3.11+ on Windows.

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app                  # the window
python -m app headless         # monitors only, in this console
pytest                         # tests
python tools/preview.py        # render every page to PNG, offscreen
build.bat                      # dist\PLCMonitor\ and, with Inno Setup, the installer
```

A release is a tag push: bump `__version__` in `app/__init__.py` and
`pyproject.toml`, add a CHANGELOG.md entry, `git tag v1.0.1 && git push --follow-tags`.

---

## Changelog

### 1.0.1 -- 2026-10-02

- Fixed: moving the mouse over the window printed an error on every move
  (the Latency page's "Give up after" field was named in a way that hid a
  Qt method the window relies on). Renamed it and two others like it, and
  added tests that catch the whole class of mistake.
- Fixed: a font-size warning from the Live log's event badges.

### 1.0.0 -- 2026-10-02

Rebuilt to match the family (File Manager, NetControl, Redline PDF) and cleaned up.

- New window in PySide6 on the shared design system: custom title bar with the
  menus and running-state chips, a page rail, cards, 11 themes x 6 accents x 3
  densities, follow Windows light/dark. No emojis.
- New **Latency** page: pairs every trigger with its response and reports
  min/median/mean/p95/max, unanswered triggers, a histogram, and CSV export.
- Trend: one chart per tag on a shared time axis, step-accurate lines, hover
  readout, any log file can be opened, validated colour palette.
- Live log: event badges, search, filter by kind, archive list.
- Port check is gentle by default (known PLC ports, 4 at a time); Fast is
  labelled bench-only. Results and connections send their address to the
  other pages by right-click.
- NetControl and File Manager are used when installed, never required.
- Service fixes: the installed exe now registers with the service manager
  properly, the service actually polls (the Unattended switches save
  `enabled`), Passive sniff and libplctag settings run under the service, the
  PLC is retried every 15 s, crashes restart it, and `service.log` records it.
- Config and logs moved to `C:\ProgramData\PLC Monitor\`; an existing config is
  copied across on first start. Old logs stay where they were (open them on the
  Trend or Latency page with *Open file...*).
- libplctag: per-tag data types now take effect (they were stored but ignored).
- Passive sniff stops promptly on a quiet line.
- Installer with GitHub update check; the in-app guide is this README.
