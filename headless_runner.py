"""Kept so an existing shortcut or scheduled task that runs this file still works.

    python headless_runner.py [config_path]

Same as `python -m app headless [--config PATH]`.
"""

import sys

from app.engine.headless import run

if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1] if len(sys.argv) > 1 else None))
