"""Session-owned USB ADB clients; mutations run on the host's single worker.

Only logcat_snapshot may be called from the GUI thread. It reads cached state
under a lock and never polls a process or touches the filesystem.
"""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import TextIO

from gear_contracts.api import GearError

_DISCOVERY_TIMEOUT = 10.0
_TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z_0-9]*|-?\d+|[{}:])')
_STR_FIELDS = {"serial", "bus_address", "product", "model", "device"}
_INT_FIELDS = {"transport_id", "negotiated_speed", "max_speed"}
_STATES = {
    "ANY",
    "CONNECTING",
    "AUTHORIZING",
    "UNAUTHORIZED",
    "NOPERMISSION",
    "DETACHED",
    "OFFLINE",
    "BOOTLOADER",
    "DEVICE",
    "HOST",
    "RECOVERY",
    "SIDELOAD",
    "RESCUE",
}


def _parse_devices(payload: bytes) -> list[dict[str, str]]:
    """Parse only ADB's flat Device text-protobuf schema, rejecting bad frames.

    ADB's Windows native USB backend omits bus_address. connection_type is the
    authoritative discriminator, unlike serial heuristics or devices -l usb:.
    Schema: https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/proto/adb_host.proto
    """
    source = payload.decode("utf-8")
    tokens = []
    pos = 0
    while source[pos:].strip():
        match = _TOKEN.match(source, pos)
        if match is None:
            raise ValueError("Malformed ADB device snapshot")
        tokens.append(match[1])
        pos = match.end()
    devices = []
    index = 0
    while index < len(tokens):
        if tokens[index : index + 2] != ["device", "{"]:
            raise ValueError("Expected an ADB device record")
        index += 2
        values = {}
        while index < len(tokens) and tokens[index] != "}":
            if index + 2 >= len(tokens) or tokens[index + 1] != ":":
                raise ValueError("Malformed ADB device field")
            name, value = tokens[index], tokens[index + 2]
            if name in values:
                raise ValueError("Duplicate ADB device field")
            if name in _STR_FIELDS:
                if not value.startswith('"'):
                    raise ValueError("Expected an ADB string field")
                # TextFormat escapes UTF-8 bytes using C octal string escapes.
                value = ast.literal_eval("b" + value).decode("utf-8")
            elif name in _INT_FIELDS:
                if re.fullmatch(r"-?\d+", value) is None:
                    raise ValueError("Expected an ADB integer field")
            elif name == "state":
                if value not in _STATES:
                    raise ValueError("Unknown ADB device state")
            elif name == "connection_type":
                if value not in {"USB", "SOCKET"}:
                    raise ValueError("Unknown ADB connection type")
            else:
                raise ValueError(f"Unknown ADB device field: {name}")
            values[name] = value
            index += 3
        if index == len(tokens):
            raise ValueError("Unterminated ADB device record")
        index += 1
        if not values.get("serial") or int(values.get("transport_id", "0")) <= 0:
            raise ValueError("ADB device lacks a serial or transport ID")
        if "connection_type" not in values:
            raise ValueError("ADB device lacks its connection type")
        if values["connection_type"] == "USB":
            devices.append(
                {
                    "serial": values["serial"],
                    "state": values.get("state", "ANY").lower(),
                    "usb": values.get("bus_address", ""),
                    "transport_id": values["transport_id"],
                    "model": values.get("model", ""),
                }
            )
    return devices


def _process_options() -> dict:
    return {
        "stdin": subprocess.DEVNULL,
        "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    }


def _stop_client(process: subprocess.Popen) -> None:
    """Stop only an owned continuous client, never the shared ADB server."""
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@dataclass
class _LogCapture:
    process: subprocess.Popen
    path: str
    output: TextIO
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=2000))
    stopping: threading.Event = field(default_factory=threading.Event)
    reader: threading.Thread | None = None
    running: bool = True
    error: str | None = None


class AdbService:
    def __init__(self):
        self._adb_path = "adb"
        self._closed = False
        self._logs: dict[str, _LogCapture] = {}
        self._lock = threading.Lock()

    def configure(self, adb_path: str) -> None:
        self._ensure_open()
        if adb_path != self._adb_path:
            for serial in list(self._logs):
                self.stop_logcat(serial)
            self._adb_path = adb_path

    def _ensure_open(self) -> None:
        if self._closed:
            raise GearError("ADB_CLOSED", "The ADB service is closed")

    def _start(self, arguments: list[str], **options) -> subprocess.Popen:
        self._ensure_open()
        try:
            return subprocess.Popen(
                [self._adb_path, *arguments], **_process_options(), **options
            )
        except OSError as exc:
            raise GearError("ADB_TOOL_ERROR", f"Could not start ADB: {exc}") from exc

    def discover(self) -> list[dict[str, str]]:
        # Take one 4-hex-length framed snapshot and close this listing client.
        # No subscription, device reconnection, or global server control remains.
        process = self._start(
            ["track-devices", "--proto-text"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        frames = []
        errors = []

        def read_frame():
            try:
                header = process.stdout.read(4)
                if re.fullmatch(rb"[0-9a-fA-F]{4}", header) is None:
                    raise ValueError("ADB did not return a framed device snapshot")
                length = int(header, 16)
                payload = process.stdout.read(length)
                if len(payload) != length:
                    raise ValueError("Incomplete ADB device snapshot")
                frames.append(payload)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))

        reader = threading.Thread(target=read_frame, name="gear-adb-discovery")
        reader.start()
        reader.join(_DISCOVERY_TIMEOUT)
        timed_out = reader.is_alive()
        try:
            _stop_client(process)
        finally:
            reader.join()
            stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
            process.stdout.close()
            process.stderr.close()
        if timed_out or errors or not frames:
            reason = (
                "ADB discovery timed out" if timed_out else stderr or "; ".join(errors)
            )
            raise GearError("ADB_DISCOVERY_FAILED", reason)
        try:
            return _parse_devices(frames[0])
        except (ValueError, SyntaxError, UnicodeError) as exc:
            raise GearError("ADB_DISCOVERY_FAILED", str(exc)) from exc

    def _target(self, serial: str) -> list[str]:
        matches = [device for device in self.discover() if device["serial"] == serial]
        if len(matches) > 1:
            raise GearError(
                "ADB_AMBIGUOUS_DEVICE", f"Multiple USB devices have serial {serial}"
            )
        if not matches or matches[0]["state"] != "device":
            raise GearError(
                "ADB_DEVICE_UNAVAILABLE", f"USB device {serial} is not available"
            )
        # A serial can also match network transports. Pin the verified USB ID.
        return ["-t", matches[0]["transport_id"]]

    def _run(self, arguments: list[str]) -> dict:
        process = self._start(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        # Finite foreground commands follow the framework's cooperative Stop:
        # the worker waits for this call to return; no forced timeout is added.
        stdout, stderr = process.communicate()
        return {"exit_code": process.returncode, "stdout": stdout, "stderr": stderr}

    def shell(self, serial: str, command: str) -> dict:
        return self._run([*self._target(serial), "shell", "-n", "-T", command])

    def pull(self, serial: str, remote_path: str, destination: str) -> dict:
        return self._run([*self._target(serial), "pull", remote_path, destination])

    def start_logcat(self, serial: str, destination: str) -> dict:
        self._ensure_open()
        with self._lock:
            current = self._logs.get(serial)
            if current is not None and current.running:
                raise GearError(
                    "ADB_LOGCAT_RUNNING", f"Logcat is already running for {serial}"
                )
        # Reap a previous exited reader before replacing its capture object.
        self.stop_logcat(serial)
        target = self._target(serial)
        try:
            output = Path(destination).open("x", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise GearError(
                "ADB_LOGCAT_FILE_ERROR", f"Cannot create logcat file: {exc}"
            ) from exc
        try:
            process = self._start(
                [*target, "logcat", "-v", "threadtime"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except BaseException:
            output.close()
            raise
        capture = _LogCapture(process, str(destination), output)
        capture.reader = threading.Thread(
            target=self._read_logcat,
            args=(capture,),
            name=f"gear-adb-logcat-{serial}",
        )
        with self._lock:
            self._logs[serial] = capture
        capture.reader.start()
        return self.logcat_snapshot(serial)

    def _read_logcat(self, capture: _LogCapture) -> None:
        error = None
        try:
            for line in capture.process.stdout:
                capture.output.write(line)
                capture.output.flush()
                with self._lock:
                    capture.lines.append(line.rstrip("\r\n"))
            code = capture.process.wait()
            if not capture.stopping.is_set():
                error = f"ADB_LOGCAT_EXIT: logcat exited unexpectedly with code {code}"
        except OSError as exc:
            error = f"ADB_LOGCAT_IO_ERROR: {exc}"
            _stop_client(capture.process)
        finally:
            try:
                capture.process.stdout.close()
                capture.output.close()
            except OSError as exc:
                error = (error + "; " if error else "") + f"ADB_LOGCAT_IO_ERROR: {exc}"
            finally:
                with self._lock:
                    capture.error = error
                    capture.running = False

    def stop_logcat(self, serial: str) -> dict:
        with self._lock:
            capture = self._logs.get(serial)
        if capture is not None:
            capture.stopping.set()
            _stop_client(capture.process)
            capture.reader.join()
        return self.logcat_snapshot(serial)

    def logcat_snapshot(self, serial: str) -> dict:
        with self._lock:
            capture = self._logs.get(serial)
            if capture is None:
                return {"running": False, "lines": [], "path": None, "error": None}
            return {
                "running": capture.running,
                "lines": list(capture.lines),
                "path": capture.path,
                "error": capture.error,
            }

    def close(self) -> None:
        if self._closed:
            return
        for serial in list(self._logs):
            self.stop_logcat(serial)
        self._closed = True
