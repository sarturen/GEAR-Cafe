import importlib
from pathlib import Path
import sys

import pytest


@pytest.fixture
def modules(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    monkeypatch.setattr(
        "subprocess.Popen", lambda *a, **k: pytest.fail("Unexpected real process")
    )
    transport = importlib.import_module("gear_adb.transport")
    monkeypatch.setattr(transport, "_fastboot_home", lambda: tmp_path / "fake-profile")
    yield lambda name: importlib.import_module("gear_adb." + name)
    for name in list(sys.modules):
        if name == "gear_adb" or name.startswith("gear_adb."):
            sys.modules.pop(name, None)
