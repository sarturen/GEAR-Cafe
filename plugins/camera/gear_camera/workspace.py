"""One camera workspace: explicit discovery, preview, device binding and ROI."""

from copy import deepcopy
import re
from PySide6.QtCore import QSignalBlocker, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QComboBox,
    QPushButton,
    QLineEdit,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QGroupBox,
)
from gear_contracts.api import GearError
from .config import FULL_ROI, valid_roi, validate_slice
from .preview import Preview


class CameraWorkspace:
    def __init__(self, context, runtime):
        self.context, self.runtime = context, runtime
        self._disposed = self._busy = False
        self._active = context.run_state() == "ACTIVE"
        self._resources, self._devices = {}, {}
        self._selected = None
        self._frame_key = None
        self._event_sequence = 0
        self.widget = QWidget()
        self.widget.setMinimumSize(620, 300)
        layout = QVBoxLayout(self.widget)
        title = QLabel("USB 摄像头 · 预览与屏幕资源")
        title.setStyleSheet("font-size: 19px; font-weight: 600;")
        layout.addWidget(title)
        toolbar = QHBoxLayout()
        self.camera_selector = QComboBox()
        self.camera_selector.setMinimumWidth(180)
        toolbar.addWidget(self.camera_selector, 1)
        self.refresh_button = QPushButton("刷新输入")
        self.open_button = QPushButton("打开预览")
        self.stop_button = QPushButton("关闭输入")
        for button in (self.refresh_button, self.open_button, self.stop_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        content_layout = QVBoxLayout(content)
        view = QHBoxLayout()
        self.preview = Preview()
        view.addWidget(self.preview, 2)
        panel = QGroupBox("资源归属")
        fields = QFormLayout(panel)
        self.alias, self.role = QLineEdit(), QLineEdit()
        self.alias.setPlaceholderText("例如 center")
        self.role.setPlaceholderText("例如 中控屏 / 仪表屏")
        self.device = QComboBox()
        self.resource_selector = QComboBox()
        fields.addRow("已存资源", self.resource_selector)
        fields.addRow("SCREEN. 别名", self.alias)
        fields.addRow("单板设备号", self.device)
        fields.addRow("屏幕用途", self.role)
        self.roi_label = QLabel()
        self.roi_label.setWordWrap(True)
        fields.addRow("ROI", self.roi_label)
        self.reset_roi_button = QPushButton("ROI 恢复全画面")
        fields.addRow(self.reset_roi_button)
        self.bind_button = QPushButton("分配资源")
        self.remove_button = QPushButton("移除绑定")
        fields.addRow(self.bind_button, self.remove_button)
        note = QLabel(
            "先预览确认屏幕，再分配资源。\n在画面上拖拽选择 ROI。\n本版不进行黑屏、冻屏等检测。"
        )
        note.setWordWrap(True)
        fields.addRow(note)
        view.addWidget(panel, 1)
        content_layout.addLayout(view)
        self.capture_status = QLabel("未打开输入")
        self.capture_status.setWordWrap(True)
        content_layout.addWidget(self.capture_status)
        content_layout.addWidget(QLabel("采集事件"))
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setMaximumBlockCount(300)
        self.events.setFixedHeight(80)
        content_layout.addWidget(self.events)
        layout.addWidget(scroll, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_button.clicked.connect(self._discover)
        self.open_button.clicked.connect(lambda: self._control_camera(runtime.open))
        self.stop_button.clicked.connect(lambda: self._control_camera(runtime.stop))
        self.resource_selector.currentIndexChanged.connect(
            lambda: self.select_resource(self.resource_selector.currentData())
        )
        self.camera_selector.currentIndexChanged.connect(self._select_camera)
        self.bind_button.clicked.connect(self._save)
        self.remove_button.clicked.connect(self._remove)
        self.alias.editingFinished.connect(self._save_existing)
        self.role.editingFinished.connect(self._save_existing)
        self.device.currentIndexChanged.connect(self._save_existing)
        self.preview.roi_changed.connect(self._roi_changed)
        self.reset_roi_button.clicked.connect(self._reset_roi)
        self._subscriptions = [
            context.subscribe_environment(self._reload),
            context.subscribe_run_state(self._state_changed),
        ]
        self.timer = QTimer(self.widget)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._refresh)
        self._reload()
        self.timer.start()

    def _camera(self):
        return self.camera_selector.currentData() or ""

    def _editable(self):
        return not (self._disposed or self._busy or self._active)

    def _controls(self):
        allowed = self._editable()
        for control in (
            self.refresh_button,
            self.open_button,
            self.stop_button,
            self.alias,
            self.device,
            self.role,
            self.reset_roi_button,
            self.bind_button,
            self.remove_button,
        ):
            control.setEnabled(allowed)
        self.open_button.setEnabled(allowed and bool(self._camera()))
        self.stop_button.setEnabled(allowed and bool(self._camera()))
        self.remove_button.setEnabled(allowed and self._selected is not None)
        self.bind_button.setEnabled(
            allowed and self._selected is None and bool(self._camera())
        )
        self.preview.editable = allowed
        if not allowed:
            self.preview._start = self.preview._drag = None

    def _state_changed(self, state):
        self._active = state == "ACTIVE"
        self._controls()

    def _reload(self):
        if self._disposed:
            return
        data = self.context.current_slice()
        self._resources = data["resources"]
        with QSignalBlocker(self.resource_selector):
            self.resource_selector.clear()
            self.resource_selector.addItem("按输入查看 / 新建", None)
            for rid in sorted(self._resources):
                self.resource_selector.addItem(rid, rid)
        camera_id = self._camera()
        names = dict(self._devices)
        for record in self._resources.values():
            config = record["config"]
            key = config.get("camera_id")
            if type(key) is str and key:
                names.setdefault(key, str(config.get("role") or "已配置输入"))
        with QSignalBlocker(self.camera_selector):
            self.camera_selector.clear()
            for key, name in names.items():
                self.camera_selector.addItem(name + " · " + key[:18], key)
            index = self.camera_selector.findData(camera_id)
            if index >= 0:
                self.camera_selector.setCurrentIndex(index)
        associated = {r["device"] for r in self._resources.values() if r.get("device")}
        with QSignalBlocker(self.device):
            self.device.clear()
            self.device.addItems(
                [""] + sorted(set(self.context.configured_device_ids()) | associated)
            )
        self._select_camera()
        report = validate_slice(data)
        self.status.setText(
            "配置有效"
            if report["status"] == "VALID"
            else "；".join(d["message"] for d in report["diagnostics"])
        )
        self._controls()

    def _select_camera(self, *_):
        camera_id = self._camera()
        matches = [
            rid
            for rid, r in self._resources.items()
            if r["config"].get("camera_id") == camera_id
        ]
        self._selected = matches[0] if len(matches) == 1 else None
        self._load_selected()

    def _load_selected(self):
        with QSignalBlocker(self.resource_selector):
            self.resource_selector.setCurrentIndex(
                max(0, self.resource_selector.findData(self._selected))
            )
        record = self._resources.get(self._selected, {"config": {}})
        config = record["config"]
        with (
            QSignalBlocker(self.alias),
            QSignalBlocker(self.device),
            QSignalBlocker(self.role),
        ):
            self.alias.setText(
                self._selected.removeprefix("SCREEN.") if self._selected else ""
            )
            self.device.setCurrentText(record.get("device", ""))
            self.role.setText(str(config.get("role", "")))
        self.preview.set_roi(
            config.get("roi") if valid_roi(config.get("roi")) else FULL_ROI
        )
        self.preview.set_frame(None)
        self._frame_key = None
        self._show_roi()
        self._controls()
        self._refresh()

    def _show_roi(self):
        r = self.preview.roi
        self.roi_label.setText(
            f"x={r['x']:.3f}, y={r['y']:.3f}\nw={r['width']:.3f}, h={r['height']:.3f}"
        )

    def _roi_changed(self, roi):
        self._show_roi()
        self._save_existing()

    def _reset_roi(self):
        if self._editable():
            self.preview.set_roi(FULL_ROI)
            self._roi_changed(self.preview.roi)

    def _commit(self, edit):
        if not self._editable():
            return False
        try:
            data = self.context.current_slice()
            updated = deepcopy(data)
            edit(updated)
            if updated != data:
                self.context.commit(updated, validate_slice(updated))
            self._reload()
            return True
        except GearError as exc:
            self._reload()
            self.status.setText(" · ".join(map(str, exc.args)))
            return False

    def _save_existing(self, *_):
        if self._selected:
            self._save()

    def _save(self):
        if not self._editable():
            return
        alias = self.alias.text().strip().removeprefix("SCREEN.")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", alias) is None:
            self.status.setText(
                "别名须以英文字母开头，仅含字母、数字、下划线和短横线。"
            )
            return
        rid, selected = "SCREEN." + alias, self._selected
        record = deepcopy(
            self._resources.get(selected, {"type": "SCREEN", "config": {}})
        )
        record["config"] = {
            "camera_id": self._camera(),
            "role": self.role.text().strip(),
            "roi": dict(self.preview.roi),
        }
        if self.device.currentText():
            record["device"] = self.device.currentText()
        else:
            record.pop("device", None)

        def edit(data):
            if rid != selected and rid in data["resources"]:
                raise GearError("DUPLICATE_RESOURCE", "资源别名已存在。")
            if selected:
                data["resources"].pop(selected, None)
            data["resources"][rid] = record

        self._commit(edit)

    def _remove(self):
        if self._selected:
            rid = self._selected
            self._commit(lambda data: data["resources"].pop(rid, None))

    def _discover(self):
        def done(value):
            self._devices = {item["id"]: item["description"] for item in value}
            self._reload()

        self._manual(self.runtime.discover, done)

    def _control_camera(self, method):
        camera_id = self._camera()
        self._manual(lambda: method(camera_id))

    def _manual(self, action, done=None):
        if not self._editable():
            return
        self._busy = True
        self._controls()
        self.status.setText("正在执行…")

        def listener(result):
            if self._disposed:
                return
            self._busy = False
            if result["ok"]:
                if done:
                    done(result["value"])
                self.status.setText("操作已完成")
            else:
                diagnostic = result["diagnostic"]
                self.status.setText(diagnostic["code"] + " · " + diagnostic["message"])
            self._controls()
            self._refresh()

        try:
            self.context.submit_manual(action, listener)
        except GearError as exc:
            self._busy = False
            self._controls()
            self.status.setText(" · ".join(map(str, exc.args)))

    def _refresh(self):
        if self._disposed:
            return
        snapshot = self.runtime.snapshot()
        camera_id = self._camera()
        state = snapshot["streams"].get(camera_id, {})
        message = state.get("fault") or (
            "正在采集"
            if state.get("streaming")
            else "等待新画面" if state.get("requested") else "未打开输入"
        )
        self.capture_status.setText(message)
        frame = self.runtime.frame(camera_id)
        if frame:
            key = (camera_id, frame["sequence"])
            if key != self._frame_key:
                image = QImage.fromData(frame["jpeg"])
                if not image.isNull():
                    self.preview.set_frame(image)
                    self._frame_key = key
        elif self._frame_key is not None:
            self.preview.set_frame(None)
            self._frame_key = None
        for event in snapshot["events"]:
            if event["sequence"] > self._event_sequence:
                self.events.appendPlainText(
                    f"{event['timestamp']} · {event['camera_id'][:18]} · {event['message']}"
                )
                self._event_sequence = event["sequence"]

    def resource_statuses(self):
        snapshot = self.runtime.snapshot()["streams"]
        return {
            rid: (
                snapshot.get(r["config"].get("camera_id"), {}).get("fault")
                or (
                    "采集中"
                    if snapshot.get(r["config"].get("camera_id"), {}).get("streaming")
                    else "未采集"
                )
            )
            for rid, r in self._resources.items()
            if type(r["config"].get("camera_id")) is str
        }

    def select_resource(self, resource_id):
        record = self._resources.get(resource_id)
        if record is None:
            return False
        camera_id = record["config"].get("camera_id")
        with QSignalBlocker(self.camera_selector):
            index = (
                self.camera_selector.findData(camera_id)
                if type(camera_id) is str
                else -1
            )
            if index < 0:
                index = self.camera_selector.findData(None)
                if index < 0:
                    self.camera_selector.addItem(
                        "未绑定输入 · 请选择输入或移除该资源", None
                    )
                    index = self.camera_selector.count() - 1
            self.camera_selector.setCurrentIndex(index)
        self._selected = resource_id
        self._load_selected()
        return True

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.timer.stop()
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        self._controls()


def create_workspace(context, runtime):
    return CameraWorkspace(context, runtime)
