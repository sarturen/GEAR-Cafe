"""Strict YAML 1.2, DSL grammar and detached Environment slices."""

from __future__ import annotations
import copy
import math
import re
from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.events import AliasEvent
from gear_contracts.api import GearError
from .common import ensure_json

TYPE = r"[A-Z][A-Z0-9_]*"
ALIAS = r"[A-Za-z][A-Za-z0-9_-]*"
PLUGIN = r"[a-z][a-z0-9]*(\.[a-z0-9_-]+)+"


def require(condition, message, code="INVALID_CONFIG"):
    if not condition:
        raise GearError(code, message)


def mapping(value, required=(), optional=(), label="object"):
    require(type(value) is dict, f"{label} must be an object")
    require(
        set(required) <= value.keys(),
        f"{label} is missing fields: {set(required)-value.keys()}",
    )
    require(
        not set(value) - set(required) - set(optional),
        f"{label} has unknown fields: {set(value)-set(required)-set(optional)}",
    )
    return value


def string(value, label="value", pattern=None):
    require(type(value) is str and bool(value), f"{label} must be a non-empty string")
    if pattern:
        require(re.fullmatch(pattern, value) is not None, f"Invalid {label}: {value}")
    return value


def load_yaml(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
        yaml = YAML(typ="safe", pure=True)
        yaml.version = (1, 2)
        yaml.allow_duplicate_keys = False
        for event in yaml.parse(text):
            require(
                getattr(event, "version", None) in (None, (1, 2)),
                "Only YAML 1.2 is supported",
            )
            require(
                not isinstance(event, AliasEvent)
                and getattr(event, "anchor", None) is None
                and getattr(event, "tag", None) is None,
                "YAML aliases, anchors and tags are forbidden",
            )
        value = yaml.load(text)
        ensure_json(value)
        require(type(value) is dict, "Document must be an object")
        return value
    except GearError:
        raise
    except Exception as exc:
        raise GearError("INVALID_DOCUMENT", f"{path}: {exc}") from exc


def duration(value):
    require(type(value) is str, "Duration must be a string")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)", value)
    require(match is not None, f"Invalid duration: {value}")
    result = float(match[1]) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[match[2]]
    require(
        math.isfinite(result) and result > 0, "Duration must be finite and positive"
    )
    return result


def resource(value, wildcard=False):
    return string(
        value, "resource", TYPE + r"\.(" + ALIAS + (r"|\*" if wildcard else "") + r")"
    )


def _request(value, kind, wildcard):
    mapping(value, ("resource", kind), ("args",), kind)
    resource(value["resource"], wildcard)
    string(value[kind], kind, TYPE)
    value.setdefault("args", {})
    require(type(value["args"]) is dict, "args must be an object")


def _steps(steps, nested=False):
    require(type(steps) is list, "steps must be an array")
    for step in steps:
        require(
            type(step) is dict and len(step) == 1, "Step must have exactly one kind"
        )
        kind = next(iter(step))
        v = step[kind]
        if kind == "do":
            _request(v, "operation", False)
        elif kind == "wait":
            mapping(v, (), ("duration", "random"), "wait")
            require(len(v) == 1, "wait needs exactly duration or random")
            if "duration" in v:
                duration(v["duration"])
            else:
                r = mapping(v["random"], ("min", "max"), label="random")
                require(
                    duration(r["min"]) <= duration(r["max"]), "random min exceeds max"
                )
        elif kind == "assert":
            mapping(v, ("all",), ("within", "for", "every"), "assert")
            require(
                type(v["all"]) is list and bool(v["all"]),
                "all must be a non-empty array",
            )
            for item in v["all"]:
                _request(item, "condition", True)
            modes = set(v) & {"within", "for"}
            require(len(modes) <= 1, "within and for are mutually exclusive")
            require(
                ("every" in v) == bool(modes),
                "every is required only for temporal assertions",
            )
            for key in modes | ({"every"} if modes else set()):
                duration(v[key])
        elif kind == "repeat":
            require(not nested, "Nested repeat is forbidden")
            mapping(v, ("count", "steps"), label="repeat")
            require(
                type(v["count"]) is int and v["count"] > 0,
                "repeat count must be positive integer",
            )
            require(
                type(v["steps"]) is list and bool(v["steps"]),
                "repeat steps must not be empty",
            )
            _steps(v["steps"], True)
        else:
            raise GearError("INVALID_CONFIG", f"Unknown step {kind}")


def parse_case(data):
    ensure_json(data)
    v = copy.deepcopy(data)
    mapping(
        v,
        ("api", "name", "body"),
        ("description", "setup", "teardown", "evidence_on_fail"),
        "Test Case",
    )
    require(v["api"] == "gear.dsl/v1", "Unsupported DSL API")
    string(v["name"], "case name", ALIAS)
    if "description" in v:
        require(type(v["description"]) is str, "description must be string")
    require(type(v["body"]) is list and bool(v["body"]), "body must not be empty")
    for phase in ("setup", "body", "teardown"):
        v.setdefault(phase, [])
        _steps(v[phase])
    v.setdefault("evidence_on_fail", [])
    require(type(v["evidence_on_fail"]) is list, "evidence_on_fail must be an array")
    for item in v["evidence_on_fail"]:
        _request(item, "evidence", True)
    return v


def _resource_record(rid, record, environment):
    resource(rid)
    mapping(
        record,
        ("type", "plugin") if environment else ("type",),
        ("config", "device") if environment else (),
        rid,
    )
    string(record["type"], "resource type", TYPE)
    require(rid.split(".")[0] == record["type"], "Resource id prefix/type mismatch")
    if environment:
        string(record["plugin"], "plugin id", PLUGIN)
        record.setdefault("config", {})
        require(type(record["config"]) is dict, "resource config must be object")
        if "device" in record:
            string(record["device"], "device")


def parse_project(data):
    ensure_json(data)
    v = copy.deepcopy(data)
    mapping(v, ("api", "name", "resources"), label="Project")
    require(v["api"] == "gear.project/v1", "Unsupported Project API")
    string(v["name"], "Project name")
    require(
        type(v["resources"]) is dict and bool(v["resources"]),
        "Project resources must not be empty",
    )
    for rid, record in v["resources"].items():
        _resource_record(rid, record, False)
    return v


def parse_environment(data):
    ensure_json(data)
    v = copy.deepcopy(data)
    mapping(v, ("api", "name", "plugins", "resources"), ("devices",), "Environment")
    require(v["api"] == "gear.environment/v1", "Unsupported Environment API")
    string(v["name"], "Environment name")
    v.setdefault("devices", {})
    for key in ("plugins", "resources", "devices"):
        require(type(v[key]) is dict, f"{key} must be object")
    for device, meta in v["devices"].items():
        string(device, "device")
        require(meta == {}, "device metadata must be empty object")
    for pid, record in v["plugins"].items():
        string(pid, "plugin id", PLUGIN)
        mapping(record, (), ("config",), pid)
        record.setdefault("config", {})
        require(type(record["config"]) is dict, "plugin config must be object")
    for rid, record in v["resources"].items():
        _resource_record(rid, record, True)
        require(record["plugin"] in v["plugins"], "Resource owner missing from plugins")
    return v


def plugin_slice(environment, plugin_id, owns_adb=False):
    env = environment
    result = {
        "plugin": {
            "id": plugin_id,
            "config": copy.deepcopy(
                env["plugins"].get(plugin_id, {}).get("config", {})
            ),
        },
        "resources": {
            rid: {k: copy.deepcopy(v) for k, v in record.items() if k != "plugin"}
            for rid, record in env["resources"].items()
            if record["plugin"] == plugin_id
        },
    }
    if owns_adb:
        result["devices"] = copy.deepcopy(env.get("devices", {}))
    return result


def replace_slice(environment, plugin_id, replacement, owns_adb=False):
    ensure_json(replacement)
    r = copy.deepcopy(replacement)
    mapping(
        r,
        ("plugin", "resources", "devices") if owns_adb else ("plugin", "resources"),
        label="Plugin slice",
    )
    mapping(r["plugin"], ("id", "config"), label="Plugin record")
    require(
        r["plugin"]["id"] == plugin_id,
        "Plugin cannot replace another owner",
        "INVALID_SLICE",
    )
    require(
        type(r["plugin"]["config"]) is dict and type(r["resources"]) is dict,
        "Malformed slice",
        "INVALID_SLICE",
    )
    env = copy.deepcopy(environment)
    for rid in r["resources"]:
        require(
            rid not in env["resources"] or env["resources"][rid]["plugin"] == plugin_id,
            "Cannot steal another Plugin's resource",
            "INVALID_SLICE",
        )
    env["resources"] = {
        rid: v for rid, v in env["resources"].items() if v["plugin"] != plugin_id
    }
    for rid, record in r["resources"].items():
        mapping(record, ("type", "config"), ("device",), rid)
        env["resources"][rid] = {**record, "plugin": plugin_id}
    env["plugins"][plugin_id] = {"config": r["plugin"]["config"]}
    if owns_adb:
        env["devices"] = r["devices"]
    return parse_environment(env)
