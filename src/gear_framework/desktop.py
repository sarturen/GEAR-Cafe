"""Optional Qt desktop shell; plugin-specific controls stay in Workspaces."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
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
        self.setWindowTitle("GEAR · 设备测试")
        self.resize(1120, 820)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        heading = QLabel("GEAR  设备测试")
        heading.setStyleSheet("font-size: 22px; font-weight: 600")
        layout.addWidget(heading)
        environment = QLabel(f"环境：{framework.environment_path}")
        environment.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        environment.setWordWrap(True)
        layout.addWidget(environment)

        self.inputs = QWidget()
        files = QFormLayout(self.inputs)
        files.setContentsMargins(0, 0, 0, 0)
        self.case_path = self._file_row(files, "测试用例", case)
        self.project_path = self._file_row(files, "项目配置", project)
        layout.addWidget(self.inputs)
        controls = QHBoxLayout()
        self.submit_button = QPushButton("开始预检")
        self.confirm_button = QPushButton("确认执行")
        self.stop_button = QPushButton("停止 / 取消")
        for button in (self.submit_button, self.confirm_button, self.stop_button):
            controls.addWidget(button)
        controls.addStretch()
        self.status_label = QLabel("就绪")
        self.status_label.setWordWrap(True)
        controls.addWidget(self.status_label, 1)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._home_page(), "首页")
        for plugin_id, entry in framework._registry.entries.items():
            if entry.workspace is not None:
                workspace = gui.create_workspace(
                    framework, plugin_id, self.dispatcher.dispatch
                )
                self.tabs.addTab(workspace.widget, plugin_id)
        splitter.addWidget(self.tabs)
        results = QWidget()
        result_layout = QVBoxLayout(results)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.addWidget(QLabel("预检与运行诊断"))
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setPlaceholderText("预检完成后，在此查看配置检查、资源绑定和诊断。")
        result_layout.addWidget(self.diagnostics)
        report_row = QHBoxLayout()
        report_row.addWidget(QLabel("报告"))
        self.report_path = QLineEdit()
        self.report_path.setReadOnly(True)
        self.open_report_button = QPushButton("打开报告")
        self.open_report_button.clicked.connect(self._open_report)
        report_row.addWidget(self.report_path, 1)
        report_row.addWidget(self.open_report_button)
        result_layout.addLayout(report_row)
        splitter.addWidget(results)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(body)

        self.submit_button.clicked.connect(self._submit)
        self.confirm_button.clicked.connect(self._confirm)
        self.stop_button.clicked.connect(self._stop)
        self.case_path.textChanged.connect(self.refresh_status)
        self.project_path.textChanged.connect(self.refresh_status)
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start()
        self.refresh_status()

    def _home_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        title = QLabel("欢迎使用 GEAR")
        title.setStyleSheet("font-size: 20px; font-weight: 600")
        layout.addWidget(title)
        steps = QLabel(
            "1. 在插件页面配置设备和资源，配置会保存到同一份环境文件。\n"
            "2. 在上方选择项目和测试用例，点击“开始预检”。\n"
            "3. 检查预检结果并确认执行，完成后查看报告。"
        )
        steps.setWordWrap(True)
        layout.addWidget(steps)
        plugins = QGroupBox("已安装插件")
        plugin_layout = QVBoxLayout(plugins)
        entries = self.framework._registry.entries
        if entries:
            for plugin_id, entry in entries.items():
                label = QLabel(f"{plugin_id}   ·   {entry.version}")
                label.setWordWrap(True)
                plugin_layout.addWidget(label)
        else:
            empty = QLabel("尚未安装插件。安装插件并重启后，即可使用对应功能。")
            empty.setWordWrap(True)
            plugin_layout.addWidget(empty)
        layout.addWidget(plugins)
        layout.addStretch()
        return page

    def _file_row(self, form, label, initial):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        field = QLineEdit(str(Path(initial).resolve()) if initial else "")
        field.setPlaceholderText("选择 YAML 文件")
        browse = QPushButton("选择…")

        def choose():
            path, _ = QFileDialog.getOpenFileName(
                self, label, field.text(), "YAML / JSON (*.yaml *.yml *.json);;所有文件 (*)"
            )
            if path:
                field.setText(path)

        browse.clicked.connect(choose)
        layout.addWidget(field, 1)
        layout.addWidget(browse)
        form.addRow(label, row)
        return field

    def _submit(self):
        try:
            self._action_error = None
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
            idle and bool(self.case_path.text().strip() and self.project_path.text().strip())
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
