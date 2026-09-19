"""Qt-only acquisition process; stdin/stdout JSON, no windows or GUI controls."""

import base64
import json
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal, Slot, QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import (
    QCamera,
    QMediaCaptureSession,
    QMediaDevices,
    QVideoSink,
)


def output(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=True) + "\n")
    sys.stdout.flush()


class CaptureWorker(QObject):
    received = Signal(object)

    def __init__(self, app, emit=output):
        super().__init__()
        self.app, self.emit = app, emit
        self.cameras = {}
        self.received.connect(self.command)

    def devices(self):
        return QMediaDevices.videoInputs()

    def stop(self, camera_id):
        stream = self.cameras.pop(camera_id, None)
        if stream:
            stream["camera"].stop()
            stream["session"].setCamera(None)
            stream["session"].setVideoSink(None)
            for key in ("camera", "sink", "session"):
                stream[key].deleteLater()
        self.emit({"event": "active", "camera_id": camera_id, "active": False})

    @Slot(object)
    def command(self, value):
        key = value.get("request")
        try:
            command = value["command"]
            result = None
            if command == "list":
                result = [
                    {"id": bytes(d.id()).hex(), "description": d.description()}
                    for d in self.devices()
                ]
            elif command == "open":
                camera_id = value["camera_id"]
                if camera_id not in self.cameras:
                    device = next(
                        (d for d in self.devices() if bytes(d.id()).hex() == camera_id),
                        None,
                    )
                    if device is None:
                        raise ValueError("所选摄像头当前不可用，请刷新输入列表。")
                    camera, session, sink = (
                        QCamera(device),
                        QMediaCaptureSession(),
                        QVideoSink(),
                    )
                    stream = {
                        "camera": camera,
                        "session": session,
                        "sink": sink,
                        "last": 0.0,
                    }
                    self.cameras[camera_id] = stream
                    session.setCamera(camera)
                    session.setVideoSink(sink)
                    sink.videoFrameChanged.connect(
                        lambda frame, cid=camera_id, s=stream: self.frame(cid, s, frame)
                    )
                    camera.activeChanged.connect(
                        lambda active, cid=camera_id, s=stream: self.active(
                            cid, s, active
                        )
                    )
                    camera.errorOccurred.connect(
                        lambda error, text, cid=camera_id, s=stream: self.error(
                            cid, s, text
                        )
                    )
                    camera.start()
            elif command == "stop":
                self.stop(value["camera_id"])
            elif command == "close":
                errors = []
                try:
                    for camera_id in list(self.cameras):
                        try:
                            self.stop(camera_id)
                        except Exception as exc:
                            errors.append(str(exc))
                    try:
                        self.emit(
                            {
                                "reply": key,
                                "ok": not errors,
                                "error": "; ".join(errors),
                                "value": None,
                            }
                        )
                    except (OSError, ValueError):
                        pass  # Parent may already be gone when stdin reaches EOF.
                finally:
                    self.app.quit()
                return
            else:
                raise ValueError("未知采集命令。")
            self.emit({"reply": key, "ok": True, "value": result})
        except Exception as exc:
            self.emit({"reply": key, "ok": False, "error": str(exc)})

    def active(self, camera_id, stream, active):
        if self.cameras.get(camera_id) is stream:
            self.emit({"event": "active", "camera_id": camera_id, "active": active})

    def error(self, camera_id, stream, message):
        if self.cameras.get(camera_id) is stream:
            self.emit({"event": "error", "camera_id": camera_id, "message": message})

    def frame(self, camera_id, stream, frame):
        if (
            self.cameras.get(camera_id) is not stream
            or time.monotonic() - stream["last"] < 0.2
        ):
            return
        image = frame.toImage()
        if image.isNull():
            return
        stream["last"] = time.monotonic()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "JPEG", 85):
            self.error(camera_id, stream, "图像编码失败。")
            return
        self.emit(
            {
                "event": "frame",
                "camera_id": camera_id,
                "width": image.width(),
                "height": image.height(),
                "jpeg": base64.b64encode(bytes(buffer.data())).decode("ascii"),
            }
        )


def main():
    app = QGuiApplication([])
    worker = CaptureWorker(app)

    def read():
        for line in sys.stdin:
            try:
                worker.received.emit(json.loads(line))
            except ValueError:
                continue
        worker.received.emit({"command": "close", "request": "eof"})

    reader = threading.Thread(target=read, name="camera-control-input", daemon=True)
    reader.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
