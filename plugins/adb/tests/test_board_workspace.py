import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from gear_contracts.api import GearError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def ui(modules, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[3] / "tests"))
    from test_adb_workspace import Context

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    context = Context()
    context.data["plugin"]["config"]["fastboot_path"] = "fake-fastboot"
    runtime = modules("runtime").AdbRuntime()
    runtime.configure(context.data)
    workspace = modules("workspace").create_workspace(context, runtime)
    yield app, workspace, context, runtime
    workspace.dispose()
    runtime.close()
    workspace.widget.close()
    workspace.widget.deleteLater()
    app.processEvents()


def test_active_gui_reflects_evaluate_and_command_cache_without_io(ui, monkeypatch):
    _, workspace, context, runtime = ui
    svc = runtime.service
    monkeypatch.setattr(
        svc,
        "_discover_adb",
        lambda: [
            {
                "serial": "saved-phone",
                "state": "recovery",
                "transport_id": "11",
                "model": "",
                "usb": "",
            }
        ],
    )
    monkeypatch.setattr(svc, "_discover_fastboot", lambda: [])
    ctx = SimpleNamespace(run_id="r1")
    runtime.begin_run({"plugin_slice": context.data, "resource_ids": ["ADB.main"]}, ctx)
    context.set_state("ACTIVE")
    assert runtime.evaluate("ADB.main", "STATE_IS", {"state": "recovery"}, ctx)[
        "satisfied"
    ]

    def no_query():
        pytest.fail("GUI queried hardware")

    monkeypatch.setattr(svc, "_discover_adb", no_query)
    monkeypatch.setattr(svc, "_discover_fastboot", no_query)
    workspace.log_timer.timeout.emit()
    assert workspace.resource_statuses()["ADB.main"] == "recovery"
    assert not workspace.monitor_start_button.isEnabled()
    assert not workspace.fastboot_button.isEnabled()
    workspace.select_resource("ADB.main")
    assert workspace.target_device.currentText() == "saved-phone"
    assert (
        workspace.binding_table.item(workspace.binding_table.currentRow(), 0).text()
        == "ADB.main"
    )
    assert not context.pending
    with pytest.raises(GearError, match="BUSY"):
        runtime.manual_fastboot("saved-phone", ["reboot"])


def test_query_unavailability_is_visible_without_hiding_adb_rows(ui, monkeypatch):
    _, workspace, context, runtime = ui
    monkeypatch.setattr(
        runtime.service,
        "_discover_adb",
        lambda: [
            {
                "serial": "saved-phone",
                "state": "device",
                "transport_id": "11",
                "model": "",
                "usb": "",
            }
        ],
    )

    def absent():
        raise GearError("FASTBOOT_TOOL_ERROR", "not installed")

    monkeypatch.setattr(runtime.service, "_discover_fastboot", absent)
    workspace.refresh_button.click()
    context.complete()
    workspace.log_timer.timeout.emit()
    assert workspace.resource_statuses()["ADB.main"] == "device"
    assert "FASTBOOT_TOOL_ERROR" in workspace.resource_statuses()["ADB.spare"]
    assert "FASTBOOT_TOOL_ERROR" in workspace.refresh_label.text()


def test_fastboot_input_is_submitted_as_arguments_and_output_is_visible(
    ui, monkeypatch
):
    _, workspace, context, runtime = ui
    calls = []

    def command(serial, args, timeout_s=30):
        calls.append((serial, args))
        return {"exit_code": 0, "stdout": "product: test", "stderr": "OKAY"}

    monkeypatch.setattr(runtime.service, "fastboot", command)
    workspace.target_device.setCurrentText("saved-phone")
    workspace.fastboot_command.setText('getvar "product"')
    workspace.fastboot_button.click()
    assert not calls
    context.complete()
    assert calls == [("saved-phone", ["getvar", "product"])]
    assert "product: test" in workspace.fastboot_output.toPlainText()
    assert "OKAY" in workspace.fastboot_output.toPlainText()


def test_invoke_output_updates_visible_cache_during_run(ui, monkeypatch):
    _, workspace, context, runtime = ui
    monkeypatch.setattr(
        runtime.service,
        "_discover_adb",
        lambda: [
            {
                "serial": "saved-phone",
                "state": "device",
                "transport_id": "11",
                "model": "",
                "usb": "",
            }
        ],
    )
    monkeypatch.setattr(
        runtime.service,
        "_run",
        lambda args: {"exit_code": 0, "stdout": "during run", "stderr": ""},
    )
    ctx = SimpleNamespace(run_id="r1")
    runtime.begin_run({"plugin_slice": context.data, "resource_ids": ["ADB.main"]}, ctx)
    context.set_state("ACTIVE")
    result = runtime.invoke("ADB.main", "SHELL", {"command": "getprop"}, ctx)
    assert result["ok"]
    workspace.log_timer.timeout.emit()
    assert workspace.shell_output.toPlainText() == "during run"
    assert workspace.resource_statuses()["ADB.main"] == "device"


def test_duplicate_legacy_binding_is_shown_as_configuration_error(ui):
    _, workspace, context, runtime = ui
    context.data["resources"]["ADB.spare"]["device"] = "saved-phone"
    context.environment_sub.listener()
    assert "最多一个" in workspace.status_label.text()
    statuses = workspace.resource_statuses()
    assert "ADB_DEVICE_DUPLICATE" in statuses["ADB.main"]
    assert "ADB_DEVICE_DUPLICATE" in statuses["ADB.spare"]
