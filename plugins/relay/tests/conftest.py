import ctypes
from pathlib import Path
import sys

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))


@pytest.fixture(autouse=True)
def forbid_real_serial(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Relay tests must never load a real Windows device API")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
