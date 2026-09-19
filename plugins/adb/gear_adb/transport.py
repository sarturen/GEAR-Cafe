"""Session-owned USB ADB clients; mutations run on the host's single worker.

Snapshot methods may be called from the GUI thread. They read cached state
under a lock and never poll a process or touch the filesystem.
"""

from __future__ import annotations

import ast
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import os
import math
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


def _fastboot_home() -> Path:
    """Match AOSP fastboot's profile resolution, including Windows shell API."""
    if os.name == "nt":
        import ctypes

        path = ctypes.create_unicode_buffer(260)
        if (
            ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, path) != 0
            or not path.value
        ):
            raise OSError("Cannot resolve fastboot user profile")
        return Path(path.value)
    return Path.home()


def _check_fastboot_usb_listing():
    # Modern fastboot `devices` also probes addresses stored by `connect`.
    # Refuse that external configuration instead of mutating it or contacting it.
    try:
        registry = _fastboot_home() / ".fastboot" / "devices"
        try:
            entries = registry.read_bytes().strip()
        except FileNotFoundError:
            return
    except (OSError, RuntimeError) as exc:
        raise GearError(
            "FASTBOOT_NETWORK_CHECK_FAILED",
            f"无法确认 fastboot 网络登记为空，未启动查询：{exc}",
        ) from exc
    if entries:
        raise GearError(
            "FASTBOOT_NETWORK_CONFIGURED",
            "fastboot 存在已登记网络目标；USB-only 模式未启动发现，请先在外部清理该登记。",
        )


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
        self._fastboot_path = ""
        self._query_lock = threading.RLock()
        self._monitor_stop = threading.Event()
        self._monitor = None
        self._records = {"adb": [], "fastboot": []}
        self._queries = {
            "adb": {"status": "unknown", "diagnostic": None},
            "fastboot": {
                "status": "unavailable",
                "diagnostic": {
                    "code": "FASTBOOT_UNCONFIGURED",
                    "message": "未配置 fastboot，查询不可用。",
                    "details": {},
                },
            },
        }
        self._outputs = {}
        self._revision = 0
        self._closed = False
        self._logs: dict[str, _LogCapture] = {}
        self._lock = threading.Lock()

    def configure(self, adb_path: str) -> None:
        self._ensure_open()
        if adb_path != self._adb_path:
            self.stop_monitor()
            self._invalidate("adb")
            for serial in list(self._logs):
                self.stop_logcat(serial)
            self._adb_path = adb_path

    def configure_fastboot(self, path: str) -> None:
        self._ensure_open()
        if path != self._fastboot_path:
            self.stop_monitor()
            self._fastboot_path = path
            self._invalidate("fastboot")

    def _invalidate(self, tool):
        with self._lock:
            self._records[tool] = []
            self._queries[tool] = {"status": "unknown", "diagnostic": None}
            if tool == "fastboot" and not self._fastboot_path:
                self._queries[tool] = {
                    "status": "unavailable",
                    "diagnostic": {
                        "code": "FASTBOOT_UNCONFIGURED",
                        "message": "未配置 fastboot，查询不可用。",
                        "details": {},
                    },
                }
            self._revision += 1

    def _discover(self, tool, action, retire_other=True):
        with self._query_lock:
            try:
                records = action()
            except GearError as exc:
                with self._lock:
                    self._queries[tool] = {
                        "status": "error",
                        "diagnostic": {
                            "code": exc.args[0],
                            "message": str(exc),
                            "details": {},
                        },
                    }
                    self._revision += 1
                raise
            with self._lock:
                self._records[tool] = deepcopy(records)
                if retire_other:
                    # A command's fresh target observation supersedes a previous
                    # mode of that same board without inventing a new identity.
                    serials = {record["serial"] for record in records}
                    other = "fastboot" if tool == "adb" else "adb"
                    self._records[other] = [
                        record
                        for record in self._records[other]
                        if record["serial"] not in serials
                    ]
                self._queries[tool] = {"status": "ok", "diagnostic": None}
                self._revision += 1
            return records

    def discover(self):
        return self._discover("adb", self._discover_adb)

    def discover_fastboot(self):
        return self._discover("fastboot", self._discover_fastboot)

    def refresh_devices(self):
        with self._query_lock:
            for tool, query in (
                ("adb", self._discover_adb),
                ("fastboot", self._discover_fastboot),
            ):
                try:
                    self._discover(tool, query, retire_other=False)
                except GearError:
                    pass  # Each failed query is retained, independently, in the cache.
            return self.device_snapshot()["records"]

    def device_snapshot(self):
        with self._lock:
            return deepcopy(
                {
                    "records": self._records["adb"] + self._records["fastboot"],
                    "by_tool": self._records,
                    "queries": self._queries,
                    "outputs": self._outputs,
                    "revision": self._revision,
                    "monitoring": self._monitor is not None
                    and not self._monitor_stop.is_set(),
                }
            )

    def device_status(self, serial):
        snapshot = self.device_snapshot()
        matches = [
            record
            for tool, records in snapshot["by_tool"].items()
            if snapshot["queries"][tool]["status"] == "ok"
            for record in records
            if record["serial"] == serial
        ]
        errors = [
            query["diagnostic"]
            for query in snapshot["queries"].values()
            if query["diagnostic"]
        ]
        if len(matches) > 1:
            return {
                "state": "ambiguous",
                "diagnostic": {
                    "code": "ADB_AMBIGUOUS_DEVICE",
                    "message": "多个接口使用同一单板编码；请重新刷新核对。",
                    "details": {},
                },
            }
        if matches:
            return {"state": matches[0]["state"], "diagnostic": None}
        if errors:
            return {"state": "unknown", "diagnostic": errors[0]}
        complete = all(
            query["status"] == "ok" for query in snapshot["queries"].values()
        )
        return {"state": "missing" if complete else "unknown", "diagnostic": None}

    def _remember_output(self, serial, kind, result):
        with self._lock:
            self._outputs[serial] = {"kind": kind, **result}
            self._revision += 1
        return result

    def start_monitor(self, interval_s=1.0):
        self._ensure_open()
        if self._monitor is not None:
            return
        if not 0 < interval_s <= 60:
            raise GearError("ADB_ARGUMENTS_INVALID", "观察间隔必须为 0–60 秒。")
        self._monitor_stop.clear()

        def observe():
            while not self._monitor_stop.is_set():
                self.refresh_devices()
                if self._monitor_stop.wait(interval_s):
                    break

        self._monitor = threading.Thread(target=observe, name="gear-board-observer")
        self._monitor.start()

    def stop_monitor(self):
        self._monitor_stop.set()
        if self._monitor is not None:
            self._monitor.join()
            self._monitor = None

    def _run_fastboot(self, arguments, timeout):
        self._ensure_open()
        if not self._fastboot_path:
            raise GearError("FASTBOOT_UNCONFIGURED", "未配置 fastboot，查询不可用。")
        try:
            process = subprocess.Popen(
                [self._fastboot_path, *arguments],
                **_process_options(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise GearError(
                "FASTBOOT_TOOL_ERROR", f"Could not start fastboot: {exc}"
            ) from exc
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate(timeout=2)
            raise GearError(
                "FASTBOOT_TIMEOUT", f"fastboot 超过 {timeout} 秒，客户端已停止。"
            ) from exc
        return {"exit_code": process.returncode, "stdout": stdout, "stderr": stderr}

    def _discover_fastboot(self):
        if self._fastboot_path:
            _check_fastboot_usb_listing()
        result = self._run_fastboot(["devices", "-l"], _DISCOVERY_TIMEOUT)
        if result["exit_code"]:
            raise GearError(
                "FASTBOOT_DISCOVERY_FAILED",
                result["stderr"] or "fastboot devices 查询失败。",
            )
        records = []
        for line in result["stdout"].splitlines():
            fields = line.split()
            if not fields:
                continue
            if fields[0].lower().startswith(("tcp:", "udp:")):
                continue
            if len(fields) < 2 or fields[1] != "fastboot" or fields[0].startswith("?"):
                raise GearError(
                    "FASTBOOT_DISCOVERY_FAILED",
                    "无法解析 fastboot USB 设备记录：" + line,
                )
            self._fastboot_serial(fields[0])
            records.append(
                {
                    "serial": fields[0],
                    "state": "fastboot",
                    "usb": " ".join(fields[2:]),
                    "model": "",
                }
            )
        return records

    @staticmethod
    def _fastboot_serial(serial):
        # Fastboot interprets tcp:/udp: as network connections; do not permit
        # network addresses, USB device-path aliases, or selection placeholders.
        if (
            type(serial) is not str
            or not serial
            or serial.startswith(("-", "?"))
            or any(c.isspace() or c in ":\0" for c in serial)
        ):
            raise GearError(
                "FASTBOOT_TARGET_INVALID", "fastboot 只接受 USB 单板序列号。"
            )

    def fastboot(self, serial, arguments, timeout_s=30):
        self._fastboot_serial(serial)
        if (
            type(arguments) is not list
            or not arguments
            or any(
                type(arg) is not str or not arg or "\0" in arg or arg.startswith("-")
                for arg in arguments
            )
            or arguments[0] in {"devices", "connect", "disconnect", "help"}
            or type(timeout_s) not in (int, float)
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 300
        ):
            raise GearError(
                "FASTBOOT_ARGUMENTS_INVALID",
                "请输入子命令及参数；不接受全局选项或网络命令，timeout_s 必须大于 0 且不超过 300。",
            )
        with self._query_lock:
            matches = [r for r in self.discover_fastboot() if r["serial"] == serial]
            if len(matches) != 1:
                code = (
                    "ADB_AMBIGUOUS_DEVICE" if matches else "FASTBOOT_DEVICE_UNAVAILABLE"
                )
                raise GearError(code, f"无法唯一定位 fastboot USB 单板 {serial}。")
            result = self._run_fastboot(["-s", serial, "--", *arguments], timeout_s)
            return self._remember_output(serial, "FASTBOOT", result)

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

    def _discover_adb(self) -> list[dict[str, str]]:
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
        with self._query_lock:
            result = self._run([*self._target(serial), "shell", "-n", "-T", command])
            return self._remember_output(serial, "SHELL", result)

    def pull(self, serial: str, remote_path: str, destination: str) -> dict:
        with self._query_lock:
            result = self._run(
                [*self._target(serial), "pull", remote_path, destination]
            )
            return self._remember_output(serial, "PULL", result)

    def dump_logcat(self, serial: str) -> dict:
        with self._query_lock:
            result = self._run(
                [*self._target(serial), "logcat", "-d", "-v", "threadtime"]
            )
            return self._remember_output(serial, "LOGCAT", result)

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
        self.stop_monitor()
        for serial in list(self._logs):
            self.stop_logcat(serial)
        self._closed = True
