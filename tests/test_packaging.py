"""The installer leaves out the modules listed in EXCLUDE in
packaging/plcmonitor.spec. Each one taken away must not break anything the
app imports -- 1.4.0 shipped without unittest, which matplotlib needs (through
pyparsing), and the charts were gone. Run in a fresh interpreter with the
excluded modules made unimportable."""

import ast
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: What the built app imports at run time, including what it imports late.
RUNTIME = [
    "app.ui.window", "app.ui.pages.trend", "app.ui.pages.latency", "app.ui.tagpicker",
    "app.engine.headless", "app.engine.profiles", "app.engine.tagimport", "app.engine.tagspec",
    "app.ui.tagedit", "sqlite3", "zipfile", "xml.etree.ElementTree", "app.instance",
    "matplotlib", "matplotlib.backends.backend_qtagg", "matplotlib.figure",
    "numpy", "psutil", "pycomm3", "snap7", "snap7.client", "snap7.util", "pymcprotocol",
    "scapy.all",
]


def excluded() -> list[str]:
    with open(os.path.join(ROOT, "packaging", "plcmonitor.spec"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "EXCLUDE"
                                                for t in node.targets):
            return [ast.literal_eval(e) for e in node.value.elts]
    raise AssertionError("EXCLUDE not found in the spec")


def test_nothing_the_app_imports_needs_an_excluded_module():
    blocked = excluded()
    assert blocked, "EXCLUDE is empty?"
    script = f"""
import importlib, sys
BLOCKED = {blocked!r}
class Block:
    def find_spec(self, name, path=None, target=None):
        if any(name == b or name.startswith(b + ".") for b in BLOCKED):
            raise ImportError("No module named " + repr(name) + " (excluded from the build)")
sys.meta_path.insert(0, Block())
failed = []
for mod in {RUNTIME!r}:
    try:
        importlib.import_module(mod)
    except ImportError as exc:
        # A driver library missing from this environment is not the point;
        # only a failure that names an excluded module is.
        if "excluded from the build" in str(exc):
            failed.append(f"{{mod}}: {{exc}}")
print("\\n".join(failed))
sys.exit(1 if failed else 0)
"""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_installer_waits_for_the_same_mutex_the_app_holds():
    """1.5.1: an update replaced files under a running copy and left an
    install that could not start. The app holds named mutexes; the installer
    waits for them. The names must agree."""
    from app.instance import MUTEX_NAMES
    with open(os.path.join(ROOT, "packaging", "installer.iss"), encoding="utf-8") as f:
        iss = f.read()
    line = next(l for l in iss.splitlines() if l.strip().startswith("RunningMutexes ="))
    names = line.split("=", 1)[1].strip().strip(";").strip().strip("'").split(",")
    assert tuple(names) == MUTEX_NAMES
    assert "function InitializeSetup(): Boolean;" in iss
    assert "SetupLogging=yes" in iss


def test_holding_the_mutex_is_harmless_off_windows():
    from app import instance
    assert instance.hold_running_mutex() == (0 if os.name != "nt" else len(instance._handles))


def test_the_updater_asks_the_installer_for_a_log(tmp_path, monkeypatch):
    from app.core import updates
    staged = tmp_path / "PLCMonitor-Setup-9.9.9.exe"
    staged.write_bytes(b"MZ")
    seen = {}
    monkeypatch.setattr(updates.subprocess, "Popen", lambda args, **k: seen.setdefault("args", args))
    assert updates.launch(str(staged))
    assert f"/LOG={tmp_path / 'PLCMonitor-Setup-9.9.9.log'}" in seen["args"]
    assert "/SUPPRESSMSGBOXES" in seen["args"]
