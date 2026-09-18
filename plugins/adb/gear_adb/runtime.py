"""Contract adapter for the session-owned USB ADB service; no Qt imports."""

import copy
from pathlib import Path
import uuid

from gear_contracts.api import GearError
from .config import PLUGIN_ID, diagnostic, validate_slice
from .transport import AdbService


class AdbRuntime:
    def __init__(self, service=None):
        self.service = service if service is not None else AdbService()
        self._slice = {
            "plugin": {"id": PLUGIN_ID, "config": {}},
            "resources": {},
            "devices": {},
        }
        self._binding = None
        self._run_id = None
        self._closed = False

    def configure(self, plugin_slice):
        self._slice = copy.deepcopy(plugin_slice)
        path = plugin_slice["plugin"]["config"].get("adb_path", "adb")
        # Saving unfinished configuration is allowed. Its use fails explicitly.
        if isinstance(path, str) and path.strip() and "\0" not in path:
            self.service.configure(path)

    def validate_config(self, plugin_slice):
        return validate_slice(plugin_slice)

    def begin_run(self, binding, context):
        if self._closed or self._binding is not None:
            raise GearError("ADB_INVALID_STATE", "ADB 会话已关闭或已有用例在执行。")
        self._binding = copy.deepcopy(binding)
        self._run_id = context.run_id

    def _resource(self, resource_id, context):
        if (
            self._binding is None
            or context.run_id != self._run_id
            or resource_id not in self._binding["resource_ids"]
        ):
            raise GearError("ADB_NOT_BOUND", "该 ADB 资源未绑定到当前用例。")
        return self._binding["plugin_slice"]["resources"][resource_id]["device"]

    def _tool_config(self):
        if self._closed:
            raise GearError("ADB_CLOSED", "ADB 会话已关闭。")
        config = self._slice["plugin"]["config"]
        report = validate_slice(
            {
                "plugin": {"id": PLUGIN_ID, "config": config},
                "resources": {},
                "devices": {},
            }
        )
        if report["status"] != "VALID":
            first = report["diagnostics"][0]
            raise GearError(first["code"], first["message"])

    def refresh_devices(self):
        self._tool_config()
        return self.service.discover()

    def manual_shell(self, serial, command):
        self._tool_config()
        return self.service.shell(serial, command)

    def manual_pull(self, serial, remote_path, destination):
        self._tool_config()
        try:
            Path(destination).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise GearError("ADB_FILE_ERROR", str(exc)) from exc
        return self.service.pull(serial, remote_path, destination)

    def start_logcat(self, serial, destination):
        self._tool_config()
        return self.service.start_logcat(serial, destination)

    def stop_logcat(self, serial):
        return self.service.stop_logcat(serial)

    def logcat_snapshot(self, serial):
        return self.service.logcat_snapshot(serial)

    def invoke(self, resource_id, operation, args, context):
        serial = self._resource(resource_id, context)
        destination = None
        try:
            if operation == "SHELL":
                result = self.service.shell(serial, args["command"])
            elif operation == "PULL":
                destination = f"files/{PLUGIN_ID}/{uuid.uuid4().hex}"
                local = Path(context.artifact_dir) / destination
                local.mkdir(parents=True)
                result = self.service.pull(serial, args["remote_path"], str(local))
            else:
                raise GearError("ADB_UNKNOWN_CAPABILITY", operation)
        except (GearError, OSError) as exc:
            code = exc.args[0] if isinstance(exc, GearError) else "ADB_FILE_ERROR"
            return {
                "ok": False,
                "diagnostic": diagnostic(code, str(exc)),
                "details": {"serial": serial},
            }
        details = {"serial": serial, **result}
        if destination is not None:
            details["destination"] = destination
        ok = result["exit_code"] == 0
        return {
            "ok": ok,
            "diagnostic": (
                None
                if ok
                else diagnostic(
                    "ADB_COMMAND_FAILED",
                    "ADB 命令执行失败。",
                    exit_code=result["exit_code"],
                )
            ),
            "details": details,
        }

    def evaluate(self, resource_id, condition, args, context):
        self._resource(resource_id, context)
        raise GearError("ADB_UNKNOWN_CAPABILITY", condition)

    def collect(self, resource_id, evidence, args, context):
        self._resource(resource_id, context)
        raise GearError("ADB_UNKNOWN_CAPABILITY", evidence)

    def end_run(self):
        self._binding = None
        self._run_id = None

    def close(self):
        if self._closed:
            return
        self.end_run()
        self.service.close()
        self._closed = True


def create_plugin():
    return AdbRuntime()
