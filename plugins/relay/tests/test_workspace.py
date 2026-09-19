"""Real relay Qt controls with scheduling and physical transport replaced."""

from __future__ import annotations
from copy import deepcopy
import importlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from gear_contracts.api import GearError
from PySide6 import QtWidgets


class Subscription:
    def __init__(self, listener):
        self.listener, self.active = listener, True

    def unsubscribe(self):
        self.active = False


class Context:
    plugin_id = "gear.relay"

    def __init__(self):
        self.data = {
            "plugin": {"id": "gear.relay", "config": {}},
            "resources": {
                "POWER.main": {
                    "type": "POWER",
                    "config": {"channel": 1},
                    "device": "phone",
                },
                "POWER.spare": {"type": "POWER", "config": {"channel": 8}},
            },
        }
        self.devices = ["phone", "second-phone"]
        self.state = "IDLE"
        self.commits, self.pending, self.subscriptions = [], [], []
        self.commit_error = self.submit_error = None

    def current_slice(self):
        return deepcopy(self.data)

    def configured_device_ids(self):
        return list(self.devices)

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
        if error is None:
            listener({"ok": True, "value": action(), "diagnostic": None})
        else:
            listener({"ok": False, "value": None, "diagnostic": error})

    def set_state(self, state):
        self.state = state
        if self.state_sub.active:
            self.state_sub.listener(state)


class Runtime:
    def __init__(self):
        self.calls = []
        self.cached = {
            "connected": False,
            "fault": None,
            "states": [None] * 8,
            "sources": [None] * 8,
        }
        self.snapshot_reads = 0

    def snapshot(self, controller="main"):
        self.snapshot_reads += 1
        return deepcopy(self.cached)

    def snapshots(self):
        return {"main": self.snapshot(), "rear": self.snapshot()}

    def connect(self, controller="main"):
        self.calls.append(("connect", controller))
        self.cached["connected"] = True
        return self.snapshot()

    def disconnect(self, controller="main"):
        self.calls.append(("disconnect", controller))
        self.cached["connected"] = False
        return self.snapshot()

    def read_states(self, controller="main"):
        self.calls.append(("read_states", controller))
        self.cached["states"] = [True, False, None, False, True, None, False, True]
        self.cached["sources"] = ["read_coils"] * 8
        return self.snapshot()

    def set_channel(self, channel, on, controller="main"):
        self.calls.append(("set_channel", channel, on, controller))
        self.cached["connected"] = True
        self.cached["states"][channel - 1] = on
        self.cached["sources"][channel - 1] = "write_acknowledgement"
        return self.snapshot()

    def set_all(self, on, controller="main"):
        self.calls.append(("set_all", on, controller))
        self.cached["states"] = [on] * 8
        return self.snapshot()

    def close(self):
        raise AssertionError("Workspace does not own the runtime connection")


def create():
    module = importlib.import_module("gear_relay.workspace")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    context, runtime = Context(), Runtime()
    return app, module.create_workspace(context, runtime), context, runtime


def dispose(gui):
    app, workspace, _, _ = gui
    workspace.dispose()
    workspace.widget.close()
    workspace.widget.deleteLater()
    app.processEvents()


@pytest.fixture
def gui():
    result = create()
    yield result
    dispose(result)


def test_open_navigation_status_are_read_only_and_show_eight_physical_channels(gui):
    _, workspace, context, runtime = gui
    before = context.current_slice()
    assert isinstance(workspace.widget, QtWidgets.QWidget)
    assert len(workspace.channel_buttons) == 8
    assert workspace.select_resource("POWER.main")
    assert workspace.binding_device.currentData() == "phone"
    assert "POWER.main" in workspace.resource_statuses()
    assert not workspace.select_resource("POWER.missing")
    assert context.data == before
    assert context.pending == context.commits == runtime.calls == []


def test_edit_parameter_migrates_controller_but_preserves_ids_and_board_device(gui):
    _, workspace, context, runtime = gui
    workspace.port.setText("COM77")
    workspace.port.editingFinished.emit()
    assert context.data["plugin"]["config"] == {
        "controllers": {"main": {"port": "COM77"}}
    }
    assert context.data["resources"]["POWER.main"] == {
        "type": "POWER",
        "device": "phone",
        "config": {"controller": "main", "channel": 1},
    }
    assert context.commits[-1][1]["status"] == "VALID"
    assert "devices" not in context.data and runtime.calls == []


@pytest.mark.parametrize(
    "field,text,expected",
    [
        ("baudrate", "19200", 19200),
        ("stopbits", "2", 2),
        ("unit_id", "247", 247),
        ("timeout_s", "0.5", 0.5),
        ("poll_interval_ms", "500", 500),
        ("baudrate", "bad", "bad"),
        ("timeout_s", "nan", "nan"),
    ],
)
def test_numeric_edits_save_finite_json_and_validation(gui, field, text, expected):
    _, workspace, context, _ = gui
    editor = getattr(workspace, field)
    editor.setText(text)
    editor.editingFinished.emit()
    assert context.data["plugin"]["config"]["controllers"]["main"][field] == expected
    if isinstance(expected, str):
        assert context.commits[-1][1]["status"] == "INVALID"


@pytest.mark.parametrize(
    "button,expected",
    [
        ("connect_button", ("connect", "main")),
        ("disconnect_button", ("disconnect", "main")),
        ("read_button", ("read_states", "main")),
        ("all_on_button", ("set_all", True, "main")),
        ("all_off_button", ("set_all", False, "main")),
    ],
)
def test_actions_queued_and_disabled_until_completion(gui, button, expected):
    _, workspace, context, runtime = gui
    control = getattr(workspace, button)
    control.click()
    assert not runtime.calls and len(context.pending) == 1
    assert not control.isEnabled()
    context.complete()
    assert runtime.calls == [expected] and control.isEnabled()


@pytest.mark.parametrize("channel", range(1, 9))
@pytest.mark.parametrize("on", [True, False])
def test_physical_channel_buttons_route_to_selected_controller(gui, channel, on):
    _, workspace, context, runtime = gui
    context.data["plugin"]["config"] = {
        "controllers": {"main": {"port": "COM77"}, "rear": {"port": "COM78"}}
    }
    context.environment_sub.listener()
    workspace.controller_selector.setCurrentText("rear")
    runtime.cached["connected"] = True
    runtime.cached["states"][channel - 1] = not on
    workspace._refresh_snapshot()
    button = workspace.channel_buttons[channel - 1]
    assert button.isChecked() is (not on)
    button.click()
    # A queued write must not display an unconfirmed relay state.
    assert button.isChecked() is (not on)
    context.complete()
    assert runtime.calls == [("set_channel", channel, on, "rear")]
    assert button.isChecked() is on
    assert ("吸合" if on else "释放") in button.text()
    assert "回执" in button.toolTip()


def test_assignment_uses_shared_device_custom_role_no_missing_role_placeholders(gui):
    _, workspace, context, runtime = gui
    workspace.channel_picker.setCurrentIndex(2)
    workspace.binding_alias.setText("custom")
    workspace.binding_device.setCurrentIndex(
        workspace.binding_device.findData("second-phone")
    )
    workspace.binding_role.setCurrentText("reset")
    workspace.binding_save_button.click()
    assert context.data["resources"]["POWER.custom"] == {
        "type": "POWER",
        "device": "second-phone",
        "config": {"controller": "main", "channel": 3, "role": "reset"},
    }
    assert len(context.data["resources"]) == 3
    assert "devices" not in context.data and runtime.calls == []
    workspace.binding_unassign_button.click()
    assert context.data["resources"]["POWER.custom"] == {
        "type": "POWER",
        "config": {"controller": "main", "channel": 3},
    }
    workspace.binding_remove_button.click()
    assert "POWER.custom" not in context.data["resources"]


def test_legacy_private_name_is_pending_and_never_automatically_becomes_device(gui):
    _, workspace, context, runtime = gui
    context.data["resources"]["POWER.main"]["config"].update(
        device_name="自由名称", terminal="KL30"
    )
    context.data["resources"]["POWER.old"] = {
        "type": "POWER",
        "config": {"device_name": "不是真编码", "terminal": "KL15"},
    }
    context.environment_sub.listener()
    workspace.select_resource("POWER.old")
    assert workspace.binding_channel.currentData() is None
    assert "待归属" in workspace.legacy_hint.text()
    assert workspace.binding_device.currentData() == ""
    assert "不是真编码" not in [
        workspace.binding_device.itemData(i)
        for i in range(workspace.binding_device.count())
    ]
    workspace.binding_remove_button.click()
    assert "POWER.old" not in context.data["resources"]
    workspace.select_resource("POWER.main")
    workspace.binding_save_button.click()
    record = context.data["resources"]["POWER.main"]
    assert record["device"] == "phone" and record["config"]["role"] == "KL30"
    assert "device_name" not in record["config"] and "terminal" not in record["config"]
    assert runtime.calls == []


def test_offline_registered_device_preserved_on_configuration_and_reload(gui):
    _, workspace, context, runtime = gui
    workspace.select_resource("POWER.main")
    assert workspace.binding_device.currentData() == "phone"
    workspace.binding_role.setCurrentText("KL30")
    workspace.binding_save_button.click()
    assert context.data["resources"]["POWER.main"]["device"] == "phone"
    context.devices.remove("phone")
    context.environment_sub.listener()
    workspace.select_resource("POWER.main")
    assert workspace.binding_device.currentData() == "phone"
    assert "未登记" in workspace.binding_device.currentText()
    assert runtime.calls == []


def test_run_locks_mutations_but_preserves_navigation_and_cache_updates(gui):
    _, workspace, context, runtime = gui
    before = context.current_slice()
    context.set_state("ACTIVE")
    assert all(not control.isEnabled() for control in workspace._controls)
    assert workspace.select_resource("POWER.main")
    workspace.port.setText("COM99")
    workspace.port.editingFinished.emit()
    workspace.binding_save_button.click()
    runtime.cached["connected"] = True
    runtime.cached["states"][0] = True
    runtime.cached["sources"][0] = "write_acknowledgement"
    workspace.state_timer.timeout.emit()
    assert workspace.channel_buttons[0].isChecked()
    assert not workspace.channel_buttons[0].isEnabled()
    assert "回执" in workspace.resource_statuses()["POWER.main"]
    assert context.data == before and context.pending == runtime.calls == []
    context.set_state("IDLE")
    assert workspace.connect_button.isEnabled()


def test_add_controller_does_not_create_resources_and_delete_cannot_orphan(gui):
    _, workspace, context, runtime = gui
    workspace.new_controller_name.setText("rear")
    workspace.add_controller_button.click()
    assert set(context.data["plugin"]["config"]["controllers"]) == {"main", "rear"}
    assert len(context.data["resources"]) == 2
    workspace.controller_selector.setCurrentText("main")
    workspace.remove_controller_button.click()
    assert "main" in context.data["plugin"]["config"]["controllers"]
    assert "资源" in workspace.status_label.text()
    workspace.controller_selector.setCurrentText("rear")
    workspace.remove_controller_button.click()
    assert "rear" not in context.data["plugin"]["config"]["controllers"]
    assert runtime.calls == []


def test_errors_visible_and_dispose_ignores_late_callbacks_without_closing_runtime(gui):
    _, workspace, context, runtime = gui
    context.commit_error = GearError("PERSISTENCE_ERROR", "save failed")
    workspace.port.setText("COM77")
    workspace.port.editingFinished.emit()
    assert "PERSISTENCE_ERROR" in workspace.status_label.text()
    context.submit_error = GearError("BUSY", "active")
    workspace.connect_button.click()
    assert (
        "BUSY" in workspace.status_label.text() and workspace.connect_button.isEnabled()
    )
    context.submit_error = None
    workspace.read_button.click()
    workspace.dispose()
    before = workspace.status_label.text()
    reads = runtime.snapshot_reads
    context.complete({"code": "TIMEOUT", "message": "late", "details": {}})
    workspace.state_timer.timeout.emit()
    assert workspace.status_label.text() == before and runtime.snapshot_reads == reads
    assert not workspace.state_timer.isActive()
    assert all(not subscription.active for subscription in context.subscriptions)


def test_legacy_name_without_device_requires_explicit_selection_or_unassign(gui):
    _, workspace, context, _ = gui
    context.data["resources"]["POWER.spare"]["config"].update(
        device_name="not-a-device", terminal="KL15"
    )
    context.environment_sub.listener()
    workspace.select_resource("POWER.spare")
    before = context.current_slice()
    workspace.binding_save_button.click()
    assert context.data == before
    assert "设备号" in workspace.status_label.text()
    workspace.binding_unassign_button.click()
    assert context.data["resources"]["POWER.spare"] == {
        "type": "POWER",
        "config": {"controller": "main", "channel": 8},
    }


def test_unknown_state_never_looks_like_a_confirmed_release(gui):
    _, workspace, context, runtime = gui
    button = workspace.channel_buttons[0]
    assert "未知" in button.text() and not button.isEnabled()
    runtime.cached.update(connected=True, states=[False] * 8)
    workspace._refresh_snapshot()
    assert "释放" in button.text() and button.isEnabled()
    runtime.cached["fault"] = "disconnected"
    workspace._refresh_snapshot()
    assert "未知" in button.text() and not button.isEnabled()
    assert context.pending == []


def test_failed_toggle_restores_cached_state_and_keeps_target_controller(gui):
    _, workspace, context, runtime = gui
    context.data["plugin"]["config"] = {
        "controllers": {"main": {"port": "COM77"}, "rear": {"port": "COM78"}}
    }
    workspace._reload()
    runtime.cached.update(connected=True, states=[False] * 8)
    workspace._refresh_snapshot()
    button = workspace.channel_buttons[0]
    button.click()
    workspace.controller_selector.setCurrentText("rear")
    action, listener = context.pending.pop(0)
    action()
    assert runtime.calls == [("set_channel", 1, True, "main")]
    runtime.cached["states"][0] = False
    listener(
        {
            "ok": False,
            "value": None,
            "diagnostic": {
                "code": "RELAY_TIMEOUT",
                "message": "timeout",
                "details": {},
            },
        }
    )
    assert not button.isChecked() and "RELAY_TIMEOUT" in workspace.status_label.text()


def test_compact_controls_are_one_row_with_collapsed_configuration(gui):
    app, workspace, context, runtime = gui
    workspace.widget.resize(1060, 350)
    workspace.widget.show()
    app.processEvents()
    assert not workspace.serial_box.isVisible()
    assert not workspace.assignment_box.isVisible()
    assert (
        len(
            {
                b.mapTo(workspace.widget, b.rect().topLeft()).y()
                for b in workspace.channel_buttons
            }
        )
        == 1
    )
    assert all(b.isCheckable() for b in workspace.channel_buttons)
    assert "phone" in workspace.channel_owners[0].text()
    workspace.binding_toggle.click()
    assert workspace.assignment_box.isVisible()
    workspace.select_resource("POWER.spare")
    assert workspace.channel_picker.currentData() == 8
    assert context.pending == []
