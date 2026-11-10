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
    "app.engine.headless", "app.engine.profiles", "app.engine.tagimport",
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
