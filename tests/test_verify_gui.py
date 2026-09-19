"""Desktop verification mode: the same windows, driven by the simulated bench."""

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from gear_framework.desktop import DesktopWindow
from gear_framework.host import Framework

from gear_verify.inject import install, prepare
from gear_verify.runner import build_bench
from gear_verify.seal import sealed

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "examples" / "verify"
ENVIRONMENT = VERIFY / "environment.yaml"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_for(app, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_desktop_drives_the_simulated_bench(qapp):
    data, bench = build_bench(ENVIRONMENT, boot_delay=0.5)
    with sealed() as seal:
        host = Framework(ROOT, ENVIRONMENT)
        try:
            seal.ports()
            install(host, data, bench)
            bench.start()
            prepare(host, data)
            window = DesktopWindow(
                host,
                case=VERIFY / "cases" / "power-cycle.case.yaml",
                project=VERIFY / "project.yaml",
            )
            window.resize(1000, 700)
            window.show()
            window._refresh_resources()
            assert window.board_title.text() == "BOARD001"
            assert window.resources_table.rowCount() == 6

            window._submit()
            assert wait_for(qapp, lambda: window.confirm_button.isEnabled())
            window._confirm()
            assert wait_for(qapp, lambda: window.submit_button.isEnabled())
            status = host.get_status(window.run_id)
            assert status["outcome"] == "PASS", status["primary_failure"]
            # The case went through the GUI, not the CLI, and still drove the bench.
            assert bench.devices["BOARD001"].boots == 1
            assert bench.screens["sim-center"].frames > 0
            window.close()
        finally:
            bench.stop()
            host.close()
