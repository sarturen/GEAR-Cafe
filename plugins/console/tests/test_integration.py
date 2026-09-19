"""Actual registry, schemas, executor and reports; only serial transport is fake."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]

PROGRAM = r"""
import ctypes
import json
from pathlib import Path
import queue
import sys
import time

native_dll = ctypes.WinDLL
class NoDeviceAPI:
    def __init__(self, api): self.api = api
    def __getattr__(self, name):
        if name.startswith(("CreateFile", "GetComm", "SetComm", "PurgeComm", "ReadFile", "WriteFile", "QueryDosDevice")):
            raise AssertionError("Actual serial device APIs are forbidden")
        return getattr(self.api, name)
ctypes.WinDLL = lambda *a, **kw: NoDeviceAPI(native_dll(*a, **kw))

from gear_framework.host import Framework

created = []
class FakeSerial:
    def __init__(self, **settings):
        assert settings["port"] == "COM77"
        self.rx = queue.Queue()
        self.opens = self.closes = 0
        self.writes = []
        created.append(self)
    def open(self): self.opens += 1
    def close(self): self.closes += 1
    def write(self, data, timeout_s=None):
        self.writes.append(data)
        self.rx.put(b"reply:" + data)
        return len(data)
    def read(self, size, timeout_s=None):
        try: return self.rx.get(timeout=timeout_s)
        except queue.Empty: return b""

app = Path.cwd()
mode = sys.argv[1]
config = {"port": "COM77", "role": "MCU"} if mode != "incomplete" else {"role": "MCU"}
environment = {
    "api": "gear.environment/v1", "name": "fake-console",
    "devices": {"ADB001": {}}, "plugins": {"gear.console": {"config": {}}},
    "resources": {"CONSOLE.mcu": {"type": "CONSOLE", "plugin": "gear.console", "device": "ADB001", "config": config}}}
project = {"api": "gear.project/v1", "name": "fake-console", "resources": {"CONSOLE.mcu": {"type": "CONSOLE"}}}
for name, data in (("environment", environment), ("project", project)):
    (app / (name + ".yaml")).write_text(json.dumps(data), encoding="utf-8")

def wait(host, rid, phases):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = host.get_status(rid)
        if value["phase"] in phases: return value
        time.sleep(.005)
    raise AssertionError(host.get_status(rid))

with Framework(app, app / "environment.yaml") as host:
    assert not any(name.startswith("PySide6") for name in sys.modules)
    runtime = host._registry.entries["gear.console"].runtime
    host._worker.submit(lambda: setattr(runtime, "_factory", FakeSerial)).result()
    for index in range(3 if mode == "configured" else 1):
        case = {"api": "gear.dsl/v1", "name": "console-check", "body": [
            {"do": {"resource": "CONSOLE.mcu", "operation": "SEND", "args": {"command": "run-" + str(index)}}},
            {"assert": {"all": [{"resource": "CONSOLE.mcu", "condition": "OUTPUT_CONTAINS", "args": {"text": "reply:run-" + str(index) if index < 2 else "absent"}}], "within": "150ms", "every": "10ms"}}],
            "evidence_on_fail": [{"resource": "CONSOLE.mcu", "evidence": "TRANSCRIPT"}]}
        if mode == "schema-invalid": case["body"][0]["do"]["args"]["command"] = 7
        (app / "case.yaml").write_text(json.dumps(case), encoding="utf-8")
        rid = host.submit(str(app / "case.yaml"), str(app / "project.yaml"), str(app / "environment.yaml"))
        status = wait(host, rid, ("WAITING_CONFIRMATION", "FINISHED", "BLOCKED"))
        if mode != "configured":
            assert status["outcome"] == "REJECTED", status
            assert not created
            break
        assert status["phase"] == "WAITING_CONFIRMATION", status
        assert len(created) == (0 if index == 0 else 1), "Preflight must not open COM"
        host.confirm(rid)
        status = wait(host, rid, ("FINISHED", "BLOCKED"))
        assert status["outcome"] == ("PASS" if index < 2 else "FAIL"), status
        assert len(created) == 1 and created[0].opens == 1 and created[0].closes == 0
        report_path = Path(status["report_path"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if index == 2:
            result = report["evidence"][0]["result"]
            assert result["ok"], result
            artifact = result["artifacts"][0]["path"]
            assert artifact.startswith("evidence/gear.console/"), artifact
            assert (report_path.parent / artifact).is_file()
    if mode == "configured": assert len(created[0].writes) == 3
if created: assert created[0].closes == 1
print("CONSOLE_FRAMEWORK_OK")
"""


@pytest.mark.parametrize("mode", ["configured", "incomplete", "schema-invalid"])
def test_console_discovery_execution_and_evidence_without_hardware(tmp_path, mode):
    app = tmp_path / "app"
    plugin = app / "plugins" / "console"
    plugin.mkdir(parents=True)
    shutil.copy2(ROOT / "gear-plugin.yaml", plugin / "gear-plugin.yaml")
    shutil.copytree(
        ROOT / "gear_console",
        plugin / "gear_console",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", PROGRAM, mode],
        cwd=app,
        env=env,
        text=True,
        capture_output=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONSOLE_FRAMEWORK_OK" in result.stdout
