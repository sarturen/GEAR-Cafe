"""GEAR v1 runtime and private manual surface sharing long-lived services."""

from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import threading
import uuid
from gear_contracts.api import GearError, StopRequested
from .config import (
    PLUGIN_ID,
    diagnostic,
    error_diagnostic,
    normalize_config,
    validate_slice,
)
from .service import PortService
from .serial_win32 import WindowsSerial


class ConsoleRuntime:
    def __init__(self, *, serial_factory=WindowsSerial, cache_chars=262144):
        self._factory, self._cache_chars = serial_factory, cache_chars
        self._slice = {"plugin": {"id": PLUGIN_ID, "config": {}}, "resources": {}}
        self._services, self._resource_services = {}, {}
        self._binding = self._run_id = None
        self._run_services, self._cursors = {}, {}
        self._closed = False
        self._lock = threading.RLock()

    def _alive(self):
        if self._closed:
            raise GearError("CONSOLE_CLOSED", "串口会话已关闭。")

    def _idle(self):
        self._alive()
        if self._binding is not None:
            raise GearError("CONSOLE_BUSY", "用例执行期间禁止手动操作或配置。")

    def configure(self, plugin_slice):
        self._idle()
        with self._lock:
            self._slice = deepcopy(plugin_slice)

    def validate_config(self, plugin_slice):
        return validate_slice(plugin_slice)

    def _service(self, rid, record):
        config = normalize_config(record["config"])
        port = config["port"]
        with self._lock:
            previous_service = self._resource_services.get(rid)
            if previous_service and previous_service.config["port"] != port:
                previous = previous_service.snapshot()
                if (
                    previous["connected"]
                    or previous["worker_alive"]
                    or previous["closing"]
                ):
                    raise GearError(
                        "CONSOLE_CONFIG_CHANGED",
                        "原 COM 尚未断开；请先断开再使用新 COM。",
                    )
            service = self._services.get(port)
            owned_elsewhere = service is not None and any(
                owner != rid and owned is service
                for owner, owned in self._resource_services.items()
            )
            if service and (service.config != config or owned_elsewhere):
                view = service.snapshot()
                if view["connected"] or view["worker_alive"] or view["closing"]:
                    raise GearError(
                        "CONSOLE_CONFIG_CHANGED", "连接使用旧配置；请先断开该串口。"
                    )
                service = None
            if service is None:
                service = PortService(
                    config, serial_factory=self._factory, cache_chars=self._cache_chars
                )
                self._services[port] = service
            return service

    def _connect_service(self, resource_id, service):
        # Ownership points to this exact service generation, never a COM lookup.
        # A replacement service for the same COM must not become addressable by
        # an old resource. Keep failed attempts for explicit disconnect retries.
        with self._lock:
            self._resource_services[resource_id] = service
        service.connect()

    def begin_run(self, binding, context):
        self._idle()
        self._binding = deepcopy(binding)
        self._run_id = context.run_id
        report = validate_slice(self._binding["plugin_slice"])
        if report["status"] != "VALID":
            raise GearError("CONSOLE_CONFIG_INVALID", "归档串口配置不可执行。")
        for rid in self._binding["resource_ids"]:
            record = self._binding["plugin_slice"]["resources"].get(rid)
            if record is None:
                raise GearError("CONSOLE_NOT_BOUND", "归档资源不存在。")
            service = self._service(rid, record)
            self._run_services[rid] = service
            self._cursors[rid] = service.mark_run()

    def _bound(self, rid, context):
        self._alive()
        if (
            self._binding is None
            or context.run_id != self._run_id
            or rid not in self._run_services
        ):
            raise GearError("CONSOLE_NOT_BOUND", "资源未绑定到当前用例。")
        return self._run_services[rid]

    @staticmethod
    def _arguments(name, supported, args):
        if name not in supported:
            raise GearError("CONSOLE_UNKNOWN_CAPABILITY", str(name))
        key = {"SEND": "command", "OUTPUT_CONTAINS": "text"}.get(name)
        if type(args) is not dict or set(args) != ({key} if key else set()):
            raise GearError("CONSOLE_INVALID_ARGUMENT", "能力参数不匹配。")
        if key and (not isinstance(args[key], str) or not 1 <= len(args[key]) <= 65536):
            raise GearError("CONSOLE_INVALID_ARGUMENT", f"{key} 必须为 1–65536 字符。")

    @staticmethod
    def _token(context):
        token = (
            None if context.phase in ("teardown", "evidence") else context.stop_token
        )
        if token and token.is_requested():
            raise StopRequested()
        return token

    def invoke(self, resource_id, operation, args, context):
        try:
            self._arguments(operation, ("SEND",), args)
            service = self._bound(resource_id, context)
            token = self._token(context)
            self._connect_service(resource_id, service)
            written = service.send(args["command"], token)
            return {
                "ok": True,
                "diagnostic": None,
                "details": {
                    "code": "CONSOLE_SENT",
                    "port": service.config["port"],
                    "bytes": written,
                },
            }
        except GearError as exc:
            return {"ok": False, "diagnostic": error_diagnostic(exc), "details": {}}

    def evaluate(self, resource_id, condition, args, context):
        try:
            self._arguments(condition, ("OUTPUT_CONTAINS", "CONNECTED"), args)
            service = self._bound(resource_id, context)
            self._token(context)
            if condition == "OUTPUT_CONTAINS":
                self._connect_service(resource_id, service)
            view = service.snapshot(self._cursors[resource_id])
            if view["fault"]:
                raise GearError(view["fault"]["code"], view["fault"]["message"])
            if condition == "CONNECTED":
                satisfied = view["connected"] and not view["closing"]
            else:
                satisfied = args["text"] in view["text"]
                if not satisfied and view["truncated"]:
                    raise GearError(
                        "CONSOLE_CACHE_GAP",
                        "Run 开始后的部分输出已超出缓存，无法断言未出现。",
                    )
            return {
                "ok": True,
                "satisfied": satisfied,
                "diagnostic": None,
                "details": {
                    "code": "CONSOLE_OBSERVED",
                    "port": view["port"],
                    "start": view["start"],
                    "end": view["end"],
                    "truncated": view["truncated"],
                },
            }
        except GearError as exc:
            return {
                "ok": False,
                "satisfied": False,
                "diagnostic": error_diagnostic(exc),
                "details": {},
            }

    def collect(self, resource_id, evidence, args, context):
        try:
            self._arguments(evidence, ("TRANSCRIPT",), args)
            service = self._bound(resource_id, context)
            view = service.snapshot(self._cursors[resource_id])
            base = Path(context.artifact_dir).resolve()
            directory = (base / "evidence" / PLUGIN_ID).resolve()
            if not directory.is_relative_to(base):
                raise GearError(
                    "CONSOLE_ARTIFACT_PATH", "证据目录必须位于当前 Run 内。"
                )
            directory.mkdir(parents=True, exist_ok=True)
            # Generated basename only: resource ids and call ids never become paths.
            path = directory / ("console-" + uuid.uuid4().hex + ".txt")
            metadata = {key: value for key, value in view.items() if key != "text"}
            metadata.update(
                resource_id=resource_id,
                run_id=context.run_id,
                requested_start=self._cursors[resource_id],
                encoding="utf-8",
            )
            path.write_text(
                json.dumps(metadata, ensure_ascii=False) + "\n\n" + view["text"],
                encoding="utf-8",
            )
            return {
                "ok": True,
                "diagnostic": None,
                "artifacts": [
                    {
                        "path": path.relative_to(base).as_posix(),
                        "media_type": "text/plain",
                        "description": "本 Run 接收文本及缓存截断信息",
                    }
                ],
            }
        except (GearError, OSError) as exc:
            return {"ok": False, "diagnostic": error_diagnostic(exc), "artifacts": []}

    def _manual_service(self, rid):
        self._idle()
        report = validate_slice(self._slice)
        if report["status"] == "INVALID":
            raise GearError("CONSOLE_CONFIG_INVALID", "请先修正冲突或无效配置。")
        record = self._slice["resources"].get(rid)
        if record is None:
            raise GearError("CONSOLE_NOT_BOUND", "资源不存在。")
        return self._service(rid, record)

    def connect(self, resource_id):
        service = self._manual_service(resource_id)
        self._connect_service(resource_id, service)
        return self.snapshot(resource_id)

    def disconnect(self, resource_id):
        self._idle()
        with self._lock:
            service = self._resource_services.get(resource_id)
        if service:
            service.disconnect()
            with self._lock:
                if self._resource_services.get(resource_id) is service:
                    self._resource_services.pop(resource_id, None)
        return self.snapshot(resource_id)

    def send(self, resource_id, command):
        service = self._manual_service(resource_id)
        count = service.send(command)
        return {"code": "CONSOLE_SENT", "bytes": count, "port": service.config["port"]}

    def snapshot(self, resource_id, cursor=None):
        with self._lock:
            record = self._slice.get("resources", {}).get(resource_id, {})
            config = record.get("config", {})
            port = config.get("port", "") if isinstance(config, dict) else ""
            service = (
                self._services.get(port.upper()) if isinstance(port, str) else None
            )
            previous = self._resource_services.get(resource_id)
            if previous and previous is not service:
                state = previous.snapshot()
                if state["connected"] or state["worker_alive"] or state["closing"]:
                    service = previous
        if service:
            view = service.snapshot(cursor)
            try:
                changed = service.config != normalize_config(config)
            except GearError:
                changed = True
            view["config_changed"] = changed
            return view
        return {
            "port": port,
            "generation": None,
            "connected": False,
            "closing": False,
            "worker_alive": False,
            "fault": None,
            "text": "",
            "start": 0,
            "end": 0,
            "truncated": False,
            "config_changed": False,
        }

    def end_run(self):
        if any(
            service.snapshot()["pending_writes"]
            for service in self._run_services.values()
        ):
            raise GearError(
                "CONSOLE_PENDING_WRITE",
                "串口写入尚未退出；不能结束 Run 或开始下一 Run。",
            )
        self._binding = self._run_id = None
        self._run_services, self._cursors = {}, {}

    def close(self):
        if self._closed:
            return
        errors = []
        # Some closed, faulted instances may have been superseded in the COM
        # cache while still retained by their original resource for disconnect.
        services = dict.fromkeys(
            [*self._services.values(), *self._resource_services.values()]
        )
        for service in services:
            try:
                service.disconnect()
            except GearError as exc:
                errors.append(exc)
        if errors:
            raise errors[0]
        self.end_run()
        self._closed = True


def create_plugin():
    return ConsoleRuntime()
