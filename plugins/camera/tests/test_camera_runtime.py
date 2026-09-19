import copy
import importlib
import sys
import time
from types import SimpleNamespace

import pytest
from gear_contracts.api import GearError
from gear_camera.config import validate_slice
from gear_camera.runtime import CameraRuntime
from gear_camera.service import CameraService


def data():
    return {
        "plugin": {"id": "gear.camera", "config": {}},
        "resources": {
            "SCREEN.center": {
                "type": "SCREEN",
                "device": "BOARD001",
                "config": {
                    "camera_id": "camera-a",
                    "role": "中控屏",
                    "roi": {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.6},
                },
            }
        },
    }


class Backend:
    def __init__(self, emit):
        self.emit = emit
        self.calls = []
        self.closed = False

    def request(self, command, **values):
        self.calls.append((command, values))
        if command == "list":
            return [{"id": "camera-a", "description": "USB input"}]
        if command == "open":
            self.emit(
                {"event": "active", "camera_id": values["camera_id"], "active": True}
            )
        if command == "stop":
            self.emit(
                {"event": "active", "camera_id": values["camera_id"], "active": False}
            )
        return None

    def close(self):
        self.closed = True


def service():
    instances = []

    def factory(emit):
        backend = Backend(emit)
        instances.append(backend)
        return backend

    return CameraService(factory), instances


def frame(svc, camera_id="camera-a"):
    import base64

    svc._receive(
        {
            "event": "frame",
            "camera_id": camera_id,
            "width": 100,
            "height": 80,
            "jpeg": base64.b64encode(b"test-jpeg").decode(),
        }
    )


def context():
    return SimpleNamespace(
        run_id="r1",
        stop_token=SimpleNamespace(is_requested=lambda: False),
        artifact_dir=None,
        phase="body",
    )


def test_configuration_and_factory_never_start_capture():
    svc, backends = service()
    runtime = CameraRuntime(svc)
    runtime.configure(data())
    assert runtime.validate_config(data())["status"] == "VALID"
    assert backends == []
    runtime.close()
    assert backends == []


@pytest.mark.parametrize(
    "roi",
    [
        {"x": -0.1, "y": 0, "width": 1, "height": 1},
        {"x": 0.8, "y": 0, "width": 0.3, "height": 1},
        {"x": 0, "y": 0, "width": 0, "height": 1},
        {"x": 0, "y": 0, "width": float("nan"), "height": 1},
    ],
)
def test_invalid_roi_is_rejected_without_hardware(roi):
    saved = data()
    saved["resources"]["SCREEN.center"]["config"]["roi"] = roi
    assert validate_slice(saved)["status"] == "INVALID"


def test_camera_is_exclusive_and_per_board_limit_is_six():
    saved = data()
    saved["resources"]["SCREEN.copy"] = copy.deepcopy(
        saved["resources"]["SCREEN.center"]
    )
    assert validate_slice(saved)["status"] == "INVALID"
    saved = data()
    for index in range(1, 7):
        saved["resources"][f"SCREEN.extra{index}"] = {
            "type": "SCREEN",
            "device": "BOARD001",
            "config": {"camera_id": f"camera-{index}", "role": f"screen-{index}"},
        }
    assert validate_slice(saved)["status"] == "INVALID"
    del saved["resources"]["SCREEN.extra6"]
    assert validate_slice(saved)["status"] == "VALID"


def test_resources_may_be_absent_but_recorded_binding_must_be_complete():
    saved = data()
    saved["resources"] = {}
    assert validate_slice(saved)["status"] == "VALID"
    saved["resources"]["SCREEN.empty"] = {"type": "SCREEN", "config": {}}
    assert validate_slice(saved)["status"] == "INCOMPLETE"


def test_discovery_is_explicit_and_returns_device_ids():
    svc, backends = service()
    assert backends == []
    assert svc.discover() == [{"id": "camera-a", "description": "USB input"}]
    assert backends[0].calls == [("list", {})]
    svc.close()
    assert backends[0].closed


def test_open_is_reused_and_frames_are_shared_cache_not_hardware_reads():
    svc, backends = service()
    svc.open("camera-a")
    svc.open("camera-a")
    frame(svc)
    assert len(backends) == 1
    assert [x[0] for x in backends[0].calls] == ["open"]
    assert svc.frame("camera-a")["jpeg"] == b"test-jpeg"
    assert svc.snapshot()["streams"]["camera-a"]["streaming"]
    svc.close()


def test_disconnection_or_failure_clears_frame_and_does_not_auto_retry():
    svc, backends = service()
    svc.open("camera-a")
    frame(svc)
    svc._receive({"event": "error", "camera_id": "camera-a", "message": "unplugged"})
    assert not svc.snapshot()["streams"]["camera-a"]["streaming"]
    assert svc.frame("camera-a") is None
    with pytest.raises(GearError):
        svc.open("camera-a")
    assert len(backends[0].calls) == 1
    svc.open("camera-a", retry=True)
    assert len(backends[0].calls) == 3  # stop, then explicit open
    svc.close()


def test_configure_roi_preserves_connection_and_camera_rebinding_stops_old():
    svc, backends = service()
    runtime = CameraRuntime(svc)
    saved = data()
    runtime.configure(saved)
    runtime.open("camera-a")
    saved["resources"]["SCREEN.center"]["config"]["roi"]["x"] = 0.2
    runtime.configure(saved)
    assert [x[0] for x in backends[0].calls] == ["open"]
    saved["resources"]["SCREEN.center"]["config"]["camera_id"] = "camera-b"
    runtime.configure(saved)
    assert [x[0] for x in backends[0].calls] == ["open", "stop"]
    runtime.close()


def test_run_snapshot_conditions_and_end_run_preserve_observer():
    svc, backends = service()
    runtime = CameraRuntime(svc)
    saved, ctx = data(), context()
    runtime.configure(saved)
    runtime.open("camera-a")
    frame(svc)
    runtime.begin_run({"plugin_slice": saved, "resource_ids": ["SCREEN.center"]}, ctx)
    saved["resources"]["SCREEN.center"]["config"]["camera_id"] = "camera-other"
    result = runtime.evaluate("SCREEN.center", "STREAMING", {}, ctx)
    assert result["ok"] and result["satisfied"]
    runtime.end_run()
    assert svc.snapshot()["streams"]["camera-a"]["streaming"]
    with pytest.raises(GearError):
        runtime.evaluate("SCREEN.center", "STREAMING", {}, ctx)
    runtime.close()


def test_snapshot_evidence_is_archived_with_roi(tmp_path):
    import json

    svc, _ = service()
    runtime = CameraRuntime(svc)
    saved, ctx = data(), context()
    ctx.artifact_dir = str(tmp_path)
    runtime.configure(saved)
    runtime.open("camera-a")
    frame(svc)
    runtime.begin_run({"plugin_slice": saved, "resource_ids": ["SCREEN.center"]}, ctx)
    result = runtime.collect("SCREEN.center", "SNAPSHOT", {}, ctx)
    assert result["ok"]
    assert len(result["artifacts"]) == 2
    files = [tmp_path / a["path"] for a in result["artifacts"]]
    assert any(p.read_bytes() == b"test-jpeg" for p in files)
    metadata = json.loads(
        next(p for p in files if p.suffix == ".json").read_text(encoding="utf-8")
    )
    assert metadata["roi"] == saved["resources"]["SCREEN.center"]["config"]["roi"]
    runtime.close()


def test_invalid_camera_id_remains_editable_without_capture():
    svc, backends = service()
    runtime = CameraRuntime(svc)
    saved = data()
    saved["resources"]["SCREEN.center"]["config"]["camera_id"] = []
    runtime.configure(saved)
    assert runtime.validate_config(saved)["status"] == "INVALID"
    assert backends == []


def test_manual_retry_restarts_inactive_and_failed_backend():
    svc, backends = service()
    svc.open("camera-a")
    svc._receive({"event": "active", "camera_id": "camera-a", "active": False})
    svc.open("camera-a", retry=True)
    assert [x[0] for x in backends[0].calls] == ["open", "stop", "open"]
    svc._receive({"event": "backend_error", "message": "worker exited"})
    with pytest.raises(GearError):
        svc.open("camera-a")
    svc.open("camera-a", retry=True)
    assert backends[0].closed and len(backends) == 2
    assert backends[1].calls[-1] == ("open", {"camera_id": "camera-a"})
    svc.close()
