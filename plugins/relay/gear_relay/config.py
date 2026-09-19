"""Pure validation for independent eight-channel controllers. Never do I/O."""

import math
import re
from gear_contracts.api import GearError
from .bindings import controller_configs, resource_location, resource_role

PLUGIN_ID = "gear.relay"
DEFAULT_CONFIG = {
    "port": "",
    "baudrate": 9600,
    "parity": "N",
    "stopbits": 1,
    "unit_id": 1,
    "timeout_s": 1.0,
    "poll_interval_ms": 500,
}


def diagnostic(code, message, **details):
    return {"code": code, "message": message, "details": details}


def _config_issues(config, path="plugin.config"):
    issues = []

    def issue(message, key="", incomplete=False):
        item = diagnostic(
            "RELAY_CONFIG_INCOMPLETE" if incomplete else "RELAY_CONFIG_INVALID", message
        )
        item["path"] = path + ("." + key if key else "")
        issues.append(item)

    if type(config) is not dict:
        issue("继电器串口配置必须为对象。")
        return {}, issues
    values = {**DEFAULT_CONFIG, **config}
    if set(config) - DEFAULT_CONFIG.keys():
        issue("串口配置包含不支持的字段。")
    port = values["port"]
    if type(port) is not str:
        issue("COM 端口必须为字符串。", "port")
    elif not port.strip():
        issue("请手工填写 COM 端口。", "port", True)
    elif re.fullmatch(r"COM[1-9][0-9]*", port.strip(), re.IGNORECASE) is None:
        issue("端口必须为 COM 加正整数编号。", "port")
    else:
        values["port"] = port.strip().upper()
    baud = values["baudrate"]
    if type(baud) is not int or not 300 <= baud <= 115200:
        issue("波特率必须为 300–115200 的整数。", "baudrate")
    parity = values["parity"]
    if type(parity) is not str or parity.strip().upper() not in ("N", "E", "O"):
        issue("校验必须为 N、E 或 O。", "parity")
    else:
        values["parity"] = parity.strip().upper()
    if type(values["stopbits"]) is not int or values["stopbits"] not in (1, 2):
        issue("停止位必须为 1 或 2。", "stopbits")
    if type(values["unit_id"]) is not int or not 1 <= values["unit_id"] <= 247:
        issue("从站地址必须为 1–247，不支持广播。", "unit_id")
    timeout = values["timeout_s"]
    if (
        type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 0.05 <= timeout <= 30
    ):
        issue("超时必须为 0.05–30 秒。", "timeout_s")
    else:
        values["timeout_s"] = float(timeout)
    poll = values["poll_interval_ms"]
    if type(poll) is not int or not 0 <= poll <= 60000:
        issue("轮询间隔必须为 0–60000 毫秒；0 关闭后台读取。", "poll_interval_ms")
    return values, issues


def normalize_config(config):
    values, issues = _config_issues(config)
    if issues:
        first = next(
            (i for i in issues if i["code"] == "RELAY_CONFIG_INVALID"), issues[0]
        )
        raise GearError(first["code"], first["message"])
    return values


def controller_report(config):
    """Return normalized usable configs, with invalid controllers disabled."""
    issues, normalized = [], {}
    if type(config) is not dict:
        return {}, [diagnostic("RELAY_CONFIG_INVALID", "插件配置必须为对象。")]
    if "controllers" in config and set(config) != {"controllers"}:
        issues.append(
            diagnostic("RELAY_CONFIG_INVALID", "controllers 不能与旧单串口字段混用。")
        )
    raw = controller_configs(config)
    if type(raw) is not dict:
        return {}, [
            diagnostic("RELAY_CONFIG_INVALID", "controllers 必须为控制器对象字典。")
        ]
    ports = {}
    for cid, values in raw.items():
        if type(cid) is not str or not cid.strip() or cid != cid.strip():
            issues.append(
                diagnostic(
                    "RELAY_CONTROLLER_INVALID",
                    "控制器名称必须为非空且无首尾空格的文本。",
                )
            )
            continue
        settings, errors = _config_issues(values, f"plugin.config.controllers.{cid}")
        issues.extend(errors)
        normalized[cid] = None if errors else settings
        if not errors:
            port = settings["port"]
            if port in ports:
                issues.append(
                    diagnostic(
                        "RELAY_PORT_DUPLICATE",
                        f"{cid} 与 {ports[port]} 使用同一个 {port}。",
                    )
                )
                normalized[cid] = normalized[ports[port]] = None
            else:
                ports[port] = cid
    if "controllers" in config and set(config) != {"controllers"}:
        normalized = {cid: None for cid in normalized}
    return normalized, issues


def validate_slice(plugin_slice):
    configs, issues = controller_report(plugin_slice["plugin"]["config"])
    assigned, roles = {}, {}
    blocking = list(issues)

    def issue(rid, key, code, message, warning=False):
        item = diagnostic(code, message)
        item["path"] = f"resources.{rid}.config.{key}"
        issues.append(item)
        if not warning:
            blocking.append(item)

    for rid, record in plugin_slice["resources"].items():
        rc = record.get("config", {})
        if record.get("type") != "POWER" or type(rc) is not dict:
            issue(
                rid,
                "channel",
                "RELAY_RESOURCE_INVALID",
                "继电器资源必须为 POWER 且 config 为对象。",
            )
            continue
        if set(rc) - {"controller", "channel", "role", "terminal", "device_name"}:
            issue(
                rid, "channel", "RELAY_RESOURCE_INVALID", "资源配置包含不支持的字段。"
            )
        cid, channel = resource_location(record)
        if type(cid) is not str or cid not in configs:
            issue(
                rid,
                "controller",
                "RELAY_CONTROLLER_INVALID",
                "资源指向未配置的控制器。",
            )
        if "channel" not in rc:
            issue(
                rid,
                "channel",
                "RELAY_CONFIG_INCOMPLETE",
                "旧未完成资源没有通道：请分配通道或删除此资源。",
            )
        elif type(channel) is not int or not 1 <= channel <= 8:
            issue(rid, "channel", "RELAY_CHANNEL_INVALID", "通道必须为 1–8 的整数。")
        elif type(cid) is str:
            key = (cid, channel)
            if key in assigned:
                issue(
                    rid,
                    "channel",
                    "RELAY_CHANNEL_DUPLICATE",
                    f"{cid} / CH{channel} 已分配给 {assigned[key]}。",
                )
            assigned[key] = rid
        role = resource_role(record)
        if type(role) is not str or ("role" in rc and not role.strip()):
            issue(rid, "role", "RELAY_ROLE_INVALID", "用途若填写，必须是非空文本。")
        device = record.get("device")
        if device is not None and (type(device) is not str or not device.strip()):
            issue(
                rid, "device", "RELAY_DEVICE_INVALID", "设备号必须为已登记的非空编码。"
            )
        if (
            type(device) is str
            and device.strip()
            and type(role) is str
            and role.strip()
        ):
            key = (device.strip(), role.strip())
            if key in roles:
                issue(
                    rid,
                    "role",
                    "RELAY_ROLE_DUPLICATE",
                    f"单板 {device} 的 {role} 已分配给 {roles[key]}。",
                )
            roles[key] = rid
        if "device_name" in rc or "terminal" in rc:
            issue(
                rid,
                "device_name",
                "RELAY_LEGACY_ATTRIBUTION",
                "旧私有名称不作为设备号；请核对归属并保存，或解绑以清理旧字段。",
                True,
            )
    invalid = any(x["code"] != "RELAY_CONFIG_INCOMPLETE" for x in blocking)
    return {
        "status": "INVALID" if invalid else "INCOMPLETE" if blocking else "VALID",
        "diagnostics": issues,
    }
