"""Contract adapter for the session-owned USB ADB service; no Qt imports."""

import copy
from pathlib import Path
import uuid

from gear_contracts.api import GearError
from .config import PLUGIN_ID, diagnostic, validate_slice
from .transport import AdbService
from .evidence import collect_logs


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
        fastboot_path = plugin_slice["plugin"]["config"].get("fastboot_path", "")
        if (
            isinstance(fastboot_path, str)
            and "\0" not in fastboot_path
            and hasattr(self.service, "configure_fastboot")
        ):
            self.service.configure_fastboot(fastboot_path.strip())

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

    def _manual_guard(self):
        if self._binding is not None:
            raise GearError("BUSY", "用例运行期间禁止手动操作。")
        self._tool_config()

    def refresh_devices(self):
        self._manual_guard()
        return self.service.refresh_devices()

    def device_snapshot(self):
        return self.service.device_snapshot()

    def device_status(self, serial):
        return self.service.device_status(serial)

    def start_monitor(self):
        self._manual_guard()
        self.service.start_monitor()
        return self.device_snapshot()

    def stop_monitor(self):
        self._manual_guard()
        self.service.stop_monitor()
        return self.device_snapshot()

    def manual_fastboot(self, serial, arguments, timeout_s=30):
        self._manual_guard()
        return self.service.fastboot(serial, arguments, timeout_s)

    def manual_shell(self, serial, command):
        self._manual_guard()
        self._check_args("SHELL", {"command": command})
        return self.service.shell(serial, command)

    def manual_pull(self, serial, remote_path, destination):
        self._manual_guard()
        self._check_args("PULL", {"remote_path": remote_path})
        try:
            Path(destination).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise GearError("ADB_FILE_ERROR", str(exc)) from exc
        return self.service.pull(serial, remote_path, destination)

    def start_logcat(self, serial, destination):
        self._manual_guard()
        return self.service.start_logcat(serial, destination)

    def stop_logcat(self, serial):
        self._manual_guard()
        return self.service.stop_logcat(serial)

    def logcat_snapshot(self, serial):
        return self.service.logcat_snapshot(serial)

    @staticmethod
    def _check_args(capability, args):
        fields = {
            "SHELL": {"command"},
            "PULL": {"remote_path"},
            "FASTBOOT": {"arguments", "timeout_s"},
            "AVAILABLE": set(),
            "UNAVAILABLE": set(),
            "STATE_IS": {"state"},
            "OUTPUT_CONTAINS": {"command", "text"},
        }
        if capability not in fields:
            raise GearError("ADB_UNKNOWN_CAPABILITY", capability)
        allowed = fields[capability]
        required = allowed - {"timeout_s"}
        valid = type(args) is dict and required <= set(args) <= allowed
        if valid:
            for field in required - {"arguments"}:
                value = args[field]
                valid &= (
                    type(value) is str
                    and bool(value if field == "text" else value.strip())
                    and "\0" not in value
                )
            if capability == "PULL":
                valid &= type(args["remote_path"]) is str and args[
                    "remote_path"
                ].startswith("/")
            if capability == "STATE_IS" and valid:
                valid &= args["state"] in {
                    "device",
                    "recovery",
                    "sideload",
                    "fastboot",
                    "offline",
                    "unauthorized",
                    "missing",
                }
        if not valid:
            raise GearError(
                "ADB_ARGUMENTS_INVALID", "参数不符合 " + capability + " 的声明。"
            )

    def invoke(self, resource_id, operation, args, context):
        serial = self._resource(resource_id, context)
        destination = None
        try:
            self._check_args(operation, args)
            if operation == "SHELL":
                result = self.service.shell(serial, args["command"])
            elif operation == "FASTBOOT":
                result = self.service.fastboot(
                    serial, args["arguments"], args.get("timeout_s", 30)
                )
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
                    (
                        "FASTBOOT_COMMAND_FAILED"
                        if operation == "FASTBOOT"
                        else "ADB_COMMAND_FAILED"
                    ),
                    operation + " 命令执行失败。",
                    exit_code=result["exit_code"],
                )
            ),
            "details": details,
        }

    def evaluate(self, resource_id, condition, args, context):
        serial = self._resource(resource_id, context)
        try:
            self._check_args(condition, args)
            if condition == "OUTPUT_CONTAINS":
                result = self.service.shell(serial, args["command"])
                ok = result["exit_code"] == 0
                return {
                    "ok": ok,
                    "satisfied": ok and args["text"] in result["stdout"],
                    "diagnostic": (
                        None
                        if ok
                        else diagnostic("ADB_COMMAND_FAILED", "输出校验命令执行失败。")
                    ),
                    "details": {"serial": serial, **result},
                }
            if condition == "STATE_IS":
                self.service.refresh_devices()
                observation = self.service.device_status(serial)
                state = observation["state"]
                if observation["diagnostic"]:
                    return {
                        "ok": False,
                        "satisfied": False,
                        "diagnostic": observation["diagnostic"],
                        "details": {"serial": serial, "state": state},
                    }
                satisfied = state == args["state"]
            else:
                # Preserve the original ADB-only availability semantics.
                matches = [
                    device
                    for device in self.service.discover()
                    if device["serial"] == serial
                ]
                if len(matches) > 1:
                    raise GearError(
                        "ADB_AMBIGUOUS_DEVICE", "多个 USB 设备使用相同序列号。"
                    )
                state = matches[0]["state"] if matches else "missing"
                satisfied = (state == "device") == (condition == "AVAILABLE")
        except GearError as exc:
            return {
                "ok": False,
                "satisfied": False,
                "diagnostic": diagnostic(exc.args[0], str(exc)),
                "details": {"serial": serial},
            }
        return {
            "ok": True,
            "satisfied": satisfied,
            "diagnostic": None,
            "details": {"serial": serial, "state": state},
        }

    def collect(self, resource_id, evidence, args, context):
        serial = self._resource(resource_id, context)
        if evidence != "DIAGNOSTIC_LOGS":
            raise GearError("ADB_UNKNOWN_CAPABILITY", evidence)
        config = self._binding["plugin_slice"]["resources"][resource_id]["config"]
        return collect_logs(self.service, serial, config.get("log_paths", []), context)

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
