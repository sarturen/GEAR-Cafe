import json
import os
import subprocess
import sys
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from gear_contracts.api import GearError
from gear_framework.host import Framework


WORKSPACE_CODE = '''
import threading
from PySide6.QtWidgets import QPushButton

class Workspace:
    def __init__(self, context, runtime):
        self.context = context
        self.runtime = runtime
        self.widget = QPushButton("Plugin action")
        self.callback_threads = []
        self.results = []
        self.disposed_thread = None
        self.subscription = context.subscribe_run_state(self.state)
        self.widget.clicked.connect(self.manual)

    def state(self, value):
        self.callback_threads.append(threading.get_ident())
        self.widget.setEnabled(value == "IDLE")

    def manual(self):
        self.context.submit_manual(lambda: threading.get_ident(), self.result)

    def result(self, value):
        self.callback_threads.append(threading.get_ident())
        self.results.append(value)

    def dispose(self):
        self.disposed_thread = threading.get_ident()
        self.subscription.unsubscribe()

def create_workspace(context, runtime):
    return Workspace(context, runtime)
'''


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_gui(qapp, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "GUI did not reach the expected state"


def add_workspace(bench):
    package = bench["manifest"]["entrypoints"]["runtime"].split(".")[0]
    bench["manifest"]["entrypoints"]["workspace"] = package + ".workspace:create_workspace"
    (bench["directory"] / "gear-plugin.yaml").write_text(
        json.dumps(bench["manifest"]), encoding="utf-8"
    )
    (bench["directory"] / package / "workspace.py").write_text(
        WORKSPACE_CODE, encoding="utf-8"
    )


@pytest.fixture
def desktop(bench, qapp):
    from gear_framework.desktop import DesktopWindow

    add_workspace(bench)
    host = Framework(bench["app"], bench["environment"])
    runtime = host._registry.entries["gear.demo"].runtime
    window = DesktopWindow(host, case=bench["case"], project=bench["project"])
    window.show()
    try:
        yield window, host, runtime, host._workspaces[0]
    finally:
        runtime.release.set()
        window.close()
        wait_gui(qapp, lambda: host._closed)
        window.deleteLater()
        qapp.processEvents()


def test_desktop_hosts_registered_workspace_and_dispatches_on_gui_thread(bench, qapp):
    from gear_framework.desktop import DesktopWindow

    add_workspace(bench)
    host = Framework(bench["app"], bench["environment"])
    window = DesktopWindow(host)
    workspace = host._workspaces[0]
    try:
        assert window.tabs.count() == 2
        assert window.tabs.tabText(0) == "首页"
        assert window.tabs.tabText(1) == "gear.demo"
        workspace.widget.click()
        wait_gui(qapp, lambda: bool(workspace.results))
        assert workspace.results[0]["value"] == host._worker.thread.ident
        assert workspace.callback_threads == [threading.get_ident()]
        assert not window.submit_button.isEnabled()
    finally:
        window.close()
        wait_gui(qapp, lambda: host._closed)
        assert workspace.disposed_thread == threading.get_ident()


def test_run_requires_confirmation_and_displays_report(desktop, qapp):
    window, host, runtime, workspace = desktop
    window.submit_button.click()
    wait_gui(qapp, lambda: window.confirm_button.isEnabled())
    assert host.get_status(window.run_id)["phase"] == "WAITING_CONFIRMATION"
    assert "begin" not in runtime.calls
    assert not window.submit_button.isEnabled()
    assert not window.case_path.isEnabled()
    wait_gui(qapp, lambda: not workspace.widget.isEnabled())
    assert '"ok": true' in window.diagnostics.toPlainText()

    window.confirm_button.click()
    wait_gui(qapp, lambda: window.submit_button.isEnabled())
    status = host.get_status(window.run_id)
    assert status["outcome"] == "PASS"
    assert status["report_path"] in window.report_path.text()
    assert window.open_report_button.isEnabled()
    assert not window.confirm_button.isEnabled()
    assert not window.stop_button.isEnabled()


def test_stop_keeps_shell_busy_through_finalization(desktop, qapp):
    window, host, runtime, workspace = desktop
    runtime.block = True
    finalized = threading.Event()
    release_finalization = threading.Event()
    original_end = runtime.end_run

    def end_run():
        original_end()
        finalized.set()
        release_finalization.wait(3)

    runtime.end_run = end_run
    try:
        window.submit_button.click()
        wait_gui(qapp, lambda: window.confirm_button.isEnabled())
        window.confirm_button.click()
        wait_gui(qapp, runtime.entered.is_set)
        window.stop_button.click()
        assert not window.submit_button.isEnabled()
        runtime.release.set()
        wait_gui(qapp, finalized.is_set)
        assert host.get_status(window.run_id)["active"]
        assert not window.submit_button.isEnabled()
        assert not workspace.widget.isEnabled()
        with pytest.raises(GearError):
            workspace.context.submit_manual(lambda: None, lambda _: None)
    finally:
        release_finalization.set()
    wait_gui(qapp, lambda: window.submit_button.isEnabled())
    assert host.get_status(window.run_id)["outcome"] == "STOPPED"


def test_close_requests_stop_and_waits_without_blocking_event_loop(desktop, qapp):
    window, host, runtime, workspace = desktop
    runtime.block = True
    finalizing = threading.Event()
    release_finalization = threading.Event()
    original_end = runtime.end_run

    def end_run():
        original_end()
        finalizing.set()
        release_finalization.wait(3)

    runtime.end_run = end_run
    window.submit_button.click()
    wait_gui(qapp, lambda: window.confirm_button.isEnabled())
    window.confirm_button.click()
    wait_gui(qapp, runtime.entered.is_set)
    tick = []
    QTimer.singleShot(0, lambda: tick.append(True))
    window.close()
    assert not host._closed
    assert window.isVisible()
    assert runtime.context.stop_token.is_requested()
    wait_gui(qapp, lambda: bool(tick))
    assert workspace.disposed_thread is None
    runtime.release.set()
    try:
        wait_gui(qapp, finalizing.is_set)
        assert not host._closed
        assert workspace.disposed_thread is None
        assert window.isVisible()
    finally:
        release_finalization.set()
    wait_gui(qapp, lambda: host._closed)
    assert not window.isVisible()
    assert workspace.disposed_thread == threading.get_ident()
    assert runtime.calls[-1] == "close"


def test_close_waits_for_pending_manual_command(desktop, qapp):
    window, host, runtime, workspace = desktop
    entered = threading.Event()

    def finite_command():
        entered.set()
        runtime.release.wait(3)
        return None

    workspace.context.submit_manual(finite_command, workspace.result)
    wait_gui(qapp, entered.is_set)
    window.close()
    assert not host._closed
    assert workspace.disposed_thread is None
    with pytest.raises(GearError):
        workspace.context.submit_manual(lambda: None, lambda _: None)
    runtime.release.set()
    wait_gui(qapp, lambda: host._closed)


def test_blocked_finalization_is_visible_and_can_close(desktop, qapp):
    window, host, runtime, workspace = desktop
    runtime.cleanup_fail = True
    window.submit_button.click()
    wait_gui(qapp, lambda: window.confirm_button.isEnabled())
    window.confirm_button.click()
    wait_gui(qapp, lambda: host.get_status(window.run_id)["phase"] == "BLOCKED")
    window.refresh_status()
    assert not window.submit_button.isEnabled()
    assert "PLUGIN_CLEANUP_FAILED" in window.diagnostics.toPlainText()
    assert "重启" in window.status_label.text()
    window.close()
    wait_gui(qapp, lambda: host._closed)


def test_gui_command_launches_without_required_case_or_project(
    bench, qapp, monkeypatch
):
    from gear_framework.cli import main
    from gear_framework.desktop import DesktopWindow, QFileDialog

    closed = []

    saved_environment = bench["environment"].read_bytes()

    def choose(*args, **kwargs):
        pytest.fail("GUI startup must load its fixed environment without a file picker")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)

    def close_window():
        for widget in qapp.topLevelWidgets():
            if isinstance(widget, DesktopWindow) and widget.isVisible():
                closed.append(widget.framework.environment_path)
                widget.close()

    args = ["gui", "--app-dir", str(bench["app"])]
    QTimer.singleShot(30, close_window)
    assert main(args) == 0
    assert closed == [bench["environment"].resolve()]
    assert bench["environment"].read_bytes() == saved_environment


@pytest.mark.parametrize("plugins_directory", [False, True])
def test_gui_starts_without_plugins_and_creates_one_environment(
    tmp_path, qapp, monkeypatch, plugins_directory
):
    from gear_framework.cli import main
    from gear_framework.desktop import DesktopWindow, QFileDialog
    from gear_framework.documents import load_yaml

    if plugins_directory:
        (tmp_path / "plugins").mkdir()
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        lambda *args: pytest.fail("No startup file picker is allowed"),
    )
    opened = []

    def close_window():
        for widget in qapp.topLevelWidgets():
            if isinstance(widget, DesktopWindow) and widget.isVisible():
                opened.append({
                    "environment": widget.framework.environment_path,
                    "tabs": [widget.tabs.tabText(i) for i in range(widget.tabs.count())],
                    "inputs": widget.case_path.isEnabled() and widget.project_path.isEnabled(),
                    "plugins": list(widget.framework._registry.entries),
                })
                widget.close()

    QTimer.singleShot(30, close_window)
    assert main(["gui", "--app-dir", str(tmp_path)]) == 0
    assert opened == [{
        "environment": tmp_path / "environment.yaml",
        "tabs": ["首页"], "inputs": True, "plugins": [],
    }]
    assert load_yaml(tmp_path / "environment.yaml") == {
        "api": "gear.environment/v1", "name": "GEAR",
        "plugins": {}, "resources": {}, "devices": {},
    }


def test_gui_rejects_invalid_saved_environment_without_overwriting(
    tmp_path, qapp, monkeypatch, capsys
):
    from gear_framework.cli import main
    from gear_framework.desktop import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        lambda *args: pytest.fail("Saved environment must be loaded directly"),
    )
    environment = tmp_path / "environment.yaml"
    saved = b"api: invalid\n"
    environment.write_bytes(saved)
    assert main(["gui", "--app-dir", str(tmp_path)]) == 3
    assert environment.read_bytes() == saved
    assert "GEAR:" in capsys.readouterr().err


def test_cli_run_remains_headless_and_gui_explains_optional_dependency(bench):
    code = '''
import importlib.abc
import sys
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PySide6" or fullname.startswith("PySide6."):
            raise ModuleNotFoundError("Qt is unavailable", name="PySide6")
sys.meta_path.insert(0, NoQt())
from gear_framework.cli import main
sys.exit(main(sys.argv[1:]))
'''
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(["src", "doc/contracts"]))
    paths = [
        "--app-dir", str(bench["app"]), "--environment", str(bench["environment"]),
        "--case", str(bench["case"]), "--project", str(bench["project"]),
    ]
    run = subprocess.run(
        [sys.executable, "-c", code, "run", *paths], input="yes\n",
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert run.returncode == 0, run.stderr
    assert '"outcome": "PASS"' in run.stdout
    gui = subprocess.run(
        [sys.executable, "-c", code, "gui", "--app-dir", str(bench["app"])],
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert gui.returncode == 3
    assert "gear-framework[gui]" in gui.stderr
