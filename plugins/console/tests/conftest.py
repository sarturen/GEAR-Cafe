import ctypes
import os
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Console tests may never load a real device DLL")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
