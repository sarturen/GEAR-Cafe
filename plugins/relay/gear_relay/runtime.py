"""GEAR v1 adapter: one long-lived service per independent COM controller."""

from copy import deepcopy
from gear_contracts.api import GearError
from .bindings import resource_location
from .config import PLUGIN_ID, controller_report, diagnostic, validate_slice
from .transport import RelayService


class RelayRuntime:
    def __init__(self, service=None, service_factory=RelayService):
        self._service_factory = service_factory
        self.services = {}
        if service is not None:
            self.services["main"] = service
        self._slice = {"plugin": {"id": PLUGIN_ID, "config": {}}, "resources": {}}
        self._binding = None
        self._run_id = None
        self._closed = False

    @property
    def service(self):
        """Legacy single-controller adapter, retained for existing integrations."""
        if "main" not in self.services:
            self.services["main"] = self._service_factory()
        return self.services["main"]

    @service.setter
    def service(self, service):
        previous = self.services.get("main")
        if previous is not None and previous is not service:
            previous.close()
        self.services["main"] = service

    def _alive(self):
        if self._closed:
            raise GearError("RELAY_CLOSED", "继电器会话已关闭。")

    def _manual_allowed(self):
        self._alive()
        if self._binding is not None:
            raise GearError("RELAY_RUN_ACTIVE", "用例运行中，禁止人工控制或修改配置。")

    def configure(self, plugin_slice):
        self._manual_allowed()
        configs, _ = controller_report(plugin_slice["plugin"]["config"])
        for cid in list(self.services):
            if cid not in configs:
                self.services[cid].close()
                del self.services[cid]
        for cid, config in configs.items():
            if cid not in self.services:
                self.services[cid] = self._service_factory()
            self.services[cid].configure(config)
        self._slice = deepcopy(plugin_slice)

    def validate_config(self, plugin_slice):
        return validate_slice(plugin_slice)

    def begin_run(self, binding, context):
        self._alive()
        if self._binding is not None:
            raise GearError("RELAY_INVALID_STATE", "已有用例在执行。")
        # Only identifiers and the immutable archived slice are retained.
        self._binding = deepcopy(binding)
        self._run_id = context.run_id

    def _bound_resource(self, resource_id, context):
        self._alive()
        if (
            self._binding is None
            or context.run_id != self._run_id
            or resource_id not in self._binding["resource_ids"]
            or resource_id not in self._binding["plugin_slice"]["resources"]
        ):
            raise GearError("RELAY_NOT_BOUND", "资源未绑定到当前用例。")
        return self._location(self._binding["plugin_slice"]["resources"][resource_id])

    @staticmethod
    def _location(record):
        cid, channel = resource_location(record)
        if (
            record.get("type") != "POWER"
            or type(channel) is not int
            or not 1 <= channel <= 8
        ):
            raise GearError("RELAY_CHANNEL_INVALID", "资源未绑定有效的继电器通道。")
        if type(cid) is not str:
            raise GearError("RELAY_CONTROLLER_INVALID", "资源未指定有效控制器。")
        return cid, channel

    def _settings(self, controller, plugin_slice):
        configs, _ = controller_report(plugin_slice["plugin"]["config"])
        if controller not in configs or configs[controller] is None:
            raise GearError("RELAY_NOT_CONFIGURED", "所选控制器配置无效或缺少 COM 口。")
        if controller not in self.services:
            self.services[controller] = self._service_factory()
        service = self.services[controller]
        service.configure(configs[controller])
        return service

    @staticmethod
    def _arguments(name, supported, args):
        if name not in supported:
            raise GearError("RELAY_UNKNOWN_CAPABILITY", str(name))
        if type(args) is not dict or args:
            raise GearError("RELAY_INVALID_ARGUMENT", "此能力不接受参数。")

    @staticmethod
    def _token(context):
        return None if context.phase == "teardown" else context.stop_token

    @staticmethod
    def _error(exc):
        return diagnostic(
            exc.args[0], str(exc.args[1]) if len(exc.args) > 1 else str(exc)
        )

    def invoke(self, resource_id, operation, args, context):
        self._arguments(operation, ("ON", "OFF"), args)
        cid, channel = self._bound_resource(resource_id, context)
        details = {"controller": cid, "channel": channel}
        try:
            service = self._settings(cid, self._binding["plugin_slice"])
            service.set_channel(channel, operation == "ON", self._token(context))
        except GearError as exc:
            return {"ok": False, "diagnostic": self._error(exc), "details": details}
        details.update(state=operation == "ON", source="write_acknowledgement")
        return {"ok": True, "diagnostic": None, "details": details}

    def evaluate(self, resource_id, condition, args, context):
        self._arguments(condition, ("IS_ON", "IS_OFF"), args)
        cid, channel = self._bound_resource(resource_id, context)
        details = {"controller": cid, "channel": channel}
        try:
            service = self._settings(cid, self._binding["plugin_slice"])
            state = service.read_states(self._token(context))[channel - 1]
        except GearError as exc:
            return {
                "ok": False,
                "satisfied": False,
                "diagnostic": self._error(exc),
                "details": details,
            }
        details.update(state=state, source="read_coils")
        return {
            "ok": True,
            "satisfied": state == (condition == "IS_ON"),
            "diagnostic": None,
            "details": details,
        }

    def collect(self, resource_id, evidence, args, context):
        self._bound_resource(resource_id, context)
        raise GearError("RELAY_UNKNOWN_CAPABILITY", "此插件未声明取证能力。")

    def _manual_settings(self, controller):
        self._manual_allowed()
        return self._settings(controller, self._slice)

    def connect(self, controller="main"):
        self._manual_settings(controller).open()
        return self.snapshot(controller)

    def disconnect(self, controller="main"):
        self._manual_allowed()
        service = self.services.get(controller)
        if service is not None:
            service.disconnect()
        return self.snapshot(controller)

    def read_states(self, controller="main"):
        self._manual_settings(controller).read_states()
        return self.snapshot(controller)

    def set_channel(self, channel, on, controller="main"):
        self._manual_settings(controller).set_channel(channel, on)
        return self.snapshot(controller)

    def set_resource(self, resource_id, on):
        self._manual_allowed()
        if type(on) is not bool:
            raise GearError("RELAY_INVALID_ARGUMENT", "继电器状态必须为布尔值。")
        record = self._slice["resources"].get(resource_id)
        if record is None:
            raise GearError("RELAY_NOT_BOUND", "资源不存在，请重新绑定。")
        cid, channel = self._location(record)
        return self.set_channel(channel, on, cid)

    def set_all(self, on, controller="main"):
        self._manual_settings(controller).set_all(on)
        return self.snapshot(controller)

    def snapshot(self, controller="main"):
        service = self.services.get(controller)
        if service is not None:
            return service.snapshot()
        return {
            "connected": False,
            "fault": None,
            "states": [None] * 8,
            "sources": [None] * 8,
        }

    def snapshots(self):
        # GUI observation only; service snapshots never acquire the serial lock.
        return {cid: service.snapshot() for cid, service in list(self.services.items())}

    def end_run(self):
        self._binding = None
        self._run_id = None

    def close(self):
        if self._closed:
            return
        errors = []
        for service in list(self.services.values()):
            try:
                service.close()
            except Exception as exc:
                errors.append(exc)
        self.end_run()
        if errors:
            raise errors[0]
        self._closed = True


def create_plugin():
    return RelayRuntime()
