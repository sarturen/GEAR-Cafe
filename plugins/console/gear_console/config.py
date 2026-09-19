"""Pure console configuration validation; no device discovery or access."""

from copy import deepcopy
import re
from gear_contracts.api import GearError

PLUGIN_ID = "gear.console"
DEFAULTS = {
    "baudrate": 115200,
    "parity": "N",
    "stopbits": 1,
    "data_bits": 8,
    "encoding": "utf-8",
    "line_ending": "LF",
}
LINE_ENDINGS = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}


def diagnostic(code, message, path=None, **details):
    result = {"code": code, "message": message, "details": details}
    if path is not None:
        result["path"] = path
    return result


def error_diagnostic(exc):
    if isinstance(exc, GearError):
        return diagnostic(
            str(exc.args[0]), str(exc.args[1]) if len(exc.args) > 1 else str(exc)
        )
    return diagnostic("CONSOLE_IO_ERROR", str(exc))


def normalize_config(config):
    if type(config) is not dict:
        raise GearError("CONSOLE_CONFIG_INVALID", "串口配置必须为对象。")
    if set(config) - (set(DEFAULTS) | {"port", "role"}):
        raise GearError("CONSOLE_CONFIG_INVALID", "串口配置包含未知字段。")
    result = {**DEFAULTS, **deepcopy(config)}
    port, role = result.get("port"), result.get("role")
    if not port or not role:
        raise GearError("CONSOLE_CONFIG_INCOMPLETE", "请填写 COM 口和 MCU/SOC 用途。")
    if not isinstance(port, str) or not re.fullmatch(r"COM[1-9][0-9]*", port.upper()):
        raise GearError("CONSOLE_PORT_INVALID", "COM 口必须为手工指定的 COM 数字。")
    result["port"] = port.upper()
    if role not in ("MCU", "SOC"):
        raise GearError("CONSOLE_ROLE_INVALID", "用途必须为 MCU 或 SOC。")
    if type(result["baudrate"]) is not int or not 1 <= result["baudrate"] <= 0xFFFFFFFF:
        raise GearError("CONSOLE_CONFIG_INVALID", "波特率必须为正整数。")
    if result["parity"] not in ("N", "E", "O"):
        raise GearError("CONSOLE_CONFIG_INVALID", "校验位必须为 N/E/O。")
    if type(result["stopbits"]) not in (int, float) or result["stopbits"] not in (1, 2):
        raise GearError("CONSOLE_CONFIG_INVALID", "停止位必须为 1 或 2。")
    if type(result["data_bits"]) is not int or result["data_bits"] != 8:
        raise GearError("CONSOLE_CONFIG_INVALID", "数据位固定为 8。")
    if result["encoding"] not in ("utf-8", "gbk"):
        raise GearError("CONSOLE_CONFIG_INVALID", "编码必须为 utf-8 或 gbk。")
    if result["line_ending"] not in ("LF", "CRLF", "CR"):
        raise GearError("CONSOLE_CONFIG_INVALID", "行结束符必须为 LF/CRLF/CR。")
    return result


def validate_slice(data):
    problems = []
    states = []

    def add(code, message, path, state="INVALID"):
        states.append(state)
        problems.append(diagnostic(code, message, path))

    if (
        type(data) is not dict
        or type(data.get("plugin")) is not dict
        or type(data.get("resources")) is not dict
    ):
        return {
            "status": "INVALID",
            "diagnostics": [diagnostic("CONSOLE_SLICE_INVALID", "插件切片格式无效。")],
        }
    plugin = data["plugin"]
    if plugin.get("id") != PLUGIN_ID or "devices" in data:
        add("CONSOLE_SLICE_INVALID", "串口插件不能拥有单板登记。", "plugin.id")
    config = plugin.get("config")
    if type(config) is not dict or set(config) - {"shortcuts"}:
        add("CONSOLE_CONFIG_INVALID", "插件配置只接受 shortcuts。", "plugin.config")
    else:
        shortcuts = config.get("shortcuts", [])
        if type(shortcuts) is not list or any(
            type(s) is not dict
            or set(s) != {"label", "command"}
            or any(
                not isinstance(s.get(k), str) or not s[k].strip() or len(s[k]) > 65536
                for k in ("label", "command")
            )
            for s in shortcuts
        ):
            add(
                "CONSOLE_SHORTCUT_INVALID",
                "快捷命令需要非空 label 和 command。",
                "plugin.config.shortcuts",
            )
    ports, roles = {}, {}
    for rid, record in data["resources"].items():
        path = "resources." + str(rid)
        if (
            not isinstance(rid, str)
            or not re.fullmatch(r"CONSOLE\.[A-Za-z][A-Za-z0-9_-]*", rid)
            or type(record) is not dict
            or record.get("type") != "CONSOLE"
        ):
            add("CONSOLE_RESOURCE_INVALID", "逻辑资源必须为 CONSOLE.alias。", path)
            continue
        device = record.get("device")
        if device is None or device == "":
            add(
                "CONSOLE_DEVICE_MISSING",
                "请选择已登记的单板设备号。",
                path + ".device",
                "INCOMPLETE",
            )
        elif not isinstance(device, str) or not device.strip():
            add(
                "CONSOLE_DEVICE_INVALID",
                "单板设备号必须为非空字符串。",
                path + ".device",
            )
        try:
            value = normalize_config(record.get("config"))
        except GearError as exc:
            add(
                exc.args[0],
                exc.args[1],
                path + ".config",
                (
                    "INCOMPLETE"
                    if exc.args[0] == "CONSOLE_CONFIG_INCOMPLETE"
                    else "INVALID"
                ),
            )
            continue
        port = value["port"]
        if port in ports:
            add(
                "CONSOLE_PORT_DUPLICATE",
                f"{port} 已由 {ports[port]} 独占。",
                path + ".config.port",
            )
        ports[port] = rid
        if isinstance(device, str) and device:
            key = (device, value["role"])
            if key in roles:
                add(
                    "CONSOLE_ROLE_DUPLICATE",
                    f"同单板的 {value['role']} 已由 {roles[key]} 配置。",
                    path + ".config.role",
                )
            roles[key] = rid
    return {
        "status": (
            "INVALID" if "INVALID" in states else "INCOMPLETE" if states else "VALID"
        ),
        "diagnostics": problems,
    }
