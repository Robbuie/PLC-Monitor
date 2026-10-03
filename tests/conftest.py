import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def isolated_data(monkeypatch):
    """Every test gets its own site and user folder, never the real ProgramData."""
    folder = tempfile.mkdtemp(prefix="plcmon-test-")
    monkeypatch.setenv("PLC_MONITOR_DATA", folder)
    monkeypatch.setenv("LOCALAPPDATA", folder)
    monkeypatch.delenv("PLC_MONITOR_CONFIG", raising=False)
    yield folder
