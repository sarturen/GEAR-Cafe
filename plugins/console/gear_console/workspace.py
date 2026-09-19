"""Private Qt workspace. Every device action goes through submit_manual."""

from copy import deepcopy
import re
from PySide6 import QtCore, QtGui, QtWidgets
from .config import DEFAULTS, validate_slice


def combo(values, selected=None):
    widget = QtWidgets.QComboBox()
    widget.addItems([str(value) for value in values])
    if selected is not None:
        text = str(selected)
        if widget.findText(text) < 0:
            widget.addItem(text)
        widget.setCurrentText(text)
    return widget


def fill_devices(widget, devices, selected=None):
    widget.clear()
    widget.addItem("未分配单板", None)
    for device in devices:
        widget.addItem(device, device)
    if selected and selected not in devices:
        widget.addItem(str(selected) + "（未登记）", selected)
    index = widget.findData(selected)
    widget.setCurrentIndex(max(0, index))


class TerminalPanel(QtWidgets.QWidget):
    def __init__(self, owner, rid, record, shortcuts):
        super().__init__()
        self.owner, self.rid = owner, rid
        self.display_cursor, self.last_end = 0, -1
        self.generation = None
        layout = QtWidgets.QVBoxLayout(self)
        config = record.get("config", {})
        if not isinstance(config, dict):
            config = {}
        self.config_group = QtWidgets.QGroupBox("COM 归属与通信参数（连接前配置）")
        form = QtWidgets.QGridLayout(self.config_group)
        self.alias = QtWidgets.QLineEdit(rid.partition(".")[2])
        self.port = QtWidgets.QLineEdit(str(config.get("port", "")))
        self.port.setPlaceholderText("手工填写，例如 COM77")
        self.device = QtWidgets.QComboBox()
        fill_devices(
            self.device, owner.context.configured_device_ids(), record.get("device")
        )
        self.role = combo(["MCU", "SOC"], config.get("role", "MCU"))
        self.baudrate = QtWidgets.QLineEdit(str(config.get("baudrate", 115200)))
        self.parity = combo(["N", "E", "O"], config.get("parity", "N"))
        self.stopbits = combo(["1", "2"], config.get("stopbits", 1))
        self.encoding = combo(["utf-8", "gbk"], config.get("encoding", "utf-8"))
        self.line_ending = combo(["LF", "CRLF", "CR"], config.get("line_ending", "LF"))
        fields = [
            ("逻辑别名", self.alias),
            ("COM 口", self.port),
            ("单板设备号", self.device),
            ("用途", self.role),
            ("波特率 / 8 数据位", self.baudrate),
            ("校验位", self.parity),
            ("停止位", self.stopbits),
            ("编码", self.encoding),
            ("发送结束符", self.line_ending),
        ]
        for i, (label, widget) in enumerate(fields):
            form.addWidget(QtWidgets.QLabel(label), i // 3, (i % 3) * 2)
            form.addWidget(widget, i // 3, (i % 3) * 2 + 1)
        self.remove_button = QtWidgets.QPushButton("移除这个串口资源")
        form.addWidget(self.remove_button, 3, 0, 1, 2)
        layout.addWidget(self.config_group)

        row = QtWidgets.QHBoxLayout()
        self.connect_button = QtWidgets.QPushButton("连接")
        self.disconnect_button = QtWidgets.QPushButton("断开")
        self.clear_button = QtWidgets.QPushButton("清空显示")
        self.status = QtWidgets.QLabel()
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        row.addWidget(self.clear_button)
        row.addWidget(self.status, 1)
        layout.addLayout(row)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QtGui.QFont("Consolas", 10))
        self.output.setPlaceholderText("显式连接后在这里持续接收串口输出。")
        layout.addWidget(self.output, 1)
        command_row = QtWidgets.QHBoxLayout()
        self.command = QtWidgets.QLineEdit()
        self.command.setPlaceholderText("输入由用户确定的命令；发送时附加配置的结束符")
        self.send_button = QtWidgets.QPushButton("发送")
        command_row.addWidget(self.command, 1)
        command_row.addWidget(self.send_button)
        layout.addLayout(command_row)
        shortcut_row = QtWidgets.QHBoxLayout()
        self.shortcut_buttons = []
        for shortcut in shortcuts:
            if not isinstance(shortcut, dict):
                continue
            label, command = shortcut.get("label", ""), shortcut.get("command", "")
            if not isinstance(label, str) or not isinstance(command, str):
                continue
            button = QtWidgets.QPushButton(label)
            button.setToolTip(command)
            button.clicked.connect(lambda checked=False, text=command: self.send(text))
            shortcut_row.addWidget(button)
            self.shortcut_buttons.append(button)
        shortcut_row.addStretch(1)
        layout.addLayout(shortcut_row)
        self.alias.editingFinished.connect(
            lambda: owner._rename(rid, self.alias.text())
        )
        self.port.editingFinished.connect(
            lambda: owner._edit(rid, "port", self.port.text().strip().upper())
        )
        self.baudrate.editingFinished.connect(self._baudrate_changed)
        self.device.currentIndexChanged.connect(
            lambda: owner._edit(rid, "device", self.device.currentData())
        )
        for key in ("role", "parity", "encoding", "line_ending"):
            widget = getattr(self, key)
            widget.currentTextChanged.connect(
                lambda value, field=key: owner._edit(rid, field, value)
            )
        self.stopbits.currentTextChanged.connect(
            lambda value: owner._edit(
                rid, "stopbits", int(value) if value in ("1", "2") else value
            )
        )
        self.remove_button.clicked.connect(lambda: owner._remove(rid))
        self.connect_button.clicked.connect(
            lambda: owner._manual(lambda: owner.runtime.connect(rid))
        )
        self.disconnect_button.clicked.connect(
            lambda: owner._manual(lambda: owner.runtime.disconnect(rid))
        )
        self.send_button.clicked.connect(lambda: self.send(self.command.text()))
        self.command.returnPressed.connect(lambda: self.send(self.command.text()))
        self.clear_button.clicked.connect(self.clear_display)

    def _baudrate_changed(self):
        value = self.baudrate.text().strip()
        self.owner._edit(
            self.rid, "baudrate", int(value) if value.isdecimal() else value
        )

    def send(self, command):
        if command:
            self.owner._manual(lambda: self.owner.runtime.send(self.rid, command))

    def clear_display(self):
        view = self.owner.runtime.snapshot(self.rid)
        self.generation = view["generation"]
        self.display_cursor = view["end"]
        self.last_end = -1
        self.output.clear()
        self.refresh()

    def refresh(self):
        view = self.owner.runtime.snapshot(self.rid, self.display_cursor)
        if view["generation"] != self.generation or view["end"] < self.display_cursor:
            self.generation = view["generation"]
            self.display_cursor = 0
            self.last_end = -1
            view = self.owner.runtime.snapshot(self.rid, 0)
        state = self.owner._status_for(self.rid, view)
        self.status.setText(state + (" · 显示缓存已截断" if view["truncated"] else ""))
        if self.last_end != view["end"]:
            bar = self.output.verticalScrollBar()
            follow = bar.value() >= bar.maximum() - 2
            previous = bar.value()
            self.output.setPlainText(view["text"])
            bar.setValue(bar.maximum() if follow else previous)
            self.last_end = view["end"]
        idle = self.owner._editable()
        connected = view["connected"] or view["worker_alive"] or view["closing"]
        self.config_group.setEnabled(idle and not connected)
        self.connect_button.setEnabled(
            idle and not connected and not self.owner._invalid
        )
        self.disconnect_button.setEnabled(idle and (connected or bool(view["fault"])))
        can_send = (
            idle
            and view["connected"]
            and not view["closing"]
            and not view["fault"]
            and not self.owner._invalid
        )
        self.command.setEnabled(can_send)
        self.send_button.setEnabled(can_send)
        for button in self.shortcut_buttons:
            button.setEnabled(can_send)
        self.owner._statuses[self.rid] = state


class ConsoleWorkspace:
    def __init__(self, context, runtime):
        self.context, self.runtime = context, runtime
        self._disposed = self._loading = self._busy = False
        self._active, self._invalid = False, False
        self._statuses, self.panels = {}, {}
        self.widget = QtWidgets.QScrollArea()
        self.widget.setWidgetResizable(True)
        self.content = QtWidgets.QWidget()
        self.widget.setWidget(self.content)
        layout = QtWidgets.QVBoxLayout(self.content)
        title = QtWidgets.QLabel("CH340 串口 · 按 COM 分配单板与 MCU / SOC")
        title.setStyleSheet("font-weight: 600; font-size: 16px")
        layout.addWidget(title)
        self.status_label = QtWidgets.QLabel("COM 口只手工填写；不会扫描或自动连接。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.add_group = QtWidgets.QGroupBox("添加一个实际串口")
        row = QtWidgets.QHBoxLayout(self.add_group)
        self.new_alias = QtWidgets.QLineEdit()
        self.new_alias.setPlaceholderText("逻辑别名，如 mcu")
        self.new_port = QtWidgets.QLineEdit()
        self.new_port.setPlaceholderText("手填 COM 口")
        self.new_device = QtWidgets.QComboBox()
        self.new_role = combo(["MCU", "SOC"])
        self.add_button = QtWidgets.QPushButton("添加串口")
        for w in (
            self.new_alias,
            self.new_port,
            self.new_device,
            self.new_role,
            self.add_button,
        ):
            row.addWidget(w)
        layout.addWidget(self.add_group)
        self.add_button.clicked.connect(self._add)

        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.empty_label = QtWidgets.QLabel(
            "尚未配置串口。没有 MCU 或 SOC 的单板无需创建占位资源。"
        )
        layout.addWidget(self.empty_label)
        self.shortcuts_group = QtWidgets.QGroupBox("用户快捷命令")
        shortcut_layout = QtWidgets.QVBoxLayout(self.shortcuts_group)
        shortcut_inputs = QtWidgets.QHBoxLayout()
        self.shortcut_label = QtWidgets.QLineEdit()
        self.shortcut_label.setPlaceholderText("按钮名称")
        self.shortcut_command = QtWidgets.QLineEdit()
        self.shortcut_command.setPlaceholderText("用户定义的命令")
        self.add_shortcut = QtWidgets.QPushButton("添加快捷命令")
        self.remove_shortcut = QtWidgets.QPushButton("移除选中快捷命令")
        for w in (
            self.shortcut_label,
            self.shortcut_command,
            self.add_shortcut,
            self.remove_shortcut,
        ):
            shortcut_inputs.addWidget(w)
        shortcut_layout.addLayout(shortcut_inputs)
        self.shortcut_table = QtWidgets.QTableWidget(0, 2)
        self.shortcut_table.setHorizontalHeaderLabels(["名称", "命令"])
        self.shortcut_table.horizontalHeader().setStretchLastSection(True)
        self.shortcut_table.setMaximumHeight(110)
        shortcut_layout.addWidget(self.shortcut_table)
        layout.addWidget(self.shortcuts_group)
        self.add_shortcut.clicked.connect(self._add_shortcut)
        self.remove_shortcut.clicked.connect(self._remove_shortcut)
        self.shortcut_table.cellChanged.connect(self._shortcut_changed)

        self._subscriptions = [
            context.subscribe_run_state(self._run_state),
            context.subscribe_environment(self._reload),
        ]
        self._active = context.run_state() == "ACTIVE"
        self._reload()
        self.timer = QtCore.QTimer(self.widget)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()

    def _editable(self):
        return (
            not (self._active or self._busy or self._disposed or self._loading)
            and self.context.run_state() == "IDLE"
        )

    def _run_state(self, state):
        if self._disposed:
            return
        self._active = state == "ACTIVE"
        self._refresh()

    def _reload(self):
        if self._disposed:
            return
        self._loading = True
        selected = next(
            (
                rid
                for rid, panel in self.panels.items()
                if panel is self.tabs.currentWidget()
            ),
            None,
        )
        old = {
            rid: (panel.display_cursor, panel.generation, panel.command.text())
            for rid, panel in self.panels.items()
        }
        while self.tabs.count():
            panel = self.tabs.widget(0)
            self.tabs.removeTab(0)
            panel.deleteLater()
        self.panels, self._statuses = {}, {}
        data = self.context.current_slice()
        self._report = validate_slice(data)
        self._invalid = self._report["status"] == "INVALID"
        shortcuts = data.get("plugin", {}).get("config", {}).get("shortcuts", [])
        if not isinstance(shortcuts, list):
            shortcuts = []
        current_device = self.new_device.currentData()
        fill_devices(
            self.new_device, self.context.configured_device_ids(), current_device
        )
        for rid, record in data.get("resources", {}).items():
            panel = TerminalPanel(self, rid, record, shortcuts)
            if rid in old:
                panel.display_cursor, panel.generation, text = old[rid]
                panel.command.setText(text)
            self.panels[rid] = panel
            config = record.get("config", {})
            if not isinstance(config, dict):
                config = {}
            self.tabs.addTab(
                panel,
                f"{config.get('port', '未配置 COM')} · {config.get('role', '?')} · {record.get('device', '未分配')}",
            )
            self.tabs.setTabToolTip(self.tabs.count() - 1, rid)
        self.shortcut_table.setRowCount(len(shortcuts))
        for i, entry in enumerate(shortcuts):
            for column, key in enumerate(("label", "command")):
                self.shortcut_table.setItem(
                    i,
                    column,
                    QtWidgets.QTableWidgetItem(
                        str(entry.get(key, "")) if isinstance(entry, dict) else ""
                    ),
                )
        self.empty_label.setVisible(not self.panels)
        self._loading = False
        if selected:
            self.select_resource(selected)
        if self._report["diagnostics"]:
            self.status_label.setText(
                " · ".join(d["message"] for d in self._report["diagnostics"])
            )
        else:
            self.status_label.setText(
                "配置已保存。连接后可持续接收；清空显示不清除 Run 观察缓存。"
            )
        self._refresh()

    def _status_for(self, rid, view):
        diagnostics = [
            d
            for d in self._report["diagnostics"]
            if d.get("path", "").startswith("resources." + rid + ".")
        ]
        if diagnostics:
            return "配置待修正：" + diagnostics[0]["message"]
        if view.get("config_changed"):
            return "连接使用旧配置，请先断开"
        if view["fault"]:
            return "故障：" + view["fault"]["message"]
        if view["closing"]:
            return "正在关闭，尚未完成"
        return "已连接" if view["connected"] else "未连接"

    def _refresh(self):
        if self._disposed:
            return
        idle = self._editable()
        self.add_group.setEnabled(idle)
        self.shortcuts_group.setEnabled(idle)
        for panel in list(self.panels.values()):
            panel.refresh()

    def _commit(self, data):
        if not self._editable():
            return False
        try:
            self.context.commit(data, validate_slice(data))
        except Exception as exc:
            self._reload()
            self.status_label.setText("保存失败：" + str(exc))
            return False
        self._reload()
        return True

    def _edit(self, rid, field, value):
        if not self._editable():
            return
        panel = self.panels.get(rid)
        if panel and not panel.config_group.isEnabled():
            return
        data = self.context.current_slice()
        record = data["resources"].get(rid)
        if record is None:
            return
        if field == "device":
            if record.get("device") == value:
                return
            if value is None:
                record.pop("device", None)
            else:
                record["device"] = value
        else:
            if not isinstance(record.get("config"), dict):
                record["config"] = {}
            if record["config"].get(field, DEFAULTS.get(field)) == value:
                return
            record["config"][field] = value
        self._commit(data)

    def _rename(self, rid, alias):
        if not self._editable() or not self.panels[rid].config_group.isEnabled():
            return
        new_rid = "CONSOLE." + alias.strip()
        if new_rid == rid:
            return
        data = self.context.current_slice()
        if (
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", alias.strip())
            or new_rid in data["resources"]
        ):
            self._reload()
            self.status_label.setText("别名格式无效或已存在。")
            return
        data["resources"][new_rid] = data["resources"].pop(rid)
        if self._commit(data):
            self.select_resource(new_rid)

    def _add(self):
        if not self._editable():
            return
        alias = self.new_alias.text().strip()
        data = self.context.current_slice()
        rid = "CONSOLE." + alias
        if (
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", alias)
            or rid in data["resources"]
        ):
            self.status_label.setText("请填写唯一的逻辑别名，首字符为英文字母。")
            return
        record = {
            "type": "CONSOLE",
            "config": {
                "port": self.new_port.text().strip().upper(),
                "role": self.new_role.currentText(),
            },
        }
        if self.new_device.currentData():
            record["device"] = self.new_device.currentData()
        data["resources"][rid] = record
        if self._commit(data):
            self.new_alias.clear()
            self.new_port.clear()
            self.select_resource(rid)

    def _remove(self, rid):
        if not self._editable() or not self.panels[rid].config_group.isEnabled():
            return
        data = self.context.current_slice()
        data["resources"].pop(rid, None)
        self._commit(data)

    def _add_shortcut(self):
        if not self._editable():
            return
        label, command = (
            self.shortcut_label.text().strip(),
            self.shortcut_command.text(),
        )
        if not label or not command.strip():
            self.status_label.setText("请填写快捷命令名称和命令。")
            return
        data = self.context.current_slice()
        config = data["plugin"]["config"]
        shortcuts = config.get("shortcuts", [])
        if not isinstance(shortcuts, list):
            shortcuts = []
        config["shortcuts"] = shortcuts + [{"label": label, "command": command}]
        if self._commit(data):
            self.shortcut_label.clear()
            self.shortcut_command.clear()

    def _remove_shortcut(self):
        if not self._editable():
            return
        index = self.shortcut_table.currentRow()
        data = self.context.current_slice()
        shortcuts = data["plugin"]["config"].get("shortcuts", [])
        if 0 <= index < len(shortcuts):
            shortcuts.pop(index)
            self._commit(data)

    def _shortcut_changed(self, row, column):
        if not self._editable():
            return
        data = self.context.current_slice()
        entry = data["plugin"]["config"]["shortcuts"][row]
        if not isinstance(entry, dict):
            entry = {"label": "", "command": ""}
            data["plugin"]["config"]["shortcuts"][row] = entry
        entry[("label", "command")[column]] = self.shortcut_table.item(
            row, column
        ).text()
        self._commit(data)

    def _manual(self, action):
        if not self._editable():
            return
        self._busy = True
        self._refresh()

        def completed(result):
            if self._disposed:
                return
            self._busy = False
            if result["ok"]:
                self.status_label.setText("操作完成。")
            else:
                diagnostic = result.get("diagnostic") or {}
                self.status_label.setText(
                    str(diagnostic.get("code", "CONSOLE_ERROR"))
                    + "："
                    + str(diagnostic.get("message", ""))
                )
            self._refresh()

        try:
            self.context.submit_manual(action, completed)
        except Exception as exc:
            self._busy = False
            self.status_label.setText("操作未提交：" + str(exc))
            self._refresh()

    def resource_statuses(self):
        return dict(self._statuses)

    def select_resource(self, resource_id):
        panel = self.panels.get(resource_id)
        if panel is None:
            return False
        self.tabs.setCurrentWidget(panel)
        return True

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.timer.stop()
        for subscription in self._subscriptions:
            subscription.unsubscribe()


def create_workspace(context, runtime):
    return ConsoleWorkspace(context, runtime)
