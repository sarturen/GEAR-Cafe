import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from gear_contracts.api import GearError
from gear_framework.common import StopToken


@pytest.fixture
def adb_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "plugins" / "adb"))
    yield lambda name: importlib.import_module("gear_adb." + name)
    for name in list(sys.modules):
        if name == "gear_adb" or name.startswith("gear_adb."):
            del sys.modules[name]


@pytest.fixture
def adb_slice():
    return {
        "plugin": {"id": "gear.adb", "config": {"adb_path": "missing-adb"}},
        "devices": {"USB123": {}},
        "resources": {"ADB.main": {"type": "ADB", "device": "USB123", "config": {}}},
    }


def test_static_validation_does_not_require_an_installed_tool_or_online_device(
    adb_modules, adb_slice, monkeypatch
):
    def forbidden(*a, **k):
        pytest.fail("Static validation launched a subprocess")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    report = adb_modules("config").validate_slice(adb_slice)
    assert report == {"status": "VALID", "diagnostics": []}


@pytest.mark.parametrize(
    "change,status,code",
    [
        (
            lambda s: s["resources"]["ADB.main"].pop("device"),
            "INCOMPLETE",
            "ADB_DEVICE_REQUIRED",
        ),
        (
            lambda s: s["resources"]["ADB.main"].update(device="UNKNOWN"),
            "INVALID",
            "ADB_DEVICE_UNKNOWN",
        ),
        (
            lambda s: s["plugin"]["config"].update(adb_path=""),
            "INCOMPLETE",
            "ADB_PATH_REQUIRED",
        ),
        (
            lambda s: s["plugin"]["config"].update(network_host="127.0.0.1"),
            "INVALID",
            "ADB_CONFIG_INVALID",
        ),
        (
            lambda s: s["resources"]["ADB.main"]["config"].update(port=5555),
            "INVALID",
            "ADB_CONFIG_INVALID",
        ),
    ],
)
def test_invalid_and_unfinished_config_remain_distinct(
    adb_modules, adb_slice, change, status, code
):
    change(adb_slice)
    before = copy.deepcopy(adb_slice)
    report = adb_modules("config").validate_slice(adb_slice)
    assert report["status"] == status
    assert code in {d["code"] for d in report["diagnostics"]}
    assert adb_slice == before


class Device:
    """Physical boundary fake; production Runtime and filesystem stay real."""

    def __init__(self):
        self.calls = []
        self.exit_code = 0
        self.closed = False
        self.running = False
        self.devices = [
            {
                "serial": "USB123",
                "state": "device",
                "transport_id": "11",
                "usb": "",
                "model": "",
            }
        ]

    def configure(self, path):
        self.calls.append(("configure", path))

    def shell(self, serial, command):
        self.calls.append(("shell", serial, command))
        return {
            "exit_code": self.exit_code,
            "stdout": "设备\n",
            "stderr": "reason" if self.exit_code else "",
        }

    def discover(self):
        self.calls.append(("discover",))
        return copy.deepcopy(self.devices)

    def dump_logcat(self, serial):
        self.calls.append(("dump_logcat", serial))
        return {"exit_code": 0, "stdout": "current device log\n", "stderr": ""}

    def pull(self, serial, remote, destination):
        self.calls.append(("pull", serial, remote))
        (Path(destination) / "device.txt").write_text("device data", encoding="utf-8")
        return {"exit_code": 0, "stdout": "pulled", "stderr": ""}

    def close(self):
        self.closed = True
        self.running = False

    def start_logcat(self, serial, destination):
        self.running = True
        return {"running": True}

    def logcat_snapshot(self, serial):
        return {
            "running": self.running,
            "lines": ["existing"],
            "path": None,
            "error": None,
        }


def context(tmp_path, run_id="r1", call_id="c1"):
    return SimpleNamespace(
        run_id=run_id,
        call_id=call_id,
        artifact_dir=str(tmp_path),
        stop_token=StopToken(),
    )


def test_configure_accepts_unfinished_data_without_opening_device(
    adb_modules, adb_slice, monkeypatch
):
    monkeypatch.setattr(
        "subprocess.Popen", lambda *a, **k: pytest.fail("configuration probed ADB")
    )
    runtime = adb_modules("runtime").create_plugin()
    adb_slice["plugin"]["config"]["adb_path"] = ""
    runtime.configure(adb_slice)
    assert runtime.validate_config(adb_slice)["status"] == "INCOMPLETE"
    with pytest.raises(GearError, match="ADB_PATH_REQUIRED"):
        runtime.refresh_devices()
    runtime.close()


def test_runs_reuse_session_service_and_snapshot_binding(
    adb_modules, adb_slice, tmp_path
):
    service = Device()
    runtime = adb_modules("runtime").AdbRuntime(service=service)
    runtime.configure(adb_slice)
    runtime.start_logcat("USB123", str(tmp_path / "session.log"))
    for run_id in ("r1", "r2"):
        ctx = context(tmp_path, run_id)
        binding = {
            "plugin_slice": copy.deepcopy(adb_slice),
            "resource_ids": ["ADB.main"],
        }
        runtime.begin_run(binding, ctx)
        binding["plugin_slice"]["resources"]["ADB.main"]["device"] = "MUTATED"
        result = runtime.invoke("ADB.main", "SHELL", {"command": "echo hello"}, ctx)
        assert result["ok"] and result["details"]["stdout"] == "设备\n"
        assert service.calls[-1] == ("shell", "USB123", "echo hello")
        runtime.end_run()
        runtime.end_run()
        assert runtime.logcat_snapshot("USB123")["running"]
        with pytest.raises(GearError, match="ADB_NOT_BOUND"):
            runtime.invoke("ADB.main", "SHELL", {"command": "echo hello"}, ctx)
    assert not service.closed
    runtime.close()
    runtime.close()
    assert service.closed and not service.running


def test_nonzero_shell_is_a_structured_operation_failure(
    adb_modules, adb_slice, tmp_path
):
    service = Device()
    service.exit_code = 7
    runtime = adb_modules("runtime").AdbRuntime(service=service)
    runtime.configure(adb_slice)
    ctx = context(tmp_path)
    runtime.begin_run({"plugin_slice": adb_slice, "resource_ids": ["ADB.main"]}, ctx)
    result = runtime.invoke("ADB.main", "SHELL", {"command": "exit 7"}, ctx)
    assert not result["ok"]
    assert result["diagnostic"]["code"] == "ADB_COMMAND_FAILED"
    assert result["details"]["exit_code"] == 7
    assert result["details"]["stderr"] == "reason"


def test_pull_uses_distinct_run_local_directories(adb_modules, adb_slice, tmp_path):
    runtime = adb_modules("runtime").AdbRuntime(service=Device())
    runtime.configure(adb_slice)
    ctx = context(tmp_path)
    runtime.begin_run({"plugin_slice": adb_slice, "resource_ids": ["ADB.main"]}, ctx)
    results = [
        runtime.invoke("ADB.main", "PULL", {"remote_path": "/sdcard/device.txt"}, ctx)
        for _ in range(2)
    ]
    paths = [r["details"]["destination"] for r in results]
    assert paths[0] != paths[1]
    for relative in paths:
        assert relative.startswith("files/gear.adb/")
        assert (tmp_path / relative / "device.txt").read_text() == "device data"


def test_expected_adb_transport_error_becomes_operation_diagnostic(
    adb_modules, adb_slice, tmp_path
):
    class Missing(Device):
        def shell(self, serial, command):
            raise GearError("ADB_DEVICE_MISSING", "USB device is absent")

    runtime = adb_modules("runtime").AdbRuntime(service=Missing())
    runtime.configure(adb_slice)
    ctx = context(tmp_path)
    runtime.begin_run({"plugin_slice": adb_slice, "resource_ids": ["ADB.main"]}, ctx)
    result = runtime.invoke("ADB.main", "SHELL", {"command": "echo hi"}, ctx)
    assert result["ok"] is False
    assert result["diagnostic"]["code"] == "ADB_DEVICE_MISSING"


def test_manual_pull_creates_the_chosen_directory(adb_modules, adb_slice, tmp_path):
    runtime = adb_modules("runtime").AdbRuntime(service=Device())
    runtime.configure(adb_slice)
    destination = tmp_path / "nested" / "downloads"
    result = runtime.manual_pull("USB123", "/sdcard/device.txt", str(destination))
    assert result["exit_code"] == 0
    assert (destination / "device.txt").read_text() == "device data"
