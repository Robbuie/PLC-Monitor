# Changelog

One section per version. The release workflow copies the matching section onto
the GitHub release page. The same entries are at the bottom of README.md.

## [1.5.1] - 2026-11-10

- Fixed: the Mitsubishi simulator in `tools/` crashed at start on Windows
  when its output went to a file or pipe (a character the Windows code page
  cannot show), which failed the 1.5.0 release tests. The test now says why
  if the simulator ever exits early, and runs it with the Windows code page
  on every platform so this is caught before a release.

## [1.5.0] - 2026-11-10

Values that span registers, read as what they are.

- Grouped tags: several consecutive registers read in one request and
  decoded as text (a VIN), INT16/UINT16, INT32/UINT32, REAL, BCD or hex,
  with byte and word order. Type "D1916 x4 ascii" in the Add box, or select
  registers and Edit... to combine them. One request means one PLC scan: no
  more half-written values.
- Names: any tag can be named ("Plant VIN"); the log, Trend and Latency use
  the name, and the notes column says which registers it came from.
- Import from GX Works3 (.gx3): global labels with their comments, and
  grouped tags suggested from array element comments.
- Latency matched by value: pair each trigger with the response that carries
  the same value -- a VIN from arrival to send-out -- with several in flight
  at once. "Changes to" also takes !value (anything but).
- Older logs decoded: Trend and Latency apply the profile's grouped tags to
  logs that recorded the registers one per row.
- The Mitsubishi simulator in tools/ now plays VINs through D1916 and D1000.

## [1.4.2] - 2026-11-10

- Fixed: the Trend and Latency charts were missing from the installed build
  ("matplotlib is not available in this build ... No module named
  'unittest'"). The installer left out a standard module that matplotlib
  needs. A test now checks that nothing the app imports depends on a module
  the installer leaves out.

## [1.4.1] - 2026-11-10

- Fixed: a UDP listener whose port was already taken said so only on the
  status line; the error now goes into the log, as it does for Proxy and
  Server. A Windows "port unreachable" report no longer stops the listener.
- Tests: the socket-monitor tests wait for the listener instead of sleeping
  a fixed time, and pick a port free for UDP, which failed the 1.4.0 build
  on a busy Windows runner.

## [1.4.0] - 2026-11-09

Profiles: one saved setup per site, line or PLC.

- The open profile is named in the title bar; click it to switch. Profiles
  are grouped by site, carry a line or cell and free-text notes, and each logs
  into its own folder.
- New, Duplicate, Rename / edit details, Delete, and Manage profiles (a
  searchable list, Ctrl+P). Switching stops running monitors and asks about
  unsaved changes first.
- Export a profile to a .plcmon file and import it on another PC; the
  network adapter and this PC's address stay behind.
- The Service page chooses which profile the service runs. Saving the
  service's profile while it runs says to restart it.
- --profile "Site/Name" for the window and for headless.
- Tags: Import... reads a Studio 5000 L5X or tag CSV export, or any CSV or
  text list, into a searchable list to tick from.
- The configuration from earlier versions becomes the profile "Default";
  an installed service carries on running it.
- Fixed: a setting changed in one config could change the defaults for any
  config created later in the same session.

## [1.3.0] - 2026-11-09

Runs unattended for weeks.

- The PLC is reconnected by itself when it stops answering mid-run (power
  cycle, cable, switch). Before, the window and the service went on logging
  read errors until someone restarted them. The PLC chip shows
  "reconnecting" meanwhile, and Disconnect still works. A connection only
  counts as back when a tag answers.
- The log is written on its own thread. In Proxy mode each message used to
  wait for the file to be opened, written and closed before it was passed
  on, so the monitor added its own delay to what it was measuring. A disk
  that refuses a write (full, locked) is retried instead of losing the rows.
- Fixed: two log rotations in the same second overwrote the first archive.
- A PLC driver error that is raised rather than reported no longer stops
  polling silently.
- libplctag: a tag whose create timed out (PLC unreachable) is retried on
  the next poll; packed reads are only given up on a device that has never
  answered one.

## [1.2.0] - 2026-11-09

Timestamps you can trust to the millisecond.

- Every row is stamped with the precise Windows clock. Builds on Python
  before 3.13 (including every release so far) stamped with a clock that
  only moved every 15.6 ms.
- Passive sniff stamps each message with the time the adapter captured it,
  takes the first packet's time for a message split over several, and no
  longer logs a TCP retransmission as a second message. Data that arrived
  with, or just before, a connection's FIN is logged before the disconnect
  instead of being dropped.
- Rockwell tags are read in one request per poll (pycomm3 and libplctag), so
  a longer tag list no longer stretches every poll. A device that refuses
  packed requests is detected and read one tag at a time.
- Each tag change records its poll window (new log column `window_ms`). The
  Latency page says how much longer each latency could be, and the export
  includes it. A log begun by an older version is archived and a new one
  started, so columns never shift.
- Latency pairs rows by time even when they were written slightly out of
  order (a captured packet is stamped before it is written).
- Fixed: Siemens M, I and Q tags failed with "no attribute 'types'" on the
  current python-snap7.
- Fixed: a Rockwell (pycomm3) device that refused the EtherNet/IP session was
  shown as connected, and every read then failed.
- Fixed: libplctag logged an error code as the value when a tag returned no
  data, and retried a tag it could not create on every poll, holding up the
  others each time.
- The log is written as UTF-8, so payload text in any language is kept.

## [1.1.0] - 2026-10-05

- Trend: a Robots card shows the signals RobControl records (registers, I/O,
  system variables) and plots them on the same time axis as PLC tags. Read from
  RobControl's database, read-only; Open... reads one from another PC.
- User guide rewritten for someone new to the tool: what it is for, a worked
  example, how it works, how accurate the timestamps are (and why the poll
  interval matters), a step-by-step first measurement, and more troubleshooting.

## [1.0.1] - 2026-10-02

- Fixed: moving the mouse over the window printed an error on every move
  (the Latency page's "Give up after" field was named in a way that hid a
  Qt method the window relies on). Renamed it and two others like it, and
  added tests that catch the whole class of mistake.
- Fixed: a font-size warning from the Live log's event badges.

## [1.0.0] - 2026-10-02

Rebuilt to match the family (File Manager, NetControl, Redline PDF) and cleaned up.

- New window in PySide6 on the shared design system: custom title bar with the
  menus and running-state chips, a page rail, cards, 11 themes x 6 accents x 3
  densities, follow Windows light/dark. No emojis.
- New Latency page: pairs every trigger with its response and reports
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
  copied across on first start.
- libplctag: per-tag data types now take effect (they were stored but ignored).
- Passive sniff stops promptly on a quiet line.
- Installer with GitHub update check; the in-app guide is README.md.
