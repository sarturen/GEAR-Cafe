"""Session-owned capture cache. The backend alone owns the actual cameras."""

import base64
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from gear_contracts.api import GearError


class CameraService:
    def __init__(self, backend_factory=None):
        if backend_factory is None:
            from .backend import CaptureBackend

            backend_factory = CaptureBackend
        self._factory = backend_factory
        self._backend = None
        self._backend_failed = False
        self._condition = threading.Condition()
        self._streams, self._frames = {}, {}
        self._events = deque(maxlen=200)
        self._sequence = 0
        self._closed = False

    def _engine(self, retry=False):
        if self._closed:
            raise GearError("CAMERA_CLOSED", "摄像头会话已关闭。")
        if retry and self._backend_failed and self._backend is not None:
            self._backend.close()
            self._backend = None
            self._backend_failed = False
        if self._backend is None:
            self._backend = self._factory(self._receive)
        return self._backend

    def _state(self, camera_id):
        return self._streams.setdefault(
            camera_id,
            {
                "active": False,
                "requested": False,
                "fault": None,
                "frame_sequence": 0,
                "width": 0,
                "height": 0,
                "last_frame": None,
            },
        )

    def _event(self, camera_id, message):
        self._sequence += 1
        self._events.append(
            {
                "sequence": self._sequence,
                "camera_id": camera_id,
                "timestamp": datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds"
                ),
                "message": message,
            }
        )

    def _receive(self, message):
        with self._condition:
            kind, camera_id = message.get("event"), message.get("camera_id")
            if kind == "backend_error":
                self._backend_failed = True
                for key, state in self._streams.items():
                    state.update(
                        active=False, requested=False, fault=message["message"]
                    )
                    self._frames.pop(key, None)
                    self._event(key, message["message"])
            elif camera_id:
                state = self._state(camera_id)
                if kind == "frame":
                    if not state["requested"] or state["fault"]:
                        return
                    first = state["frame_sequence"] == 0
                    state.update(
                        active=True,
                        width=message["width"],
                        height=message["height"],
                        last_frame=time.monotonic(),
                        frame_sequence=state["frame_sequence"] + 1,
                    )
                    self._frames[camera_id] = {
                        "jpeg": base64.b64decode(message["jpeg"], validate=True),
                        "width": message["width"],
                        "height": message["height"],
                        "sequence": state["frame_sequence"],
                    }
                    if first:
                        self._event(camera_id, "已收到画面")
                elif kind == "active":
                    state["active"] = bool(message["active"])
                    if not state["active"]:
                        self._frames.pop(camera_id, None)
                        state["last_frame"] = None
                    self._event(
                        camera_id, "采集已启动" if state["active"] else "采集已停止"
                    )
                elif kind == "error":
                    state.update(
                        active=False,
                        requested=False,
                        fault=message["message"],
                        last_frame=None,
                    )
                    self._frames.pop(camera_id, None)
                    self._event(camera_id, message["message"])
            self._condition.notify_all()

    def discover(self):
        return self._engine(retry=True).request("list")

    def open(self, camera_id, retry=False):
        if type(camera_id) is not str or not camera_id:
            raise GearError("CAMERA_NOT_CONFIGURED", "请先选择摄像头。")
        with self._condition:
            state = self._state(camera_id)
            if state["fault"] and not retry:
                raise GearError("CAMERA_FAULT", state["fault"])
            restart = retry and state["requested"] and not state["active"]
            if state["requested"] and not state["fault"] and not restart:
                return self.snapshot()
            fault = state["fault"]
        engine = self._engine(retry=retry)
        if fault or restart:
            engine.request("stop", camera_id=camera_id)
        with self._condition:
            self._frames.pop(camera_id, None)
            state.update(
                requested=True,
                active=False,
                fault=None,
                frame_sequence=0,
                last_frame=None,
            )
        try:
            engine.request("open", camera_id=camera_id)
        except GearError as exc:
            self._receive(
                {"event": "error", "camera_id": camera_id, "message": str(exc)}
            )
            raise
        return self.snapshot()

    def stop(self, camera_id):
        if self._backend is not None:
            if self._backend_failed:
                self._backend.close()
                self._backend = None
                self._backend_failed = False
            else:
                self._backend.request("stop", camera_id=camera_id)
        with self._condition:
            state = self._state(camera_id)
            state.update(active=False, requested=False, last_frame=None)
            self._frames.pop(camera_id, None)
        return self.snapshot()

    def snapshot(self):
        with self._condition:
            streams = deepcopy(self._streams)
            now = time.monotonic()
            for state in streams.values():
                last = state.pop("last_frame")
                age = now - last if last is not None else None
                state["frame_age_s"] = age
                state["streaming"] = bool(
                    state["active"]
                    and not state["fault"]
                    and age is not None
                    and age < 3.0
                )
            return {"streams": streams, "events": list(self._events)}

    def frame(self, camera_id):
        with self._condition:
            state = self._streams.get(camera_id, {})
            last = state.get("last_frame")
            if state.get("fault") or last is None or time.monotonic() - last >= 3:
                return None
            return dict(self._frames[camera_id]) if camera_id in self._frames else None

    def wait_frame(self, camera_id, timeout=3.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                value = self.frame(camera_id)
                if value is not None:
                    return value
                fault = self._state(camera_id)["fault"]
                if fault:
                    raise GearError("CAMERA_FAULT", fault)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GearError("CAMERA_NO_FRAME", "摄像头没有返回有效画面。")
                self._condition.wait(remaining)

    def close(self):
        if self._closed:
            return
        if self._backend is not None:
            self._backend.close()
        with self._condition:
            self._frames.clear()
            for state in self._streams.values():
                state.update(active=False, requested=False, last_frame=None)
        self._closed = True
