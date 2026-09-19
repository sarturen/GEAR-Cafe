"""Four-plugin acceptance harness. All hardware boundaries are fake."""

import base64
import ctypes
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"


def forbidden(*args, **kwargs):
    raise AssertionError("Real devices / tools must never be accessed by this harness")


ctypes.WinDLL = forbidden
subprocess.Popen = forbidden

import gear_framework.host as host_module


class HostLock:
    def acquire(self):
        pass

    def close(self):
        pass


host_module.HostLock = HostLock
from gear_framework.host import Framework

root = Path(__file__).resolve().parents[3]
app_dir = Path(sys.argv[1]).resolve()
allowed_root = (root / "plugins" / "camera" / ".test-tmp").resolve()
if app_dir == allowed_root or not app_dir.is_relative_to(allowed_root):
    raise ValueError(
        "The simulation output must be a child of plugins/camera/.test-tmp"
    )
app_dir.mkdir(parents=True, exist_ok=True)
for name in ("adb", "relay", "console", "camera"):
    source = root / "plugins" / name
    target = app_dir / "plugins" / name
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "gear-plugin.yaml", target / "gear-plugin.yaml")
    shutil.copytree(
        source / ("gear_" + name),
        target / ("gear_" + name),
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )

resources = {
    "ADB.main": {
        "type": "ADB",
        "plugin": "gear.adb",
        "device": "BOARD001",
        "config": {},
    },
    "POWER.kl30": {
        "type": "POWER",
        "plugin": "gear.relay",
        "device": "BOARD001",
        "config": {"controller": "bench", "channel": 1, "role": "KL30"},
    },
    "POWER.kl15": {
        "type": "POWER",
        "plugin": "gear.relay",
        "device": "BOARD001",
        "config": {"controller": "bench", "channel": 2, "role": "KL15"},
    },
    "CONSOLE.mcu": {
        "type": "CONSOLE",
        "plugin": "gear.console",
        "device": "BOARD001",
        "config": {"port": "COM77", "role": "MCU"},
    },
    "CONSOLE.soc": {
        "type": "CONSOLE",
        "plugin": "gear.console",
        "device": "BOARD001",
        "config": {"port": "COM78", "role": "SOC"},
    },
    "SCREEN.center": {
        "type": "SCREEN",
        "plugin": "gear.camera",
        "device": "BOARD001",
        "config": {
            "camera_id": "fake-camera",
            "role": "中控屏",
            "roi": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
        },
    },
}
environment = {
    "api": "gear.environment/v1",
    "name": "four-plugin simulation",
    "devices": {"BOARD001": {}, "BOARD002": {}},
    "resources": resources,
    "plugins": {
        "gear.adb": {
            "config": {"adb_path": "fake-adb", "fastboot_path": "fake-fastboot"}
        },
        "gear.relay": {
            "config": {
                "controllers": {"bench": {"port": "COM79", "poll_interval_ms": 0}}
            }
        },
        "gear.console": {"config": {}},
        "gear.camera": {"config": {}},
    },
}
project = {
    "api": "gear.project/v1",
    "name": "four-plugin",
    "resources": {rid: {"type": r["type"]} for rid, r in resources.items()},
}


def write(name, data):
    (app_dir / (name + ".yaml")).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


write("environment", environment)
write("project", project)

created = []


class Serial:
    def __init__(self, **config):
        self.config = config
        self.rx = queue.Queue()
        self.opens = self.closes = 0
        self.states = [False] * 8
        self.frames = []
        self.pending = b""
        created.append(self)

    def open(self):
        self.opens += 1

    def close(self):
        self.closes += 1

    def write(self, data, timeout_s=None):
        if self.config["port"] == "COM79":
            self.frames.append(data)
            if data[1] == 5:
                self.states[int.from_bytes(data[2:4], "big")] = data[4:6] == b"\xff\x00"
                self.rx.put(data)
            elif data[1] == 1:
                mask = sum(int(v) << i for i, v in enumerate(self.states))
                body = bytes([1, 1, 1, mask])
                self.rx.put(body + crc(body))
            else:
                raise AssertionError(data)
        else:
            self.rx.put(b"reply:" + data)
        return len(data)

    def read(self, size, timeout_s=None):
        if not self.pending:
            try:
                self.pending = self.rx.get(timeout=timeout_s or 0.02)
            except queue.Empty:
                return b""
        value, self.pending = self.pending[:size], self.pending[size:]
        return value


def crc(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value.to_bytes(2, "little")


capture = []


class Capture:
    def __init__(self, emit):
        self.emit = emit
        self.calls = []
        self.closed = False
        capture.append(self)

    def request(self, command, **args):
        self.calls.append((command, args))
        if command == "list":
            return [{"id": "fake-camera", "description": "模拟中控屏"}]
        if command == "open":
            self.emit(
                {"event": "active", "camera_id": args["camera_id"], "active": True}
            )
            self.emit(
                {
                    "event": "frame",
                    "camera_id": args["camera_id"],
                    "width": 640,
                    "height": 360,
                    "jpeg": jpeg,
                }
            )
        if command == "stop":
            self.emit(
                {"event": "active", "camera_id": args["camera_id"], "active": False}
            )

    def close(self):
        self.closed = True


queries = []


def case(index):
    return {
        "api": "gear.dsl/v1",
        "name": "single-board-smoke",
        "body": [
            {"do": {"resource": "POWER.kl30", "operation": "ON"}},
            {"assert": {"all": [{"resource": "POWER.kl30", "condition": "IS_ON"}]}},
            {
                "do": {
                    "resource": "CONSOLE.mcu",
                    "operation": "SEND",
                    "args": {"command": "hello-" + str(index)},
                }
            },
            {
                "do": {
                    "resource": "CONSOLE.soc",
                    "operation": "SEND",
                    "args": {"command": "version"},
                }
            },
            {
                "assert": {
                    "all": [
                        {
                            "resource": "CONSOLE.mcu",
                            "condition": "OUTPUT_CONTAINS",
                            "args": {"text": "hello-" + str(index)},
                        }
                    ],
                    "within": "200ms",
                    "every": "10ms",
                }
            },
            {
                "do": {
                    "resource": "ADB.main",
                    "operation": "SHELL",
                    "args": {"command": "echo ready"},
                }
            },
            {
                "assert": {
                    "all": [{"resource": "SCREEN.center", "condition": "STREAMING"}]
                }
            },
            {
                "assert": {
                    "all": [
                        {
                            "resource": "CONSOLE.mcu",
                            "condition": "OUTPUT_CONTAINS",
                            "args": {
                                "text": (
                                    "absent" if index == 2 else "hello-" + str(index)
                                )
                            },
                        }
                    ],
                    "within": "120ms",
                    "every": "10ms",
                }
            },
            {"do": {"resource": "POWER.kl15", "operation": "ON"}},
        ],
        "teardown": [{"do": {"resource": "POWER.kl30", "operation": "OFF"}}],
        "evidence_on_fail": [
            {"resource": "SCREEN.center", "evidence": "SNAPSHOT"},
            {"resource": "CONSOLE.mcu", "evidence": "TRANSCRIPT"},
        ],
    }


write("case", case(0))

with Framework(app_dir, app_dir / "environment.yaml") as host:
    assert not any(n.startswith("PySide6") for n in sys.modules)
    assert not created and not capture
    adb = host._registry.entries["gear.adb"].runtime
    relay = host._registry.entries["gear.relay"].runtime
    console = host._registry.entries["gear.console"].runtime
    camera = host._registry.entries["gear.camera"].runtime
    from gear_relay.transport import RelayService
    from gear_camera.service import CameraService
    from gear_framework.documents import plugin_slice

    def inject():
        for service in relay.services.values():
            service.close()
        relay.services.clear()
        relay._service_factory = lambda: RelayService(serial_factory=Serial)
        relay.configure(plugin_slice(environment, "gear.relay"))
        console._factory = Serial
        camera.service = CameraService(Capture)

        def query():
            queries.append("adb")
            return [
                {
                    "serial": "BOARD001",
                    "state": "device",
                    "transport_id": "1",
                    "usb": "fake-usb",
                    "attributes": {},
                    "selectable": True,
                }
            ]

        adb.service._discover_adb = query
        adb.service._discover_fastboot = lambda: []
        adb.service.shell = lambda serial, command: {
            "exit_code": 0,
            "stdout": "ready",
            "stderr": "",
        }

    host._worker.submit(inject).result()

    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter, QColor, QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from gear_framework.desktop import DesktopWindow

    qapp = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyhbd.ttc")
    qapp.setFont(QFont("Microsoft YaHei", 9))
    image = QImage(640, 360, QImage.Format.Format_RGB32)
    image.fill(QColor("#14324c"))
    painter = QPainter(image)
    painter.setPen(Qt.GlobalColor.white)
    font = QFont("Microsoft YaHei", 18)
    painter.setFont(font)
    painter.drawText(
        image.rect(),
        Qt.AlignmentFlag.AlignCenter,
        "SIMULATED CAMERA\nBOARD001 · CENTER SCREEN",
    )
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG")
    jpeg = base64.b64encode(bytes(buffer.data())).decode()

    window = DesktopWindow(
        host, case=app_dir / "case.yaml", project=app_dir / "project.yaml"
    )
    window.resize(1120, 820)
    window.show()

    def wait(predicate, timeout=5):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            qapp.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError(
            "Timed out: "
            + str(host.get_status(window.run_id) if window.run_id else "no run")
        )

    window._submit()
    wait(lambda: window.confirm_button.isEnabled())
    assert (
        not created and not capture and not queries
    ), "Construction/preflight must be configuration-only"
    window._stop()
    wait(lambda: window.submit_button.isEnabled())

    def prepare():
        relay.connect("bench")
        console.connect("CONSOLE.mcu")
        console.connect("CONSOLE.soc")
        camera.open("fake-camera")
        adb.refresh_devices()

    host._worker.submit(prepare).result()
    for index in range(3):
        write("case", case(index))
        window._submit()
        wait(lambda: window.confirm_button.isEnabled())
        assert len(created) == 3 and len(capture) == 1 and len(queries) == 1
        assert not window._plugin_workspaces["gear.camera"].open_button.isEnabled()
        window._confirm()
        wait(lambda: window.submit_button.isEnabled())
        status = host.get_status(window.run_id)
        assert status["outcome"] == ("FAIL" if index == 2 else "PASS"), status
        assert all(s.opens == 1 and s.closes == 0 for s in created)
        assert len(capture) == 1 and not capture[0].closed
        assert (
            window.run_events.toPlainText()
            and "POWER.kl30" in window.run_events.toPlainText()
        )
        assert window.case_preview.toPlainText() == (
            Path(status["report_path"]).parent / "input" / "test-case.yaml"
        ).read_text(encoding="utf-8")
        if index == 2:
            report = json.loads(Path(status["report_path"]).read_text(encoding="utf-8"))
            assert len(report["evidence"]) == 2, report["evidence"]
            for item in report["evidence"]:
                result = item["result"]
                assert result["ok"], result
                for artifact in result["artifacts"]:
                    assert (
                        Path(status["report_path"]).parent / artifact["path"]
                    ).is_file()
    window._refresh_resources()
    assert window.board_title.text() == "BOARD001"
    assert window.resources_table.rowCount() == 6
    window.board_selector.setCurrentIndex(1)
    qapp.processEvents()
    assert window.board_title.text() == "BOARD002"
    assert window.resources_table.rowCount() == 1
    window.board_selector.setCurrentIndex(0)
    qapp.processEvents()
    screenshots = app_dir / "screenshots"
    screenshots.mkdir(exist_ok=True)
    for tab in range(window.tabs.count()):
        window.tabs.setCurrentIndex(tab)
        qapp.processEvents()
        pid = next(
            (
                pid
                for pid, page in window._plugin_workspaces.items()
                if page.widget is window.tabs.widget(tab)
            ),
            None,
        )
        if pid:
            expected = {
                "gear.adb": "ADB",
                "gear.camera": "摄像头",
                "gear.console": "串口",
                "gear.relay": "继电器",
            }
            assert window.tabs.tabText(tab) == expected[pid]
        name = "overview" if pid is None else pid.replace(".", "-")
        if name == "gear-camera":
            page = window._plugin_workspaces["gear.camera"]
            page.select_resource("SCREEN.center")
            page._refresh()
            qapp.processEvents()
        assert window.grab().save(str(screenshots / (name + ".png")))
    window.close()
    wait(lambda: host._closed)
assert all(s.closes == 1 for s in created)
assert capture[0].closed
print(
    "FOUR_PLUGIN_OK: configuration-only preflight, 2 PASS + 1 FAIL, shared sessions, GUI, evidence, cleanup"
)
