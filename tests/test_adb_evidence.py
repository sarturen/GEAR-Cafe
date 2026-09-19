"""Availability and evidence exercise the real contract adapter and filesystem."""

from pathlib import Path

import pytest
from gear_contracts.api import GearError
from test_adb_runtime import Device, adb_modules, adb_slice, context


def bound(adb_modules, adb_slice, tmp_path, device=None):
    device = device or Device()
    runtime = adb_modules("runtime").AdbRuntime(service=device)
    runtime.configure(adb_slice)
    ctx = context(tmp_path)
    runtime.begin_run({"plugin_slice": adb_slice, "resource_ids": ["ADB.main"]}, ctx)
    return runtime, device, ctx


@pytest.mark.parametrize(
    "state,available",
    [
        ("device", True),
        ("offline", False),
        ("unauthorized", False),
        ("recovery", False),
        ("missing", False),
    ],
)
def test_availability_is_usb_transport_state_without_shell_probe(
    adb_modules, adb_slice, tmp_path, state, available
):
    runtime, device, ctx = bound(adb_modules, adb_slice, tmp_path)
    if state == "missing":
        device.devices = []
    else:
        device.devices[0]["state"] = state
    for condition, expected in (
        ("AVAILABLE", available),
        ("UNAVAILABLE", not available),
    ):
        result = runtime.evaluate("ADB.main", condition, {}, ctx)
        assert result == {
            "ok": True,
            "satisfied": expected,
            "diagnostic": None,
            "details": {"serial": "USB123", "state": state},
        }
    assert not any(call[0] in ("shell", "dump_logcat") for call in device.calls)


@pytest.mark.parametrize("condition", ["AVAILABLE", "UNAVAILABLE"])
def test_query_error_never_masquerades_as_unavailable(
    adb_modules, adb_slice, tmp_path, condition
):
    class Broken(Device):
        def discover(self):
            raise GearError("ADB_DISCOVERY_FAILED", "server query failed")

    runtime, _, ctx = bound(adb_modules, adb_slice, tmp_path, Broken())
    result = runtime.evaluate("ADB.main", condition, {}, ctx)
    assert result["ok"] is False and result["satisfied"] is False
    assert result["diagnostic"]["code"] == "ADB_DISCOVERY_FAILED"


def test_duplicate_usb_identity_is_an_error_instead_of_a_guess(
    adb_modules, adb_slice, tmp_path
):
    runtime, device, ctx = bound(adb_modules, adb_slice, tmp_path)
    device.devices.append({**device.devices[0], "transport_id": "12"})
    result = runtime.evaluate("ADB.main", "AVAILABLE", {}, ctx)
    assert not result["ok"]
    assert result["diagnostic"]["code"] == "ADB_AMBIGUOUS_DEVICE"


@pytest.mark.parametrize(
    "paths,status",
    [
        ([], "VALID"),
        (["/sdcard/log dir", "/data/local/tmp/report.txt"], "VALID"),
        ("/sdcard/log", "INVALID"),
        ([1], "INVALID"),
        (["relative/log"], "INVALID"),
        (["/bad\0path"], "INVALID"),
    ],
)
def test_log_paths_validation_is_configuration_only(
    adb_modules, adb_slice, monkeypatch, paths, status
):
    monkeypatch.setattr(
        "subprocess.Popen", lambda *a, **k: pytest.fail("validation started ADB")
    )
    adb_slice["resources"]["ADB.main"]["config"]["log_paths"] = paths
    report = adb_modules("config").validate_slice(adb_slice)
    assert report["status"] == status
    if status == "INVALID":
        assert report["diagnostics"][0]["code"] == "ADB_LOG_PATHS_INVALID"


def artifact_texts(result, root):
    texts = {}
    for artifact in result["artifacts"]:
        relative = artifact["path"]
        assert relative.startswith("evidence/gear.adb/")
        path = root / relative
        assert path.is_file()
        texts[path.name] = path.read_text(encoding="utf-8")
    return texts


def test_evidence_uses_archived_paths_and_returns_existing_files_with_distinct_captures(
    adb_modules, adb_slice, tmp_path
):
    adb_slice["resources"]["ADB.main"]["config"]["log_paths"] = ["/sdcard/a"]
    runtime, device, ctx = bound(adb_modules, adb_slice, tmp_path)
    adb_slice["resources"]["ADB.main"]["config"]["log_paths"] = ["/MUTATED"]
    captures = [
        runtime.collect("ADB.main", "DIAGNOSTIC_LOGS", {}, ctx) for _ in range(2)
    ]
    for result in captures:
        assert result["ok"] and result["diagnostic"] is None
        assert artifact_texts(result, tmp_path) == {
            "logcat.txt": "current device log\n",
            "device.txt": "device data",
        }
    assert {a["path"] for a in captures[0]["artifacts"]}.isdisjoint(
        a["path"] for a in captures[1]["artifacts"]
    )
    assert [call for call in device.calls if call[0] == "pull"] == [
        ("pull", "USB123", "/sdcard/a"),
        ("pull", "USB123", "/sdcard/a"),
    ]


def test_evidence_retains_partial_dump_and_pulls_other_paths_after_failure(
    adb_modules, adb_slice, tmp_path
):
    class Partial(Device):
        def dump_logcat(self, serial):
            return {"exit_code": 1, "stdout": "partial log\n", "stderr": "log denied"}

        def pull(self, serial, remote, destination):
            if remote == "/missing":
                return {"exit_code": 1, "stdout": "", "stderr": "no such file"}
            target = Path(destination) / "nested"
            target.mkdir()
            (target / "other.log").write_text("extra log", encoding="utf-8")
            return {"exit_code": 0, "stdout": "", "stderr": ""}

    adb_slice["resources"]["ADB.main"]["config"]["log_paths"] = ["/missing", "/good"]
    runtime, _, ctx = bound(adb_modules, adb_slice, tmp_path, Partial())
    result = runtime.collect("ADB.main", "DIAGNOSTIC_LOGS", {}, ctx)
    assert not result["ok"]
    assert result["diagnostic"]["code"] == "ADB_EVIDENCE_FAILED"
    assert len(result["diagnostic"]["details"]["failures"]) == 2
    assert artifact_texts(result, tmp_path) == {
        "logcat.txt": "partial log\n",
        "other.log": "extra log",
    }


def test_evidence_local_write_error_is_reported_without_losing_other_files(
    adb_modules, adb_slice, tmp_path, monkeypatch
):
    adb_slice["resources"]["ADB.main"]["config"]["log_paths"] = ["/good"]
    runtime, _, ctx = bound(adb_modules, adb_slice, tmp_path)
    original = Path.write_text

    def fail_dump(path, *args, **kwargs):
        if path.name == "logcat.txt":
            raise OSError("disk write failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_dump)
    result = runtime.collect("ADB.main", "DIAGNOSTIC_LOGS", {}, ctx)
    assert not result["ok"]
    assert artifact_texts(result, tmp_path) == {"device.txt": "device data"}


def test_evidence_query_errors_remain_errors_and_no_fake_empty_log_is_created(
    adb_modules, adb_slice, tmp_path
):
    class Offline(Device):
        def dump_logcat(self, serial):
            raise GearError("ADB_DEVICE_UNAVAILABLE", "device missing")

    runtime, _, ctx = bound(adb_modules, adb_slice, tmp_path, Offline())
    result = runtime.collect("ADB.main", "DIAGNOSTIC_LOGS", {}, ctx)
    assert not result["ok"] and result["artifacts"] == []
    assert (
        result["diagnostic"]["details"]["failures"][0]["code"]
        == "ADB_DEVICE_UNAVAILABLE"
    )


def test_stop_during_dump_preserves_finished_artifact_but_skips_further_commands(
    adb_modules, adb_slice, tmp_path
):
    class StopDuringDump(Device):
        def dump_logcat(self, serial):
            ctx.stop_token.request()
            return super().dump_logcat(serial)

    adb_slice["resources"]["ADB.main"]["config"]["log_paths"] = ["/one", "/two"]
    runtime, device, ctx = bound(adb_modules, adb_slice, tmp_path, StopDuringDump())
    result = runtime.collect("ADB.main", "DIAGNOSTIC_LOGS", {}, ctx)
    assert not result["ok"] and result["diagnostic"]["code"] == "ADB_EVIDENCE_CANCELLED"
    assert artifact_texts(result, tmp_path) == {"logcat.txt": "current device log\n"}
    assert not any(call[0] == "pull" for call in device.calls)


def test_stop_before_evidence_starts_no_device_command(
    adb_modules, adb_slice, tmp_path
):
    runtime, device, ctx = bound(adb_modules, adb_slice, tmp_path)
    ctx.stop_token.request()
    result = runtime.collect("ADB.main", "DIAGNOSTIC_LOGS", {}, ctx)
    assert not result["ok"] and result["artifacts"] == []
    assert device.calls == [("configure", "missing-adb")]
