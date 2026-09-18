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
