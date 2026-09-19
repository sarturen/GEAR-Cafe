from copy import deepcopy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from gear_contracts.api import GearError
from gear_camera.workspace import create_workspace
from gear_camera.runtime import CameraRuntime
from gear_camera.service import CameraService


class Subscription:
    def unsubscribe(self):
        pass


class Context:
    def __init__(self):
        self.data = {"plugin": {"id": "gear.camera", "config": {}}, "resources": {}}
        self.state = "IDLE"
        self.pending, self.commits = [], []
        self.failure = None

    def current_slice(self):
        return deepcopy(self.data)

    def configured_device_ids(self):
        return ["BOARD001"]

    def run_state(self):
        return self.state

    def subscribe_run_state(self, callback):
        self.state_callback = callback
        return Subscription()

    def subscribe_environment(self, callback):
        self.environment_callback = callback
        return Subscription()

    def commit(self, data, report):
        if self.failure:
            raise self.failure
        self.data = deepcopy(data)
        self.commits.append((deepcopy(data), report))

    def submit_manual(self, action, listener):
        self.pending.append((action, listener))

    def complete(self):
        action, listener = self.pending.pop(0)
        listener({"ok": True, "value": action(), "diagnostic": None})


class Backend:
    def __init__(self, callback):
        self.callback, self.calls = callback, []

    def request(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command == "list":
            return [{"id": "camera-a", "description": "USB Camera"}]

    def close(self):
        pass


@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    made = []

    def factory(callback):
        backend = Backend(callback)
        made.append(backend)
        return backend

    runtime = CameraRuntime(CameraService(factory))
    context = Context()
    workspace = create_workspace(context, runtime)
    yield app, workspace, context, runtime, made
    workspace.dispose()
    runtime.close()
    workspace.widget.close()
    workspace.widget.deleteLater()
    app.processEvents()


def test_page_load_has_no_discovery_or_capture(gui):
    _, page, ctx, _, backends = gui
    assert backends == []
    assert ctx.pending == []
    assert ctx.commits == []
    page.refresh_button.click()
    assert backends == []
    ctx.complete()
    assert page.camera_selector.currentData() == "camera-a"


def test_preview_can_start_before_any_binding(gui):
    _, page, ctx, _, backends = gui
    page.refresh_button.click()
    ctx.complete()
    page.open_button.click()
    assert len(ctx.pending) == 1 and ctx.data["resources"] == {}
    ctx.complete()
    assert backends[0].calls[-1] == ("open", {"camera_id": "camera-a"})


def test_binding_uses_device_id_and_roi_and_failed_commit_restores(gui):
    _, page, ctx, _, _ = gui
    page.refresh_button.click()
    ctx.complete()
    page.alias.setText("center")
    page.device.setCurrentText("BOARD001")
    page.role.setText("中控屏")
    page.bind_button.click()
    record = ctx.data["resources"]["SCREEN.center"]
    assert record["device"] == "BOARD001"
    assert record["config"]["camera_id"] == "camera-a"
    assert record["config"]["role"] == "中控屏"
    ctx.failure = GearError("PERSISTENCE_ERROR", "disk unavailable")
    page.role.setText("wrong")
    page.role.editingFinished.emit()
    assert page.role.text() == "中控屏"
    assert "PERSISTENCE_ERROR" in page.status.text()


def test_run_locks_control_and_roi_but_keeps_cache_refresh(gui):
    _, page, ctx, _, _ = gui
    ctx.state = "ACTIVE"
    ctx.state_callback("ACTIVE")
    assert not page.open_button.isEnabled()
    assert not page.bind_button.isEnabled()
    assert not page.preview.editable
    assert page.timer.isActive()


def test_roi_drag_accounts_for_image_letterboxing(gui):
    app, page, ctx, _, _ = gui
    page.preview.resize(500, 240)
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    page.preview.set_frame(image)
    page.preview.show()
    app.processEvents()
    rect = page.preview.image_rect()
    begin = QPoint(
        int(rect.x() + rect.width() * 0.1), int(rect.y() + rect.height() * 0.2)
    )
    end = QPoint(
        int(rect.x() + rect.width() * 0.6), int(rect.y() + rect.height() * 0.8)
    )
    QTest.mousePress(page.preview, Qt.MouseButton.LeftButton, pos=begin)
    QTest.mouseMove(page.preview, end)
    QTest.mouseRelease(page.preview, Qt.MouseButton.LeftButton, pos=end)
    assert page.preview.roi["x"] == pytest.approx(0.1, abs=0.01)
    assert page.preview.roi["y"] == pytest.approx(0.2, abs=0.01)
    assert page.preview.roi["width"] == pytest.approx(0.5, abs=0.01)
    assert page.preview.roi["height"] == pytest.approx(0.6, abs=0.01)
    assert ctx.commits == []


def test_queued_manual_action_keeps_the_original_camera(gui):
    _, page, ctx, _, backends = gui
    page.refresh_button.click()
    ctx.complete()
    page.camera_selector.addItem("Other input", "camera-b")
    page.open_button.click()
    page.camera_selector.setCurrentIndex(1)
    ctx.complete()
    assert backends[0].calls[-1] == ("open", {"camera_id": "camera-a"})
    page.stop_button.click()
    page.camera_selector.setCurrentIndex(0)
    ctx.complete()
    assert backends[0].calls[-1] == ("stop", {"camera_id": "camera-b"})


def test_duplicate_or_missing_input_records_can_be_selected_and_removed(gui):
    _, page, ctx, _, _ = gui
    record = {
        "type": "SCREEN",
        "device": "BOARD001",
        "config": {"camera_id": "camera-a", "role": "screen"},
    }
    ctx.data["resources"] = {
        "SCREEN.one": deepcopy(record),
        "SCREEN.two": deepcopy(record),
        "SCREEN.empty": {"type": "SCREEN", "config": {}},
    }
    page._reload()
    page.select_resource("SCREEN.two")
    assert page.alias.text() == "two"
    page.remove_button.click()
    assert "SCREEN.two" not in ctx.data["resources"]
    page.select_resource("SCREEN.empty")
    assert page.alias.text() == "empty"
    page.remove_button.click()
    assert set(ctx.data["resources"]) == {"SCREEN.one"}
