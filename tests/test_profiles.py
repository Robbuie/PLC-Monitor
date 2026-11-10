"""Profiles: creating, finding, renaming, copying, exporting and importing
them, the first-run migration of the old single config, and which profile
the service and the command line pick."""

import json
import os

import pytest

from app import paths
from app.engine import profiles
from app.engine.config_manager import ConfigManager
from app.engine.profiles import ProfileError


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_a_new_profile_has_its_own_log_folder(isolated_data):
    a = profiles.create("Press PLC", site="Toledo", line="Line 3")
    b = profiles.create("Press PLC", site="Dayton")
    la = ConfigManager(a.path).logging["log_path"]
    lb = ConfigManager(b.path).logging["log_path"]
    assert os.path.dirname(la) == os.path.join(paths.log_dir(), "Toledo - Press PLC")
    assert os.path.dirname(lb) == os.path.join(paths.log_dir(), "Dayton - Press PLC")
    assert a.label == "Toledo / Press PLC" and a.line == "Line 3"


def test_names_are_unique_per_site(isolated_data):
    profiles.create("Cell 1", site="A")
    profiles.create("Cell 1", site="B")
    with pytest.raises(ProfileError):
        profiles.create("cell 1", site="a")
    with pytest.raises(ProfileError):
        profiles.create("   ")


def test_awkward_names_make_safe_files_and_folders(isolated_data):
    p = profiles.create('Line 2: "North" <cell>/robot?', site="Plant*1")
    assert os.path.isfile(p.path)
    folder = os.path.dirname(ConfigManager(p.path).logging["log_path"])
    assert not any(c in os.path.basename(folder) for c in '<>:"/\\|?*')


def test_listing_groups_by_site_with_unsited_last(isolated_data):
    profiles.create("Zed", site="")
    profiles.create("b", site="Beta")
    profiles.create("a", site="Alpha")
    profiles.create("c", site="Beta")
    assert [p.label for p in profiles.list_profiles()] == [
        "Alpha / a", "Beta / b", "Beta / c", "Zed"]


def test_find_by_id_label_or_unique_name(isolated_data):
    p = profiles.create("Press PLC", site="Toledo")
    profiles.create("Press PLC", site="Dayton")
    profiles.create("Oven", site="Toledo")
    assert profiles.find(p.id).id == p.id
    assert profiles.find("toledo/press plc").id == p.id
    assert profiles.find("Toledo / Press PLC").id == p.id
    assert profiles.find("Oven").site == "Toledo"
    assert profiles.find("Press PLC") is None          # two of them: ambiguous
    assert profiles.find("nothing") is None


def test_renaming_keeps_id_file_and_log_folder(isolated_data):
    p = profiles.create("Old", site="S")
    log_before = ConfigManager(p.path).logging["log_path"]
    q = profiles.update_details(p.id, "New name", site="T", line="L2", notes="call Bob")
    assert (q.id, q.path) == (p.id, p.path)
    assert q.label == "T / New name" and q.notes == "call Bob"
    assert ConfigManager(q.path).logging["log_path"] == log_before


def test_saving_an_open_config_does_not_undo_a_rename(isolated_data):
    p = profiles.create("Old")
    cfg = ConfigManager(p.path)                        # open in the window
    profiles.update_details(p.id, "Renamed")           # renamed meanwhile
    cfg.set("plc", "ip", "10.0.0.5")
    cfg.save()                                         # Ctrl+S
    data = load(p.path)
    assert data["profile"]["name"] == "Renamed"
    assert data["plc"]["ip"] == "10.0.0.5"


def test_duplicate_copies_settings_but_not_the_log(isolated_data):
    p = profiles.create("Cell 1", site="S", notes="notes")
    cfg = ConfigManager(p.path)
    cfg.set("plc", "ip", "10.1.1.1")
    cfg.set("plc", "tags_per_driver", {"Rockwell (pycomm3)": [{"tag": "Go", "dtype": "BOOL"}]})
    cfg.save()
    q = profiles.duplicate(p.id, "Cell 2", site="S")
    c2 = ConfigManager(q.path)
    assert c2.plc["ip"] == "10.1.1.1"
    assert c2.plc["tags_per_driver"]["Rockwell (pycomm3)"][0]["tag"] == "Go"
    assert c2.logging["log_path"] != cfg.logging["log_path"]
    assert q.notes == "notes"


def test_export_leaves_out_this_pcs_settings_and_import_round_trips(isolated_data, tmp_path):
    p = profiles.create("Press", site="Toledo", line="L3", notes="n")
    cfg = ConfigManager(p.path)
    cfg.set("plc", "ip", "10.0.40.11")
    cfg.set("plc", "source_ip", "10.0.40.2")
    cfg.set("plc", "enabled", True)
    cfg.set("socket", "sniff", {"iface": r"\Device\NPF_{X}", "host": "10.0.40.50",
                                "port": "9000", "proto": "tcp", "our_ip": "10.0.40.2", "bpf": ""})
    cfg.save()
    out = profiles.export(p.id, str(tmp_path / "press"))
    assert out.endswith(".plcmon")
    data = load(out)
    assert "source_ip" not in data["plc"] and data["plc"]["enabled"] is False
    assert "iface" not in data["socket"]["sniff"] and "our_ip" not in data["socket"]["sniff"]
    assert "log_path" not in data["logging"]
    assert data["socket"]["sniff"]["host"] == "10.0.40.50"

    q = profiles.import_file(out)                     # same name exists: gets (2)
    assert q.label == "Toledo / Press (2)" and q.line == "L3"
    c = ConfigManager(q.path)
    assert c.plc["ip"] == "10.0.40.11"
    assert c.plc["source_ip"] == ""                   # default, not the other PC's
    assert c.logging["log_path"] != cfg.logging["log_path"]


def test_import_refuses_files_that_are_not_profiles(isolated_data, tmp_path):
    bad = tmp_path / "x.plcmon"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(ProfileError):
        profiles.import_file(str(bad))
    bad.write_text('{"hello": 1}', encoding="utf-8")
    with pytest.raises(ProfileError):
        profiles.import_file(str(bad))


def test_an_old_config_file_from_another_pc_imports(isolated_data, tmp_path):
    old = tmp_path / "plc_monitor_config.json"
    old.write_text(json.dumps({"plc": {"ip": "1.2.3.4", "tags": ["A"]},
                               "logging": {"log_path": "plc_monitor_log.csv"}}), encoding="utf-8")
    q = profiles.import_file(str(old))
    assert q.name == "plc_monitor_config"
    assert ConfigManager(q.path).plc["ip"] == "1.2.3.4"


def test_first_run_turns_the_old_config_into_default(isolated_data):
    legacy = os.path.join(paths.data_dir(), paths.CONFIG_NAME)
    os.makedirs(paths.data_dir(), exist_ok=True)
    with open(legacy, "w", encoding="utf-8") as f:
        json.dump({"plc": {"ip": "192.168.5.5", "enabled": True},
                   "logging": {"log_path": paths.default_log_path()}}, f)
    path = profiles.service_config_path()
    p = profiles.info(path)
    assert p.name == "Default"
    c = ConfigManager(path)
    assert c.plc["ip"] == "192.168.5.5" and c.plc["enabled"] is True
    # It keeps the log it had, so history carries on in one file.
    assert c.logging["log_path"] == paths.default_log_path()
    assert profiles.service_profile_id() == p.id
    assert os.path.exists(legacy)                      # left in place
    # Second run: nothing new is made.
    assert profiles.service_config_path() == path
    assert len(profiles.list_profiles()) == 1


def test_a_fresh_install_gets_a_default_with_its_own_folder(isolated_data):
    p = profiles.ensure_default()
    assert p.name == "Default"
    assert os.path.dirname(ConfigManager(p.path).logging["log_path"]) == os.path.join(
        paths.log_dir(), "Default")


def test_the_service_runs_the_profile_it_is_pointed_at(isolated_data, monkeypatch):
    profiles.ensure_default()
    q = profiles.create("Oven", site="T")
    profiles.set_service_profile(q.id)
    assert profiles.service_config_path() == q.path
    monkeypatch.setenv(paths.CONFIG_ENV, "C:/elsewhere.json")
    assert profiles.service_config_path() == "C:/elsewhere.json"


def test_the_services_profile_cannot_be_deleted(isolated_data):
    p = profiles.ensure_default()
    q = profiles.create("Spare")
    with pytest.raises(ProfileError):
        profiles.delete(p.id)
    profiles.delete(q.id)
    assert profiles.get(q.id) is None


def test_a_deleted_profiles_pointer_falls_back(isolated_data):
    p = profiles.ensure_default()
    q = profiles.create("Other")
    profiles.set_service_profile(q.id)
    os.remove(q.path)                                  # removed by hand
    assert profiles.service_config_path() == p.path


def test_resolve_for_the_command_line(isolated_data, tmp_path):
    p = profiles.create("Press", site="Toledo")
    assert profiles.resolve("Toledo/Press") == p.path
    other = tmp_path / "c.json"
    other.write_text("{}", encoding="utf-8")
    assert profiles.resolve(str(other)) == str(other)
    with pytest.raises(ProfileError) as err:
        profiles.resolve("Nope")
    assert "Toledo / Press" in str(err.value)


def test_startup_prefers_last_used_then_service(isolated_data):
    d = profiles.ensure_default()
    q = profiles.create("Q")
    assert profiles.startup_path(q.id) == q.path
    assert profiles.startup_path("gone") == d.path
    assert profiles.startup_path(None) == d.path


def test_headless_runs_the_service_profile(isolated_data, tmp_path):
    import threading
    from app.engine import logdata
    from app.engine.headless import run
    profiles.ensure_default()
    q = profiles.create("Runner")
    profiles.set_service_profile(q.id)
    stop = threading.Event()
    stop.set()
    assert run(None, stop) == 0
    log = ConfigManager(q.path).logging["log_path"]
    notes = [r["notes"] for r in logdata.read_rows(log)]
    assert any("profile Runner" in n for n in notes)


def test_the_command_line_takes_a_profile(isolated_data, monkeypatch):
    from app import __main__ as entry
    q = profiles.create("Line A", site="S")
    seen = {}
    monkeypatch.setattr("app.engine.headless.run", lambda config_path=None: seen.setdefault("p", config_path) and 0)
    assert entry.main(["headless", "--profile", "S/Line A"]) == 0
    assert seen["p"] == q.path
    assert entry.main(["headless", "--profile", "missing"]) == 2
