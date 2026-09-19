"""Optional Qt desktop shell; plugin-specific controls stay in Workspaces."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import sys

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import gui
from .host import Framework


class GuiDispatcher(QObject):
    posted = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.posted.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def dispatch(self, callback):
        self.posted.emit(callback)

    @Slot(object)
    def _deliver(self, callback):
        try:
            callback()
        except Exception as exc:
            print(f"GEAR GUI callback failed: {exc}", file=sys.stderr)


class DesktopWindow(QMainWindow):
    def __init__(self, framework, *, case=None, project=None):
        super().__init__()
        self.framework = framework
        self.run_id = None
        self._closing_requested = False
        self._shutdown_complete = False
        self._last_diagnostics = None
        self._action_error = None
        self.dispatcher = GuiDispatcher(self)
        self._plugin_workspaces = {}
        self._resource_rows = ()
        self._selected_board = None
        self._board_names = None
        self.setWindowTitle("GEAR · 设备测试")
        self.resize(1120, 820)

        body = QWidget()
        body.setObjectName("desktopRoot")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_layout = QVBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 9, 12, 9)
        top_layout.setSpacing(7)
        identity = QHBoxLayout()
        heading = QLabel("GEAR")
        heading.setObjectName("brandLabel")
        identity.addWidget(heading)
        environment = QLabel(f"环境 · {Path(framework.environment_path).name}")
        environment.setObjectName("environmentLabel")
        environment.setToolTip(str(framework.environment_path))
        environment.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        identity.addWidget(environment)
        identity.addStretch()
        identity.addWidget(QLabel("当前单板"))
        self.board_selector = QComboBox()
        self.board_selector.setObjectName("boardSelector")
        self.board_selector.setMinimumWidth(150)
        self.board_selector.setMaximumWidth(240)
        identity.addWidget(self.board_selector)
        top_layout.addLayout(identity)

        self.inputs = QWidget()
        files = QHBoxLayout(self.inputs)
        files.setContentsMargins(0, 0, 0, 0)
        files.setSpacing(7)
        case_control, self.case_path = self._file_control("测试用例", case)
        project_control, self.project_path = self._file_control("项目配置", project)
        files.addWidget(case_control, 3)
        files.addWidget(project_control, 2)
        commands = QHBoxLayout()
        commands.setSpacing(7)
        commands.addWidget(self.inputs, 1)
        self.submit_button = QPushButton("开始预检")
        self.submit_button.setObjectName("submitButton")
        self.confirm_button = QPushButton("确认执行")
        self.stop_button = QPushButton("停止 / 取消")
        for button in (self.submit_button, self.confirm_button, self.stop_button):
            commands.addWidget(button)
        top_layout.addLayout(commands)
        layout.addWidget(top_bar)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("workspaceTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.addTab(self._home_page(), "首页")
        for plugin_id, entry in framework._registry.entries.items():
            if entry.workspace is not None:
                workspace = gui.create_workspace(
                    framework, plugin_id, self.dispatcher.dispatch
                )
                self._plugin_workspaces[plugin_id] = workspace
                labels = {
                    "gear.adb": "ADB",
                    "gear.relay": "继电器",
                    "gear.console": "串口",
                    "gear.camera": "摄像头",
                }
                label = workspace.widget.windowTitle() or labels.get(
                    plugin_id, plugin_id.removeprefix("gear.")
                )
                index = self.tabs.addTab(workspace.widget, label)
                self.tabs.setTabToolTip(index, plugin_id)
        self._style_tab_bar(self.tabs)
        layout.addWidget(self.tabs, 1)

        run_header = QFrame()
        run_header.setObjectName("runHeader")
        run_header_layout = QHBoxLayout(run_header)
        run_header_layout.setContentsMargins(10, 5, 10, 5)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusPill")
        run_header_layout.addWidget(self.status_label)
        self.run_case_label = QLabel("未选择用例")
        self.run_case_label.setObjectName("runCaseLabel")
        run_header_layout.addWidget(self.run_case_label)
        self.step_label = QLabel("当前步骤：尚未执行")
        run_header_layout.addWidget(self.step_label, 1)
        self.report_path = QLineEdit()
        self.report_path.setReadOnly(True)
        self.report_path.setPlaceholderText("测试报告将在运行完成后显示")
        run_header_layout.addWidget(self.report_path, 1)
        self.open_report_button = QPushButton("打开报告")
        self.open_report_button.clicked.connect(self._open_report)
        run_header_layout.addWidget(self.open_report_button)
        layout.addWidget(run_header)

        self.run_panel = QWidget()
        self.run_panel.setObjectName("runPanel")
        result_layout = QVBoxLayout(self.run_panel)
        result_layout.setContentsMargins(0, 0, 0, 0)
        self.result_tabs = QTabWidget()
        self.run_events = QPlainTextEdit()
        self.run_events.setReadOnly(True)
        self.run_events.setMaximumBlockCount(2000)
        self.run_events.setPlaceholderText(
            "执行时实时显示带时间戳的步骤事件；完整记录随报告归档。"
        )
        self.case_preview = QPlainTextEdit()
        self.case_preview.setReadOnly(True)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setPlaceholderText(
            "预检完成后，在此查看配置检查、资源绑定和诊断。"
        )
        self.result_tabs.addTab(self.diagnostics, "预检与诊断")
        self.result_tabs.addTab(self.run_events, "运行事件")
        self.result_tabs.addTab(self.case_preview, "用例内容")
        self._style_tab_bar(self.result_tabs, compact=True)
        result_layout.addWidget(self.result_tabs)
        self.run_panel.setMinimumHeight(150)
        self.run_panel.setMaximumHeight(240)
        layout.addWidget(self.run_panel)
        self.setCentralWidget(body)

        body.setStyleSheet("""
            QWidget#desktopRoot { background: #f4f6f8; color: #1f2937; }
            QFrame#topBar, QFrame#runHeader {
                background: white; border: 1px solid #dfe5ec; border-radius: 8px;
            }
            QLabel#brandLabel { font-size: 20px; font-weight: 700; color: #172033; }
            QLabel#environmentLabel { color: #667085; }
            QLabel#statusPill {
                background: #eef3ff; color: #2457c5; border-radius: 10px;
                padding: 3px 9px; font-weight: 600;
            }
            QComboBox#boardSelector {
                background: #f4f7fb; border: 1px solid #ccd7e5; border-radius: 6px;
                padding: 5px 9px; font-weight: 700; color: #26354d;
            }
            QTabWidget#workspaceTabs::pane {
                border: 1px solid #dfe5ec; background: white; border-radius: 9px;
            }
            QPushButton#submitButton {
                background: #2563eb; color: white; border: 0; border-radius: 6px;
                padding: 6px 12px; font-weight: 700;
            }
            QPushButton#submitButton:disabled { background: #aebbd2; }
            QLabel#runCaseLabel { font-weight: 700; color: #26354d; }
            """)

        self.submit_button.clicked.connect(self._submit)
        self.confirm_button.clicked.connect(self._confirm)
        self.stop_button.clicked.connect(self._stop)
        self.board_selector.currentTextChanged.connect(self._select_board)
        self.case_path.textChanged.connect(self.refresh_status)
        self.case_path.textChanged.connect(self._load_case_preview)
        self.case_path.textChanged.connect(self._update_run_case_label)
        self._update_run_case_label(self.case_path.text())
        self._load_case_preview()
        self._event_subscription = framework.subscribe(self._queue_run_event)
        self.resource_timer = QTimer(self)
        self.resource_timer.setInterval(500)
        self.resource_timer.timeout.connect(self._refresh_resources)
        self.resource_timer.start()
        self._refresh_resources()
        self.project_path.textChanged.connect(self.refresh_status)
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start()
        self.refresh_status()

    def _style_tab_bar(self, tabs, *, compact=False):
        tabs.setDocumentMode(True)
        tabs.tabBar().setDrawBase(False)
        tabs.tabBar().setExpanding(False)
        vertical = "5px" if compact else "8px"
        tabs.tabBar().setStyleSheet(f"""
            QTabBar {{ background: transparent; }}
            QTabBar::tab {{
                background: #edf1f6; color: #526075;
                border: 1px solid #dce3ec; border-radius: 7px;
                padding: {vertical} 15px; margin: 0 5px 5px 0;
            }}
            QTabBar::tab:hover {{ background: #e3eaf5; color: #27446f; }}
            QTabBar::tab:selected {{
                background: #2563eb; color: white; border-color: #2563eb;
                font-weight: 700;
            }}
            """)

    def _home_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        self.board_title = QLabel("尚未登记单板")
        self.board_title.setStyleSheet("font-size: 20px; font-weight: 700")
        title_box.addWidget(self.board_title)
        self.board_subtitle = QLabel("先在 ADB 页面登记设备号，再绑定硬件资源")
        self.board_subtitle.setStyleSheet("color: #667085")
        title_box.addWidget(self.board_subtitle)
        header.addLayout(title_box)
        header.addStretch()
        layout.addLayout(header)

        summary = QHBoxLayout()
        summary.setSpacing(8)
        self.board_summary = {}
        for kind, title in (
            ("ADB", "ADB"),
            ("POWER", "电源"),
            ("CONSOLE", "串口"),
            ("SCREEN", "摄像头"),
        ):
            card = QFrame()
            card.setStyleSheet(
                "QFrame { background: #f8fafc; border: 1px solid #e1e7ef; "
                "border-radius: 7px; } QLabel { border: 0; }"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            caption = QLabel(title)
            caption.setStyleSheet("color: #667085; font-weight: 600")
            value = QLabel("未配置")
            value.setStyleSheet("font-size: 14px; font-weight: 700")
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            self.board_summary[kind] = value
            summary.addWidget(card, 1)
        layout.addLayout(summary)

        overview = QLabel("资源归属与实时状态 · 双击资源进入对应控制页面")
        overview.setStyleSheet("font-weight: 600; color: #475467")
        layout.addWidget(overview)
        self.resources_table = QTableWidget(0, 6)
        self.resources_table.setHorizontalHeaderLabels(
            ["单板设备号", "资源", "类型", "用途", "物理连接", "当前状态"]
        )
        self.resources_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.resources_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.resources_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.resources_table.verticalHeader().setVisible(False)
        self.resources_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.resources_table.horizontalHeader().setStretchLastSection(True)
        self.resources_table.cellDoubleClicked.connect(self._open_resource)
        layout.addWidget(self.resources_table, 1)
        return page

    def _sync_board_selector(self, boards):
        names = tuple(sorted(boards))
        if names == self._board_names:
            return
        selected = self._selected_board if self._selected_board in names else None
        if selected is None and names:
            selected = names[0]
        previous = self.board_selector.blockSignals(True)
        try:
            self.board_selector.clear()
            if names:
                self.board_selector.addItems(names)
            else:
                self.board_selector.addItem("未登记单板")
            if selected is not None:
                self.board_selector.setCurrentIndex(names.index(selected))
            self.board_selector.setEnabled(len(names) > 1)
        finally:
            self.board_selector.blockSignals(previous)
        self._board_names = names
        self._selected_board = selected
        self._update_board_heading()

    def _select_board(self, board):
        if board not in self._board_names or board == self._selected_board:
            return
        self._selected_board = board
        self._resource_rows = ()
        self._update_board_heading()
        self._refresh_resources()

    def _update_board_heading(self):
        if self._selected_board is None:
            self.board_title.setText("尚未登记单板")
            self.board_subtitle.setText("先在 ADB 页面登记设备号，再绑定硬件资源")
            return
        self.board_title.setText(self._selected_board)
        self.board_subtitle.setText("当前单板 · 资源配置与插件状态汇总")

    def _update_board_summary(self, resources):
        grouped = {kind: [] for kind in self.board_summary}
        for _, record in resources:
            if record["type"] in grouped:
                grouped[record["type"]].append(record)
        for kind, records in grouped.items():
            roles = sorted(
                {
                    str(record["config"].get("role"))
                    for record in records
                    if record["config"].get("role")
                }
            )
            if kind == "SCREEN":
                text = f"{len(records)} / 6" if records else "未配置"
            elif roles:
                text = " · ".join(roles)
            elif records:
                text = f"{len(records)} 项"
            else:
                text = "未配置"
            self.board_summary[kind].setText(text)

    def _refresh_resources(self):
        """GUI-only projection of saved ownership plus Workspace caches."""
        if self._shutdown_complete:
            return
        with self.framework._mutex:
            environment = deepcopy(self.framework._environment)
        resources = environment.get("resources", {})
        registered_boards = set(environment.get("devices", {}))
        boards = set(registered_boards)
        boards.update(
            record.get("device")
            for record in resources.values()
            if type(record.get("device")) is str and record.get("device")
        )
        self._sync_board_selector(boards)
        statuses = {}
        for plugin_id, workspace in self._plugin_workspaces.items():
            getter = getattr(workspace, "resource_statuses", None)
            if callable(getter):
                try:
                    statuses.update(getter())
                except Exception as exc:
                    for rid, record in resources.items():
                        if (
                            self.framework._registry.owners.get(record["type"])
                            == plugin_id
                        ):
                            statuses[rid] = "状态读取失败：" + str(exc)
        visible_resources = [
            (rid, record)
            for rid, record in sorted(resources.items())
            if self._selected_board is None
            or record.get("device") == self._selected_board
        ]
        self._update_board_summary(visible_resources)
        rows = []
        for rid, record in visible_resources:
            board = record.get("device")
            config = record["config"]
            kind = record["type"]
            physical = ""
            if kind == "POWER":
                controller = config.get("controller", "main")
                plugin_id = self.framework._registry.owners.get(kind)
                settings = (
                    environment.get("plugins", {}).get(plugin_id, {}).get("config", {})
                )
                controllers = settings.get("controllers")
                cfg = (
                    controllers.get(controller, {})
                    if type(controllers) is dict and type(controller) is str
                    else settings
                )
                port = cfg.get("port", "未配置COM") if type(cfg) is dict else "配置无效"
                physical = f"{controller} · {port} · CH{config.get('channel', '?')}"
            elif kind == "CONSOLE":
                physical = str(config.get("port") or "未配置COM")
            elif kind == "SCREEN":
                physical = str(config.get("camera_id") or "未配置输入")
            elif kind == "ADB":
                physical = str(board or "未绑定设备号")
            label = board or "未分配单板"
            if board and board not in registered_boards:
                label += "（未登记）"
            rows.append(
                (
                    label,
                    rid,
                    kind,
                    str(
                        config.get("role")
                        or ("ADB / fastboot" if kind == "ADB" else "—")
                    ),
                    physical,
                    str(statuses.get(rid, "—")),
                )
            )
        if self._selected_board is not None and not rows:
            rows.append((self._selected_board, "未配置资源", "—", "—", "—", "—"))
        if not rows:
            rows.append(
                (
                    "尚未登记单板",
                    "在 ADB 页登记设备号，再分配各插件资源",
                    "—",
                    "—",
                    "—",
                    "—",
                )
            )
        rows = tuple(sorted(rows))
        if rows == self._resource_rows:
            return
        selected = self.resources_table.currentRow()
        selected_id = (
            self._resource_rows[selected][1]
            if self._resource_rows and 0 <= selected < len(self._resource_rows)
            else None
        )
        self._resource_rows = rows
        self.resources_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                display = (
                    value[:32] + "…"
                    if column == 4 and values[2] == "SCREEN" and len(value) > 32
                    else value
                )
                item = QTableWidgetItem(display)
                item.setToolTip(value)
                self.resources_table.setItem(row, column, item)
            if values[1] == selected_id:
                self.resources_table.selectRow(row)

    def _open_resource(self, row, column):
        if not 0 <= row < len(self._resource_rows):
            return
        values = self._resource_rows[row]
        workspace = self._plugin_workspaces.get(
            self.framework._registry.owners.get(values[2])
        )
        if workspace is not None:
            self.tabs.setCurrentWidget(workspace.widget)
            select = getattr(workspace, "select_resource", None)
            if callable(select):
                select(values[1])

    def _load_case_preview(self):
        path = self.case_path.text().strip()
        if not path:
            self.case_preview.clear()
            return
        try:
            self.case_preview.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError) as exc:
            self.case_preview.setPlainText("无法读取用例：" + str(exc))

    def _update_run_case_label(self, path):
        path = path.strip()
        self.run_case_label.setText(
            "用例 · " + Path(path).name if path else "未选择用例"
        )
        self.run_case_label.setToolTip(path)

    def _queue_run_event(self, event):
        saved = deepcopy(event)
        self.dispatcher.dispatch(lambda: self._show_run_event(saved))

    def _show_run_event(self, event):
        if self._shutdown_complete or event["run_id"] != self.run_id:
            return
        if event["event"] == "run.state" and event["phase"] == "WAITING_CONFIRMATION":
            archived = (
                self.framework.app_dir
                / "runs"
                / self.run_id
                / "input"
                / "test-case.yaml"
            )
            try:
                self.case_preview.setPlainText(archived.read_text(encoding="utf-8-sig"))
                self.case_preview.setToolTip("本次运行的归档输入：" + str(archived))
            except (OSError, UnicodeError) as exc:
                self.case_preview.setPlainText("无法读取归档用例：" + str(exc))
        step = event.get("step_path")
        if step:
            self.step_label.setText("当前步骤：" + step)
        detail = json.dumps(event["details"], ensure_ascii=False)
        self.run_events.appendPlainText(
            f"{event['timestamp']}  #{event['sequence']}  {event['phase']}  {event['event']}"
            f"  {step or ''}  {event.get('resource_id') or ''}\n{detail[:4000]}"
        )

    def _file_control(self, label, initial):
        row = QFrame()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(7, 0, 0, 0)
        layout.setSpacing(5)
        caption = QLabel(label)
        caption.setStyleSheet("color: #667085; font-weight: 600")
        field = QLineEdit(str(Path(initial).resolve()) if initial else "")
        field.setPlaceholderText(f"选择{label}")
        browse = QPushButton("选择…")

        def choose():
            path, _ = QFileDialog.getOpenFileName(
                self,
                label,
                field.text(),
                "YAML / JSON (*.yaml *.yml *.json);;所有文件 (*)",
            )
            if path:
                field.setText(path)

        browse.clicked.connect(choose)
        layout.addWidget(caption)
        layout.addWidget(field, 1)
        layout.addWidget(browse)
        return row, field

    def _show_run_panel(self, widget=None):
        if widget is not None:
            self.result_tabs.setCurrentWidget(widget)

    def _submit(self):
        self._show_run_panel(self.diagnostics)
        try:
            self._action_error = None
            self._load_case_preview()
            self.run_events.clear()
            self.step_label.setText("当前步骤：预检")
            self.run_id = self.framework.submit(
                str(Path(self.case_path.text().strip()).resolve()),
                str(Path(self.project_path.text().strip()).resolve()),
                str(self.framework.environment_path),
            )
        except Exception as exc:
            self._action_error = str(exc)
        self.refresh_status()

    def _confirm(self):
        try:
            self.framework.confirm(self.run_id)
            self._show_run_panel(self.run_events)
        except Exception as exc:
            self._action_error = str(exc)
        self.refresh_status()

    def _stop(self):
        try:
            self.framework.stop(self.run_id)
        except Exception as exc:
            self._action_error = str(exc)
        self.refresh_status()

    def _open_report(self):
        if self.report_path.text():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.report_path.text()))

    def refresh_status(self):
        if self._shutdown_complete:
            return
        host = self.framework
        status = host.get_status(self.run_id) if self.run_id else None
        with host._mutex:
            pending = bool(host._pending_manual)
            blocked = host._blocked
            busy = host._active is not None or pending or blocked
            # BLOCKED keeps the session reserved after finalization has finished.
            quiescent = not pending and (
                host._active is None
                or host._active.status["phase"] in ("FINISHED", "BLOCKED")
            )
            session_diagnostics = list(host.session_diagnostics)
        phase = status["phase"] if status else None
        idle = not busy and not self._closing_requested
        self.inputs.setEnabled(idle)
        self.submit_button.setEnabled(
            idle
            and bool(self.case_path.text().strip() and self.project_path.text().strip())
        )
        self.confirm_button.setEnabled(
            phase == "WAITING_CONFIRMATION" and not self._closing_requested
        )
        self.stop_button.setEnabled(
            phase in ("PREFLIGHT", "WAITING_CONFIRMATION", "RUNNING", "FINALIZING")
            and not self._closing_requested
        )
        labels = {
            "PREFLIGHT": "正在预检…",
            "WAITING_CONFIRMATION": "预检通过，请确认执行",
            "RUNNING": "正在执行…",
            "FINALIZING": "正在收尾并保存报告…",
            "FINISHED": "已完成",
        }
        text = labels.get(phase, "手动操作进行中…" if pending else "就绪")
        if status and status["outcome"]:
            text += f" · {status['outcome']}"
        if blocked:
            text = "会话已阻止：请查看诊断，解决问题后重启 GEAR"
        if self._closing_requested:
            text = "正在关闭，等待当前调用和收尾完成…"
        if self._action_error:
            text = "操作失败，请查看诊断"
        if blocked or self._action_error:
            self._show_run_panel(self.diagnostics)
        self.status_label.setText(text)

        details = {}
        if status:
            details.update(
                (key, status[key])
                for key in ("preflight", "primary_failure", "finalization_errors")
            )
        if session_diagnostics:
            details["session_diagnostics"] = session_diagnostics
        if self._action_error:
            details["error"] = self._action_error
        rendered = json.dumps(details, ensure_ascii=False, indent=2) if details else ""
        if rendered != self._last_diagnostics:
            self.diagnostics.setPlainText(rendered)
            self._last_diagnostics = rendered
        report = status["report_path"] if status else None
        if self.report_path.text() != (report or ""):
            self.report_path.setText(report or "")
        self.open_report_button.setEnabled(bool(report))
        if self._closing_requested and quiescent:
            self._finish_close()

    def closeEvent(self, event):
        if self._shutdown_complete:
            event.accept()
            return
        event.ignore()
        if not self._closing_requested:
            self._closing_requested = True
            self.centralWidget().setEnabled(False)
            with self.framework._mutex:
                self.framework._closing = True
                active = self.framework._active
                if active is not None:
                    self.framework.stop(active.status["run_id"])
            self.status_label.setText("正在关闭，等待当前调用和收尾完成…")
            # Let the close event return before polling or disposing any widgets.
            QTimer.singleShot(0, self.refresh_status)

    def _finish_close(self):
        self.timer.stop()
        self.resource_timer.stop()
        self._event_subscription.unsubscribe()
        try:
            # Run/manual work is complete; Workspace disposal stays on the GUI thread.
            self.framework.close()
        except Exception as exc:
            self.status_label.setText(f"关闭失败：{exc}。请记录诊断后关闭窗口。")
            print(f"GEAR shutdown failed: {exc}", file=sys.stderr)
            self._shutdown_complete = True
            return
        self._shutdown_complete = True
        self.close()


def run_gui(app_dir, *, case=None, project=None):
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app_dir = Path(app_dir).resolve()
    environment = app_dir / "environment.yaml"
    try:
        with environment.open("x", encoding="utf-8") as stream:
            stream.write(
                "api: gear.environment/v1\nname: GEAR\n"
                "plugins: {}\ndevices: {}\nresources: {}\n"
            )
    except FileExistsError:
        pass
    framework = Framework(app_dir, environment)
    try:
        window = DesktopWindow(framework, case=case, project=project)
        window.show()
        return app.exec()
    finally:
        framework.close()
