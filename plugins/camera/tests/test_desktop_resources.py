"""Desktop additions: fake host and workspaces only, never hardware."""

from types import SimpleNamespace
from pathlib import Path
import threading
import pytest
from PySide6.QtWidgets import QApplication, QWidget
from gear_framework.desktop import DesktopWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class Host:
    def __init__(self, path):
        self.environment_path = path / "environment.yaml"
        self._mutex = threading.RLock()
        self._pending_manual, self._blocked, self._active = [], False, None
        self.session_diagnostics = []
        self._environment = {
            "devices": {"BOARD001": {}, "BOARD002": {}},
            "resources": {
                "POWER.kl30": {
                    "type": "POWER",
                    "device": "BOARD001",
                    "config": {"controller": "bench", "channel": 2, "role": "KL30"},
                },
                "CONSOLE.mcu": {
                    "type": "CONSOLE",
                    "device": "BOARD001",
                    "config": {"port": "FAKE_COM", "role": "MCU"},
                },
            },
            "plugins": {
                "gear.relay": {
                    "config": {"controllers": {"bench": {"port": "FAKE_RELAY"}}}
                }
            },
        }
        self._registry = SimpleNamespace(
            entries={
                "gear.relay": SimpleNamespace(version="1", workspace=True),
                "gear.console": SimpleNamespace(version="1", workspace=True),
            },
            owners={"POWER": "gear.relay", "CONSOLE": "gear.console"},
        )
        self.callbacks, self.unsubscribed = [], False

    def subscribe(self, callback):
        self.callbacks.append(callback)
        return SimpleNamespace(unsubscribe=lambda: setattr(self, "unsubscribed", True))

    def close(self):
        pass


@pytest.fixture
def desktop(qapp, tmp_path, monkeypatch):
    from gear_framework import desktop as module

    host = Host(tmp_path)
    workspaces = {}

    def create(host, pid, dispatcher):
        ws = SimpleNamespace(
            widget=QWidget(),
            selected=None,
            resource_statuses=lambda: {"POWER.kl30": "吸合", "CONSOLE.mcu": "已连接"},
        )
        ws.select_resource = lambda rid: setattr(ws, "selected", rid)
        workspaces[pid] = ws
        return ws

    monkeypatch.setattr(module.gui, "create_workspace", create)
    window = DesktopWindow(host)
    yield window, host, workspaces
    window.close()
    qapp.processEvents()
    window.deleteLater()
    qapp.processEvents()


def test_board_overview_filters_selected_board_and_navigates_to_resource(desktop):
    window, host, workspaces = desktop
    window._refresh_resources()
    table = window.resources_table
    rows = [
        [table.item(i, j).text() for j in range(table.columnCount())]
        for i in range(table.rowCount())
    ]
    power = next(i for i, row in enumerate(rows) if "POWER.kl30" in row)
    assert "BOARD001" in rows[power] and "KL30" in rows[power] and "吸合" in rows[power]
    assert any("FAKE_RELAY" in c and "CH2" in c for c in rows[power])
    window._open_resource(power, 0)
    assert workspaces["gear.relay"].selected == "POWER.kl30"
    assert window.tabs.currentWidget() is workspaces["gear.relay"].widget

    window.board_selector.setCurrentIndex(1)
    rows = [
        [table.item(i, j).text() for j in range(table.columnCount())]
        for i in range(table.rowCount())
    ]
    assert rows == [["BOARD002", "未配置资源", "—", "—", "—", "—"]]


def test_case_preview_is_read_only_and_read_failure_visible(desktop, tmp_path):
    window, _, _ = desktop
    case = tmp_path / "sample.yaml"
    case.write_text("name: sample\nbody: []\n", encoding="utf-8")
    window.case_path.setText(str(case))
    assert window.case_preview.toPlainText() == case.read_text(encoding="utf-8")
    assert window.case_preview.isReadOnly()
    window.case_path.setText(str(tmp_path / "missing.yaml"))
    assert "无法读取" in window.case_preview.toPlainText()


def test_event_callback_dispatches_from_worker_with_time_step_and_resource(
    desktop, qapp
):
    window, host, _ = desktop
    window.run_id = "run-one"
    event = {
        "run_id": "run-one",
        "sequence": 1,
        "timestamp": "2026-09-19T10:20:30Z",
        "phase": "RUNNING",
        "event": "step.started",
        "step_path": "body[0]",
        "resource_id": "POWER.kl30",
        "details": {"operation": "ON"},
    }
    worker = threading.Thread(target=lambda: host.callbacks[0](event))
    worker.start()
    worker.join()
    assert not window.run_events.toPlainText()
    qapp.processEvents()
    text = window.run_events.toPlainText()
    assert all(s in text for s in ("10:20:30", "body[0]", "POWER.kl30", "ON"))
    assert "body[0]" in window.step_label.text()
    window.run_id = None  # fake host has no get_status


def test_invalid_saved_controller_remains_visible_and_editable(desktop):
    window, host, _ = desktop
    host._environment["plugins"]["gear.relay"]["config"]["controllers"]["bench"] = None
    window._refresh_resources()
    assert any(
        "配置无效" in window.resources_table.item(i, 4).text()
        for i in range(window.resources_table.rowCount())
    )
