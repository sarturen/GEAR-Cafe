"""Camera transport and Qt worker tests, fake inputs/processes only."""

import base64
import json
import queue
import threading
from types import SimpleNamespace
import pytest
from gear_contracts.api import GearError
from gear_camera import backend


class Output:
    def __init__(self):
        self.lines = queue.Queue()
        self.closed = False

    def __iter__(self):
        while (value := self.lines.get()) is not None:
            yield value

    def close(self):
        self.closed = True


class Input:
    def __init__(self, process):
        self.process = process
        self.closed = False

    def write(self, line):
        value = json.loads(line)
        self.process.commands.append(value)
        response = {
            "reply": value["request"],
            "ok": value["command"] != "bad",
            "value": [{"id": "fake-camera"}],
        }
        if not response["ok"]:
            response["error"] = "fake error"
        self.process.stdout.lines.put(json.dumps(response) + "\n")
        if value["command"] == "close":
            self.process.exit()

    def flush(self):
        pass

    def close(self):
        self.closed = True


class Process:
    def __init__(self):
        self.stdout = Output()
        self.stdin = Input(self)
        self.code = None
        self.commands = []

    def poll(self):
        return self.code

    def exit(self):
        self.code = 0
        self.stdout.lines.put(None)

    def wait(self, timeout):
        return self.code

    def terminate(self):
        self.exit()


def test_transport_routes_replies_and_closes_reader_without_visible_process(
    monkeypatch,
):
    process = Process()
    launch = []

    def popen(command, **kwargs):
        launch.append((command, kwargs))
        return process

    monkeypatch.setattr(backend.subprocess, "Popen", popen)
    events = []
    transport = backend.CaptureBackend(events.append)
    assert transport.request("list") == [{"id": "fake-camera"}]
    with pytest.raises(GearError, match="CAMERA_BACKEND_ERROR"):
        transport.request("bad")
    transport.request("open", camera_id="target")
    assert process.commands[-1]["camera_id"] == "target"
    transport.close()
    transport.close()
    assert (
        not transport.reader.is_alive()
        and process.stdin.closed
        and process.stdout.closed
    )
    assert launch[0][0][-1].endswith("qt_capture.py")
    assert launch[0][1]["creationflags"] == getattr(
        backend.subprocess, "CREATE_NO_WINDOW", 0
    )


def test_reader_failure_is_visible_and_cannot_be_reused(monkeypatch):
    process = Process()
    monkeypatch.setattr(backend.subprocess, "Popen", lambda *a, **k: process)
    failed = threading.Event()
    transport = backend.CaptureBackend(
        lambda value: failed.set() if value["event"] == "backend_error" else None
    )
    process.stdout.lines.put("invalid-json\n")
    assert failed.wait(1)
    with pytest.raises(GearError, match="CAMERA_BACKEND_FAILED"):
        transport.request("open", camera_id="target")
    process.exit()
    transport.close()


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class Camera:
    made = []

    def __init__(self, device):
        self.device = device
        self.started = self.stopped = self.deleted = False
        self.activeChanged, self.errorOccurred = Signal(), Signal()
        self.made.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        self.activeChanged.emit(False)

    def deleteLater(self):
        self.deleted = True


class Session:
    def setCamera(self, camera):
        self.camera = camera

    def setVideoSink(self, sink):
        self.sink = sink

    def deleteLater(self):
        pass


class Sink:
    def __init__(self):
        self.videoFrameChanged = Signal()

    def deleteLater(self):
        pass


@pytest.fixture
def worker(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gear_camera import qt_capture

    app = QApplication.instance() or QApplication([])
    device = SimpleNamespace(id=lambda: b"fake-usb-id", description=lambda: "Fake USB")
    Camera.made = []
    monkeypatch.setattr(qt_capture, "QCamera", Camera)
    monkeypatch.setattr(qt_capture, "QMediaCaptureSession", Session)
    monkeypatch.setattr(qt_capture, "QVideoSink", Sink)
    monkeypatch.setattr(
        qt_capture, "QMediaDevices", SimpleNamespace(videoInputs=lambda: [device])
    )
    events = []
    quits = []
    capture = qt_capture.CaptureWorker(
        SimpleNamespace(quit=lambda: quits.append(True)), events.append
    )
    yield capture, events, quits, app
    capture.command({"command": "close", "request": "cleanup"})


def test_qt_worker_uses_exact_id_reuses_input_and_releases_it(worker):
    capture, events, _, _ = worker
    capture.command({"command": "list", "request": "list"})
    camera_id = b"fake-usb-id".hex()
    assert events[-1]["value"] == [{"id": camera_id, "description": "Fake USB"}]
    capture.command({"command": "open", "request": "invalid", "camera_id": "other"})
    assert not events[-1]["ok"] and Camera.made == []
    for _ in range(2):
        capture.command({"command": "open", "request": "open", "camera_id": camera_id})
    assert len(Camera.made) == 1 and Camera.made[0].started
    capture.command({"command": "stop", "request": "stop", "camera_id": camera_id})
    assert Camera.made[0].stopped and Camera.made[0].deleted and not capture.cameras


def test_qt_worker_encodes_frames_and_ignores_stale_callbacks(worker):
    from PySide6.QtGui import QImage

    capture, events, _, _ = worker
    camera_id = b"fake-usb-id".hex()
    capture.command({"command": "open", "request": "open", "camera_id": camera_id})
    stream = capture.cameras[camera_id]
    image = QImage(64, 32, QImage.Format.Format_RGB32)
    image.fill(0xFF33AA55)
    capture.frame(camera_id, stream, SimpleNamespace(toImage=lambda: image))
    event = events[-1]
    assert event["event"] == "frame"
    assert not QImage.fromData(base64.b64decode(event["jpeg"])).isNull()
    capture.stop(camera_id)
    count = len(events)
    capture.frame(camera_id, stream, SimpleNamespace(toImage=lambda: image))
    capture.error(camera_id, stream, "old fault")
    assert len(events) == count


def test_parent_pipe_failure_does_not_prevent_all_inputs_closing_and_quit(worker):
    capture, events, quits, _ = worker
    camera_id = b"fake-usb-id".hex()
    capture.command({"command": "open", "request": "open", "camera_id": camera_id})
    # A second fake stream simulates multiple already-open inputs, no device query.
    capture.cameras["second"] = {
        "camera": Camera(None),
        "session": Session(),
        "sink": Sink(),
    }

    def broken(value):
        raise BrokenPipeError("parent exited")

    capture.emit = broken
    capture.command({"command": "close", "request": "eof"})
    assert quits and not capture.cameras
    assert all(camera.stopped and camera.deleted for camera in Camera.made)
