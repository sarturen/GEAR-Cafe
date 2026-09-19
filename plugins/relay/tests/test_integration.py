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
import sys
import time

def no_device(*args, **kwargs):
    raise AssertionError("Loading a real WinDLL is forbidden in relay tests")
ctypes.WinDLL = no_device

import gear_framework.host as host_module
class SimulatedHostLock:
    def acquire(self):
        pass
    def close(self):
        pass
    release = close
host_module.HostLock = SimulatedHostLock
from gear_framework.host import Framework

def crc(data):
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
    return value.to_bytes(2, "little")

created = []
class SimulatedSerial:
    def __init__(self, **settings):
        assert settings["port"] in ("COM77", "COM78")
        self.port = settings["port"]
        self.states = [False] * 8
        self.rx = b""
        self.frames = []
        self.opens = self.closes = 0
        created.append(self)
    def open(self):
        self.opens += 1
    def close(self):
        self.closes += 1
    def write(self, data, timeout_s=None):
        assert crc(data[:-2]) == data[-2:]
        assert data[0] == 1
        self.frames.append(data.hex())
        if data[1] == 5:
            channel = int.from_bytes(data[2:4], "big")
            self.states[channel] = data[4:6] == b"\xff\x00"
            self.rx = data
        elif data[1] == 1:
            assert data[2:6] == b"\x00\x00\x00\x08"
            mask = sum(int(v) << i for i, v in enumerate(self.states))
            body = bytes([1, 1, 1, mask])
            self.rx = body + crc(body)
        else:
            raise AssertionError("Unexpected function")
        return len(data)
    def read(self, size, timeout_s=None):
        # Fragment responses to exercise the real transport's frame assembly.
        size = min(size, 2)
        data, self.rx = self.rx[:size], self.rx[size:]
        return data

app = Path.cwd()
config = {"port": "COM77", "poll_interval_ms": 0} if sys.argv[1] != "incomplete" else {}
resource_config = {"channel": 1}
if sys.argv[1] == "grouped":
    resource_config.update(device_name="Independent rig", terminal="KL30")
environment = {
    "api": "gear.environment/v1", "name": "simulation",
    "plugins": {"gear.relay": {"config": config}},
    "resources": {"POWER.main": {"type": "POWER", "plugin": "gear.relay", "config": resource_config}},
}
project = {"api": "gear.project/v1", "name": "relay", "resources": {"POWER.main": {"type": "POWER"}}}
case = {
    "api": "gear.dsl/v1", "name": "relay-cycle",
    "body": [
        {"do": {"resource": "POWER.main", "operation": "ON"}},
        {"assert": {"all": [{"resource": "POWER.main", "condition": "IS_ON"}]}},
        {"do": {"resource": "POWER.main", "operation": "OFF"}},
        {"assert": {"all": [{"resource": "POWER.main", "condition": "IS_OFF"}]}},
    ],
}
if sys.argv[1] == "multi":
    config = {"controllers": {
        "front": {"port": "COM77", "poll_interval_ms": 0},
        "rear": {"port": "COM78", "poll_interval_ms": 0},
    }}
    environment["plugins"]["gear.relay"]["config"] = config
    environment["devices"] = {"board-serial": {}}
    environment["resources"] = {
        "POWER.main": {"type": "POWER", "plugin": "gear.relay", "device": "board-serial", "config": {"controller": "front", "channel": 1, "role": "KL30"}},
        "POWER.aux": {"type": "POWER", "plugin": "gear.relay", "device": "board-serial", "config": {"controller": "rear", "channel": 1, "role": "reset"}},
    }
    project["resources"]["POWER.aux"] = {"type": "POWER"}
    case["body"].extend([
        {"do": {"resource": "POWER.aux", "operation": "ON"}},
        {"assert": {"all": [{"resource": "POWER.aux", "condition": "IS_ON"}]}},
        {"do": {"resource": "POWER.aux", "operation": "OFF"}},
        {"assert": {"all": [{"resource": "POWER.aux", "condition": "IS_OFF"}]}},
    ])
if sys.argv[1] == "placeholder":
    environment["resources"]["POWER.old"] = {"type": "POWER", "plugin": "gear.relay", "config": {"device_name": "legacy", "terminal": "KL15"}}
expected_count = 2 if sys.argv[1] == "multi" else 1
for name, value in (("environment", environment), ("project", project), ("case", case)):
    (app / (name + ".yaml")).write_text(json.dumps(value), encoding="utf-8")
with Framework(app, app / "environment.yaml") as host:
    assert not any(name.startswith("PySide6") for name in sys.modules)
    runtime = host._registry.entries["gear.relay"].runtime
    def inject_serial():
        from gear_relay.transport import RelayService
        from gear_framework.documents import plugin_slice
        for cid in list(runtime.services):
            runtime.services[cid].close()
            runtime.services[cid] = RelayService(serial_factory=SimulatedSerial)
        runtime._service_factory = lambda: RelayService(serial_factory=SimulatedSerial)
        runtime.configure(plugin_slice(environment, "gear.relay"))
    host._worker.submit(inject_serial).result()
    def wait(run_id, phases):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = host.get_status(run_id)
            if status["phase"] in phases:
                return status
            time.sleep(0.002)
        raise AssertionError(host.get_status(run_id))
    for index in range(2 if config else 1):
        run_id = host.submit(str(app / "case.yaml"), str(app / "project.yaml"), str(app / "environment.yaml"))
        status = wait(run_id, ("WAITING_CONFIRMATION", "FINISHED", "BLOCKED"))
        if not config or sys.argv[1] == "placeholder":
            assert status["outcome"] == "REJECTED", status
            assert created == []
            break
        assert status["phase"] == "WAITING_CONFIRMATION", status
        assert len(created) == index * expected_count, "Preflight must not open serial"
        host.confirm(run_id)
        status = wait(run_id, ("FINISHED", "BLOCKED"))
        assert status["outcome"] == "PASS", status
        assert len(created) == expected_count and all(serial.opens == 1 for serial in created)
        assert all(serial.closes == 0 for serial in created)
        assert Path(status["report_path"]).is_file()
if config and sys.argv[1] != "placeholder":
    assert all(serial.closes == 1 for serial in created)
    assert all(len(serial.frames) == 8 for serial in created)
print("SIMULATED_FRAMEWORK_OK")
"""


@pytest.mark.parametrize(
    "mode", ["configured", "grouped", "multi", "incomplete", "placeholder"]
)
def test_plugin_discovers_and_runs_through_framework_without_hardware(tmp_path, mode):
    app = tmp_path / "app"
    plugin = app / "plugins" / "relay"
    plugin.mkdir(parents=True)
    shutil.copy2(ROOT / "gear-plugin.yaml", plugin / "gear-plugin.yaml")
    shutil.copytree(
        ROOT / "gear_relay",
        plugin / "gear_relay",
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
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SIMULATED_FRAMEWORK_OK" in result.stdout
