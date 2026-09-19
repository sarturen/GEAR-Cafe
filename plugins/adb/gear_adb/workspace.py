"""Optional Qt workspace. Device I/O belongs to the host's manual worker."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import shlex

from gear_contracts.api import GearError
from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config import validate_slice


class AdbWorkspace:
    def __init__(self, context, runtime):
        self.context = context
        self.runtime = runtime
        self._disposed = False
        self._manual_busy = False
        self._active = context.run_state() == "ACTIVE"
        self._discovered = []
        self._refreshed = False
        self._cache_revision = None
        self._last_output = None
        self._selected_binding = None
        self._controls = []
        self.widget = QWidget()
        self.widget.setObjectName("adb_workspace")
        self.widget.setMinimumSize(620, 480)
        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("ADB · USB 设备")
        title.setStyleSheet("font-size: 19px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        self.run_label = QLabel()
        header.addWidget(self.run_label)
        layout.addLayout(header)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("操作设备"))
        self.target_device = self._control(QComboBox(), "target_device")
        self.target_device.setMinimumWidth(240)
        self.target_device.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        target_row.addWidget(self.target_device, 1)
        layout.addLayout(target_row)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("adb_tabs")
        layout.addWidget(self.tabs, 1)
        self._build_devices()
        self._build_shell()
        self._build_fastboot()
        self._build_pull()
        self._build_logcat()
        self._build_evidence()

        self.status_label = QLabel(
            "配置即时保存；状态为最近观察值。点击开始观察后每秒更新，用例期间继续。"
        )
        self.status_label.setObjectName("status_label")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.status_label)
        self._subscriptions = [
            context.subscribe_run_state(self._on_run_state),
            context.subscribe_environment(self._reload),
        ]
        self.log_timer = QTimer(self.widget)
        self.log_timer.setInterval(300)
        self.log_timer.timeout.connect(self._update_cached)
        self.target_device.currentIndexChanged.connect(self._update_cached)
        self._reload()
        self._update_controls()
        self.log_timer.start()

    def _control(self, control, name):
        control.setObjectName(name)
        self._controls.append(control)
        return control

    def _button(self, text, name, callback):
        button = self._control(QPushButton(text), name)
        button.clicked.connect(callback)
        return button

    def _edit(self, name, placeholder=""):
        edit = self._control(QLineEdit(), name)
        edit.setPlaceholderText(placeholder)
        return edit

    @staticmethod
    def _output(name):
        output = QPlainTextEdit()
        output.setObjectName(name)
        output.setReadOnly(True)
        output.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        return output

    @staticmethod
    def _table(headers, name):
        table = QTableWidget(0, len(headers))
        table.setObjectName(name)
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().hide()
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def _tab(self, title):
        page = QWidget()
        self.tabs.addTab(page, title)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        return layout

    def _build_devices(self):
        layout = self._tab("设备与绑定")
        settings = QFormLayout()
        self.adb_path = self._edit("adb_path", "adb 或 adb.exe 的完整路径")
        self.adb_path.editingFinished.connect(self._save_adb_path)
        settings.addRow("ADB 程序", self.adb_path)
        self.fastboot_path = self._edit(
            "fastboot_path", "可选：fastboot 或完整路径；空白表示查询不可用"
        )
        self.fastboot_path.editingFinished.connect(self._save_fastboot_path)
        settings.addRow("Fastboot 程序", self.fastboot_path)
        layout.addLayout(settings)
        refresh_row = QHBoxLayout()
        self.refresh_button = self._button(
            "刷新 USB 设备", "refresh_button", self._refresh
        )
        refresh_row.addWidget(self.refresh_button)
        self.monitor_start_button = self._button(
            "开始观察", "monitor_start_button", self._start_monitor
        )
        self.monitor_stop_button = self._button(
            "停止观察", "monitor_stop_button", self._stop_monitor
        )
        refresh_row.addWidget(self.monitor_start_button)
        refresh_row.addWidget(self.monitor_stop_button)
        self.refresh_label = QLabel("尚未刷新")
        self.refresh_label.setWordWrap(True)
        self.refresh_label.setTextFormat(Qt.TextFormat.PlainText)
        refresh_row.addWidget(self.refresh_label, 1)
        layout.addLayout(refresh_row)
        self.device_table = self._table(
            ["序列号", "状态", "登记", "型号 / USB"], "device_table"
        )
        self.device_table.setMinimumHeight(110)
        self.device_table.itemSelectionChanged.connect(self._select_device)
        layout.addWidget(self.device_table, 1)
        register_row = QHBoxLayout()
        self.serial_input = self._edit(
            "serial_input", "USB 序列号（设备离线时也可登记）"
        )
        register_row.addWidget(self.serial_input, 1)
        self.register_button = self._button(
            "登记身份", "register_button", self._register
        )
        register_row.addWidget(self.register_button)
        self.unregister_button = self._button(
            "移除身份", "unregister_button", self._unregister
        )
        register_row.addWidget(self.unregister_button)
        layout.addLayout(register_row)

        group = QGroupBox("逻辑资源绑定")
        binding_layout = QVBoxLayout(group)
        self.binding_table = self._table(["资源", "设备序列号"], "binding_table")
        self.binding_table.setMinimumHeight(100)
        self.binding_table.itemSelectionChanged.connect(self._select_binding)
        binding_layout.addWidget(self.binding_table, 1)
        edit_row = QHBoxLayout()
        edit_row.addWidget(QLabel("ADB."))
        self.binding_alias = self._edit("binding_alias", "别名，如 main")
        edit_row.addWidget(self.binding_alias, 1)
        self.binding_device = self._control(QComboBox(), "binding_device")
        self.binding_device.setMinimumWidth(160)
        edit_row.addWidget(self.binding_device, 1)
        binding_layout.addLayout(edit_row)
        actions = QHBoxLayout()
        self.binding_new_button = self._button(
            "新增绑定", "binding_new_button", self._new_binding
        )
        self.binding_save_button = self._button(
            "保存绑定", "binding_save_button", self._save_binding
        )
        self.binding_remove_button = self._button(
            "删除绑定", "binding_remove_button", self._remove_binding
        )
        for button in (
            self.binding_new_button,
            self.binding_save_button,
            self.binding_remove_button,
        ):
            actions.addWidget(button)
        actions.addStretch()
        binding_layout.addLayout(actions)
        layout.addWidget(group, 1)

    def _build_shell(self):
        layout = self._tab("Shell")
        hint = QLabel("执行会自行结束的 Android shell 命令；完成后显示退出码及输出。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.shell_command = self._edit(
            "shell_command", "例如 getprop ro.product.model"
        )
        row.addWidget(self.shell_command, 1)
        self.shell_button = self._button("执行", "shell_button", self._shell)
        row.addWidget(self.shell_button)
        layout.addLayout(row)
        self.shell_result = QLabel("尚未执行")
        layout.addWidget(self.shell_result)
        self.shell_output = self._output("shell_output")
        layout.addWidget(self.shell_output, 1)

    def _build_fastboot(self):
        layout = self._tab("Fastboot")
        hint = QLabel(
            "输入 USB 单板的 fastboot 子命令及参数，例如 getvar product。设备号由所选单板固定；不支持全局选项。默认超时 30 秒。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.fastboot_command = self._edit(
            "fastboot_command", "getvar product；含空格参数可用双引号"
        )
        row.addWidget(self.fastboot_command, 1)
        self.fastboot_button = self._button("执行", "fastboot_button", self._fastboot)
        row.addWidget(self.fastboot_button)
        layout.addLayout(row)
        self.fastboot_result = QLabel("尚未执行")
        layout.addWidget(self.fastboot_result)
        self.fastboot_output = self._output("fastboot_output")
        layout.addWidget(self.fastboot_output, 1)

    def _build_pull(self):
        layout = self._tab("文件拉取")
        form = QFormLayout()
        self.pull_remote = self._edit("pull_remote", "/sdcard/Download/report.txt")
        form.addRow("设备路径", self.pull_remote)
        row = QHBoxLayout()
        self.pull_destination = self._edit("pull_destination", "保存到本地文件夹")
        row.addWidget(self.pull_destination, 1)
        row.addWidget(
            self._button("选择文件夹…", "pull_browse_button", self._browse_pull)
        )
        form.addRow("本地目录", row)
        layout.addLayout(form)
        self.pull_button = self._button("拉取文件", "pull_button", self._pull)
        layout.addWidget(self.pull_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.pull_result = QLabel("尚未拉取")
        layout.addWidget(self.pull_result)
        self.pull_output = self._output("pull_output")
        layout.addWidget(self.pull_output, 1)

    def _build_logcat(self):
        layout = self._tab("Logcat")
        hint = QLabel(
            "日志持续写入指定文件；用例运行及页面切换时继续采集。下方显示最近日志。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.log_destination = self._edit("log_destination", "日志文件的完整路径")
        row.addWidget(self.log_destination, 1)
        row.addWidget(self._button("选择文件…", "log_browse_button", self._browse_log))
        layout.addLayout(row)
        buttons = QHBoxLayout()
        self.log_start_button = self._button(
            "开始保存日志", "log_start_button", self._start_log
        )
        self.log_stop_button = self._button(
            "停止采集", "log_stop_button", self._stop_log
        )
        buttons.addWidget(self.log_start_button)
        buttons.addWidget(self.log_stop_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.log_status = QLabel("未采集")
        self.log_status.setTextFormat(Qt.TextFormat.PlainText)
        self.log_status.setWordWrap(True)
        layout.addWidget(self.log_status)
        self.log_path_label = QLabel("日志文件：—")
        self.log_path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.log_path_label.setWordWrap(True)
        self.log_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.log_path_label)
        self.log_output = self._output("log_output")
        layout.addWidget(self.log_output, 1)

    def _build_evidence(self):
        layout = self._tab("失败取证")
        hint = QLabel(
            "用例启用日志取证（DIAGNOSTIC_LOGS）后，会采集设备当前的 logcat，不会清空设备日志。"
            "可为每个逻辑资源额外配置设备上的文件或目录；留空时仅采集 logcat。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("逻辑资源"))
        self.evidence_resource = QComboBox()
        self.evidence_resource.setObjectName("evidence_resource")
        self.evidence_resource.currentIndexChanged.connect(
            lambda _: self._load_evidence_paths()
        )
        row.addWidget(self.evidence_resource, 1)
        layout.addLayout(row)
        layout.addWidget(QLabel("额外设备路径（每行一条，以 / 开头的绝对路径）"))
        self.evidence_paths = self._control(QPlainTextEdit(), "evidence_paths")
        self.evidence_paths.setPlaceholderText(
            "/sdcard/Android/data/com.example.app/files/logs\n/data/local/tmp/report.txt"
        )
        layout.addWidget(self.evidence_paths, 1)
        self.evidence_save_button = self._button(
            "保存取证路径", "evidence_save_button", self._save_evidence_paths
        )
        layout.addWidget(
            self.evidence_save_button, alignment=Qt.AlignmentFlag.AlignLeft
        )

    def _load_evidence_paths(self, data=None):
        if self._disposed:
            return
        if data is None:
            try:
                data = self.context.current_slice()
            except GearError as exc:
                self._show_error(exc)
                return
        record = data["resources"].get(self.evidence_resource.currentText())
        paths = record["config"].get("log_paths", []) if record else []
        text = (
            "\n".join(str(path) for path in paths)
            if isinstance(paths, list)
            else str(paths)
        )
        self.evidence_paths.setPlainText(text)
        self._update_controls()

    def _save_evidence_paths(self):
        resource_id = self.evidence_resource.currentText()
        if not resource_id:
            return
        paths = [
            line.strip()
            for line in self.evidence_paths.toPlainText().splitlines()
            if line.strip()
        ]

        def edit(data):
            record = data["resources"].get(resource_id)
            if record is None:
                raise GearError("ADB_RESOURCE_UNKNOWN", "资源已不存在：" + resource_id)
            if paths:
                record["config"]["log_paths"] = paths
            else:
                record["config"].pop("log_paths", None)

        self._commit(edit)

    def _show_error(self, error):
        self.status_label.setText(" · ".join(str(part) for part in error.args))

    def _editable(self):
        return not (self._disposed or self._active or self._manual_busy)

    def _update_controls(self):
        enabled = self._editable()
        for control in self._controls:
            control.setEnabled(enabled)
        self.target_device.setEnabled(not self._disposed)
        self.evidence_resource.setEnabled(not self._disposed)
        evidence_enabled = enabled and bool(self.evidence_resource.currentText())
        self.evidence_paths.setEnabled(evidence_enabled)
        self.evidence_save_button.setEnabled(evidence_enabled)
        self.run_label.setText(
            "运行中 · 操作已锁定"
            if self._active
            else ("手动操作进行中…" if self._manual_busy else "空闲")
        )

    def _on_run_state(self, state):
        if not self._disposed:
            self._active = state == "ACTIVE"
            self._update_controls()

    @staticmethod
    def _set_options(combo, values):
        selected = combo.currentText()
        with QSignalBlocker(combo):
            combo.clear()
            combo.addItems(values)
            if selected in values:
                combo.setCurrentText(selected)

    def _reload(self):
        if self._disposed:
            return
        try:
            data = self.context.current_slice()
        except GearError as exc:
            self._show_error(exc)
            return
        self.adb_path.setText(str(data["plugin"]["config"].get("adb_path", "adb")))
        self.fastboot_path.setText(
            str(data["plugin"]["config"].get("fastboot_path", ""))
        )
        registered = data.get("devices", {})
        self._render_devices(registered)
        self._set_options(
            self.target_device,
            sorted(set(registered) | {item["serial"] for item in self._discovered}),
        )
        associated = {
            record["device"]
            for record in data["resources"].values()
            if record.get("device")
        }
        self._set_options(
            self.binding_device, [""] + sorted(set(registered) | associated)
        )
        selected = self._selected_binding
        with QSignalBlocker(self.binding_table):
            self.binding_table.setRowCount(len(data["resources"]))
            for row, (resource_id, resource) in enumerate(
                sorted(data["resources"].items())
            ):
                self.binding_table.setItem(row, 0, QTableWidgetItem(resource_id))
                self.binding_table.setItem(
                    row, 1, QTableWidgetItem(resource.get("device", ""))
                )
                if resource_id == selected:
                    self.binding_table.selectRow(row)
        if selected in data["resources"]:
            self.binding_alias.setText(selected.removeprefix("ADB."))
            self.binding_device.setCurrentText(
                data["resources"][selected].get("device", "")
            )
        self._set_options(self.evidence_resource, sorted(data["resources"]))
        self._load_evidence_paths(data)
        self._update_log()
        report = validate_slice(data)
        if report["diagnostics"]:
            self.status_label.setText(
                "配置需修正 · "
                + "；".join(item["message"] for item in report["diagnostics"])
            )

    def _render_devices(self, registered):
        # Keep each discovery row so ambiguous duplicate serials are visible.
        records = list(self._discovered)
        seen = {item["serial"] for item in records}
        for serial in sorted(set(registered) - seen):
            records.append(
                {
                    "serial": serial,
                    "state": "未发现" if self._refreshed else "未刷新",
                    "model": "",
                    "usb": "",
                }
            )
        with QSignalBlocker(self.device_table):
            self.device_table.setRowCount(len(records))
            for row, record in enumerate(
                sorted(records, key=lambda item: item["serial"])
            ):
                values = [
                    record["serial"],
                    (
                        self._device_status_text(record["serial"])
                        if hasattr(self.runtime, "device_status")
                        else record["state"]
                    ),
                    "已登记" if record["serial"] in registered else "未登记",
                    " / ".join(
                        value
                        for value in (record.get("model", ""), record.get("usb", ""))
                        if value
                    ),
                ]
                for column, value in enumerate(values):
                    self.device_table.setItem(row, column, QTableWidgetItem(value))

    def _commit(self, edit):
        if not self._editable():
            return False
        try:
            data = self.context.current_slice()
            edited = deepcopy(data)
            edit(edited)
            if edited == data:
                return True
            report = validate_slice(edited)
            self.context.commit(edited, report)
        except GearError as exc:
            self._show_error(exc)
            return False
        diagnostics = report["diagnostics"]
        self.status_label.setText(
            "配置已保存"
            if not diagnostics
            else "配置已保存 · " + "；".join(item["message"] for item in diagnostics)
        )
        self._reload()
        return True

    def _save_adb_path(self):
        value = self.adb_path.text().strip() or "adb"
        self._commit(lambda data: data["plugin"]["config"].update(adb_path=value))

    def _save_fastboot_path(self):
        value = self.fastboot_path.text().strip()
        self._commit(lambda data: data["plugin"]["config"].update(fastboot_path=value))

    def _register(self):
        serial = self.serial_input.text().strip()
        if not serial:
            self.status_label.setText("请输入 USB 设备序列号。")
            return
        if self._commit(
            lambda data: data.setdefault("devices", {}).setdefault(serial, {})
        ):
            self.target_device.setCurrentText(serial)
            self.status_label.setText("设备身份已登记：" + serial)

    def _unregister(self):
        serial = self.serial_input.text().strip()
        if not serial:
            self.status_label.setText("请选择或输入要移除的已登记序列号。")
            return
        if self._commit(lambda data: data["devices"].pop(serial, None)):
            self.status_label.setText(
                "已移除设备身份：" + serial + "；原有资源绑定保留，请按需重新配置。"
            )

    def _select_device(self):
        row = self.device_table.currentRow()
        if row >= 0:
            serial = self.device_table.item(row, 0).text()
            if self._editable():
                self.serial_input.setText(serial)
            self.target_device.setCurrentText(serial)

    def _select_binding(self):
        row = self.binding_table.currentRow()
        if row >= 0:
            self._selected_binding = self.binding_table.item(row, 0).text()
            self.binding_alias.setText(self._selected_binding.removeprefix("ADB."))
            self.binding_device.setCurrentText(self.binding_table.item(row, 1).text())

    def _new_binding(self):
        if self._editable():
            self.binding_table.clearSelection()
            self._selected_binding = None
            self.binding_alias.clear()
            self.binding_alias.setFocus()

    def _save_binding(self):
        alias = self.binding_alias.text().strip().removeprefix("ADB.")
        serial = self.binding_device.currentText()
        if not alias or not serial:
            self.status_label.setText("请填写资源别名并选择已登记设备。")
            return
        resource_id = "ADB." + alias
        selected = self._selected_binding
        evidence_selected = self.evidence_resource.currentText()

        def edit(data):
            resources = data["resources"]
            if resource_id != selected and resource_id in resources:
                raise GearError("DUPLICATE_RESOURCE", "资源别名已存在：" + resource_id)
            record = resources.pop(selected, {"type": "ADB", "config": {}})
            record["device"] = serial
            resources[resource_id] = record

        if self._commit(edit):
            self._selected_binding = resource_id
            if evidence_selected == selected:
                self.evidence_resource.setCurrentText(resource_id)
            self._reload()

    def _remove_binding(self):
        selected = self._selected_binding
        if selected and self._commit(
            lambda data: data["resources"].pop(selected, None)
        ):
            self._new_binding()

    def _manual(self, action, completed):
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
            if not result["ok"]:
                diagnostic = result["diagnostic"]
                self.status_label.setText(
                    diagnostic["code"] + " · " + diagnostic["message"]
                )
            else:
                self.status_label.setText("操作已完成")
                completed(result["value"])

        try:
            self.context.submit_manual(action, listener)
        except GearError as exc:
            self._manual_busy = False
            self._update_controls()
            self._show_error(exc)

    def _refresh(self):
        def completed(records):
            self._discovered = records
            self._refreshed = True
            self.refresh_label.setText(
                "最近刷新：" + datetime.now().strftime("%H:%M:%S")
            )
            self._reload()
            self._update_cached()

        self._manual(self.runtime.refresh_devices, completed)

    def _start_monitor(self):
        self._manual(self.runtime.start_monitor, lambda _: self._update_cached())

    def _stop_monitor(self):
        self._manual(self.runtime.stop_monitor, lambda _: self._update_cached())

    def _device_status_text(self, serial):
        observation = self.runtime.device_status(serial)
        error = observation["diagnostic"]
        return observation["state"] + (" · " + error["code"] if error else "")

    def resource_statuses(self):
        """Host overview hook: detached configuration and synchronized cache only."""
        data = self.context.current_slice()
        counts = {}
        for record in data["resources"].values():
            serial = record.get("device")
            counts[serial] = counts.get(serial, 0) + 1
        statuses = {}
        for rid, record in data["resources"].items():
            serial = record.get("device")
            if not serial:
                statuses[rid] = "未配置"
            elif counts[serial] > 1:
                statuses[rid] = "配置无效 · ADB_DEVICE_DUPLICATE"
            elif serial not in data.get("devices", {}):
                statuses[rid] = "配置无效 · ADB_DEVICE_UNKNOWN"
            else:
                statuses[rid] = self._device_status_text(serial)
        return statuses

    def select_resource(self, resource_id):
        """Host overview hook: select the saved binding without a device query."""
        for row in range(self.binding_table.rowCount()):
            if self.binding_table.item(row, 0).text() == resource_id:
                self.tabs.setCurrentIndex(0)
                self.binding_table.selectRow(row)
                self.target_device.setCurrentText(
                    self.binding_table.item(row, 1).text()
                )
                self.binding_table.scrollToItem(self.binding_table.item(row, 0))
                return

    def _update_cached(self, *_):
        if self._disposed:
            return
        self._update_log()
        if not hasattr(self.runtime, "device_snapshot"):
            return
        snapshot = self.runtime.device_snapshot()
        if snapshot["revision"] != self._cache_revision:
            self._cache_revision = snapshot["revision"]
            self._discovered = snapshot["records"]
            data = self.context.current_slice()
            self._render_devices(data.get("devices", {}))
            self._set_options(
                self.target_device,
                sorted(
                    set(data.get("devices", {}))
                    | {item["serial"] for item in self._discovered}
                ),
            )
        summaries = []
        for tool, query in snapshot["queries"].items():
            error = query["diagnostic"]
            summaries.append(
                tool
                + ": "
                + (
                    error["code"] + " · " + error["message"]
                    if error
                    else query["status"]
                )
            )
        self.refresh_label.setText(
            ("观察中 · " if snapshot["monitoring"] else "缓存 · ")
            + "；".join(summaries)
        )
        output = snapshot["outputs"].get(self.target_device.currentText())
        key = (self.target_device.currentText(), output)
        if output and key != self._last_output:
            self._last_output = key
            controls = {
                "SHELL": (self.shell_result, self.shell_output),
                "FASTBOOT": (self.fastboot_result, self.fastboot_output),
                "PULL": (self.pull_result, self.pull_output),
            }
            if output["kind"] in controls:
                self._show_command_result(output, *controls[output["kind"]])

    def _target(self):
        serial = self.target_device.currentText()
        if not serial:
            self.status_label.setText("请先刷新或登记设备，并选择操作设备。")
        return serial

    @staticmethod
    def _show_command_result(result, label, output):
        label.setText("退出码：" + str(result["exit_code"]))
        text = result["stdout"]
        if result["stderr"]:
            text += ("\n" if text else "") + "[stderr]\n" + result["stderr"]
        output.setPlainText(text)

    def _shell(self):
        serial, command = self._target(), self.shell_command.text().strip()
        if not command:
            self.status_label.setText("请输入 Shell 命令。")
            return
        if serial:
            self._manual(
                lambda: self.runtime.manual_shell(serial, command),
                lambda value: self._show_command_result(
                    value, self.shell_result, self.shell_output
                ),
            )

    def _fastboot(self):
        serial = self._target()
        try:
            # POSIX quote grouping with escaping disabled preserves Windows paths.
            lexer = shlex.shlex(self.fastboot_command.text(), posix=True)
            lexer.whitespace_split = True
            lexer.escape = ""
            lexer.commenters = ""
            arguments = list(lexer)
        except ValueError as exc:
            self.status_label.setText("参数引号不完整：" + str(exc))
            return
        if not arguments:
            self.status_label.setText("请输入 fastboot 子命令。")
            return
        if serial:
            self._manual(
                lambda: self.runtime.manual_fastboot(serial, arguments),
                lambda result: self._show_command_result(
                    result, self.fastboot_result, self.fastboot_output
                ),
            )

    def _pull(self):
        serial = self._target()
        remote, destination = (
            self.pull_remote.text().strip(),
            self.pull_destination.text().strip(),
        )
        if not remote or not destination:
            self.status_label.setText("请填写设备路径和本地保存目录。")
            return
        if serial:
            self._manual(
                lambda: self.runtime.manual_pull(serial, remote, destination),
                lambda value: self._show_command_result(
                    value, self.pull_result, self.pull_output
                ),
            )

    def _browse_pull(self):
        path = QFileDialog.getExistingDirectory(
            self.widget, "选择拉取文件保存目录", self.pull_destination.text()
        )
        if path:
            self.pull_destination.setText(path)

    def _browse_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self.widget,
            "选择日志文件",
            self.log_destination.text(),
            "日志文件 (*.txt *.log);;所有文件 (*)",
        )
        if path:
            self.log_destination.setText(path)

    def _start_log(self):
        serial, destination = self._target(), self.log_destination.text().strip()
        if not destination:
            self.status_label.setText("请指定日志保存文件。")
            return
        if serial:
            self._manual(
                lambda: self.runtime.start_logcat(serial, destination),
                lambda _: self._update_log(),
            )

    def _stop_log(self):
        serial = self._target()
        if serial:
            self._manual(
                lambda: self.runtime.stop_logcat(serial), lambda _: self._update_log()
            )

    def _update_log(self, *_):
        if self._disposed:
            return
        serial = self.target_device.currentText()
        snapshot = {"running": False, "lines": [], "path": None, "error": None}
        if serial:
            try:
                snapshot = self.runtime.logcat_snapshot(serial)
            except GearError as exc:
                self._show_error(exc)
                return
        text = "\n".join(snapshot["lines"])
        if text != self.log_output.toPlainText():
            scroll = self.log_output.verticalScrollBar()
            at_end, position = scroll.value() == scroll.maximum(), scroll.value()
            self.log_output.setPlainText(text)
            scroll.setValue(scroll.maximum() if at_end else position)
        self.log_path_label.setText("日志文件：" + (snapshot["path"] or "—"))
        status = (
            "正在采集"
            if snapshot["running"]
            else "已停止" if snapshot["path"] else "未采集"
        )
        if snapshot["error"]:
            status += " · " + snapshot["error"]
        self.log_status.setText(status)

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.log_timer.stop()
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        self._update_controls()


def create_workspace(context, runtime):
    return AdbWorkspace(context, runtime)
