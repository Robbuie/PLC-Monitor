"""The logger writes on its own thread: rows still arrive in order, rotation
still happens, a failing disk loses nothing, and shutdown drains it."""

import os
import threading
import time

from app.engine import logdata
from app.engine.event_log import EventLogger


def rows(path):
    return list(logdata.read_rows(str(path)))


def test_rows_from_many_threads_all_arrive(tmp_path):
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log))

    def burst(n):
        for i in range(500):
            logger.log("INFO", f"T{n}", notes=str(i))

    threads = [threading.Thread(target=burst, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert logger.flush()
    got = rows(log)
    assert len(got) == 2000
    for n in range(4):
        mine = [int(r["notes"]) for r in got if r["source"] == f"T{n}"]
        assert mine == list(range(500))       # each thread's rows in its order


def test_log_does_not_wait_for_the_disk(tmp_path, monkeypatch):
    logger = EventLogger(str(tmp_path / "log.csv"))
    real = logger._write_rows

    def slow(batch):
        time.sleep(0.2)
        real(batch)

    monkeypatch.setattr(logger, "_write_rows", slow)
    started = time.perf_counter()
    for _ in range(20):
        logger.log("INFO", "x")
    assert time.perf_counter() - started < 0.1
    assert logger.flush()
    assert len(rows(tmp_path / "log.csv")) == 20


def test_size_rotation_happens_in_the_writer(tmp_path):
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log), max_bytes=2000)
    for i in range(200):
        logger.log("INFO", "x", notes="y" * 40)
    assert logger.flush()
    deadline = time.time() + 5
    while time.time() < deadline and any(
            f.endswith(".csv") and f != "log.csv" for f in os.listdir(tmp_path)):
        time.sleep(0.05)
    archived = sum(len(rows(a)) for a in logger.list_archives())
    assert archived + len(rows(log)) == 200
    assert logger.list_archives()


def test_a_write_failure_is_retried_not_lost(tmp_path, monkeypatch):
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log))
    real = logger._write_rows
    failures = [2]

    def flaky(batch):
        if failures[0]:
            failures[0] -= 1
            raise PermissionError("locked by another program")
        real(batch)

    monkeypatch.setattr(logger, "_write_rows", flaky)
    monkeypatch.setattr("app.engine.event_log.time.sleep", lambda s: None)
    logger.log("INFO", "x", notes="kept")
    assert logger.flush()
    assert [r["notes"] for r in rows(log)] == ["kept"]
    assert logger.write_error == ""


def test_close_drains_and_later_rows_still_land(tmp_path):
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log))
    for i in range(50):
        logger.log("INFO", "x", notes=str(i))
    logger.close()
    logger.close()
    logger.log("INFO", "x", notes="after")
    assert [r["notes"] for r in rows(log)][-1] == "after"
    assert len(rows(log)) == 51


def test_rotate_now_keeps_earlier_rows_in_the_archive(tmp_path):
    log = tmp_path / "log.csv"
    logger = EventLogger(str(log))
    logger.log("INFO", "x", notes="before")
    archive = logger.rotate_now()
    logger.log("INFO", "x", notes="after")
    assert logger.flush()
    deadline = time.time() + 5
    while time.time() < deadline and not os.path.exists(archive):
        time.sleep(0.05)
    time.sleep(0.1)
    assert [r["notes"] for r in rows(archive)] == ["before"]
    assert [r["notes"] for r in rows(log)] == ["after"]


def test_set_log_path_keeps_queued_rows_in_the_old_file(tmp_path):
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    logger = EventLogger(str(first))
    logger.log("INFO", "x", notes="one")
    logger.set_log_path(str(second))
    logger.log("INFO", "x", notes="two")
    assert logger.flush()
    assert [r["notes"] for r in rows(first)] == ["one"]
    assert [r["notes"] for r in rows(second)] == ["two"]
