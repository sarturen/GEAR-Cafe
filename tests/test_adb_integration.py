import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from gear_framework.host import Framework
from test_adb_runtime import Device
from test_host import run, submit, wait_phase


@pytest.fixture
def adb_bench(tmp_path):
    app = tmp_path / "app"
    source = Path(__file__).parents[1] / "plugins" / "adb"
    shutil.copytree(
        source, app / "plugins" / "adb", ignore=shutil.ignore_patterns("__pycache__")
    )
    documents = {
        "environment": {
            "api": "gear.environment/v1",
            "name": "usb",
            "plugins": {"gear.adb": {"config": {"adb_path": "missing-adb"}}},
            "devices": {"USB123": {}},
            "resources": {
                "ADB.main": {"type": "ADB", "plugin": "gear.adb", "device": "USB123"}
            },
        },
        "project": {
            "api": "gear.project/v1",
            "name": "usb",
            "resources": {"ADB.main": {"type": "ADB"}},
        },
        "case": {
            "api": "gear.dsl/v1",
            "name": "usb",
            "body": [
                {
                    "do": {
                        "resource": "ADB.main",
                        "operation": "SHELL",
                        "args": {"command": "echo hello"},
                    }
                }
            ],
        },
    }
    paths = {}
    for key, data in documents.items():
        paths[key] = app / f"{key}.yaml"
        paths[key].write_text(json.dumps(data), encoding="utf-8")
    return {"app": app, **paths}


def test_adb_registry_preflight_and_decline_never_launch_adb(adb_bench, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("ADB was used before execution was confirmed")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with Framework(adb_bench["app"], adb_bench["environment"]) as host:
        rid = submit(host, adb_bench)
        status = wait_phase(host, rid, "WAITING_CONFIRMATION", "FINISHED")
        assert status["preflight"]["ok"], status
        assert status["phase"] == "WAITING_CONFIRMATION"
        host.stop(rid)
        assert wait_phase(host, rid, "FINISHED")["outcome"] == "DECLINED"


def test_adb_workspace_service_survives_two_framework_runs(adb_bench, tmp_path):
    with Framework(adb_bench["app"], adb_bench["environment"]) as host:
        runtime = host._registry.entries["gear.adb"].runtime
        service = Device()
        runtime.service = service
        callbacks, results = [], []
        workspace = host.workspace_context("gear.adb", callbacks.append)
        workspace.submit_manual(
            lambda: runtime.start_logcat("USB123", str(tmp_path / "live.log")),
            results.append,
        )
        host.flush()
        while callbacks:
            callbacks.pop(0)()
        assert results[0]["ok"]
        for _ in range(2):
            _, status = run(host, adb_bench)
            assert status["result"] == "PASS"
            assert runtime.logcat_snapshot("USB123")["running"]
            report = json.loads(Path(status["report_path"]).read_text(encoding="utf-8"))
            assert report["plugins"] == {"gear.adb": "1.0.0"}
            assert report["coverage"][0]["executed"]
        assert [c for c in service.calls if c[0] == "shell"] == [
            ("shell", "USB123", "echo hello"),
            ("shell", "USB123", "echo hello"),
        ]
    assert service.closed


def test_adb_core_discovery_and_configuration_do_not_import_qt(adb_bench):
    script = """
import sys
from gear_framework.registry import Registry
registry = Registry.load(sys.argv[1])
try:
    runtime = registry.entries["gear.adb"].runtime
    runtime.configure({"plugin":{"id":"gear.adb","config":{}},"resources":{},"devices":{}})
    assert not any(n.startswith(("PySide6", "PyQt")) for n in sys.modules)
finally:
    assert registry.close() == []
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(adb_bench["app"])],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_unavailable_condition_runs_without_a_shell_probe(adb_bench):
    case = {
        "api": "gear.dsl/v1",
        "name": "offline",
        "body": [
            {
                "assert": {
                    "all": [{"resource": "ADB.main", "condition": "UNAVAILABLE"}],
                    "within": "50ms",
                    "every": "5ms",
                }
            }
        ],
    }
    adb_bench["case"].write_text(json.dumps(case), encoding="utf-8")
    with Framework(adb_bench["app"], adb_bench["environment"]) as host:
        device = Device()
        device.devices = []
        host._registry.entries["gear.adb"].runtime.service = device
        _, status = run(host, adb_bench)
        assert status["result"] == "PASS"
        assert all(call[0] == "discover" for call in device.calls)


def test_complete_plugin_flow_uses_real_adb_child_processes_and_preserves_primary_failure(
    adb_bench, tmp_path, monkeypatch
):
    from test_adb_transport import FAKE_ADB, USB_FRAME

    helper = tmp_path / "adb.py"
    helper.write_text(FAKE_ADB, encoding="utf-8")
    settings = tmp_path / "physical.json"
    settings.write_text(
        json.dumps(
            {
                "frame": USB_FRAME.replace('"phone"', '"USB123"'),
                "shell_exit": 7,
                "pull_exit": 1,
            }
        ),
        encoding="utf-8",
    )
    calls = tmp_path / "commands.jsonl"
    monkeypatch.setenv("GEAR_FAKE_ADB_CONFIG", str(settings))
    monkeypatch.setenv("GEAR_FAKE_ADB_CALLS", str(calls))
    real_popen = subprocess.Popen
    processes = []

    def popen(argv, **kwargs):
        assert argv[0] == "fake-adb"
        proc = real_popen([sys.executable, "-u", str(helper), *argv[1:]], **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)

    env = json.loads(adb_bench["environment"].read_text())
    env["plugins"]["gear.adb"]["config"]["adb_path"] = "fake-adb"
    env["resources"]["ADB.main"]["config"] = {"log_paths": ["/sdcard/log dir"]}
    adb_bench["environment"].write_text(json.dumps(env), encoding="utf-8")
    case = {
        "api": "gear.dsl/v1",
        "name": "usb_failure",
        "body": [
            {
                "assert": {
                    "all": [{"resource": "ADB.main", "condition": "AVAILABLE"}],
                    "within": "2s",
                    "every": "10ms",
                }
            },
            {
                "do": {
                    "resource": "ADB.main",
                    "operation": "SHELL",
                    "args": {"command": "exit 7"},
                }
            },
        ],
        "evidence_on_fail": [{"resource": "ADB.main", "evidence": "DIAGNOSTIC_LOGS"}],
    }
    adb_bench["case"].write_text(json.dumps(case), encoding="utf-8")
    try:
        with Framework(adb_bench["app"], adb_bench["environment"]) as host:
            _, status = run(host, adb_bench)
            assert status["result"] == "FAIL"
            assert not status["session_blocked"]
            assert (
                status["primary_failure"]["diagnostic"]["code"] == "ADB_COMMAND_FAILED"
            )
            report_path = Path(status["report_path"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            evidence = report["evidence"][0]["result"]
            assert evidence["ok"] is False  # Partial pull reports its error.
            assert evidence["diagnostic"]["code"] == "ADB_EVIDENCE_FAILED"
            saved = {
                Path(a["path"])
                .name: (report_path.parent / a["path"])
                .read_text(encoding="utf-8")
                for a in evidence["artifacts"]
            }
            assert saved == {
                "logcat.txt": "current log buffer café\n",
                "copied.txt": "pulled café",
            }
            assert all(entry["executed"] for entry in report["coverage"])
        assert all(process.poll() is not None for process in processes)
        recorded = [json.loads(line) for line in calls.read_text().splitlines()]
        assert ["-t", "11", "logcat", "-d", "-v", "threadtime"] in recorded
        assert not any(
            "-c" in command or "kill-server" in command for command in recorded
        )
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()


def test_bad_extra_log_paths_reject_before_hardware_use(adb_bench, monkeypatch):
    env = json.loads(adb_bench["environment"].read_text())
    env["resources"]["ADB.main"]["config"] = {"log_paths": ["relative/file"]}
    adb_bench["environment"].write_text(json.dumps(env), encoding="utf-8")
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("Preflight contacted ADB")
    )
    with Framework(adb_bench["app"], adb_bench["environment"]) as host:
        status = wait_phase(host, submit(host, adb_bench), "FINISHED")
        assert status["outcome"] == "REJECTED"
        assert any(
            d["code"] == "ADB_LOG_PATHS_INVALID"
            for d in status["preflight"]["diagnostics"]
        )
