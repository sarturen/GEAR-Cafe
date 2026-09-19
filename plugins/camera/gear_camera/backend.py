"""Private, windowless Qt capture worker. No process is started at import."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from gear_contracts.api import GearError


class CaptureBackend:
    def __init__(self, emit):
        self.emit = emit
        self._lock = threading.Lock()
        self._pending = {}
        self._closing = False
        self._failure = None
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).with_name("qt_capture.py"))],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise GearError("CAMERA_BACKEND_START", str(exc)) from exc
        self.reader = threading.Thread(
            target=self._read, name="gear-camera-frames", daemon=True
        )
        self.reader.start()

    def _read(self):
        error = "摄像头采集进程已退出。"
        try:
            for line in self.process.stdout:
                value = json.loads(line)
                if "reply" in value:
                    with self._lock:
                        pending = self._pending.get(value["reply"])
                        if pending:
                            pending["value"] = value
                            pending["event"].set()
                else:
                    self.emit(value)
        except Exception as exc:
            error = "摄像头采集通信失败：" + str(exc)
        finally:
            with self._lock:
                self._failure = error
                for pending in self._pending.values():
                    pending["event"].set()
            if not self._closing:
                self.emit({"event": "backend_error", "message": error})

    def request(self, command, **values):
        key = uuid.uuid4().hex
        pending = {"event": threading.Event()}
        with self._lock:
            if self._failure or self.process.poll() is not None:
                raise GearError(
                    "CAMERA_BACKEND_FAILED", self._failure or "采集进程不可用。"
                )
            self._pending[key] = pending
            try:
                self.process.stdin.write(
                    json.dumps({"request": key, "command": command, **values}) + "\n"
                )
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                self._pending.pop(key, None)
                raise GearError("CAMERA_BACKEND_FAILED", str(exc)) from exc
        ready = pending["event"].wait(5)
        with self._lock:
            self._pending.pop(key, None)
            response = pending.get("value")
        if not ready:
            raise GearError("CAMERA_BACKEND_TIMEOUT", "摄像头采集控制响应超时。")
        if response is None:
            raise GearError(
                "CAMERA_BACKEND_FAILED", self._failure or "采集进程未返回响应。"
            )
        if not response["ok"]:
            raise GearError("CAMERA_BACKEND_ERROR", response["error"])
        return response.get("value")

    def close(self):
        if (
            self._closing
            and self.process.poll() is not None
            and not self.reader.is_alive()
        ):
            return
        self._closing = True
        try:
            if self.process.poll() is None:
                try:
                    self.request("close")
                except GearError:
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
            self.reader.join(3)
            if self.reader.is_alive():
                raise GearError("CAMERA_CLOSE_FAILED", "采集读取线程没有退出。")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GearError("CAMERA_CLOSE_FAILED", str(exc)) from exc
        finally:
            if self.process.poll() is not None:
                self.process.stdin.close()
                self.process.stdout.close()
