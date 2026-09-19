"""Pure physical-channel allocation using the environment's board identities."""

import re
from copy import deepcopy
from gear_contracts.api import GearError

TERMINALS = ("KL30", "KL15")  # Suggestions, never mandatory resources.


def controller_configs(config):
    if type(config) is not dict:
        return {}
    return (
        config.get("controllers", {}) if "controllers" in config else {"main": config}
    )


def resource_location(record):
    config = record.get("config", {})
    if type(config) is not dict:
        return None, None
    return config.get("controller", "main"), config.get("channel")


def resource_role(record):
    config = record.get("config", {})
    if type(config) is not dict:
        return ""
    return config.get("role", config.get("terminal", ""))


def grouped_resources(resources):
    groups = {}
    for rid, record in resources.items():
        device, role = record.get("device"), resource_role(record)
        if record.get("type") == "POWER" and type(device) is str and device:
            groups.setdefault(device, {}).setdefault(role or "未指定用途", []).append(
                rid
            )
    return groups


def migrate_controllers(data):
    """Explicit GUI edit migration; reading/validation never rewrites saved data.

    Old private names remain visible until each resource is explicitly assigned
    or unassigned. They are never copied into the shared device field.
    """
    config = data["plugin"]["config"]
    if "controllers" not in config:
        data["plugin"]["config"] = {"controllers": {"main": deepcopy(config)}}
    for record in data["resources"].values():
        rc = record.get("config")
        if type(rc) is dict:
            rc.setdefault("controller", "main")


def _new_id(resources, controller, channel):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", controller).strip("_") or "controller"
    base = f"POWER.relay_{slug}_ch{channel}"
    rid, suffix = base, 2
    while rid in resources:
        rid = f"{base}_{suffix}"
        suffix += 1
    return rid


def assign_channel(data, controller, channel, device="", role="", resource_id=None):
    """Save one physical channel atomically, preserving an existing logical ID."""
    if type(channel) is not int or not 1 <= channel <= 8:
        raise GearError("RELAY_CHANNEL_INVALID", "通道必须为 1–8 的整数。")
    controllers = controller_configs(data["plugin"]["config"])
    if type(controller) is not str or controller not in controllers:
        raise GearError("RELAY_CONTROLLER_INVALID", "请选择已配置的控制器。")
    if type(device) is not str or type(role) is not str:
        raise GearError("RELAY_RESOURCE_INVALID", "设备号与用途必须为文本。")
    device, role = device.strip(), role.strip()
    resources = data["resources"]
    owners = [
        rid
        for rid, record in resources.items()
        if resource_location(record) == (controller, channel)
    ]
    if len(owners) > 1 or (owners and resource_id and owners[0] != resource_id):
        raise GearError("RELAY_CHANNEL_DUPLICATE", "物理通道已被其他资源占用。")
    rid = resource_id or (
        owners[0] if owners else _new_id(resources, controller, channel)
    )
    if device and role:
        for other, record in resources.items():
            if (
                other != rid
                and record.get("device") == device
                and resource_role(record) == role
            ):
                raise GearError(
                    "RELAY_ROLE_DUPLICATE", "同一设备号的同一用途只能绑定一个通道。"
                )
    record = deepcopy(resources.get(rid, {"type": "POWER", "config": {}}))
    if record.get("type") != "POWER":
        raise GearError("RELAY_RESOURCE_INVALID", "继电器资源类型必须为 POWER。")
    record["config"] = {"controller": controller, "channel": channel}
    if role:
        record["config"]["role"] = role
    if device:
        record["device"] = device
    else:
        record.pop("device", None)
    resources[rid] = record
    return rid


def unassign_resource(data, rid):
    """Remove board attribution while retaining the physical logical resource."""
    record = data["resources"][rid]
    record.pop("device", None)
    for key in ("role", "terminal", "device_name"):
        record["config"].pop(key, None)
