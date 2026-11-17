# PLC Monitor

**How many milliseconds pass between the PLC changing and the other system
reacting?** PLC Monitor answers that. It watches PLC tags and the network
messages going to and from another system, writes every change and every
message into one log with a millisecond timestamp, and then measures the gap
between them -- once, or around the clock as a Windows service.

It stands on its own: install it on any Windows PC on the machine network and
nothing else is needed (Npcap only if you use Passive sniff). It never writes
to a PLC.

---

## What it is for

A typical case: a PLC sets `Part_Present` when a part arrives, and a vision
system, MES, label printer or robot cell controller is supposed to answer over
TCP. Someone says *"the camera is slow"* and someone else says *"the PLC is
late"*. PLC Monitor puts both sides on one clock:

```
10:30:45.112  PLC_CHANGED  Part_Present   False -> True
10:30:45.398  SOCK_RECV    10.0.40.50:9000 > 10.0.40.11:2000   "OK,PART=1234"
                           -> 286 ms from tag change to answer
```

Leave it running for a shift and the **Latency** page tells you the fastest,
typical and slowest answer, the 95th percentile, and how many times the answer
never came.

It is also useful on its own as:

- a **tag logger** -- every value change of any tag, with old and new value;
- a **trend viewer** for those tags, live or from last week's log -- and,
  when RobControl is on the same PC, for robot registers and I/O alongside them;
- a **socket sniffer / logger** that shows exactly what a device sent and when;
- a **"what is on this port?"** tool (the Network page).

## How it works, in one picture

```
  PLC  --(polled every N ms)-->  Tags page    \
                                                >--  one CSV log  -->  Live log, Trend, Latency
  other system <--(network)-->  Socket page   /
```

1. **Tags** reads the tags you list from the PLC every *Poll every* ms
   (default 100). Only *changes* are logged, so a tag that sits still for an
   hour costs nothing.
2. **Socket** records messages between two devices -- by listening to the
   network (Passive sniff, changes nothing) or by standing in the middle
   (Proxy / Server / UDP).
3. Both write to the same CSV, stamped by this PC's clock, so the two sides can
   be compared directly.
4. **Live log**, **Trend** and **Latency** read that CSV -- or any older one,
   from this PC or another.

## How accurate is it?

- **Socket messages** in Passive sniff are stamped with the time the network
  adapter captured the packet (Npcap's own timestamp), so the PC being busy
  does not shift them. A message split over several packets takes the time of
  the first. A packet the sender had to send again (a TCP retransmission) is
  not logged a second time. Proxy, Server and UDP stamp when the data arrives
  -- within a millisecond or two.
- **Tag changes** are stamped when the poll that saw them returns. The real
  change happened some time since the poll before, so a tag-to-message
  measurement can read *short* by up to that gap. Each change records the gap
  in the log (`window_ms`), and the **Latency** page says how big it was next
  to the results. At the default 100 ms, treat results as up to 100 ms longer
  than shown; for tighter numbers lower *Poll every*, or watch both sides on
  the network instead of polling.
- All the tags are read **in one request** where the PLC allows it (Rockwell
  with either driver), so a longer tag list costs little. Siemens and
  Mitsubishi tags are still read one after another; each is stamped as its own
  answer comes back, but a long list makes every poll -- and so the window --
  longer.
- *Changes* only catches what a poll sees: a bit that goes on and off again
  between two polls is never logged. If it matters, have the PLC latch it or
  count it, and monitor the latch or counter.
- Everything uses one PC clock, so comparing tags with messages is exact
  in that sense. Comparing logs from **two different PCs** is only as good as
  their clocks agree.

---

## Your first measurement, step by step

You need: the PLC's IP address and type, the tag that starts the event, and
the IP address (and ideally port) of the system that answers. This PC must be
on the same network.

1. **Tags page** (`Ctrl+1`). Pick the **PLC family**, type the **IP address**
   (Rockwell: also the controller's **CPU slot**). Type a tag name in the box
   above the table and press **Add**. Press **Connect** (`F5`). The *PLC* chip
   in the title bar turns green and the value appears; toggle the tag at the
   machine and watch it flash.
2. **Socket page** (`Ctrl+2`). Choose **Passive sniff**, pick the network
   adapter on the machine network, put the other system's address in
   **Filter host** (and its port in **Filter port** if you know it), and this
   PC's address in **Our IP**. Press **Start** (`F6`). If you have not
   installed Npcap or are not running as administrator, the page says so and
   offers **Restart as administrator**.
3. **Live log** (`Ctrl+3`). Make the event happen. You should see a green
   *PLC* row followed by a cyan *received* row. If only one side appears, see
   *Troubleshooting* below.
4. **Latency** (`Ctrl+5`). Under *Start the clock when*, choose the tag and,
   if only one direction matters, put the value in *Changes to* (`True`).
   Under *Stop it at the next*, choose a received message (narrow it with
   *Containing* if the device sends other traffic). Press **Measure**.
   **Export CSV** gives every pair for a report.
5. Want it to keep measuring after you leave? Go to **Service** (`Ctrl+7`),
   turn on **Unattended** on the Tags and Socket pages, then **Install** and
   **Start**. Come back later and run step 4 on the log.

`File > Save config` (`Ctrl+S`) keeps everything for next time, in the open
**profile** (see below); the service runs a saved profile too.

---

## Install

Run `PLCMonitor-Setup-<version>.exe`. It installs for the current user, with no
administrator prompt, and checks GitHub for updates when it starts (silently
if the PC has no internet).

- **Passive sniff** needs [Npcap](https://npcap.com) (free; tick *WinPcap
  API-compatible mode*) and PLC Monitor run as administrator. The Socket page
  has a *Restart as administrator* button.
- **libplctag** needs `plctag.dll`: press **Download** on the Tags page.

**Updates** download in the background and install when you close the window.
The installer waits for every running copy of PLC Monitor to close first --
including the Windows service, so stop the service on the Service page before
closing the window, and start it again afterwards. If PLC Monitor ever will
not start after an update ("Failed to start embedded python interpreter!"),
download the installer from the
[releases page](https://github.com/Robbuie/plc-monitor/releases) and run it
with PLC Monitor and its service stopped; your profiles and logs are not in
the program folder and are not touched.

The profiles and the logs live in `C:\ProgramData\PLC Monitor\`, shared by
the window and the service. Uninstalling leaves them there.

---

## The window

A rail down the left groups the pages: **Monitor** (Tags, Socket, Live log),
**Analyse** (Trend, Latency) and **Set up** (Network, Service). The title bar
holds the menus and two chips -- *PLC* and *Socket* -- that turn green while
they are running, and a third while the service is logging in the background.

Keyboard: `F5` connect the PLC, `F6` start the socket monitor, `Ctrl+.` stop
both, `Ctrl+1`...`Ctrl+7` jump to a page, `Ctrl+S` save the config, `Ctrl+P`
switch profile, `Ctrl+Shift+N` new profile, `F1` this guide.

### Profiles

A **profile** is one saved setup -- PLC, tags, socket settings, log options --
for one site, line or machine: *Acme Toledo / Press PLC*, *Acme Toledo /
Conveyor*, *Dayton / Robot cell 4*. The open one's name is in the title bar
beside the menus; click it to switch. The list is grouped by site, with the
profile the service runs marked *service*.

- **New profile...** starts from the defaults (the site is filled in from the
  open one). **Duplicate this profile...** copies everything -- PLC, tags,
  socket -- under a new name, the quick way to set up a second cell like the
  first. **Rename or edit details...** changes the name, site, line or cell,
  and the **notes**: anything worth knowing next time (the mirrored switch
  port, the camera's address, who to call). The notes show when you hover
  the name.
- **Each profile logs into its own folder** under `logs\`, named after it,
  so Live log, Trend and Latency only ever see that profile's events. Older
  logs are still there to open with **Open file...**.
- Switching stops the PLC and socket monitor first (it asks), and asks
  whether to save changes to the profile you are leaving.
- **Export this profile...** writes a `.plcmon` file to email or copy to
  another PC; **Import profile...** reads one (or any old
  `plc_monitor_config.json`). An export leaves out what belongs to this PC
  -- the network adapter, this PC's own address, the log folder -- and turns
  *Unattended* off; set those on the other PC.
- **File > Profiles > Switch profile...** (`Ctrl+P`) lists every profile with
  a search box, for when there are more than fit a menu.
- From a shortcut or a script: `PLCMonitor.exe --profile "Acme Toledo/Press
  PLC"` opens that profile; `PLCMonitor.exe headless --profile ...` runs it
  without a window.

Profiles are files in `C:\ProgramData\PLC Monitor\profiles\`. The first
time 1.4 starts, the configuration from earlier versions becomes the profile
*Default*, with its log where it was, and an installed service carries on
running it.

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

Add tags in the box above the table (or with **Browse PLC...** or
**Import...**, below), then **Connect**. Each value updates on every poll and
flashes when it changes; *Changes* counts them. Every change is written to the
log with its old and new value. Double-click a tag to rename it. Each family
keeps its own tag list, so switching back and forth loses nothing.

**Selecting tags.** Click a tag to select it (Ctrl or Shift for several). The
selected row gets an accent bar down its left edge and its name in bold, and
the bar above the table names what is selected -- *Selected: Oven_Temp*, or
*3 tags selected* -- with **Edit...** and **Remove** beside it. Those act on
the selection only, never on the text in the box. **Delete** removes the
selection, **Esc** clears it, and a right-click on a row offers the same.

**Grouped tags: text and numbers that span registers.** A PLC often keeps
one value in several registers: a VIN as ASCII text two characters per
register (`D1916`-`D1919` = `A1234567`), a 32-bit count in two, a BCD
number. Logged one register at a time, those come out as meaningless
numbers (12338, 12854 ...) on separate rows. Instead:

- type `D1916 x4 ascii` in the box (or `D200 int32`, `D300 x2 bcd`,
  `DB10,WORD20 x4 ascii`, `Data[16] x4 ascii`) and **Add**; or
- select several consecutive registers already in the list and press
  **Combine...** on the selection bar; or
- select a tag and press **Edit...** (or double-click its *Read as*) to give
  it a **name** -- *Plant VIN* -- and choose how it is read: Text (ASCII),
  INT16, UINT16, INT32, UINT32, REAL, BCD or Hex, with byte and word order
  switches for PLCs that store them the other way round.

A grouped tag's registers are read **in one request**, so they all come from
the same PLC scan: four separate reads can catch a VIN half-written and log
`A1NKNOWN`. The log gets one row per change, under the tag's name, with the
decoded value (`0UNKNOWN` -> `A1234567`) and, in *notes*, which registers it
came from. Mitsubishi (D, W, R, ZR, SD...), Siemens (a run of words in a DB)
and Rockwell (INT array elements, both drivers) can be grouped.

**Logs from before a group existed** are decoded too: Trend and Latency
apply the open profile's grouped tags to a log that has the registers one
per row, taking the rows of each poll together. Set up *Plant VIN* once and
last week's log reads as VINs. Reading the registers separately sometimes
caught the PLC half way through rewriting them (`T320NOWN`, or the digits
`320`); a value replaced within a second is taken as such a torn read and
folded into the change it was part of, and that row's *notes* say what it
passed through. A value the old log only ever caught in part -- a send held
for a single poll, read half before and half after it was cleared -- cannot
be rebuilt and shows as the part that was read. Remove the single registers (D1916...D1919) from the tag
list once the group is set up: the group reads them in one request, which is
what keeps new logs free of torn values.

**Browse PLC...** (Rockwell only) reads the tag list from the controller
itself and lets you tick the tags to watch -- no project file needed. It uses
the address, slot or path typed on the left, on a connection of its own, so it
works whether or not the page is polling. The tree is laid out as Studio 5000
shows it: *Controller tags*, then each *Program:*, then *I/O modules*. A UDT,
a TIMER or a module's `:I` data opens into its members, an array into its
elements (a long one in pages of 200), down to single values -- tick
`Station[2].Cycle.ACC` or `Robot_Faults[17]` and **Add**. Search finds a tag or
member by name or data type anywhere in the tree; ticks are kept while you
search. Tags already in the list show ticked and greyed. A Logix STRING is one
value, read as text.

- **pycomm3** (ControlLogix, CompactLogix) uploads the tag database the same
  way it does on Connect; a big program takes a few seconds.
- **libplctag** reads the controller's `@tags` listings and structure
  templates. Works with ControlLogix, CompactLogix and Micro800. PLC-5, SLC 500
  and MicroLogix have no named tags to list -- type their file addresses
  (`N7:0`, `B3:0/1`) as before.

Browsing only reads: the controller is asked to describe its tags, nothing is
written. Descriptions are not something a controller reports; import an L5X
for those.

**Import...** fills the list from a file instead of typing:

- a **GX Works3 project** (`.gx3`): the global labels on devices, named as in
  the project with their comments. Word arrays come in one element per row
  (`uSendData[0]`), plus ready-made grouped tags where the element comments
  show that consecutive registers hold one thing -- *vin digit 0/1, vin digit
  2/3 ...* becomes **uSendData VIN**, `D1000 x4` as text;
- a Studio 5000 **L5X** export or **tag CSV** (*Tools > Export > Tags and
  Logic Comments*), with program tags as `Program:<name>.<tag>`;
- any CSV or text list with the tag names in a column headed *Tag*, *Name*,
  *Address* or *Device* (or one per line).

A list opens with nothing ticked: search it, tick the tags you want,
**Add**. UDTs and arrays are hidden unless you ask for them -- the log needs
one value per tag.

If the PLC stops answering altogether -- power-cycled, cable pulled, switch
rebooted -- after a few seconds the *PLC* chip turns amber (*reconnecting*)
and PLC Monitor opens a new connection by itself, retrying every few seconds
until a tag answers again; the log records when it was lost and when it came
back. A tag that changed during the outage is logged with a window covering
the whole outage. Tags that are simply wrong (a typo) do not trigger this.

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
PLC change, cyan received, amber sent, red error). The table holds the last few
thousand rows; the CSV has all of them.

**Open file...** (or double-click an archive) shows a whole log file here
instead -- yesterday's, or one from another PC -- with grouped tags decoded, so
a VIN reads as `A1234567`, not as four register numbers. A banner says which
file is showing; **Back to live** returns to the live log.

Two ways to look for something:

- **Filter** (the box beside the event kinds) hides every row that does not
  contain the text: just the rows for one tag, or one vehicle.
- **Find** (the box under it) keeps every row and **jumps** to each one
  containing the text, centred, with the events either side of it in view.
  Enter or the arrows step to the next and previous match; it says *3 of 12*
  and the time. Type a vehicle number -- `1234567`, or part of one -- to see
  what the PLC was doing when that vehicle went through.

**Log files** shows where the CSV is and handles rotation: at a size limit
(default 50 MB), at midnight, or now. Rotated files are compressed to
`.csv.gz` (10-20 times smaller) and the oldest is deleted past the count you
keep. **Folder** opens the log folder -- in File Manager when it is installed.

### Trend

Tag values over time from the live log, an archive, or any PLC Monitor log
opened with **Open file...** (a log from another PC works). A tag that only
ever holds 0 and 1 -- a bit, a True/False -- is drawn as a **lane**, like a
logic analyser: a thin strip, filled while it is on, so a dozen handshake bits
fit on one screen and their edges line up by eye. **Text tags** -- a VIN, a model, a status word; marked *text* in the
list -- are drawn as a strip under the charts: one span per value, labelled
where there is room (zoom in to label more), so you can read which vehicle
was in the station while a count or a bit changed above it.

**Find a value** (top right of the chart) marks every place a value appears,
on the strips and on the number charts, with a thin line through all of
them. The arrows step from one to the next and zoom to it; the Live log
button beside them opens the same log in **Live log** at that row, with the
events either side (the up and down arrows step; the left and right arrows
beside Home are the view history, below).

The drop-down in the chart's header arranges them: **Chart per tag** gives each
tag its own chart on a shared time axis, each scaled to its own values, so tags
with very different ranges stay readable. **Same scale** keeps a chart per tag
but puts them all on one value scale, so their sizes compare. **One chart**
draws them together. Up to eight lines; each keeps its colour for the session.

**Reading the chart.** Each value is drawn as a level from the moment it was
logged until the next change -- the log records changes, so that is what the
signal really did. Move the pointer over the charts: a line runs through all of
them at that moment, and a readout gives the date and time to the millisecond
and the value each line *held* then (on a text strip, the value and when it
began and ended). When there are more changes in view than pixels across, each
pixel column is drawn from the lowest to the highest value in it, so a spike of
a few milliseconds still shows on a day-long chart; zoom in to see it as steps.

**Moving around.** The mouse moves time only; each chart's value axis follows
what is in view. The wheel zooms in and out around the pointer, a drag moves
along the time axis, and the magnifier button switches the drag to drawing a
box to zoom to. **Home** goes back to the whole range. The **left and right
arrows** beside it step back and forward through the views you have looked at,
like a browser's -- zoom in on a fault, look around, and step back out to where
you were. Zooming stops at a few milliseconds either side of a change.

**Cursors.** Press **Cursors** for two cursors, **A** and **B**, across every
chart, with the time between them shown above the value bar (*B - A 212 ms*).
Drag either one, or click a chart to move the nearer one there. A cursor
dropped within a few pixels of a change **snaps** to it -- the change of any
line or strip, on any chart -- so the time from a PLC bit rising to a robot
register changing, or from one VIN to the next, is read exactly rather than by
eye. Hold **Shift** to place a cursor without snapping.

**The value bar** under the chart has a row per tag: the value at the pointer
(*Latest* when the pointer is elsewhere); with the cursors on, the value at A,
at B and the difference; and the lowest, highest and average value and how many
times it changed -- over what is in view, or between A and B when the cursors
are on. The average is weighted by time: a value held for an hour counts for an
hour, not as one row of the log. Drag the handle above the bar to give the chart
more room.

**A tag's settings.** Double-click a tag in the value bar for its
**description**, **units** (shown beside its name, on the readout and in the
export), a **fixed value scale** (say 0 to 1500, so the chart does not rescale
as you scroll), and whether a 0/1 tag is drawn as a lane or a chart. These are
saved in the profile, so they travel with it when it is exported to a
`.plcmon` and are there for anyone who opens it.

**Layouts.** **Layouts** saves the ticked tags and robot signals, the
arrangement, the range and whether the cursors are on, under a name --
*Press handshake*, *Oven zone 2* -- in the profile. Pick a layout to tick it
all again and plot. Tags a layout names that are not in the chosen log are
listed under the tags rather than silently dropped.

**Export.** *Chart as PNG* saves the picture at twice the screen's
resolution. *Data in view as CSV* (or *between the cursors*) saves each tag's
value at the start of the range, then every change in it, with the time to the
millisecond and the units -- enough to rebuild exactly what the chart showed,
in Excel or anything else.

**Events.** The **Events** card under the robots adds a strip under the charts
with the log's other events on the same time axis: **Socket messages** --
*Received* and *Sent* messages, and *Status* (connections, disconnections and
errors, errors in red) -- and **Latency pairs**: whatever the **Latency** page
last measured, drawn as a bar from each trigger to its response, labelled with
the time where it is wide enough, with a red mark for a trigger that got no
answer. So *Part_Present rose, the MES answered 212 ms later* sits right under
the bit that started it. Hover the strip for what each marker is; click one to
open that row in the **Live log**. Cursors snap to events too. (Pairs are
worked out when the chart is plotted; plot again to take in new ones.)

**Flags.** In a tag's settings (double-click it in the value bar) give it a
**high** or **low limit**, a time after which **no change** is suspicious, or,
for a counter, the largest **step** it should ever take. The chart then points
them out: time over or under a limit shaded red with the limit as a dashed
line, a stuck period shaded amber from the moment it went on too long, and a
mark wherever a counter jumped by more than its step (missed pulses) or went
backwards. The value bar's **Flags** column counts them in view (hover for the
list); click the count to go to the next one. In Live they are worked out
again every second.

**Math pens.** *New math pen...* under the tag list makes a pen worked out from
others: **A minus B** (or plus, times, divided by) -- the gap between a
setpoint and the actual, two stations' counts; a counter's **rate** per second
or minute over a window -- parts per minute from a part count; or **how long
each state lasted** -- how long *Ready* was high each time, a pulse width, a
dwell time. It is listed with the tags (marked *math*), ticked like one, and
drawn, measured, flagged and exported like one; its tags are read even when
they are not ticked. Double-click it in the list to change or delete it. Math
pens are saved in the profile.

**Compare.** **Compare** draws the same tags from another time as dashed lines
in their own colours on this chart: the same time **a day** or **a week
earlier**, **another log** of the same time (one opened from another PC), or
**from a moment you choose**, lined up with the chart's left edge. The readout
gives both values. Robot lines are not compared. **Compare** again to stop.

**Cycles.** **Cycles** (beside **Time** at the top of the chart) lays every
machine cycle over the others. Choose what starts a cycle -- a tag that
**rises**, **falls**, **changes**, or **changes to** a value (a text tag: a VIN
that changes, or changes to one value) -- and a window before and after it, and
press **Overlay**. Each plotted tag is then drawn once per cycle on a time axis
counted from the start of the cycle, so a hundred cycles sit on top of each
other: where they agree the lines pile into one, and a cycle that ran late or
did something else stands out. The shaded band is the envelope -- where most
cycles were at each moment (5th to 95th percentile; with fewer than ten
cycles, lowest to highest) -- with the median dashed. The list beside it ranks
the cycles by how far they strayed outside the band, worst first; click one to
pick it out in the accent colour, double-click (or *Show in Time*) to see it on
the time chart. Untick a known-bad cycle's **Reference** box to leave it out of
the band. A cycle the log ends in the middle of is listed as *incomplete* and
left out. An oscilloscope's trigger, but over every cycle in the log at once.

**Live.** Turn on **Live** to follow the PLC: the chart reads the history for
the range from the live log once, then takes each change as the monitor logs
it, keeping now at the right edge -- *Last 5 minutes* becomes a five-minute
window scrolling with the clock. Robot lines are read again every 5 seconds.
Drag or zoom and the scrolling stops, so the moment you are looking at stays
put (the line under the chart says so); **Home** follows now again. Live reads
the live log, so picking an archive turns it off. A tag ticked for Live that
has not changed yet gets its chart anyway, and fills in at its first change.

**Robots.** When [RobControl](https://github.com/Robbuie/RobControl) (the
FANUC robot tool from the same family) is installed on this PC and recording,
the **Robots** card under the tag list shows every signal it records --
registers, I/O, system variables -- named *robot / address* (`R2-14 / R[2]
Weld count`). Tick them like tags: robot signals and PLC tags share the eight
lines and the time axis, so a weld count or a cycle-time register can be read
against the PLC tags that drive it.

- PLC Monitor only reads RobControl's recording (read-only); it never talks to a
  robot. Start and stop recording, and choose what to record, in RobControl's
  Trends tab.
- **Open...** on the card reads a `robcontrol.db` from another PC. Logs from two
  PCs only line up as well as the two PCs' clocks agree.
- RobControl reads a robot every 2 seconds at the fastest, so robot lines are
  good for trends, not for millisecond timing. For robot I/O that the PLC
  already exchanges with the robot (over EtherNet/IP, for example), monitor the
  PLC tag instead: it is logged at the full poll rate and works on the Latency
  page.
- On a PC without RobControl the card is a single note and can be ignored.

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
not the earlier one.

**Match the same value** pairs by content instead: each trigger waits for the
first response that **carries the value it changed to** -- a VIN arriving in
*Plant VIN* and leaving in *Sent VIN*, or in a socket message whose text holds
it. Several vehicles can be in flight at once and each is timed on its own.
Put `!0UNKNOWN` in *Changes to* to skip a placeholder value, and raise *Give
up after* to longer than a vehicle takes to get there. (*Changes to* takes
`!value` for "anything but" in every mode.)

Under the results, a sentence says how long after
happening the tag changes were seen (the poll window): each latency can be up
to that much longer than shown. **Export CSV** saves every pair, with each
trigger's window, for a report.

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

The service runs one **profile**, chosen under *What it will run* -- the one
open in the window when it was installed, until you pick another. It can
differ from the profile open in the window, and the page says when it does.
A new choice takes effect when the service is next started. The service's
profile cannot be deleted while it is chosen.

*What it will run* says exactly what the service will do with that profile.
If the PLC does not answer at boot the service keeps retrying every 15 seconds,
and if it goes away later it reconnects by itself, the same as the window.
Its own messages go to `service.log` beside the event log. **Run headless here**
runs the profile open in the window, in a console window instead.

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
| `tag_or_addr` for a named or grouped tag | Its name (*Plant VIN*); the registers and format are in `notes` |
| `window_ms` | For a tag change: ms since the previous read of that tag. The change happened inside this window. (Added in 1.2; a log begun by an older version is archived and a new one started.) |

## Tag formats

**Rockwell:** `Motor_Running`, `Program:Main.Counter`, `MyUDT.Member`, `MyArray[0]`

**Siemens:** `DB10,X0.0` (bit), `DB10,REAL4`, `DB10,DINT4`, `M0.0`, `Q0.0`, `I0.0`

**Mitsubishi:** `D100`, `D100.5` (bit 5 of D100), `M200`, `X0`, `Y0`, `B0`,
`TN0`/`TC0` (timer value/contact), `CN0`/`CC0` (counter value/contact)

**Grouped (any family):** an address, `xN` registers, and a format --
`D1916 x4 ascii`, `D200 int32` (two registers), `D300 x2 bcd`,
`DB10,WORD20 x4 ascii`, `Data[16] x4 ascii`, `D1916 x4 digits`. Formats:
`ascii` (or `text`), `digits` (the number written in the text: `A1234567`
reads as 1234567, a placeholder like `0UNKNOWN` as no value -- so a vehicle
number can be plotted and found as a number),
`int16`, `uint16`, `int32` (`dint`), `uint32`, `float32` (`real`), `bcd`,
`hex`. **Edit...** sets the name and the byte/word order.

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


**Latency results come out in steps of the poll interval.** That is the
polling showing through (see *How accurate is it?*). Lower *Poll every* on the
Tags page, or shorten the tag list.

**Many triggers show "no response".** Is *Give up after* long enough for the
slowest real answer? Is the *Stop it at the next* side too narrow (wrong
*Monitor*, wrong *Containing* text, or *Direction* set to Received when the
answer was logged as Sent)? Try *Direction: Either* and a blank *Containing*
first, then narrow it down.

**Trend shows a tag as missing.** The list holds the tags that changed in the
chosen file; a tag that has never changed since the log began is not in it yet.
Text values are listed with *text* beside them and drawn as a strip.

---

## Building from source

Python 3.11+ on Windows; the release is built with 3.13. (Before 3.13,
Python's clock on Windows only ticks every 15.6 ms; PLC Monitor reads the
precise Windows clock itself when it has to, so either works.)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app                  # the window
python -m app headless         # the service's profile, in this console
python -m app --profile "Site/Name"   # open a given profile
pytest                         # tests
python tools/preview.py        # render every page to PNG, offscreen
build.bat                      # dist\PLCMonitor\ and, with Inno Setup, the installer
```

A release is a tag push: bump `__version__` in `app/__init__.py` and
`pyproject.toml`, add a CHANGELOG.md entry, `git tag v1.0.1 && git push --follow-tags`.

---

## Changelog

### 1.8.0 -- 2026-11-17

Pick tags straight from the PLC, and see what you have selected.

- **Browse PLC...** on the Tags page (Rockwell): reads the controller's tag list
  and shows it as a tree -- controller tags, programs, I/O modules -- where UDTs,
  timers and arrays open down to the single values to tick. Search reaches
  members inside structures. Read-only; works with pycomm3 and libplctag.
- **A clear selection**: the selected tag is marked with an accent bar and bold
  name, and a bar above the table names it with **Edit...** and **Remove**
  beside it. Delete removes, Esc clears, right-click works too. Edit and Remove
  no longer sit beside Add, where it was not clear what they acted on.

### 1.7.0 -- 2026-11-16

A faster Trend chart, and Live.

- **Trend is drawn with pyqtgraph**: panning and zooming a day of changes is
  smooth, where the whole chart was redrawn before.
- **Spikes are never lost**: with more changes in view than pixels, each pixel
  column is drawn from the lowest to the highest value in it, so a few
  milliseconds' blip shows on a day-long chart.
- **Live** follows the PLC: changes go onto the chart as they are logged, with
  now at the right edge, instead of the file being read again every 30
  seconds. Dragging or zooming stops the scrolling; Home follows again.
- **Readout**: the pointer draws a line through every chart and gives the
  value each tag held at that moment, to the millisecond.
- The wheel and drags move time only; each value axis fits what is in view.
- **Cursors A and B** measure between two moments, snapping to the nearest
  change of any line so the time between two edges is exact (Shift places
  one freely).
- **Value bar** under the chart: each tag's value at the pointer, at A and B
  and the difference, and its min, max, time-weighted average and number of
  changes in view or between the cursors.
- **Bits as lanes**: a 0/1 tag is drawn as a thin logic-analyser strip.
- **Same scale** puts every chart on one value scale; a tag can have a fixed
  scale, units and a description (double-click it in the value bar).
- **Back and forward** step through the views you have looked at.
- **Layouts** save sets of tags, arrangement and range in the profile.
- **Export** the data in view (or between the cursors) as CSV.
- **Events** under the charts: socket messages, connections and errors, and the
  Latency page's pairs as bars from trigger to response. Click one to open it
  in the Live log.
- **Flags**: high and low limits, no change for too long, and counter jumps or
  resets, shaded and marked on the chart and counted in the value bar.
- **Math pens**: A minus B (and plus, times, divided by), a counter's rate, how
  long each state lasted.
- **Compare** with the same time yesterday, last week, or another log.
- **Cycles**: every cycle overlaid from a trigger, with the envelope of the
  usual ones and the odd cycles ranked first.

### 1.6.2 -- 2026-11-10

- Older logs decode without torn values: a VIN or vehicle number read half
  old, half new (`320`, `7118`) is folded into the change it was part of,
  with a note saying what it passed through. A first value of nothing (the
  digits of `0UNKNOWN`) no longer makes a blank-to-blank row.

### 1.6.1 -- 2026-11-10

- Fixes the release build: the 1.5.2 and 1.6.0 installers were never built,
  because a comment in the installer script began with a section name and
  Inno Setup stopped on it. 1.6.1 carries everything from 1.5.2 and 1.6.0.

### 1.6.0 -- 2026-11-10

Finding a vehicle in a log.

- **Live log opens files**: any log (or an archive, double-clicked) shows in
  the table with grouped tags decoded. **Find** jumps to each row containing
  a value -- a VIN or part of one -- centred, with the events either side;
  the Filter box still hides non-matching rows.
- **Trend shows text tags** as a strip of values under the charts, on the
  same time axis. Before, a tag grouped as text vanished from the Trend list.
- **Find on Trend** marks every place a value appears and steps through them;
  the Live log button opens that row in the log.
- New format **digits**: the number in a text value (`A1234567` -> 1234567),
  so the vehicle number can be plotted and searched as a number. The same
  registers can be set up as both the text and the number.
- Trend: a blank value breaks the line instead of drawing a slope to the next
  one; whole numbers show in full on the axis.
- Fixed: the Trend tag list could be emptied by a read of the previous
  source finishing after the new one.

### 1.5.2 -- 2026-11-10

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

### 1.5.1 -- 2026-11-10

- Fixed: the Mitsubishi simulator in `tools/` crashed at start on Windows
  when its output went to a file or pipe (a character the Windows code page
  cannot show), which failed the 1.5.0 release tests. The test now says why
  if the simulator ever exits early, and runs it with the Windows code page
  on every platform so this is caught before a release.

### 1.5.0 -- 2026-11-10

Values that span registers, read as what they are.

- **Grouped tags**: several consecutive registers read in one request and
  decoded as text (a VIN), INT16/UINT16, INT32/UINT32, REAL, BCD or hex,
  with byte and word order. Type `D1916 x4 ascii` in the Add box, or select
  registers and **Edit...** to combine them. One request means one PLC
  scan: no more half-written values.
- **Names**: any tag can be named (*Plant VIN*); the log, Trend and Latency
  use the name, and the notes column says which registers it came from.
- **Import from GX Works3** (`.gx3`): global labels with their comments, and
  grouped tags suggested from array element comments.
- **Latency matched by value**: pair each trigger with the response that
  carries the same value -- a VIN from arrival to send-out -- with several in
  flight at once. *Changes to* also takes `!value` (anything but).
- **Older logs decoded**: Trend and Latency apply the profile's grouped tags
  to logs that recorded the registers one per row.
- The Mitsubishi simulator in `tools/` now plays VINs through D1916 and
  D1000.

### 1.4.2 -- 2026-11-10

- Fixed: the Trend and Latency charts were missing from the installed build
  ("matplotlib is not available in this build ... No module named
  'unittest'"). The installer left out a standard module that matplotlib
  needs. A test now checks that nothing the app imports depends on a module
  the installer leaves out.

### 1.4.1 -- 2026-11-10

- Fixed: a UDP listener whose port was already taken said so only on the
  status line; the error now goes into the log, as it does for Proxy and
  Server. A Windows "port unreachable" report no longer stops the listener.
- Tests: the socket-monitor tests wait for the listener instead of sleeping
  a fixed time, and pick a port free for UDP, which failed the 1.4.0 build
  on a busy Windows runner.

### 1.4.0 -- 2026-11-09

Profiles: one saved setup per site, line or PLC.

- The open profile is named in the title bar; click it to switch. Profiles
  are grouped by site, carry a line or cell and free-text notes, and each logs
  into its own folder.
- New, Duplicate, Rename / edit details, Delete, and Manage profiles (a
  searchable list, `Ctrl+P`). Switching stops running monitors and asks about
  unsaved changes first.
- Export a profile to a `.plcmon` file and import it on another PC; the
  network adapter and this PC's address stay behind.
- The Service page chooses which profile the service runs. Saving the
  service's profile while it runs says to restart it.
- `--profile "Site/Name"` for the window and for `headless`.
- Tags: **Import...** reads a Studio 5000 L5X or tag CSV export, or any CSV
  or text list, into a searchable list to tick from.
- The configuration from earlier versions becomes the profile *Default*;
  an installed service carries on running it.
- Fixed: a setting changed in one config could change the defaults for any
  config created later in the same session.

### 1.3.0 -- 2026-11-09

Runs unattended for weeks.

- The PLC is reconnected by itself when it stops answering mid-run (power
  cycle, cable, switch). Before, the window and the service went on logging
  read errors until someone restarted them. The *PLC* chip shows
  *reconnecting* meanwhile, and Disconnect still works.
- The log is written on its own thread. In Proxy mode each message used to
  wait for the file to be opened, written and closed before it was passed
  on, so the monitor added its own delay to what it was measuring. A disk
  that refuses a write (full, locked) is retried instead of losing the rows.
- Fixed: two log rotations in the same second overwrote the first archive.
- A PLC driver error that is raised rather than reported no longer stops
  polling silently.

### 1.2.0 -- 2026-11-09

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
- Each tag change records its poll window (`window_ms`). The Latency page
  says how much longer each latency could be, and the export includes it.
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

### 1.1.0 -- 2026-10-05

- Trend: a **Robots** card shows the signals RobControl records (registers,
  I/O, system variables) and plots them on the same time axis as PLC tags. Read
  from RobControl's database, read-only; **Open...** reads one from another PC.
- User guide rewritten for someone new to the tool: what it is for, a worked
  example, how it works, how accurate the timestamps are (and why the poll
  interval matters), a step-by-step first measurement, and more troubleshooting.

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
