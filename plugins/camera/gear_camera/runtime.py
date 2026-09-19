"""GEAR SCREEN adapter; importing/factory/configuration never imports Qt."""

from copy import deepcopy
import json
from pathlib import Path
import uuid
from gear_contracts.api import GearError, StopRequested
from .config import PLUGIN_ID, FULL_ROI, validate_slice
from .service import CameraService


class CameraRuntime:
    def __init__(self, service=None):
        self.service = service if service is not None else CameraService()
        self._slice = {"plugin": {"id": PLUGIN_ID, "config": {}}, "resources": {}}
        self._binding = None
        self._run_id = None
        self._closed = False

    def _alive(self):
        if self._closed:
            raise GearError("CAMERA_CLOSED", "摄像头会话已关闭。")

    def configure(self, data):
        self._alive()
        old = {
            r["config"]["camera_id"]
            for r in self._slice["resources"].values()
            if type(r["config"].get("camera_id")) is str
        }
        new = {
            r["config"]["camera_id"]
            for r in data["resources"].values()
            if type(r["config"].get("camera_id")) is str
        }
        for camera_id in old - new - {None, ""}:
            self.service.stop(camera_id)
        self._slice = deepcopy(data)

    def validate_config(self, data):
        return validate_slice(data)

    def begin_run(self, binding, context):
        self._alive()
        if self._binding is not None:
            raise GearError("CAMERA_BUSY", "已有用例在执行。")
        self._binding, self._run_id = deepcopy(binding), context.run_id

    def _resource(self, resource_id, context):
        self._alive()
        if (
            self._binding is None
            or context.run_id != self._run_id
            or resource_id not in self._binding["resource_ids"]
        ):
            raise GearError("CAMERA_NOT_BOUND", "资源未绑定到当前用例。")
        record = self._binding["plugin_slice"]["resources"][resource_id]
        camera_id = record["config"].get("camera_id")
        if record["type"] != "SCREEN" or type(camera_id) is not str or not camera_id:
            raise GearError("CAMERA_NOT_CONFIGURED", "资源未绑定摄像头。")
        return record

    @staticmethod
    def _args(name, expected, args):
        if name != expected or type(args) is not dict or args:
            raise GearError("CAMERA_UNKNOWN_CAPABILITY", "不支持的能力或参数。")

    def invoke(self, resource_id, operation, args, context):
        self._resource(resource_id, context)
        raise GearError("CAMERA_UNKNOWN_CAPABILITY", "摄像头本版不提供控制操作。")

    def evaluate(self, resource_id, condition, args, context):
        self._args(condition, "STREAMING", args)
        record = self._resource(resource_id, context)
        state = self.service.snapshot()["streams"].get(
            record["config"]["camera_id"], {}
        )
        fault = state.get("fault")
        return {
            "ok": not bool(fault),
            "satisfied": bool(state.get("streaming")),
            "diagnostic": (
                {"code": "CAMERA_FAULT", "message": fault, "details": {}}
                if fault
                else None
            ),
            "details": state,
        }

    def collect(self, resource_id, evidence, args, context):
        self._args(evidence, "SNAPSHOT", args)
        record = self._resource(resource_id, context)
        if context.stop_token.is_requested():
            raise StopRequested()
        try:
            camera_id = record["config"]["camera_id"]
            self.service.open(camera_id)
            frame = self.service.wait_frame(camera_id)
            relative = Path("evidence") / PLUGIN_ID / uuid.uuid4().hex
            target = Path(context.artifact_dir) / relative
            target.mkdir(parents=True)
            (target / "frame.jpg").write_bytes(frame["jpeg"])
            metadata = {
                "device": record.get("device"),
                "camera_id": camera_id,
                "role": record["config"].get("role"),
                "width": frame["width"],
                "height": frame["height"],
                "roi": record["config"].get("roi", FULL_ROI),
            }
            (target / "frame.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {
                "ok": True,
                "diagnostic": None,
                "artifacts": [
                    {
                        "path": (relative / filename).as_posix(),
                        "media_type": media,
                        "description": label,
                    }
                    for filename, media, label in (
                        ("frame.jpg", "image/jpeg", "摄像头完整画面"),
                        ("frame.json", "application/json", "单板、屏幕与ROI配置"),
                    )
                ],
            }
        except (GearError, OSError) as exc:
            return {
                "ok": False,
                "artifacts": [],
                "diagnostic": {
                    "code": (
                        exc.args[0]
                        if isinstance(exc, GearError)
                        else "CAMERA_FILE_ERROR"
                    ),
                    "message": str(exc),
                    "details": {},
                },
            }

    def discover(self):
        self._alive()
        return self.service.discover()

    def open(self, camera_id):
        self._alive()
        return self.service.open(camera_id, retry=True)

    def stop(self, camera_id):
        self._alive()
        return self.service.stop(camera_id)

    def snapshot(self):
        return self.service.snapshot()

    def frame(self, camera_id):
        return self.service.frame(camera_id)

    def end_run(self):
        self._binding = None
        self._run_id = None

    def close(self):
        if self._closed:
            return
        self.service.close()
        self.end_run()
        self._closed = True


def create_plugin():
    return CameraRuntime()
