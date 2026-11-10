"""Profiles in the window: switching reloads every page and the log, unsaved
edits are asked about, running monitors are stopped first, and the dialogs
create, rename and import. Plus the tag picker."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.engine import profiles  # noqa: E402
from app.engine.config_manager import ConfigManager  # noqa: E402


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, isolated_data, monkeypatch):
    from app.core.settings import Settings
    from app.ui.session import Session
    from app.ui.window import MainWindow
    profiles.ensure_default()
    a = profiles.create("Press", site="Toledo", line="L3")
    cfg = ConfigManager(a.path)
    cfg.set("plc", "ip", "10.0.40.11")
    cfg.set("plc", "tags_per_driver", {"Rockwell (pycomm3)": [{"tag": "Part_Present", "dtype": "BOOL"}]})
    cfg.set("socket", "sniff", {"host": "10.0.40.50", "port": "9000", "proto": "tcp",
                                "iface": "", "our_ip": "", "bpf": ""})
    cfg.save()
    b = profiles.create("Oven", site="Dayton")
    cfg = ConfigManager(b.path)
    cfg.set("plc", "ip", "192.168.9.9")
    cfg.set("plc", "type", "Siemens (snap7)")
    cfg.set("plc", "tags_per_driver", {"Siemens (snap7)": [{"tag": "DB1,INT0", "dtype": "DINT"}]})
    cfg.save()
    settings = Settings(os.path.join(isolated_data, "ui.json"))
    session = Session(a.path)
    win = MainWindow(session, settings)
    win.show()
    app.processEvents()
    win.ids = (a.id, b.id)
    yield win
    session.shutdown()
    win.close()
    # Delete it, not just close it: every window left alive re-polishes on
    # the next one's theme change, and by the tenth test that takes minutes.
    from PySide6.QtCore import QEvent
    win.deleteLater()
    session.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def answer(monkeypatch, text):
    """Make the next QMessageBox click the button labelled `text`."""
    def exec_(box):
        for button in box.buttons():
            if button.text() == text:
                box._clicked = button
                return 0
        raise AssertionError(f"no button {text!r} in {[b.text() for b in box.buttons()]}")
    monkeypatch.setattr(QMessageBox, "exec", exec_)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: getattr(box, "_clicked", None))


def test_switching_reloads_every_page_and_the_log(window, app):
    a, b = window.ids
    tags, sock = window.pages["tags"], window.pages["socket"]
    assert tags.ip.text() == "10.0.40.11"
    assert [e["tag"] for e in tags.model.entries()] == ["Part_Present"]
    assert sock.sniff_host.text() == "10.0.40.50"
    assert window.switch_profile(b)
    app.processEvents()
    assert tags.ip.text() == "192.168.9.9"
    assert tags.driver.currentText() == "Siemens (snap7)"
    assert [e["tag"] for e in tags.model.entries()] == ["DB1,INT0"]
    assert sock.sniff_host.text() == ""
    assert window.session.logger.log_path == ConfigManager(profiles.get(b).path).logging["log_path"]
    assert "Dayton / Oven" in window.titlebar.profile.text()
    assert window.windowTitle().startswith("Dayton / Oven")
    assert window.settings.get("profile") == b
    assert window.settings.get("profiles.recent")[0] == b


def test_unsaved_edits_are_asked_about(window, app, monkeypatch):
    a, b = window.ids
    window.pages["tags"].ip.setText("10.0.40.99")
    answer(monkeypatch, "Cancel")
    assert not window.switch_profile(b)
    assert window.session.profile.id == a
    answer(monkeypatch, "Save")
    assert window.switch_profile(b)
    assert ConfigManager(profiles.get(a).path).plc["ip"] == "10.0.40.99"


def test_dont_save_discards(window, app, monkeypatch):
    a, b = window.ids
    window.pages["tags"].ip.setText("10.0.40.77")
    answer(monkeypatch, "Don't save")
    assert window.switch_profile(b)
    assert ConfigManager(profiles.get(a).path).plc["ip"] == "10.0.40.11"
    assert window.switch_profile(a)
    assert window.pages["tags"].ip.text() == "10.0.40.11"


def test_no_question_when_nothing_changed(window, app, monkeypatch):
    def fail(*_):
        raise AssertionError("asked")
    monkeypatch.setattr(QMessageBox, "exec", fail)
    a, b = window.ids
    assert window.switch_profile(b)
    assert window.switch_profile(a)


def test_running_monitors_are_stopped_first(window, app, monkeypatch):
    a, b = window.ids
    session = window.session
    stopped = []
    monkeypatch.setattr(type(session), "plc_running", property(lambda s: not stopped))
    monkeypatch.setattr(session, "disconnect_plc", lambda: stopped.append("plc"))
    monkeypatch.setattr(session, "stop_socket", lambda: stopped.append("sock"))
    monkeypatch.setattr("app.ui.widgets.ask", lambda *a, **k: False)
    assert not window.switch_profile(b)
    assert stopped == []
    monkeypatch.setattr("app.ui.widgets.ask", lambda *a, **k: True)
    assert window.switch_profile(b)
    assert stopped == ["plc", "sock"]


def test_the_session_refuses_to_switch_while_running(window, monkeypatch):
    session = window.session
    monkeypatch.setattr(type(session), "sock_running", property(lambda s: True))
    with pytest.raises(RuntimeError):
        session.open_config(profiles.get(window.ids[1]).path)
    monkeypatch.undo()          # before teardown closes the window, which would ask


def test_rows_of_the_old_profile_stay_in_its_log(window, app):
    from app.engine import logdata
    a, b = window.ids
    session = window.session
    session.logger.log("INFO", "test", notes="in A")
    assert window.switch_profile(b)
    session.logger.log("INFO", "test", notes="in B")
    session.logger.flush()
    log_a = ConfigManager(profiles.get(a).path).logging["log_path"]
    log_b = ConfigManager(profiles.get(b).path).logging["log_path"]
    assert "in A" in [r["notes"] for r in logdata.read_rows(log_a)]
    assert [r["notes"] for r in logdata.read_rows(log_b)] == ["in B"]


def test_new_profile_dialog_creates_and_opens(window, app, monkeypatch):
    from app.ui import profiles_ui

    def run(dialog):
        dialog.name.setText("Packing")
        dialog.line.setText("L9")
        dialog._accept()
        return dialog.result()

    monkeypatch.setattr(profiles_ui.ProfileDetailsDialog, "exec", run)
    window.profile_action("new")
    made = profiles.find("Toledo/Packing")              # site defaults to the open one's
    assert made is not None and made.line == "L9"
    assert window.session.profile.id == made.id


def test_a_duplicate_name_keeps_the_dialog_open(window, app, monkeypatch):
    from app.ui import profiles_ui
    seen = {}

    def run(dialog):
        dialog.name.setText("Press")                     # Toledo / Press exists
        dialog._accept()
        seen["problem"] = dialog.problem.text()
        seen["visible"] = not dialog.problem.isHidden()
        return 0

    monkeypatch.setattr(profiles_ui.ProfileDetailsDialog, "exec", run)
    window.profile_action("new")
    assert seen["visible"] and "already" in seen["problem"]


def test_rename_updates_the_title_bar_and_survives_save(window, app, monkeypatch):
    from app.ui import profiles_ui

    def run(dialog):
        dialog.name.setText("Press 2")
        dialog._accept()
        return dialog.result()

    monkeypatch.setattr(profiles_ui.ProfileDetailsDialog, "exec", run)
    window.profile_action("edit")
    assert "Toledo / Press 2" in window.titlebar.profile.text()
    window.save_config()
    assert profiles.get(window.ids[0]).name == "Press 2"


def test_manage_dialog_lists_groups_and_filters(window, app):
    from app.ui.profiles_ui import ManageProfilesDialog
    dialog = ManageProfilesDialog(window.ids[0], window)
    groups = [dialog.tree.topLevelItem(i).text(0) for i in range(dialog.tree.topLevelItemCount())]
    assert groups == ["Dayton", "Toledo", "No site"]
    assert dialog.selected_id() == window.ids[0]
    assert not dialog.open_btn.isEnabled() and not dialog.delete_btn.isEnabled()
    dialog.search.setText("oven")
    assert dialog.tree.topLevelItemCount() == 1
    assert dialog.selected_id() == window.ids[1]
    assert dialog.open_btn.isEnabled()
    dialog.close()


def test_the_profile_menu_lists_sites_and_marks_the_open_one(window, app):
    button = window.titlebar.profile
    button.menu().aboutToShow.emit()
    texts = [a.text() for a in button.menu().actions() if a.text()]
    assert "DAYTON" in texts and "TOLEDO" in texts
    checked = [a.text() for a in button.menu().actions() if a.isChecked()]
    assert checked == ["Press   (L3)"]


def test_service_page_points_the_service_at_a_profile(window, app):
    page = window.pages["service"]
    window.show_page("service")
    box = page.profile_box
    b = window.ids[1]
    index = box.findData(b)
    box.setCurrentIndex(index)
    page._profile_picked(index)
    assert profiles.service_profile_id() == b
    assert "192.168.9.9" in page.plan_plc.text() or "not run" in page.plan_plc.text()
    assert page.cfg_path.text() == profiles.get(b).path
    assert "runs Dayton / Oven" in page.profile_note.text()


def test_tag_picker(app):
    from app.engine.tagimport import TagEntry
    from app.ui.tagpicker import TagPickerDialog
    entries = [TagEntry("Go", "BOOL"), TagEntry("Recipe", "RecipeUDT"),
               TagEntry("Speed", "REAL", "line speed"), TagEntry("Old", "DINT")]
    dialog = TagPickerDialog(entries, {"Old"}, "x.L5X")
    assert dialog.proxy.rowCount() == 3                  # the UDT hidden
    dialog.basic.setChecked(False)
    assert dialog.proxy.rowCount() == 4
    dialog.search.setText("speed")
    dialog._tick_shown(True)
    assert [e.tag for e in dialog.chosen()] == ["Speed"]
    dialog.search.setText("")
    dialog._tick_shown(True)
    assert [e.tag for e in dialog.chosen()] == ["Go", "Recipe", "Speed"]   # not "Old"
    assert dialog.add_btn.text() == "Add 3"
    dialog._tick_all(False)
    assert dialog.chosen() == [] and not dialog.add_btn.isEnabled()


def test_import_adds_the_ticked_tags(window, app, monkeypatch, tmp_path):
    from app.ui import tagpicker
    src = tmp_path / "tags.csv"
    src.write_text("Tag,Type\nSpeed,REAL\nPart_Present,BOOL\nCount,DINT\n", encoding="utf-8")
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                        lambda *a, **k: (str(src), ""))

    def run(dialog):
        dialog._tick_all(True)
        return 1

    monkeypatch.setattr(tagpicker.TagPickerDialog, "exec", run)
    tags = window.pages["tags"]
    tags._import()
    assert [e["tag"] for e in tags.model.entries()] == ["Part_Present", "Speed", "Count"]


def test_duplicate_and_export_take_what_is_on_screen_without_saving_it(window, app, monkeypatch,
                                                                       tmp_path):
    import json
    from app.ui import profiles_ui
    a, _b = window.ids
    window.pages["tags"].ip.setText("10.0.40.42")          # not saved

    def run(dialog):
        dialog.name.setText("Press copy")
        dialog._accept()
        return dialog.result()

    monkeypatch.setattr(profiles_ui.ProfileDetailsDialog, "exec", run)
    monkeypatch.setattr("app.ui.widgets.ask", lambda *a, **k: False)   # do not open it
    window.profile_action("duplicate")
    copy = profiles.find("Toledo/Press copy")
    assert ConfigManager(copy.path).plc["ip"] == "10.0.40.42"
    assert ConfigManager(profiles.get(a).path).plc["ip"] == "10.0.40.11"   # original untouched

    out = tmp_path / "x.plcmon"
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName",
                        lambda *a, **k: (str(out), ""))
    window.profile_action("export")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["plc"]["ip"] == "10.0.40.42" and data["profile"]["name"] == "Press"
    assert ConfigManager(profiles.get(a).path).plc["ip"] == "10.0.40.11"
    assert window.has_unsaved_changes()


def test_the_add_box_takes_a_group_and_edit_combines_registers(window, app, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel
    from app.ui import tagedit
    tags = window.pages["tags"]
    tags.new_tag.setText("D1000 x4 ascii")
    tags._add()
    added = tags.model.rows[-1]["spec"]
    assert (added.address, added.count, added.fmt) == ("D1000", 4, "ascii")
    assert tags.model.data(tags.model.index(len(tags.model.rows) - 1, 1)) == \
        "D1000-D1003 as Text (ASCII, 2 per register)"

    for t in ("D1916", "D1917", "D1918", "D1919"):
        tags.new_tag.setText(t)
        tags._add()
    first = len(tags.model.rows) - 4
    sel = tags.table.selectionModel()
    sel.clearSelection()
    for i in range(first, first + 4):
        sel.select(tags.model.index(i, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def run(dialog):
        assert (dialog.count.value(), dialog.fmt.currentData()) == (4, "ascii")
        dialog.name.setText("Plant VIN")
        return 1

    monkeypatch.setattr(tagedit.TagEditDialog, "exec", run)
    tags._edit()
    keys = [r["spec"].key for r in tags.model.rows]
    assert "Plant VIN" in keys and "D1917" not in keys
    window.save_config()
    saved = window.session.cfg.plc["tags_per_driver"]["Rockwell (pycomm3)"]
    assert {"tag": "D1916", "dtype": "DINT", "count": 4, "format": "ascii",
            "name": "Plant VIN"} in saved


def test_combining_refuses_registers_that_are_not_consecutive(window, app, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel
    tags = window.pages["tags"]
    for t in ("D10", "D12"):
        tags.new_tag.setText(t)
        tags._add()
    sel = tags.table.selectionModel()
    sel.clearSelection()
    for i in (len(tags.model.rows) - 2, len(tags.model.rows) - 1):
        sel.select(tags.model.index(i, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    told = []
    monkeypatch.setattr("app.ui.widgets.info", lambda *a, **k: told.append(a[2]))
    tags._edit()
    assert told and "consecutive" in told[0]


def test_renaming_a_group_in_the_table_sets_its_name(window, app):
    tags = window.pages["tags"]
    tags.new_tag.setText("D200 x2 int32")
    tags._add()
    row = len(tags.model.rows) - 1
    assert tags.model.setData(tags.model.index(row, 0), "Part count")
    spec = tags.model.rows[row]["spec"]
    assert (spec.name, spec.address) == ("Part count", "D200")
    assert tags.model.setData(tags.model.index(row, 0), "D200 x2")      # back to plain
    assert spec.name == ""


def test_tag_edit_dialog(app):
    from app.engine.tagspec import TagSpec
    from app.ui.tagedit import TagEditDialog
    dialog = TagEditDialog(TagSpec("D200"), "Edit", False)
    dialog.fmt.setCurrentIndex(dialog.fmt.findData("int32"))
    assert dialog.count.value() == 2                     # a 32-bit format needs two
    dialog.count.setValue(3)
    assert not dialog.ok.isEnabled() and "even number" in dialog.preview.text()
    dialog.count.setValue(2)
    dialog.swap_words.setChecked(True)
    spec = dialog.spec()
    assert (spec.count, spec.fmt, spec.swap_words) == (2, "int32", True)
    assert dialog.ok.isEnabled()
    assert not dialog.swap_bytes.isEnabled()


def test_latency_page_matches_on_value_and_decodes_old_logs(window, app, tmp_path):
    """An old-style log with a row per register: the Latency page decodes it
    through the profile's grouped tags and pairs by VIN."""
    import time as _time
    lines = ["timestamp_ms,timestamp_human,event_type,source,tag_or_addr,old_value,new_value,raw_hex,notes"]
    vin1 = [12609, 13106, 13620, 14134]          # A1234567
    unknown = [21808, 19278, 20302, 20055]       # 0UNKNOWN
    def put(ms, base, old, new):
        for i, (a, b) in enumerate(zip(old, new)):
            if a != b:
                lines.append(f"{ms + i},x,PLC_CHANGED,M,D{base + i},{a},{b},,")
    put(1_000_000, 1916, [0] * 4, unknown)
    put(1_004_000, 1916, unknown, vin1)
    put(1_006_000, 1000, [0] * 4, vin1)          # sent 2 s later
    put(1_006_300, 1000, vin1, [0] * 4)
    path = tmp_path / "old.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    window.session.cfg.set("plc", "tags_per_driver", {"Mitsubishi (MC Proto)": [
        {"tag": "D1916", "count": 4, "format": "ascii", "name": "Plant VIN"},
        {"tag": "D1000", "count": 4, "format": "ascii", "name": "Sent VIN"}]})
    page = window.pages["latency"]
    page._extra.append(str(path))
    page.refresh_sources()
    page.source.setCurrentIndex(page.source.findData(str(path)))
    page.trigger.kind.set_value("plc")
    page.trigger._kind("plc")
    page.trigger.tag.setEditText("Plant VIN")
    page.trigger.value.setText("!0UNKNOWN")
    page.response.kind.set_value("plc")
    page.response._kind("plc")
    page.response.tag.setEditText("Sent VIN")
    page.give_up.setValue(60_000)
    page.same_value.setChecked(True)
    page.measure()
    deadline = _time.time() + 10
    while _time.time() < deadline and page._result is None:
        app.processEvents()
        _time.sleep(0.02)
    assert page._result is not None
    assert page._result.answered == [2000]       # stamped by the last register of each poll
