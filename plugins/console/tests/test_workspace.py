from copy import deepcopy
import pytest
from PySide6 import QtWidgets
from gear_contracts.api import GearError
from helpers import Factory, slice_for, wait_until


class Subscription:
    def __init__(self, callback):
        self.callback, self.active = callback, True

    def unsubscribe(self):
        self.active = False


class Context:
    plugin_id = "gear.console"

    def __init__(self, data):
        self.data, self.state = deepcopy(data), "IDLE"
        self.devices = ["ADB001", "ADB002"]
        self.commits, self.pending, self.subs = [], [], []
        self.commit_error = None

    def current_slice(self):
        return deepcopy(self.data)

    def configured_device_ids(self):
        return list(self.devices)

    def run_state(self):
        return self.state

    def subscribe_run_state(self, callback):
        self.state_sub = Subscription(callback)
        self.subs.append(self.state_sub)
        return self.state_sub

    def subscribe_environment(self, callback):
        self.env_sub = Subscription(callback)
        self.subs.append(self.env_sub)
        return self.env_sub

    def commit(self, data, report):
        if self.commit_error:
            raise self.commit_error
        assert self.state == "IDLE"
        self.data = deepcopy(data)
        self.commits.append((deepcopy(data), report))

    def submit_manual(self, action, listener):
        assert self.state == "IDLE"
        self.pending.append((action, listener))

    def complete(self):
        action, listener = self.pending.pop(0)
        try:
            listener({"ok": True, "value": action(), "diagnostic": None})
        except GearError as exc:
            listener(
                {
                    "ok": False,
                    "value": None,
                    "diagnostic": {
                        "code": exc.args[0],
                        "message": str(exc),
                        "details": {},
                    },
                }
            )

    def set_state(self, state):
        self.state = state
        self.state_sub.callback(state)


@pytest.fixture
def gui():
    from gear_console.runtime import ConsoleRuntime
    from gear_console.workspace import create_workspace

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    data, factory = slice_for(), Factory()
    runtime = ConsoleRuntime(serial_factory=factory)
    runtime.configure(data)
    context = Context(data)
    workspace = create_workspace(context, runtime)
    yield app, workspace, context, runtime, factory
    workspace.dispose()
    runtime.close()
    workspace.widget.close()
    workspace.widget.deleteLater()
    app.processEvents()


def test_workspace_creation_selection_and_statuses_do_not_open_hardware(gui):
    app, ws, context, runtime, factory = gui
    assert isinstance(ws.widget, QtWidgets.QWidget)
    assert ws.tabs.count() == 1
    assert ws.select_resource("CONSOLE.mcu")
    assert not ws.select_resource("CONSOLE.absent")
    assert set(ws.resource_statuses()) == {"CONSOLE.mcu"}
    assert factory.items == [] and context.commits == [] and context.pending == []


def test_explicit_manual_connect_send_and_clear_display_preserves_cache(gui):
    app, ws, context, runtime, factory = gui
    panel = ws.panels["CONSOLE.mcu"]
    panel.connect_button.click()
    assert factory.items == [] and len(context.pending) == 1
    context.complete()
    factory.items[0].rx.put(b"visible")
    wait_until(lambda: runtime.snapshot("CONSOLE.mcu")["text"] == "visible")
    ws.timer.timeout.emit()
    assert panel.output.toPlainText() == "visible"
    panel.command.setText("user command")
    panel.send_button.click()
    context.complete()
    assert factory.items[0].writes == [b"user command\n"]
    panel.clear_button.click()
    assert panel.output.toPlainText() == ""
    assert runtime.snapshot("CONSOLE.mcu")["text"] == "visible"
    factory.items[0].rx.put(b"next")
    wait_until(lambda: runtime.snapshot("CONSOLE.mcu")["end"] == 11)
    ws.timer.timeout.emit()
    assert panel.output.toPlainText() == "next"


def test_active_disables_manual_config_but_keeps_receiving(gui):
    app, ws, context, runtime, factory = gui
    panel = ws.panels["CONSOLE.mcu"]
    panel.connect_button.click()
    context.complete()
    context.set_state("ACTIVE")
    assert not panel.send_button.isEnabled()
    assert not panel.disconnect_button.isEnabled()
    assert not panel.config_group.isEnabled()
    assert not ws.add_button.isEnabled()
    assert panel.clear_button.isEnabled()
    factory.items[0].rx.put(b"during run")
    wait_until(lambda: "during run" in runtime.snapshot("CONSOLE.mcu")["text"])
    ws.timer.timeout.emit()
    assert panel.output.toPlainText() == "during run"
    before = context.current_slice()
    ws._edit("CONSOLE.mcu", "role", "SOC")
    assert context.current_slice() == before and context.pending == []


def test_creating_one_port_does_not_create_board_placeholders(gui):
    app, ws, context, runtime, factory = gui
    ws.new_alias.setText("soc")
    ws.new_port.setText("COM78")
    ws.new_role.setCurrentText("SOC")
    ws.new_device.setCurrentText("ADB002")
    ws.add_button.click()
    assert len(context.data["resources"]) == 2
    record = context.data["resources"]["CONSOLE.soc"]
    assert record["device"] == "ADB002"
    assert record["config"]["port"] == "COM78" and record["config"]["role"] == "SOC"
    assert "devices" not in context.data and factory.items == []
    assert len(context.commits) == 1


def test_commit_failure_restores_saved_values_and_displays_diagnostic(gui):
    app, ws, context, runtime, factory = gui
    context.commit_error = GearError("PERSISTENCE_ERROR", "disk failure")
    ws.panels["CONSOLE.mcu"].role.setCurrentText("SOC")
    assert context.data["resources"]["CONSOLE.mcu"]["config"]["role"] == "MCU"
    assert ws.panels["CONSOLE.mcu"].role.currentText() == "MCU"
    assert "PERSISTENCE_ERROR" in ws.status_label.text()


def test_shortcut_is_user_configured_and_only_submitted_on_click(gui):
    app, ws, context, runtime, factory = gui
    ws.shortcut_label.setText("Chosen")
    ws.shortcut_command.setText("abc xyz")
    ws.add_shortcut.click()
    assert context.data["plugin"]["config"]["shortcuts"] == [
        {"label": "Chosen", "command": "abc xyz"}
    ]
    assert factory.items == []
    runtime.configure(context.data)
    panel = ws.panels["CONSOLE.mcu"]
    panel.connect_button.click()
    context.complete()
    panel.shortcut_buttons[0].click()
    assert factory.items[0].writes == []
    context.complete()
    assert factory.items[0].writes == [b"abc xyz\n"]


def test_dispose_unsubscribes_and_keeps_shared_connection(gui):
    app, ws, context, runtime, factory = gui
    ws.panels["CONSOLE.mcu"].connect_button.click()
    context.complete()
    ws.dispose()
    ws.dispose()
    assert not ws.timer.isActive() and all(not sub.active for sub in context.subs)
    assert factory.items[0].close_count == 0


def test_dangling_identity_remains_visible_without_rewriting(gui):
    app, ws, context, runtime, factory = gui
    context.devices = []
    context.env_sub.callback()
    panel = ws.panels["CONSOLE.mcu"]
    assert "ADB001" in panel.device.currentText()
    assert "未登记" in panel.device.currentText()
    assert context.data["resources"]["CONSOLE.mcu"]["device"] == "ADB001"
    assert not context.commits and not factory.items


@pytest.mark.parametrize("clear_before_reconfigure", [False, True])
def test_recreated_service_refreshes_equal_length_text_and_resets_old_clear_cursor(
    gui, clear_before_reconfigure
):
    app, ws, context, runtime, factory = gui
    panel = ws.panels["CONSOLE.mcu"]
    panel.connect_button.click()
    context.complete()
    factory.items[0].rx.put(b"old")
    wait_until(lambda: runtime.snapshot("CONSOLE.mcu")["text"] == "old")
    ws.timer.timeout.emit()
    assert panel.output.toPlainText() == "old"
    if clear_before_reconfigure:
        panel.clear_button.click()
        assert panel.output.toPlainText() == ""
    panel.disconnect_button.click()
    context.complete()
    panel.baudrate.setText("9600")
    panel.baudrate.editingFinished.emit()
    runtime.configure(context.data)
    panel = ws.panels["CONSOLE.mcu"]
    panel.connect_button.click()
    action, listener = context.pending.pop(0)
    value = action()
    factory.items[-1].rx.put(b"new")
    wait_until(lambda: runtime.snapshot("CONSOLE.mcu")["text"] == "new")
    listener({"ok": True, "value": value, "diagnostic": None})
    assert panel.output.toPlainText() == "new"
    assert panel.display_cursor == 0


def test_console_can_scroll_inside_a_550px_host_area(gui):
    app, ws, context, runtime, factory = gui
    host = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(host)
    layout.addWidget(ws.widget)
    host.resize(1120, 550)
    host.show()
    app.processEvents()
    try:
        assert host.height() == 550
        assert isinstance(ws.widget, QtWidgets.QScrollArea)
        assert ws.widget.verticalScrollBar().maximum() > 0
        ws.widget.verticalScrollBar().setValue(ws.widget.verticalScrollBar().maximum())
        app.processEvents()
        assert (
            ws.shortcuts_group.mapTo(
                ws.widget.viewport(), ws.shortcuts_group.rect().bottomLeft()
            ).y()
            <= ws.widget.viewport().height()
        )
        assert not factory.items
    finally:
        ws.widget.setParent(None)
        host.close()
        host.deleteLater()


def test_gui_fault_then_com_reassignment_keeps_new_owner_independent(gui):
    app, ws, context, runtime, factory = gui
    panel = ws.panels["CONSOLE.mcu"]
    panel.connect_button.click()
    context.complete()
    failed_port = factory.items[0]
    failed_port.read_error = OSError("receiver lost")
    wait_until(
        lambda: runtime.snapshot("CONSOLE.mcu")["fault"] is not None
        and not runtime.snapshot("CONSOLE.mcu")["worker_alive"]
    )
    assert failed_port.close_count == 1
    ws.timer.timeout.emit()
    assert panel.config_group.isEnabled()
    panel.port.setText("COM78")
    panel.port.editingFinished.emit()
    runtime.configure(context.data)

    ws.new_alias.setText("other")
    ws.new_port.setText("COM77")
    ws.new_role.setCurrentText("SOC")
    ws.new_device.setCurrentText("ADB002")
    ws.add_button.click()
    runtime.configure(context.data)
    other = ws.panels["CONSOLE.other"]
    other.connect_button.click()
    context.complete()
    other_port = factory.items[-1]
    assert other_port is not failed_port
    assert runtime.snapshot("CONSOLE.other")["connected"]

    original = ws.panels["CONSOLE.mcu"]
    assert original.connect_button.isEnabled()
    assert not original.disconnect_button.isEnabled()
    assert runtime.snapshot("CONSOLE.mcu")["port"] == "COM78"
    original.connect_button.click()
    context.complete()
    assert factory.items[-1].settings["port"] == "COM78"
    assert runtime.snapshot("CONSOLE.mcu")["connected"]
    original.disconnect_button.click()
    context.complete()
    assert other_port.close_count == 0
    assert runtime.snapshot("CONSOLE.other")["connected"]
