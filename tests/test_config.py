import json
import os

from app import paths
from app.engine.config_manager import ConfigManager
from app.engine.monitors import ConfigError, build_driver, tags_for


def test_relative_log_path_moves_into_the_site_log_folder():
    cfg_path = paths.config_path()
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, "w") as f:
        json.dump({"logging": {"log_path": "plc_monitor_log.csv"}}, f)
    cfg = ConfigManager()
    assert cfg.logging["log_path"] == os.path.join(paths.log_dir(), "plc_monitor_log.csv")


def test_save_is_readable_back_and_keeps_new_sections():
    cfg = ConfigManager()
    cfg.set("plc", "enabled", True)
    cfg.set("socket", "sniff", {"host": "10.0.0.5", "port": "9000"})
    cfg.save()
    again = ConfigManager()
    assert again.plc["enabled"] is True
    assert again.socket["sniff"]["host"] == "10.0.0.5"
    assert again.plc["libplctag"]["path"] == "1,0"


def test_legacy_config_is_copied_once(tmp_path, monkeypatch):
    old = tmp_path / paths.CONFIG_NAME
    old.write_text(json.dumps({"plc": {"ip": "1.2.3.4"}}))
    monkeypatch.setattr(paths, "legacy_config_candidates", lambda: [str(old)])
    assert paths.migrate_legacy_config() == str(old)
    assert ConfigManager().plc["ip"] == "1.2.3.4"
    assert paths.migrate_legacy_config() is None


def test_tags_for_prefers_the_per_driver_list():
    plc = {"type": "Siemens (snap7)", "tags": ["old"],
           "tags_per_driver": {"Siemens (snap7)": [{"tag": "DB1,X0.0"}, "M0.0"]}}
    assert tags_for(plc) == ["DB1,X0.0", "M0.0"]
    assert tags_for({"type": "x", "tags": ["a", ""]}) == ["a"]


def test_build_driver_refuses_a_missing_address():
    try:
        build_driver({"type": "Rockwell (pycomm3)", "ip": ""})
    except ConfigError as exc:
        assert "IP" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_libplctag_driver_gets_per_tag_types():
    plc = {"type": "Rockwell (libplctag)", "ip": "10.0.0.1",
           "tags_per_driver": {"Rockwell (libplctag)": [{"tag": "Speed", "dtype": "REAL"}]}}
    driver = build_driver(plc)
    assert driver.tag_types == {"Speed": "REAL"}
    assert "elem_size=4" in driver.cfg.build_attrib("Speed", "REAL")
    assert "elem_size=1" in driver.cfg.build_attrib("Bit", "BOOL")


def test_a_config_never_changes_the_defaults(tmp_path):
    """A section the file lacks used to be the defaults' own dict."""
    from app.engine.config_manager import DEFAULT_CONFIG
    before = json.dumps(DEFAULT_CONFIG, sort_keys=True)
    cfg = ConfigManager(str(tmp_path / "none.json"))
    cfg.set("socket", "sniff", {"host": "10.9.9.9"})
    cfg.socket["listen_port"] = 1
    cfg.plc["libplctag"]["cpu"] = "micro800"
    (tmp_path / "partial.json").write_text('{"plc": {"ip": "1.1.1.1"}}', encoding="utf-8")
    other = ConfigManager(str(tmp_path / "partial.json"))
    other.service["name"] = "changed"
    assert json.dumps(DEFAULT_CONFIG, sort_keys=True) == before
