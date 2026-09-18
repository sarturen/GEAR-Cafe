"""Real Qt controls with only host scheduling and physical ADB replaced."""

from __future__ import annotations

from copy import deepcopy
import importlib
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from gear_contracts.api import GearError

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


class Subscription:
    def __init__(self, listener):
        self.listener = listener
        self.active = True

    def unsubscribe(self):
        self.active = False


class Context:
    plugin_id = "gear.adb"

    def __init__(self):
        self.data = {
            "plugin": {"id": "gear.adb", "config": {"adb_path": "adb"}},
            "devices": {"saved-phone": {}, "second-phone": {}},
            "resources": {
                "ADB.main": {"type": "ADB", "device": "saved-phone", "config": {}},
                "ADB.spare": {"type": "ADB", "device": "second-phone", "config": {}},
            },
        }
        self.state = "IDLE"
        self.subscriptions = []
        self.commits = []
        self.pending = []
        self.commit_error = None
        self.submit_error = None

    def current_slice(self):
        return deepcopy(self.data)

    def configured_device_ids(self):
        return sorted(self.data["devices"])

    def run_state(self):
        return self.state

    def subscribe_run_state(self, listener):
        self.state_sub = Subscription(listener)
        self.subscriptions.append(self.state_sub)
        return self.state_sub

    def subscribe_environment(self, listener):
        self.environment_sub = Subscription(listener)
        self.subscriptions.append(self.environment_sub)
        return self.environment_sub

    def commit(self, full_slice, validation_report):
        if self.commit_error:
            raise self.commit_error
        self.data = deepcopy(full_slice)
        self.commits.append((deepcopy(full_slice), deepcopy(validation_report)))

    def submit_manual(self, action, listener):
        if self.submit_error:
            raise self.submit_error
        self.pending.append((action, listener))

    def complete(self, error=None):
        action, listener = self.pending.pop(0)
        if error:
            listener({"ok": False, "value": None, "diagnostic": error})
        else:
            listener({"ok": True, "value": action(), "diagnostic": None})

    def set_state(self, state):
        self.state = state
        if self.state_sub.active:
            self.state_sub.listener(state)


class Runtime:
    def __init__(self):
        self.calls = []
        self.discovered = [
            {
                "serial": "new-phone",
                "state": "unauthorized",
                "usb": "1-2",
                "transport_id": "7",
                "model": "Pixel_8",
            }
        ]
        self.snapshot = {"running": False, "lines": [], "path": None, "error": None}

    def refresh_devices(self):
        self.calls.append(("refresh",))
        return self.discovered

    def manual_shell(self, serial, command):
        self.calls.append(("shell", serial, command))
        return {"exit_code": 3, "stdout": "shell output", "stderr": "shell error"}

    def manual_pull(self, serial, remote_path, destination):
        self.calls.append(("pull", serial, remote_path, destination))
        return {"exit_code": 0, "stdout": "one file pulled", "stderr": ""}

    def start_logcat(self, serial, destination):
        self.calls.append(("start_logcat", serial, destination))
        self.snapshot = {
            "running": True,
            "lines": ["first log line"],
            "path": destination,
            "error": None,
        }
        return deepcopy(self.snapshot)

    def stop_logcat(self, serial):
        self.calls.append(("stop_logcat", serial))
        self.snapshot["running"] = False
        return deepcopy(self.snapshot)

    def logcat_snapshot(self, serial):
        return deepcopy(self.snapshot)

    def close(self):
        raise AssertionError("Workspace must not close the shared runtime")


@pytest.fixture
def gui(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "plugins" / "adb"))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    module = importlib.import_module("gear_adb.workspace")
    context, runtime = Context(), Runtime()
    workspace = module.create_workspace(context, runtime)
    yield app, workspace, context, runtime
    workspace.dispose()
    workspace.widget.close()
    workspace.widget.deleteLater()
    app.processEvents()
    for name in list(sys.modules):
        if name == "gear_adb" or name.startswith("gear_adb."):
            sys.modules.pop(name, None)


def table_rows(table):
    return [
        [table.item(row, col).text() for col in range(table.columnCount())]
        for row in range(table.rowCount())
    ]


def test_configuration_commits_preserve_all_devices_and_resources(gui):
    _, workspace, context, runtime = gui
    assert isinstance(workspace.widget, QtWidgets.QWidget)
    workspace.adb_path.setText("C:/Android/platform-tools/adb.exe")
    workspace.adb_path.editingFinished.emit()
    assert context.data == {
        "plugin": {
            "id": "gear.adb",
            "config": {"adb_path": "C:/Android/platform-tools/adb.exe"},
        },
        "devices": {"saved-phone": {}, "second-phone": {}},
        "resources": {
            "ADB.main": {"type": "ADB", "device": "saved-phone", "config": {}},
            "ADB.spare": {"type": "ADB", "device": "second-phone", "config": {}},
        },
    }
    assert context.commits[-1][1]["status"] == "VALID"
    assert runtime.calls == []


def test_explicit_refresh_retains_disconnected_identities_and_shows_usb_state(gui):
    _, workspace, context, runtime = gui
    before = context.current_slice()
    workspace.refresh_button.click()
    assert runtime.calls == []  # Hardware work has only been queued.
    assert not workspace.refresh_button.isEnabled()
    context.complete()
    rows = table_rows(workspace.device_table)
    assert {row[0] for row in rows} == {"saved-phone", "second-phone", "new-phone"}
    assert "unauthorized" in " ".join(
        next(row for row in rows if row[0] == "new-phone")
    )
    assert "未发现" in " ".join(next(row for row in rows if row[0] == "saved-phone"))
    assert context.current_slice() == before
    assert workspace.refresh_button.isEnabled()
    assert "尚未" not in workspace.refresh_label.text()


def test_manual_registration_and_binding_edit_preserve_other_records(gui):
    _, workspace, context, runtime = gui
    workspace.serial_input.setText("offline-phone")
    workspace.register_button.click()
    assert set(context.data["devices"]) == {
        "saved-phone",
        "second-phone",
        "offline-phone",
    }
    workspace.binding_new_button.click()
    workspace.binding_alias.setText("test")
    workspace.binding_device.setCurrentText("offline-phone")
    workspace.binding_save_button.click()
    assert context.data["resources"]["ADB.test"] == {
        "type": "ADB",
        "device": "offline-phone",
        "config": {},
    }
    rows = table_rows(workspace.binding_table)
    workspace.binding_table.selectRow(
        next(i for i, row in enumerate(rows) if row[0] == "ADB.test")
    )
    workspace.binding_alias.setText("renamed")
    workspace.binding_device.setCurrentText("saved-phone")
    workspace.binding_save_button.click()
    assert "ADB.test" not in context.data["resources"]
    assert context.data["resources"]["ADB.renamed"]["device"] == "saved-phone"
    assert context.data["resources"]["ADB.spare"]["device"] == "second-phone"
    workspace.binding_remove_button.click()
    assert set(context.data["resources"]) == {"ADB.main", "ADB.spare"}
    assert "offline-phone" in context.data["devices"]
    assert runtime.calls == []


def test_shell_and_pull_are_submitted_with_captured_input_and_visible_results(gui):
    _, workspace, context, runtime = gui
    workspace.target_device.setCurrentText("saved-phone")
    workspace.shell_command.setText("getprop ro.product.model")
    workspace.shell_button.click()
    workspace.shell_command.setText("changed after submission")
    assert runtime.calls == []
    context.complete()
    assert runtime.calls == [("shell", "saved-phone", "getprop ro.product.model")]
    assert "shell output" in workspace.shell_output.toPlainText()
    assert "shell error" in workspace.shell_output.toPlainText()
    assert "3" in workspace.shell_result.text()
    workspace.pull_remote.setText("/sdcard/report.txt")
    workspace.pull_destination.setText("C:/results")
    workspace.pull_button.click()
    context.complete()
    assert runtime.calls[-1] == (
        "pull",
        "saved-phone",
        "/sdcard/report.txt",
        "C:/results",
    )
    assert "one file pulled" in workspace.pull_output.toPlainText()


def test_run_and_manual_busy_disable_mutations_but_retain_output(gui):
    _, workspace, context, _ = gui
    workspace.shell_output.setPlainText("previous result")
    controls = [
        workspace.adb_path,
        workspace.refresh_button,
        workspace.serial_input,
        workspace.register_button,
        workspace.binding_alias,
        workspace.binding_device,
        workspace.binding_save_button,
        workspace.binding_remove_button,
        workspace.shell_command,
        workspace.shell_button,
        workspace.pull_button,
        workspace.log_start_button,
        workspace.log_stop_button,
    ]
    context.set_state("ACTIVE")
    assert all(not control.isEnabled() for control in controls)
    assert workspace.shell_output.toPlainText() == "previous result"
    context.set_state("IDLE")
    assert workspace.refresh_button.isEnabled()
    workspace.refresh_button.click()
    assert all(not control.isEnabled() for control in controls)
    context.set_state("IDLE")
    assert not workspace.refresh_button.isEnabled()
    context.complete()
    assert workspace.refresh_button.isEnabled()


def test_manual_and_commit_errors_are_shown_inline_and_controls_recover(gui):
    _, workspace, context, _ = gui
    context.commit_error = GearError("BUSY", "Configuration is pending")
    workspace.adb_path.setText("new-adb")
    workspace.adb_path.editingFinished.emit()
    assert "BUSY" in workspace.status_label.text()
    assert context.data["plugin"]["config"]["adb_path"] == "adb"
    context.submit_error = GearError("BUSY", "Run is active")
    workspace.refresh_button.click()
    assert "BUSY" in workspace.status_label.text()
    assert workspace.refresh_button.isEnabled()
    context.submit_error = None
    workspace.refresh_button.click()
    context.complete(
        {"code": "ADB_NOT_FOUND", "message": "ADB executable missing", "details": {}}
    )
    assert "ADB_NOT_FOUND" in workspace.status_label.text()
    assert workspace.refresh_button.isEnabled()


def test_log_cache_updates_during_run_and_dispose_never_stops_shared_logcat(gui):
    app, workspace, context, runtime = gui
    workspace.target_device.setCurrentText("saved-phone")
    workspace.log_destination.setText("C:/logs/device.txt")
    workspace.log_start_button.click()
    context.complete()
    assert runtime.calls == [("start_logcat", "saved-phone", "C:/logs/device.txt")]
    context.set_state("ACTIVE")
    runtime.snapshot["lines"].append("during run")
    runtime.snapshot["error"] = "USB disconnected"
    workspace.log_timer.timeout.emit()
    assert "during run" in workspace.log_output.toPlainText()
    assert "USB disconnected" in workspace.log_status.text()
    assert "C:/logs/device.txt" in workspace.log_path_label.text()
    workspace.dispose()
    workspace.dispose()
    assert not workspace.log_timer.isActive()
    assert all(not sub.active for sub in context.subscriptions)
    assert runtime.snapshot["running"] is True
    assert len(runtime.calls) == 1
    app.processEvents()


def test_logcat_explicit_stop_is_submitted_and_late_completion_is_ignored(gui):
    _, workspace, context, runtime = gui
    workspace.target_device.setCurrentText("saved-phone")
    runtime.snapshot = {
        "running": True,
        "lines": ["retained log"],
        "path": "C:/log.txt",
        "error": None,
    }
    workspace.log_timer.timeout.emit()
    workspace.log_stop_button.click()
    assert runtime.calls == []
    context.complete()
    assert runtime.calls == [("stop_logcat", "saved-phone")]
    assert "retained log" in workspace.log_output.toPlainText()
    workspace.refresh_button.click()
    workspace.dispose()
    context.complete()
    assert runtime.calls[-1] == ("refresh",)


def test_environment_notification_updates_configuration_without_hardware_io(gui):
    _, workspace, context, runtime = gui
    context.data["devices"]["external-phone"] = {}
    context.data["plugin"]["config"]["adb_path"] = "external-adb"
    context.environment_sub.listener()
    assert workspace.adb_path.text() == "external-adb"
    assert "external-phone" in {row[0] for row in table_rows(workspace.device_table)}
    assert runtime.calls == []


def test_blank_manual_inputs_and_duplicate_binding_do_not_submit_or_overwrite(gui):
    _, workspace, context, _ = gui
    workspace.shell_command.clear()
    workspace.shell_button.click()
    workspace.pull_remote.clear()
    workspace.pull_button.click()
    assert context.pending == []
    before = context.current_slice()
    workspace.binding_new_button.click()
    workspace.binding_alias.setText("main")
    workspace.binding_device.setCurrentText("second-phone")
    workspace.binding_save_button.click()
    assert context.current_slice() == before
    assert workspace.status_label.text()


def test_removing_identity_keeps_resource_associations_for_explicit_repair(gui):
    _, workspace, context, _ = gui
    before = deepcopy(context.data["resources"])
    workspace.serial_input.setText("saved-phone")
    workspace.unregister_button.click()
    assert set(context.data["devices"]) == {"second-phone"}
    assert context.data["resources"] == before
    assert context.commits[-1][1]["status"] == "INVALID"
    workspace.binding_table.selectRow(0)
    assert workspace.binding_device.currentText() == "saved-phone"


def test_semantically_invalid_saved_config_is_editable_without_crashing(gui):
    _, workspace, context, _ = gui
    context.data["plugin"]["config"]["adb_path"] = 123
    context.environment_sub.listener()
    assert workspace.adb_path.text() == "123"
    assert "字符串" in workspace.status_label.text()
    workspace.adb_path.setText("adb")
    workspace.adb_path.editingFinished.emit()
    assert context.commits[-1][1]["status"] == "VALID"


def test_run_allows_switching_passive_log_view_without_manual_commands(gui):
    _, workspace, context, runtime = gui
    context.set_state("ACTIVE")
    assert workspace.target_device.isEnabled()
    workspace.target_device.setCurrentText("second-phone")
    assert not workspace.log_start_button.isEnabled()
    assert runtime.calls == []
