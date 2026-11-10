"""Render every page of the real window offscreen, to PNG.

The one part of the look that can be checked without a screen: whether a theme
applied, whether a card collapsed, whether a label ran off the end. It says
nothing about whether the application behaves -- no PLC answers here.

    python tools/preview.py --out-dir previews
    python tools/preview.py --themes dark,light,control --pages tags,latency

Works against a throwaway data folder with a made-up log in it, so the trend,
latency and log pages have something on them and nothing real is touched.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def demo_log(path: str, minutes: int = 50) -> None:
    from app.engine.event_log import CSV_COLUMNS
    rnd = random.Random(7)
    now = time.time() - minutes * 60
    rows = []

    def add(t, event, source, tag="", old="", new="", notes=""):
        rows.append({"timestamp_ms": int(t * 1000),
                     "timestamp_human": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
                     + f".{int((t % 1) * 1000):03d}",
                     "event_type": event, "source": source, "tag_or_addr": tag,
                     "old_value": old, "new_value": new, "raw_hex": "", "notes": notes,
                     "window_ms": 100 if event == "PLC_CHANGED" else ""})

    plc = "Rockwell (pycomm3)@10.0.40.11"
    add(now, "PLC_CONNECTED", plc, notes="Polling started")
    add(now + 0.2, "INFO", "SOCKET", notes="Passive capture started | filter: host 10.0.40.50 and port 9000")
    speed, temp, trig, count = 1200.0, 41.0, False, 0
    t = now + 1
    while t < now + minutes * 60:
        t += rnd.uniform(4, 9)
        old = speed
        speed = max(0.0, speed + rnd.uniform(-60, 60))
        add(t, "PLC_CHANGED", plc, "Conveyor_Speed", f"{old:.1f}", f"{speed:.1f}")
        if rnd.random() < 0.5:
            o = temp
            temp += rnd.uniform(-0.4, 0.5)
            add(t + 0.05, "PLC_CHANGED", plc, "Oven_Temp", f"{o:.2f}", f"{temp:.2f}")
        if rnd.random() < 0.35:
            add(t + 0.1, "PLC_CHANGED", plc, "Part_Present", str(trig), str(not trig))
            trig = not trig
            if trig:
                count += 1
                add(t + 0.1 + rnd.uniform(0.012, 0.09) + (0.4 if rnd.random() < 0.05 else 0),
                    "SOCK_RECV", "SOCKET", "10.0.40.11:51022 > 10.0.40.50:9000",
                    notes=f"PART;{count:05d};OK")
        if rnd.random() < 0.02:
            add(t + 0.3, "PLC_ERROR", plc, "Robot_Fault", notes="Read error (x1): Tag doesn't exist")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        out = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        out.writeheader()
        out.writerows(rows)


def demo_robots(path: str, minutes: int = 50) -> None:
    """A RobControl database shaped like FleetSchema.cs, with two robots."""
    import sqlite3
    rnd = random.Random(11)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE Robot (Id INTEGER PRIMARY KEY, Name TEXT NOT NULL, Address TEXT NOT NULL);
        CREATE TABLE TrendSignal (Id INTEGER PRIMARY KEY, RobotId INTEGER NOT NULL,
                                  Address TEXT NOT NULL, Label TEXT, CreatedUtc TEXT NOT NULL);
        CREATE TABLE TrendSample (SignalId INTEGER NOT NULL, UtcMs INTEGER NOT NULL,
                                  Value REAL NOT NULL);
    """)
    db.execute("INSERT INTO Robot VALUES (1, 'R2-14', '10.20.1.54'), (2, 'R2-15', '10.20.1.55')")
    db.execute("INSERT INTO TrendSignal VALUES (1, 1, 'R[2]', 'Weld count', ''),"
               " (2, 1, 'DI[1]', 'Part in nest', ''), (3, 2, '$TIMER[1].$TIMER_VAL', '', ''),"
               " (4, 2, 'R[5]', 'Cycle time', '')")
    start = int((time.time() - minutes * 60) * 1000)
    rows, welds, nest = [], 18200, 0
    for ms in range(start, start + minutes * 60_000, 2000):
        if rnd.random() < 0.12:
            welds += rnd.randint(4, 9)
            rows.append((1, ms, welds))
        if rnd.random() < 0.2:
            nest = 1 - nest
            rows.append((2, ms, nest))
        if rnd.random() < 0.06:
            rows.append((4, ms, round(rnd.uniform(38, 46), 1)))
    db.executemany("INSERT INTO TrendSample VALUES (?, ?, ?)", rows)
    db.commit()
    db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="previews")
    parser.add_argument("--themes", default="dark")
    parser.add_argument("--accent", default="blue")
    parser.add_argument("--density", default="normal")
    parser.add_argument("--pages", default="")
    parser.add_argument("--size", default="1440x900")
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    data = tempfile.mkdtemp(prefix="plcmon-preview-")
    os.environ["PLC_MONITOR_DATA"] = data
    os.environ["LOCALAPPDATA"] = data
    os.makedirs(os.path.join(data, "logs"), exist_ok=True)
    demo_log(os.path.join(data, "logs", "plc_monitor_log.csv"))
    os.makedirs(os.path.join(data, "RobControl"), exist_ok=True)
    demo_robots(os.path.join(data, "RobControl", "robcontrol.db"))

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    from app.core.settings import Settings
    from app.engine import logdata
    from app.ui.session import Session
    from app.ui.window import MainWindow

    settings = Settings(os.path.join(data, "ui.json"))
    settings.set("accent", args.accent)
    settings.set("density", args.density)
    session = Session()
    session.cfg.set("plc", "ip", "10.0.40.11")
    session.cfg.set("plc", "tags_per_driver", {"Rockwell (pycomm3)": [
        {"tag": t, "dtype": "DINT"} for t in
        ("Conveyor_Speed", "Oven_Temp", "Part_Present", "Program:Main.Cycle_Count", "Robot_Fault")]})
    session.cfg.set("socket", "sniff", {"iface": "", "host": "10.0.40.50", "port": "9000",
                                        "proto": "tcp", "our_ip": "", "bpf": ""})
    window = MainWindow(session, settings)
    width, height = (int(x) for x in args.size.split("x"))
    window.resize(width, height)
    window.show()
    window.pages["tags"].load_from_config()

    rows = list(logdata.read_rows(session.logger.log_path))
    window.pages["log"].model.append(rows[-400:])
    tags = window.pages["tags"]
    for tag, value in (("Conveyor_Speed", "1187.4"), ("Oven_Temp", "41.82"), ("Part_Present", "True"),
                       ("Program:Main.Cycle_Count", "18422")):
        tags.model.update_value(tag, value, "")
        tags.model.update_value(tag, value + "0" if "." in value else value, time.strftime("%H:%M:%S.123"))
    tags.model.update_value("Robot_Fault", "ERROR: Tag doesn't exist", "")
    session.plcState.emit("on", "Rockwell (pycomm3) at 10.0.40.11")
    session.sockState.emit("on", "sniffing 10.0.40.50 9000")

    trend = window.pages["trend"]
    trend.range.setCurrentIndex(trend.range.count() - 2)
    app.processEvents()
    # Let the page's own read of the (empty) live log finish first, or it
    # lands after these and empties the list.
    settle = time.time() + 1.0
    while time.time() < settle:
        app.processEvents()
        time.sleep(0.02)
    trend._fill_tags(trend.source.currentData(), logdata.trend_tags(rows))
    from app.engine import robdata
    db = robdata.default_db_path()
    trend._fill_robots(db, robdata.signals(db), "")
    robots = [s for s in robdata.signals(db) if s.address in ("R[2]", "DI[1]")]
    for i in range(trend.robot_list.count()):
        item = trend.robot_list.item(i)
        if item.text() in [s.name for s in robots]:
            item.setCheckState(Qt.Checked)
    data = logdata.series(rows, ["Conveyor_Speed", "Part_Present"])
    data.update(robdata.series(db, robots))
    trend._render(data, ["Conveyor_Speed", "Part_Present"] + [s.name for s in robots],
                  "everything in the file")
    lat = window.pages["latency"]
    lat.trigger.set_choices(*logdata.sources(rows))
    lat.response.set_choices(*logdata.sources(rows))
    lat.trigger.tag.setCurrentText("Part_Present")
    lat.trigger.value.setText("True")
    trig, resp = lat.trigger.matcher(), lat.response.matcher()
    lat._show(logdata.measure(rows, trig, resp, window_ms=2000),
              f"{trig.describe()} to the next {resp.describe()}")

    os.makedirs(args.out_dir, exist_ok=True)
    pages = [p for p in args.pages.split(",") if p] or list(window.pages)
    for theme in args.themes.split(","):
        settings.set("theme", theme)
        window.apply_theme()
        for key in pages:
            window.show_page(key)
            for _ in range(5):
                app.processEvents()
            target = os.path.join(args.out_dir, f"{theme}-{key}.png")
            window.grab().save(target)
            print(target)
    session.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
