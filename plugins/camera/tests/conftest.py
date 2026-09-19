import ctypes
import os
from pathlib import Path
import sys
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real camera/COM/process access is forbidden in plugin tests")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    from gear_camera import backend

    monkeypatch.setattr(backend.subprocess, "Popen", forbidden)
