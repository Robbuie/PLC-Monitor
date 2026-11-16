# Changelog

One section per version. The release workflow copies the matching section onto
the GitHub release page. The same entries are at the bottom of README.md.

## [1.7.0] - 2026-11-16

A faster Trend chart, and Live.

- Trend is drawn with pyqtgraph (new dependency, MIT licence) instead of
  matplotlib: panning and zooming a day of changes stays smooth. matplotlib is
  kept for the Latency histogram.
- Min/max decimation by time (engine/trendview.py): with more changes in view
  than pixels, each pixel column is drawn from the lowest to the highest value
  held in it, worked out again as the view moves, so a spike of a few
  milliseconds survives on a day-long chart.
- Live mode replaces "Refresh every 30 s": the chart reads the range's history
  once, then is fed from the rows the monitor logs (Session.events), with the
  right edge at now. Robot lines are read again every 5 s. Panning or zooming
  stops the scrolling; Home resumes it. Live turns off when an archive is
  picked. Ticked tags with no history yet get a chart that fills in live.
- Hover readout: a cursor line through every chart and the value each line
  held at that moment (not the nearest logged point), with the date and time
  to the millisecond; text strips give the value and when it began and ended.
- The mouse moves the time axis only; value axes autoscale to what is in view.
  Changing the arrangement or the theme redraws without reading the file again
  and keeps the time in view.
- The last value of each line is drawn up to the end of the range (or now),
  rather than stopping at its last change.
- Packaging: pydoc is no longer excluded from the build (pyqtgraph's PNG
  exporter needs it); pyqtgraph added to the hidden imports and to the
  packaging test.
- tools/preview.py writes its made-up log where the profile's logger reads it,
  so the Trend and Latency previews show PLC tags again.

Measuring on Trend (matching and going past Rockwell's trend tools):

- Cursors A and B (chart header, *Cursors*): drawn on every chart with a band
  between them, dragged or clicked into place, snapping to the nearest change
  of any line or strip within 6 px (Shift: no snap). The time between them
  shows above the value bar.
- Value bar (app/ui/trendpens.py): a row per pen with the value at the pointer
  (or the latest), at A, at B, B - A, and min, max, time-weighted average and
  change count over the view or between the cursors. StepSeries.stats and the
  change counting work on the slice in range only, so they stay cheap on long
  live sessions.
- Booleans (0/1 only) are drawn as logic-analyser lanes, filled while on.
- Arrangement *Same scale*: a chart per pen on one shared value scale.
- Pen settings, saved in the profile (engine/trendprefs.py, the config's
  "trend" block): description, units, fixed scale, draw as lane or chart.
  Double-click a row of the value bar.
- View history: Back and Forward beside Home. A drag or a wheel zoom counts as
  one step once the view has been still for 450 ms; Home and Find steps count
  too; live scrolling does not. Find's previous/next are now up/down arrows.
- Layouts (top bar): save and load named sets of ticked tags, robot signals,
  arrangement, range and cursors in the profile.
- Export is a menu: chart as PNG, data in view as CSV, data between the cursors
  as CSV (value held at the start, then every change, with units).
- Theme: a checked quiet button (Cursors) shows as on.

Past Rockwell (engine/trendmath.py, app/ui/trendtools.py, app/ui/trendcycles.py):

- Events strip: socket RECV/SENT, connection and error rows, and the Latency
  page's last measurement (Session.latency_spec) as bars from trigger to
  response, unanswered triggers marked. Hover lists them; a click opens the row
  in the Live log; cursors snap to them; socket events arrive live.
- Flags from per-pen rules in the profile (high, low, stuck_s, jump): shaded
  spans and limit lines on the chart, a Flags column in the value bar (click to
  step through), recomputed once a second in Live.
- Math pens (profile "trend.math"): A-B, A+B, A*B, A/B (held between changes of
  either, divide by zero is a gap), rate over a window per s or min, and the
  duration of each state (optionally of one value). Their sources are read even
  when not ticked and are recomputed live.
- Compare: the ticked tags from another time or log (a day or a week earlier,
  the same time, or a chosen start) as dashed lines shifted onto the chart, with
  both values in the readout.
- Cycles view: triggers on rise, fall, change or a value (text tags too),
  every pen sampled on a 600-point relative grid, the 5-95 % envelope and
  median of the reference cycles, cycles ranked by how far outside it they
  stray; incomplete cycles excluded; untick Reference to leave a cycle out;
  jump to a cycle on the time chart.
- The arrangement is a drop-down (Chart per tag / Same scale / One chart) and
  the Time / Cycles switch sits at the left of the chart header.
- Fix: reading Trend settings no longer adds an empty "trend" block to the
  config, which made the window ask to save unsaved changes on every profile
  switch once Trend had drawn.
- Fix: theme tokens written rgba(...) are read as colours on the chart.

## [1.6.2] - 2026-11-10

- Older logs decode without torn values: when the registers of a grouped tag
  were logged one by one, a read that caught the PLC rewriting them (`T320NOWN`,
  digits `320`) showed as its own change. A decoded value replaced within a
  second is now folded into the change it was part of; the row keeps the time
  of the first sign and its notes say what it passed through.
- A first decoded value of nothing no longer makes a blank-to-blank row.

## [1.6.1] - 2026-11-10

- Fixes the release build: the 1.5.2 and 1.6.0 installers were never built,
  because a comment in the installer script began with a section name and
  Inno Setup stopped on it ("Invalid section tag"). 1.6.1 carries everything
  from 1.5.2 and 1.6.0. A test now checks the script for such lines.

## [1.6.0] - 2026-11-10

Finding a vehicle in a log.

- Live log opens files: any log (or an archive, double-clicked) shows in the
  table with grouped tags decoded. Find jumps to each row containing a value
  -- a VIN or part of one -- centred, with the events either side; the Filter
  box still hides non-matching rows.
- Trend shows text tags as a strip of values under the charts, on the same
  time axis. Before, a tag grouped as text vanished from the Trend list.
- Find on Trend marks every place a value appears and steps through them; the
  Live log button opens that row in the log.
- New format "digits": the number in a text value (A1234567 -> 1234567), so
  the vehicle number can be plotted and searched as a number. The same
  registers can be set up as both the text and the number.
- Trend: a blank value breaks the line instead of drawing a slope to the next
  one; whole numbers show in full on the axis.
- Fixed: the Trend tag list could be emptied by a read of the previous source
  finishing after the new one.

## [1.5.2] - 2026-11-10

- Fixed: an update could leave PLC Monitor unable to start ("Failed to start
  embedded python interpreter!"). The installer removed the old program files
  first and then failed to copy the new ones over a copy that was still
  running. Now every running copy -- window, headless runner, service --
  announces itself, and the installer waits up to 30 s for them to close,
  or stops before changing anything and says what is still running.
- When an update is ready and the service is running, the window says to
  stop the service first.
- Each update writes a log beside the downloaded installer
  (`%LOCALAPPDATA%\PLC Monitor\updates`), and setup always logs to `%TEMP%`.

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
