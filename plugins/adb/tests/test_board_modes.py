from copy import deepcopy
import io
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from gear_contracts.api import GearError

SLICE = {
    "plugin": {"id": "gear.adb", "config": {"fastboot_path": "fake-fastboot"}},
    "devices": {"board1": {}},
    "resources": {"ADB.main": {"type": "ADB", "device": "board1", "config": {}}},
}


def board(state="device"):
    return {
        "serial": "board1",
        "state": state,
        "transport_id": "11",
        "usb": "",
        "model": "",
    }


class Process:
    def __init__(self, stdout="", stderr="", code=0, timeout=False):
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = code
        self.timeout = timeout
        self.killed = False
        self.waited = False

    def communicate(self, timeout=None):
        assert timeout is not None and 0 < timeout <= 300
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.stdout.getvalue(), self.stderr.getvalue()

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode


def processes(monkeypatch, responses):
    calls = []

    def start(argv, **kwargs):
        calls.append((argv, kwargs))
        assert not kwargs.get("shell")
        assert kwargs["stdin"] == subprocess.DEVNULL
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "Popen", start)
    return calls


def service(modules):
    result = modules("transport").AdbService()
    result.configure_fastboot("fake-fastboot")
    return result


def runtime(modules, tmp_path, monkeypatch, adb=None, fastboot=None):
    svc = service(modules)
    monkeypatch.setattr(svc, "_discover_adb", lambda: [board()] if adb is None else adb)
    monkeypatch.setattr(
        svc, "_discover_fastboot", lambda: [] if fastboot is None else fastboot
    )
    rt = modules("runtime").AdbRuntime(svc)
    rt.configure(deepcopy(SLICE))
    ctx = SimpleNamespace(run_id="r1", artifact_dir=str(tmp_path))
    rt.begin_run({"plugin_slice": deepcopy(SLICE), "resource_ids": ["ADB.main"]}, ctx)
    return rt, svc, ctx


def test_duplicate_saved_resource_is_invalid_without_rewriting_identity(modules):
    data = deepcopy(SLICE)
    data["resources"]["ADB.second"] = deepcopy(data["resources"]["ADB.main"])
    before = deepcopy(data)
    report = modules("config").validate_slice(data)
    assert report["status"] == "INVALID"
    assert "ADB_DEVICE_DUPLICATE" in {d["code"] for d in report["diagnostics"]}
    assert data == before


def test_optional_fastboot_configuration_is_static_and_adb_compatible(modules):
    data = deepcopy(SLICE)
    assert modules("config").validate_slice(data)["status"] == "VALID"
    data["plugin"]["config"]["fastboot_path"] = ""
    assert modules("config").validate_slice(data)["status"] == "VALID"


def test_fastboot_discovery_filters_network_and_reports_bad_frames(
    modules, monkeypatch
):
    svc = service(modules)
    calls = processes(
        monkeypatch,
        [
            Process(
                "board1 fastboot usb:1-2\ntcp:host:5554 fastboot\nudp:host fastboot\n"
            )
        ],
    )
    assert svc.discover_fastboot() == [
        {"serial": "board1", "state": "fastboot", "usb": "usb:1-2", "model": ""}
    ]
    assert calls[0][0] == ["fake-fastboot", "devices", "-l"]
    processes(monkeypatch, [Process("unreadable listing")])
    with pytest.raises(GearError, match="FASTBOOT_DISCOVERY_FAILED"):
        svc.discover_fastboot()


def test_fastboot_pins_target_and_preserves_output(modules, monkeypatch):
    svc = service(modules)
    calls = processes(
        monkeypatch, [Process("board1 fastboot\n"), Process("", "product: board\n", 1)]
    )
    result = svc.fastboot("board1", ["getvar", "product"], 2)
    assert calls[1][0] == ["fake-fastboot", "-s", "board1", "--", "getvar", "product"]
    assert result == {"exit_code": 1, "stdout": "", "stderr": "product: board\n"}
    assert svc.device_snapshot()["outputs"]["board1"]["exit_code"] == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ["-s", "other", "reboot"],
        ["reboot", "-sother"],
        ["--serial=other", "reboot"],
        ["reboot", "-vsother"],
        ["connect", "tcp:host"],
        ["disconnect"],
        [],
        "reboot",
        [1],
    ],
)
def test_fastboot_rejects_target_overrides_before_io(modules, arguments):
    with pytest.raises(GearError, match="FASTBOOT_ARGUMENTS_INVALID"):
        service(modules).fastboot("board1", arguments, 2)


@pytest.mark.parametrize(
    "serial", ["tcp:host:5554", "udp:host", "192.0.2.1:5554", "", "bad serial"]
)
def test_fastboot_rejects_network_or_invalid_identity_before_io(modules, serial):
    with pytest.raises(GearError, match="FASTBOOT_TARGET_INVALID"):
        service(modules).fastboot(serial, ["getvar", "product"], 2)


@pytest.mark.parametrize("timeout", [0, -1, 301, float("inf"), True, "2"])
def test_fastboot_requires_bounded_timeout(modules, timeout):
    with pytest.raises(GearError, match="FASTBOOT_ARGUMENTS_INVALID"):
        service(modules).fastboot("board1", ["getvar", "product"], timeout)


def test_fastboot_timeout_reaps_client(modules, monkeypatch):
    svc = service(modules)
    timed = Process(timeout=True)
    processes(monkeypatch, [Process("board1 fastboot\n"), timed])
    with pytest.raises(GearError, match="FASTBOOT_TIMEOUT"):
        svc.fastboot("board1", ["reboot"], 0.01)
    assert timed.killed


def test_merge_keeps_tools_errors_and_does_not_claim_missing(modules, monkeypatch):
    svc = service(modules)
    monkeypatch.setattr(svc, "_discover_adb", lambda: [board("recovery")])
    monkeypatch.setattr(svc, "_discover_fastboot", lambda: [])
    svc.refresh_devices()
    assert svc.device_status("board1")["state"] == "recovery"
    monkeypatch.setattr(svc, "_discover_adb", lambda: [])

    def failed():
        raise GearError("FASTBOOT_TOOL_ERROR", "missing executable")

    monkeypatch.setattr(svc, "_discover_fastboot", failed)
    svc.refresh_devices()
    assert svc.device_status("board1")["state"] == "unknown"
    assert (
        svc.device_snapshot()["queries"]["fastboot"]["diagnostic"]["code"]
        == "FASTBOOT_TOOL_ERROR"
    )
    monkeypatch.setattr(svc, "_discover_fastboot", lambda: [])
    svc.refresh_devices()
    assert svc.device_status("board1")["state"] == "missing"


@pytest.mark.parametrize(
    "state",
    [
        "device",
        "recovery",
        "sideload",
        "offline",
        "unauthorized",
        "fastboot",
        "missing",
    ],
)
def test_state_is_and_cache_keep_each_mode_distinct(
    modules, tmp_path, monkeypatch, state
):
    adb = [] if state in {"fastboot", "missing"} else [board(state)]
    fb = [board("fastboot")] if state == "fastboot" else []
    rt, svc, ctx = runtime(modules, tmp_path, monkeypatch, adb, fb)
    result = rt.evaluate("ADB.main", "STATE_IS", {"state": state}, ctx)
    assert result["ok"] and result["satisfied"]
    assert result["details"]["state"] == state
    assert rt.device_status("board1")["state"] == state
    assert rt.evaluate("ADB.main", "STATE_IS", {"state": "other"}, ctx)["ok"] is False


def test_state_missing_is_error_without_fastboot_but_legacy_unavailable_still_works(
    modules, tmp_path, monkeypatch
):
    rt, svc, ctx = runtime(modules, tmp_path, monkeypatch, adb=[])

    def failed():
        raise GearError("FASTBOOT_UNCONFIGURED", "not configured")

    monkeypatch.setattr(svc, "_discover_fastboot", failed)
    result = rt.evaluate("ADB.main", "STATE_IS", {"state": "missing"}, ctx)
    assert not result["ok"] and not result["satisfied"]
    assert result["diagnostic"]["code"] == "FASTBOOT_UNCONFIGURED"
    assert rt.evaluate("ADB.main", "UNAVAILABLE", {}, ctx)["satisfied"]


def test_output_contains_is_current_shell_observation_and_nonzero_is_error(
    modules, tmp_path, monkeypatch
):
    rt, svc, ctx = runtime(modules, tmp_path, monkeypatch)
    processes(
        monkeypatch,
        [Process("hello board", "warning", 0), Process("hello board", "failure", 1)],
    )

    # Existing ADB foreground communicate has no timeout; fake only its boundary here.
    def run(arguments):
        return {"exit_code": 0, "stdout": "hello board", "stderr": "warning"}

    monkeypatch.setattr(svc, "_run", run)
    result = rt.evaluate(
        "ADB.main", "OUTPUT_CONTAINS", {"command": "getprop", "text": "board"}, ctx
    )
    assert result["ok"] and result["satisfied"]
    assert rt.device_snapshot()["outputs"]["board1"]["stdout"] == "hello board"
    monkeypatch.setattr(
        svc,
        "_run",
        lambda args: {"exit_code": 4, "stdout": "board", "stderr": "failed"},
    )
    result = rt.evaluate(
        "ADB.main", "OUTPUT_CONTAINS", {"command": "getprop", "text": "board"}, ctx
    )
    assert not result["ok"] and not result["satisfied"]
    assert result["diagnostic"]["code"] == "ADB_COMMAND_FAILED"


def test_fastboot_runtime_nonzero_is_structured_failure(modules, tmp_path, monkeypatch):
    rt, svc, ctx = runtime(
        modules, tmp_path, monkeypatch, adb=[], fastboot=[board("fastboot")]
    )
    processes(monkeypatch, [Process("", "FAILED", 2)])
    result = rt.invoke(
        "ADB.main", "FASTBOOT", {"arguments": ["getvar", "product"]}, ctx
    )
    assert not result["ok"]
    assert result["diagnostic"]["code"] == "FASTBOOT_COMMAND_FAILED"
    assert result["details"]["stderr"] == "FAILED"
    assert rt.device_status("board1")["state"] == "fastboot"


def test_cache_reads_and_configure_do_not_launch_process(modules):
    svc = service(modules)
    rt = modules("runtime").AdbRuntime(svc)
    rt.configure(deepcopy(SLICE))
    assert rt.device_status("board1")["state"] == "unknown"
    assert rt.device_snapshot()["records"] == []
    rt.close()


def test_observer_updates_cache_during_run_and_close_joins(
    modules, tmp_path, monkeypatch
):
    import threading

    rt, svc, ctx = runtime(modules, tmp_path, monkeypatch)
    changed = threading.Event()

    def discovery():
        changed.set()
        return [board("recovery")]

    monkeypatch.setattr(svc, "_discover_adb", discovery)
    svc.start_monitor(interval_s=0.01)
    assert changed.wait(1)
    svc.stop_monitor()
    assert rt.device_status("board1")["state"] == "recovery"
    assert not svc.device_snapshot()["monitoring"]
    svc.start_monitor(interval_s=0.01)
    rt.close()
    assert not svc.device_snapshot()["monitoring"]


def test_monitor_and_manual_refresh_serialize_discovery(modules, monkeypatch):
    import threading

    svc = service(modules)
    entered, release = threading.Event(), threading.Event()
    count = 0

    def discovery():
        nonlocal count
        count += 1
        entered.set()
        assert release.wait(2)
        return [board()]

    monkeypatch.setattr(svc, "_discover_adb", discovery)
    monkeypatch.setattr(svc, "_discover_fastboot", lambda: [])
    svc.start_monitor(interval_s=1)
    assert entered.wait(1)
    manual = threading.Thread(target=svc.refresh_devices)
    manual.start()
    assert count == 1
    release.set()
    manual.join(2)
    svc.close()
    assert not manual.is_alive()


def test_manifest_validates_observation_arguments_without_hardware(modules):
    from gear_framework.registry import _manifest
    from jsonschema import Draft202012Validator

    manifest, _ = _manifest(Path(__file__).parents[1] / "gear-plugin.yaml")
    caps = manifest["resource_types"]["ADB"]
    for category, name, good, bad in [
        (
            "operations",
            "FASTBOOT",
            {"arguments": ["getvar", "product"]},
            {"arguments": "getvar product"},
        ),
        ("conditions", "STATE_IS", {"state": "fastboot"}, {"state": "boot"}),
        (
            "conditions",
            "OUTPUT_CONTAINS",
            {"command": "getprop", "text": "x"},
            {"command": "getprop"},
        ),
    ]:
        checker = Draft202012Validator(caps[category][name]["args_schema"])
        assert checker.is_valid(good)
        assert not checker.is_valid(bad)


def test_individual_command_discovery_supersedes_old_other_mode(modules, monkeypatch):
    svc = service(modules)
    monkeypatch.setattr(svc, "_discover_adb", lambda: [])
    monkeypatch.setattr(svc, "_discover_fastboot", lambda: [board("fastboot")])
    svc.refresh_devices()
    assert svc.device_status("board1")["state"] == "fastboot"
    monkeypatch.setattr(svc, "_discover_adb", lambda: [board()])
    monkeypatch.setattr(
        svc, "_run", lambda args: {"exit_code": 0, "stdout": "yes", "stderr": ""}
    )
    svc.shell("board1", "getprop")
    assert svc.device_status("board1")["state"] == "device"


def test_full_refresh_detects_ambiguous_same_serial_across_tools(modules, monkeypatch):
    svc = service(modules)
    monkeypatch.setattr(svc, "_discover_adb", lambda: [board()])
    monkeypatch.setattr(svc, "_discover_fastboot", lambda: [board("fastboot")])
    svc.refresh_devices()
    assert svc.device_status("board1")["state"] == "ambiguous"


@pytest.mark.parametrize(
    "condition,args",
    [
        ("STATE_IS", {"state": []}),
        ("OUTPUT_CONTAINS", {"command": "x"}),
        ("AVAILABLE", {"other": True}),
    ],
)
def test_invalid_direct_condition_arguments_return_structured_failure(
    modules, tmp_path, monkeypatch, condition, args
):
    rt, svc, ctx = runtime(modules, tmp_path, monkeypatch)
    result = rt.evaluate("ADB.main", condition, args, ctx)
    assert not result["ok"] and not result["satisfied"]
    assert result["diagnostic"]["code"] == "ADB_ARGUMENTS_INVALID"


def test_output_contains_accepts_literal_whitespace_text(
    modules, tmp_path, monkeypatch
):
    rt, svc, ctx = runtime(modules, tmp_path, monkeypatch)
    monkeypatch.setattr(
        svc, "_run", lambda args: {"exit_code": 0, "stdout": "two words", "stderr": ""}
    )
    result = rt.evaluate(
        "ADB.main", "OUTPUT_CONTAINS", {"command": "getprop", "text": " "}, ctx
    )
    assert result["ok"] and result["satisfied"]


def test_actual_missing_fastboot_tool_is_retained_without_breaking_adb(
    modules, monkeypatch
):
    svc = service(modules)

    def missing(*args, **kwargs):
        raise FileNotFoundError("absent")

    monkeypatch.setattr(subprocess, "Popen", missing)
    monkeypatch.setattr(svc, "_discover_adb", lambda: [board()])
    svc.refresh_devices()
    assert svc.device_status("board1")["state"] == "device"
    assert (
        svc.device_snapshot()["queries"]["fastboot"]["diagnostic"]["code"]
        == "FASTBOOT_TOOL_ERROR"
    )
    assert svc.device_status("absent")["state"] == "unknown"


def test_real_discovery_failure_updates_cache_instead_of_offline(modules, monkeypatch):
    svc = service(modules)
    monkeypatch.setattr(svc, "_discover_adb", lambda: [board()])
    svc.discover()

    def broken():
        raise GearError("ADB_DISCOVERY_FAILED", "server unavailable")

    monkeypatch.setattr(svc, "_discover_adb", broken)
    with pytest.raises(GearError):
        svc.discover()
    assert svc.device_status("board1")["state"] == "unknown"
    assert svc.device_status("board1")["diagnostic"]["code"] == "ADB_DISCOVERY_FAILED"


@pytest.mark.parametrize("content", [None, b"", b" \n\t"])
def test_fastboot_usb_listing_allows_absent_or_empty_network_registry(
    modules, monkeypatch, tmp_path, content
):
    module = modules("transport")
    monkeypatch.setattr(module, "_fastboot_home", lambda: tmp_path)
    if content is not None:
        (tmp_path / ".fastboot").mkdir()
        (tmp_path / ".fastboot" / "devices").write_bytes(content)
    svc = service(modules)
    calls = processes(monkeypatch, [Process("board1 fastboot\n")])
    assert svc.discover_fastboot()[0]["serial"] == "board1"
    assert len(calls) == 1


def test_fastboot_network_registry_blocks_listing_without_touching_file(
    modules, monkeypatch, tmp_path
):
    module = modules("transport")
    monkeypatch.setattr(module, "_fastboot_home", lambda: tmp_path)
    folder = tmp_path / ".fastboot"
    folder.mkdir()
    registry = folder / "devices"
    saved = b"tcp:192.0.2.10:5554\n"
    registry.write_bytes(saved)
    svc = service(modules)
    with pytest.raises(GearError, match="FASTBOOT_NETWORK_CONFIGURED"):
        svc.discover_fastboot()
    assert registry.read_bytes() == saved
    assert (
        svc.device_snapshot()["queries"]["fastboot"]["diagnostic"]["code"]
        == "FASTBOOT_NETWORK_CONFIGURED"
    )


def test_fastboot_unreadable_registry_is_query_error_before_subprocess(
    modules, monkeypatch, tmp_path
):
    module = modules("transport")
    monkeypatch.setattr(module, "_fastboot_home", lambda: tmp_path)
    original = Path.read_bytes

    def unreadable(path):
        if path == tmp_path / ".fastboot" / "devices":
            raise PermissionError("access denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    with pytest.raises(GearError, match="FASTBOOT_NETWORK_CHECK_FAILED"):
        service(modules).discover_fastboot()


def test_fastboot_unresolved_profile_blocks_query_but_not_configure(
    modules, monkeypatch
):
    module = modules("transport")

    def unknown():
        raise OSError("profile unavailable")

    monkeypatch.setattr(module, "_fastboot_home", unknown)
    svc = service(modules)
    with pytest.raises(GearError, match="FASTBOOT_NETWORK_CHECK_FAILED"):
        svc.discover_fastboot()
