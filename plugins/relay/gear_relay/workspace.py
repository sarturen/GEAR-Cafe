"""Physical relay workspace. GUI observation reads cache only, never serial I/O."""

from copy import deepcopy
import math
from gear_contracts.api import GearError
from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from .bindings import (
    TERMINALS,
    assign_channel,
    controller_configs,
    migrate_controllers,
    resource_location,
    resource_role,
    unassign_resource,
)
from .config import DEFAULT_CONFIG, validate_slice


class RelayWorkspace:
    def __init__(self, context, runtime):
        self.context, self.runtime = context, runtime
        self._disposed = self._manual_busy = False
        self._active = context.run_state() == "ACTIVE"
        self._controls = []
        self._selected_binding = None
        self._selected_channel = 1
        self._resources = {}
        self._device_ids = set()
        self.widget = QWidget()
        self.widget.setObjectName("relay_workspace")
        self.widget.setMinimumSize(640, 340)
        outer = QVBoxLayout(self.widget)
        header = QHBoxLayout()
        title = QLabel("继电器 · 控制器与物理通道")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(title, 1)
        self.run_label = self._label("run_label")
        header.addWidget(self.run_label)
        outer.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        layout = QVBoxLayout(body)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("控制器"))
        self.controller_selector = QComboBox()
        self.controller_selector.setObjectName("controller_selector")
        self.controller_selector.currentTextChanged.connect(self._controller_changed)
        toolbar.addWidget(self.controller_selector, 1)
        self.new_controller_name = self._edit("new_controller_name", "新控制器名称")
        self.add_controller_button = self._button(
            "add_controller_button", "添加控制器", self._add_controller
        )
        self.remove_controller_button = self._button(
            "remove_controller_button", "删除控制器", self._remove_controller
        )
        self.serial_toggle = QPushButton("串口设置")
        self.serial_toggle.setCheckable(True)
        toolbar.addWidget(self.serial_toggle)
        layout.addLayout(toolbar)
        self._build_serial(layout)
        self.state_hint = self._label(
            "state_hint",
            "按下＝吸合，弹起＝释放；未知时先连接并读取状态。右键通道可配置归属。",
        )
        self.state_hint.setWordWrap(True)
        layout.addWidget(self.state_hint)
        channels = QHBoxLayout()
        channels.setSpacing(6)
        self.channel_buttons, self.channel_owners = [], []
        for channel in range(1, 9):
            tile = QWidget()
            cell = QVBoxLayout(tile)
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(4)
            button = self._button(
                f"channel_{channel}",
                f"CH{channel}\n未知",
                lambda checked=False, channel=channel: self._channel_action(
                    channel, checked
                ),
            )
            button.setCheckable(True)
            button.setMinimumHeight(60)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setStyleSheet(
                "QPushButton {border: 1px solid #94a3b8; border-radius: 6px; background: #f1f5f9; color: #334155;}"
                "QPushButton:checked {background: #0f766e; color: white; border: 2px solid #115e59;}"
                "QPushButton:disabled {color: #94a3b8;}"
                "QPushButton:checked:disabled {background: #527f79; color: white;}"
            )
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, channel=channel: self._edit_channel(channel)
            )
            owner = self._label(f"channel_{channel}_owner")
            owner.setAlignment(Qt.AlignmentFlag.AlignCenter)
            owner.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            owner.setWordWrap(True)
            owner.setMaximumHeight(42)
            cell.addWidget(button)
            cell.addWidget(owner)
            self.channel_buttons.append(button)
            self.channel_owners.append(owner)
            channels.addWidget(tile, 1)
        layout.addLayout(channels)
        all_actions = QHBoxLayout()
        all_actions.addStretch()
        self.all_on_button = self._button(
            "all_on_button",
            "当前控制器全吸合（8 路）",
            lambda: self._controller_action("set_all", True),
        )
        self.all_off_button = self._button(
            "all_off_button",
            "当前控制器全释放（8 路）",
            lambda: self._controller_action("set_all", False),
        )
        all_actions.addWidget(self.all_on_button)
        all_actions.addWidget(self.all_off_button)
        layout.addLayout(all_actions)
        self._build_assignment(layout)
        self.legacy_box = QGroupBox("旧数据与待处理资源 · 选择后可修复、解绑或删除")
        legacy = QVBoxLayout(self.legacy_box)
        self.legacy_table = QTableWidget(0, 4)
        self.legacy_table.setHorizontalHeaderLabels(
            ["逻辑资源", "控制器 / 通道", "原私有名称", "待处理原因"]
        )
        self._setup_table(self.legacy_table)
        self.legacy_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.legacy_table.itemSelectionChanged.connect(self._select_legacy)
        legacy.addWidget(self.legacy_table)
        layout.addWidget(self.legacy_box)
        layout.addStretch(1)
        self.status_label = self._label("status_label")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)
        self._subscriptions = [
            context.subscribe_run_state(self._on_run_state),
            context.subscribe_environment(self._reload),
        ]
        self.state_timer = QTimer(self.widget)
        self.state_timer.setInterval(100)
        self.state_timer.timeout.connect(self._refresh_snapshot)
        self._reload()
        self.state_timer.start()

    @staticmethod
    def _setup_table(table):
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

    @staticmethod
    def _label(name, text=""):
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    def _control(self, control, name):
        control.setObjectName(name)
        self._controls.append(control)
        return control

    def _edit(self, name, placeholder=""):
        edit = self._control(QLineEdit(), name)
        edit.setPlaceholderText(placeholder)
        return edit

    def _button(self, name, text, callback):
        button = self._control(QPushButton(text), name)
        button.clicked.connect(callback)
        return button

    def _build_serial(self, layout):
        box = self.serial_box = QGroupBox("串口参数 · 手工输入")
        grid = QGridLayout(box)
        manage = QHBoxLayout()
        manage.addWidget(self.new_controller_name, 1)
        manage.addWidget(self.add_controller_button)
        manage.addWidget(self.remove_controller_button)
        grid.addLayout(manage, 0, 0, 1, 4)
        labels = {
            "port": "COM 端口",
            "baudrate": "波特率",
            "parity": "校验",
            "stopbits": "停止位",
            "unit_id": "从站地址",
            "timeout_s": "超时（秒）",
            "poll_interval_ms": "后台读取（毫秒，0 关闭）",
        }
        for index, name in enumerate(DEFAULT_CONFIG):
            field = (
                self._control(QComboBox(), name)
                if name == "parity"
                else self._edit(name)
            )
            setattr(self, name, field)
            row, column = divmod(index, 4)
            grid.addWidget(QLabel(labels[name]), row * 2 + 1, column)
            grid.addWidget(field, row * 2 + 2, column)
            if name == "parity":
                field.addItems(["N", "E", "O"])
                field.currentTextChanged.connect(
                    lambda _: self._save_parameter("parity")
                )
            else:
                field.editingFinished.connect(
                    lambda name=name: self._save_parameter(name)
                )
        layout.addWidget(box)
        box.setVisible(False)
        self.serial_toggle.toggled.connect(box.setVisible)
        actions = QHBoxLayout()
        for name, text, method in (
            ("connect_button", "连接", "connect"),
            ("disconnect_button", "断开", "disconnect"),
            ("read_button", "读取状态", "read_states"),
        ):
            button = self._button(
                name,
                text,
                lambda checked=False, method=method: self._controller_action(method),
            )
            setattr(self, name, button)
            actions.addWidget(button)
        self.connection_label = self._label("connection_label")
        self.connection_label.setWordWrap(True)
        actions.addWidget(self.connection_label, 1)
        layout.addLayout(actions)

    def _build_assignment(self, layout):
        self.binding_toggle = QPushButton("资源绑定")
        self.binding_toggle.setCheckable(True)
        layout.addWidget(self.binding_toggle)
        box = self.assignment_box = QGroupBox("通道归属 · 保存后生效")
        self.binding_toggle.toggled.connect(box.setVisible)
        form = QFormLayout(box)
        self.channel_picker = QComboBox()
        for channel in range(1, 9):
            self.channel_picker.addItem(f"CH{channel}", channel)
        self.channel_picker.currentIndexChanged.connect(self._select_channel)
        form.addRow("查看通道", self.channel_picker)
        self.binding_alias = self._edit(
            "binding_alias", "可选别名；已有资源 ID 保持不变"
        )
        self.binding_channel = self._control(QComboBox(), "binding_channel")
        self.binding_channel.setPlaceholderText("未绑定 · 请选择实际通道")
        for channel in range(1, 9):
            self.binding_channel.addItem(f"CH{channel}", channel)
        self.binding_device = self._control(QComboBox(), "binding_device")
        self.binding_role = self._control(QComboBox(), "binding_role")
        self.binding_role.setEditable(True)
        self.binding_role.addItems(["", *TERMINALS])
        form.addRow("逻辑资源 ID", self.binding_alias)
        form.addRow("绑定到通道", self.binding_channel)
        form.addRow("登记设备号", self.binding_device)
        form.addRow("用途（可选 / 可自定义）", self.binding_role)
        self.legacy_hint = self._label("legacy_hint")
        self.legacy_hint.setWordWrap(True)
        form.addRow(self.legacy_hint)
        actions = QHBoxLayout()
        self.binding_save_button = self._button(
            "binding_save_button", "保存通道归属", self._save_binding
        )
        self.binding_unassign_button = self._button(
            "binding_unassign_button", "解绑单板与用途", self._unassign_binding
        )
        self.binding_remove_button = self._button(
            "binding_remove_button", "删除选中资源", self._remove_binding
        )
        for button in (
            self.binding_save_button,
            self.binding_unassign_button,
            self.binding_remove_button,
        ):
            actions.addWidget(button)
        form.addRow(actions)
        layout.addWidget(box)
        box.setVisible(False)

    def _editable(self):
        return not (self._disposed or self._active or self._manual_busy)

    def _update_controls(self):
        enabled = self._editable()
        for control in self._controls:
            control.setEnabled(enabled)
        selected = self._selected_binding in self._resources
        self.binding_remove_button.setEnabled(enabled and selected)
        self.binding_unassign_button.setEnabled(enabled and selected)
        self.binding_alias.setReadOnly(selected)
        self.controller_selector.setEnabled(not self._disposed)
        self.channel_picker.setEnabled(not self._disposed)
        self.legacy_table.setEnabled(not self._disposed)
        self.run_label.setText(
            "运行中 · 人工操作已锁定"
            if self._active
            else (
                "操作中…"
                if self._manual_busy
                else "页面已关闭" if self._disposed else "空闲"
            )
        )

        self._refresh_snapshot()

    def _on_run_state(self, state):
        if not self._disposed:
            self._active = state == "ACTIVE"
            self._update_controls()

    def _controller(self):
        return self.controller_selector.currentText()

    def _controller_changed(self, *_):
        if self._disposed:
            return
        self._selected_binding = None
        self._selected_channel = 1
        self._load_controller()

    def _reload(self):
        if self._disposed:
            return
        try:
            data = self.context.current_slice()
            self._device_ids = set(self.context.configured_device_ids())
        except GearError as exc:
            self._show_error(exc)
            return
        self._resources = data["resources"]
        configs = controller_configs(data["plugin"]["config"])
        self._configs = configs if type(configs) is dict else {}
        selected = self._controller()
        with QSignalBlocker(self.controller_selector):
            self.controller_selector.clear()
            self.controller_selector.addItems(list(self._configs))
            if selected in self._configs:
                self.controller_selector.setCurrentText(selected)
        devices = self._device_ids | {
            r.get("device")
            for r in self._resources.values()
            if type(r.get("device")) is str
        }
        with QSignalBlocker(self.binding_device):
            self.binding_device.clear()
            self.binding_device.addItem("未归属", "")
            for device in sorted(devices):
                self.binding_device.addItem(
                    device + ("（未登记）" if device not in self._device_ids else ""),
                    device,
                )
        self._load_controller()
        self._load_legacy()
        report = validate_slice(data)
        self.status_label.setText(
            "；".join(d["message"] for d in report["diagnostics"])
            or "配置有效 · 选择通道后分配设备号与用途"
        )

    def _load_controller(self):
        config = self._configs.get(self._controller(), {})
        config = config if type(config) is dict else {}
        for name, default in DEFAULT_CONFIG.items():
            field = getattr(self, name)
            value = str(config.get(name, default))
            with QSignalBlocker(field):
                if name == "parity":
                    field.clear()
                    field.addItems(list(dict.fromkeys(["N", "E", "O", value])))
                    field.setCurrentText(value)
                else:
                    field.setText(value)
        for channel, label in enumerate(self.channel_owners, 1):
            owners = [
                (rid, record)
                for rid, record in self._resources.items()
                if resource_location(record) == (self._controller(), channel)
            ]
            if len(owners) == 1:
                rid, record = owners[0]
                role = str(resource_role(record) or rid.removeprefix("POWER."))
                device = record.get("device", "未归属")
                label.setText(f"{role[:14]}\n{device[:14]}")
                label.setToolTip(f"{rid}\n{device} · {role}")
            else:
                label.setText("通道冲突" if owners else "未分配")
                label.setToolTip("、".join(rid for rid, _ in owners))
        with QSignalBlocker(self.channel_picker):
            self.channel_picker.setCurrentIndex(self._selected_channel - 1)
        if self._selected_binding not in self._resources:
            owners = [
                rid
                for rid, record in self._resources.items()
                if resource_location(record)
                == (self._controller(), self._selected_channel)
            ]
            self._selected_binding = owners[0] if len(owners) == 1 else None
        self._load_binding()
        self._refresh_snapshot()
        self._update_controls()

    def _load_legacy(self):
        rows = []
        for rid, record in self._resources.items():
            rc = record.get("config", {})
            rc = rc if type(rc) is dict else {}
            cid, channel = resource_location(record)
            problems = []
            if type(channel) is not int or not 1 <= channel <= 8:
                problems.append("旧未完成：缺少有效通道，补齐或删除")
            if cid not in self._configs:
                problems.append("控制器未配置")
            if "device_name" in rc or "terminal" in rc:
                problems.append("待核对归属：旧私有字段")
            if (
                sum(
                    resource_location(other) == (cid, channel)
                    for other in self._resources.values()
                )
                > 1
            ):
                problems.append("通道冲突")
            if problems:
                rows.append(
                    (
                        rid,
                        (
                            f"{cid} / CH{channel}"
                            if channel is not None
                            else f"{cid} / 未绑定"
                        ),
                        str(rc.get("device_name", "")),
                        "；".join(problems),
                    )
                )
        with QSignalBlocker(self.legacy_table):
            self.legacy_table.setRowCount(len(rows))
            for row, values in enumerate(rows):
                for column, value in enumerate(values):
                    self.legacy_table.setItem(row, column, QTableWidgetItem(value))
        self.legacy_box.setVisible(bool(rows))

    def _edit_channel(self, channel):
        self.channel_picker.setCurrentIndex(channel - 1)
        self.binding_toggle.setChecked(True)

    def _select_channel(self, *_):
        if self._disposed or self.channel_picker.currentData() is None:
            return
        self._selected_channel = self.channel_picker.currentData()
        owners = [
            rid
            for rid, record in self._resources.items()
            if resource_location(record) == (self._controller(), self._selected_channel)
        ]
        self._selected_binding = owners[0] if len(owners) == 1 else None
        self._load_binding()
        self._update_controls()

    def _select_legacy(self):
        row = self.legacy_table.currentRow()
        if row >= 0:
            self.select_resource(self.legacy_table.item(row, 0).text())

    def select_resource(self, resource_id):
        """Host-private navigation hook; no configuration or hardware activity."""
        if self._disposed or resource_id not in self._resources:
            return False
        cid, channel = resource_location(self._resources[resource_id])
        with QSignalBlocker(self.controller_selector):
            if self.controller_selector.findText(cid or "") >= 0:
                self.controller_selector.setCurrentText(cid)
        self._selected_binding = resource_id
        if type(channel) is int and 1 <= channel <= 8:
            self._selected_channel = channel
        self._load_controller()
        self.binding_toggle.setChecked(True)
        self.binding_alias.setFocus()
        return True

    def _load_binding(self):
        record = self._resources.get(self._selected_binding, {})
        rc = record.get("config", {})
        rc = rc if type(rc) is dict else {}
        self.binding_alias.setText(self._selected_binding or "")
        channel = rc.get("channel") if record else self._selected_channel
        self.binding_channel.setCurrentIndex(self.binding_channel.findData(channel))
        self.binding_device.setCurrentIndex(
            max(0, self.binding_device.findData(record.get("device", "")))
        )
        self.binding_role.setCurrentText(str(resource_role(record) or ""))
        legacy = rc.get("device_name")
        if legacy is not None:
            self.legacy_hint.setText(
                f"旧名称「{legacy}」待归属核对；它不是设备号。请选择登记设备号，保存或解绑会清理私有旧字段。"
            )
        elif record and "channel" not in rc:
            self.legacy_hint.setText(
                "旧未完成资源：请选择实际通道补齐，或删除此资源。解绑单板不能补齐通道。"
            )
        else:
            self.legacy_hint.setText(
                "设备号由 ADB 登记，离线仍有效。KL30 / KL15 为快捷用途，也可填写其他用途或留空。"
            )

    @staticmethod
    def _number(text, floating=False):
        try:
            value = float(text) if floating else int(text)
            return value if not floating or math.isfinite(value) else text
        except ValueError:
            return text

    def _save_parameter(self, name):
        field = getattr(self, name)
        text = (field.currentText() if name == "parity" else field.text()).strip()
        value = (
            text
            if name in ("port", "parity")
            else self._number(text, name == "timeout_s")
        )
        cid = self._controller()
        if cid:
            self._commit(
                lambda data: data["plugin"]["config"]["controllers"][cid].update(
                    {name: value}
                )
            )

    def _add_controller(self):
        cid = self.new_controller_name.text().strip()

        def edit(data):
            controllers = data["plugin"]["config"]["controllers"]
            if not cid or cid in controllers:
                raise GearError(
                    "RELAY_CONTROLLER_INVALID", "请填写不重复的控制器名称。"
                )
            controllers[cid] = {}

        if self._commit(edit):
            self.new_controller_name.clear()
            self.controller_selector.setCurrentText(cid)

    def _remove_controller(self):
        cid = self._controller()

        def edit(data):
            if any(resource_location(r)[0] == cid for r in data["resources"].values()):
                raise GearError(
                    "RELAY_CONTROLLER_IN_USE",
                    "此控制器仍有资源，请先删除或重新分配这些资源。",
                )
            data["plugin"]["config"]["controllers"].pop(cid, None)

        self._commit(edit)

    def _save_binding(self):
        if not self._editable():
            return
        cid, channel = self._controller(), self.binding_channel.currentData()
        selected = self._selected_binding
        alias = self.binding_alias.text().strip()
        rid = selected or (
            alias if alias.startswith("POWER.") else "POWER." + alias if alias else None
        )
        device = self.binding_device.currentData() or ""
        role = self.binding_role.currentText().strip()
        saved_id = []
        legacy = self._resources.get(selected, {}).get("config", {})
        if "device_name" in legacy and not device:
            self.status_label.setText(
                "旧名称待归属：请选择登记设备号；若无需归属，请点击解绑单板与用途。"
            )
            return

        def edit(data):
            if not selected and rid in data["resources"]:
                raise GearError("RELAY_RESOURCE_DUPLICATE", "此资源 ID 已存在。")
            saved_id.append(assign_channel(data, cid, channel, device, role, rid))

        if self._commit(edit):
            self.select_resource(saved_id[0])

    def _unassign_binding(self):
        rid = self._selected_binding
        if rid:
            self._commit(lambda data: unassign_resource(data, rid))

    def _remove_binding(self):
        rid = self._selected_binding
        if rid and self._commit(lambda data: data["resources"].pop(rid, None)):
            self._selected_binding = None
            self._load_controller()

    def _commit(self, edit):
        if not self._editable():
            return False
        try:
            original = self.context.current_slice()
            data = deepcopy(original)
            migrate_controllers(data)
            edit(data)
            if data != original:
                self.context.commit(data, validate_slice(data))
        except GearError as exc:
            self._reload()
            self._show_error(exc)
            return False
        self._reload()
        return True

    def _show_error(self, error):
        self.status_label.setText(" · ".join(str(part) for part in error.args))

    def _controller_action(self, method, *args):
        cid = self._controller()
        self._manual(lambda: getattr(self.runtime, method)(*args, controller=cid))

    def _channel_action(self, channel, on):
        cid = self._controller()
        # Display only observed/acknowledged state while the write is queued.
        self._refresh_snapshot()
        self._manual(lambda: self.runtime.set_channel(channel, on, controller=cid))

    def _manual(self, action):
        if not self._editable():
            return
        self._manual_busy = True
        self._update_controls()
        self.status_label.setText("正在执行…")

        def listener(result):
            if self._disposed:
                return
            self._manual_busy = False
            self._update_controls()
            self._refresh_snapshot()
            self.status_label.setText(
                "操作已完成"
                if result["ok"]
                else result["diagnostic"]["code"]
                + " · "
                + result["diagnostic"]["message"]
            )

        try:
            self.context.submit_manual(action, listener)
        except GearError as exc:
            self._manual_busy = False
            self._update_controls()
            self._show_error(exc)

    @staticmethod
    def _state_text(snapshot, channel):
        if snapshot.get("fault"):
            return "故障 · " + str(snapshot["fault"])
        if not snapshot.get("connected"):
            return "未连接 · 状态未知"
        state = snapshot["states"][channel - 1]
        source = snapshot.get("sources", [None] * 8)[channel - 1]
        text = (
            "吸合 · ON"
            if state is True
            else "释放 · OFF" if state is False else "未知 · 等待响应"
        )
        return text + (
            " · 读回"
            if source == "read_coils"
            else " · 回执" if source == "write_acknowledgement" else ""
        )

    def resource_statuses(self):
        """Host-private cache-only status hook; never submit a manual read."""
        snapshots = self.runtime.snapshots()
        result = {}
        for rid, record in self._resources.items():
            cid, channel = resource_location(record)
            if type(channel) is not int or not 1 <= channel <= 8:
                result[rid] = "未完成 · 缺少有效通道"
            elif cid not in snapshots:
                result[rid] = "控制器未配置"
            else:
                result[rid] = self._state_text(snapshots[cid], channel)
        return result

    def _refresh_snapshot(self):
        if self._disposed:
            return
        snapshot = self.runtime.snapshot(self._controller())
        self.connection_label.setText(
            "故障 · " + str(snapshot["fault"])
            if snapshot.get("fault")
            else "已连接" if snapshot["connected"] else "未连接"
        )
        port = self.port.text().strip()
        if port:
            self.connection_label.setText(port + " · " + self.connection_label.text())
        for channel, button in enumerate(self.channel_buttons, 1):
            state = snapshot["states"][channel - 1]
            known = (
                snapshot["connected"]
                and not snapshot.get("fault")
                and type(state) is bool
            )
            with QSignalBlocker(button):
                button.setChecked(known and state is True)
            button.setText(
                f"CH{channel}\n" + ("吸合" if state else "释放")
                if known
                else f"CH{channel}\n未知"
            )
            button.setToolTip(
                self._state_text(snapshot, channel) + "\n右键配置通道归属"
            )
            button.setEnabled(self._editable() and known)

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.state_timer.stop()
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        self._update_controls()


def create_workspace(context, runtime):
    return RelayWorkspace(context, runtime)
