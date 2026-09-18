"""Fixed-directory plugin discovery. Invoke load and close on the host worker."""

from __future__ import annotations
from dataclasses import dataclass
import importlib
import importlib.util
from pathlib import Path
import re
import sys
from jsonschema import Draft202012Validator
from gear_contracts.api import GearError
from .documents import load_yaml, mapping, require, string, TYPE, PLUGIN
from .common import exception_diagnostic

EMPTY_ARGS = {"type": "object", "additionalProperties": False}


@dataclass
class PluginEntry:
    id: str
    version: str
    manifest: dict
    runtime: object
    directory: Path
    workspace: str | None


def _schema(schema):
    require(
        type(schema) is dict and schema.get("type") == "object",
        "args_schema root must be object",
    )
    Draft202012Validator.check_schema(schema)

    def references(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("$ref", "$dynamicRef"):
                    require(
                        isinstance(item, str) and item.startswith("#"),
                        "Schema references must stay in the same document",
                    )
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    references(schema)


def _manifest(path):
    v = load_yaml(path)
    mapping(
        v, ("api", "id", "version", "entrypoints", "resource_types"), label="manifest"
    )
    require(v["api"] == "gear.plugin/v1", "Unsupported Plugin API")
    string(v["id"], "Plugin id", PLUGIN)
    string(v["version"], "Plugin version")
    mapping(v["entrypoints"], ("runtime",), ("workspace",), "entrypoints")
    roots = set()
    for value in v["entrypoints"].values():
        require(
            type(value) is str
            and re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", value)
            is not None,
            "Entrypoint must be module:factory",
        )
        roots.add(value.split(".")[0].split(":")[0])
    require(len(roots) == 1, "Runtime/Workspace must use the same unique package")
    package = next(iter(roots))
    require(
        (path.parent / package / "__init__.py").is_file(),
        "Entrypoint package must belong to the Plugin directory",
    )
    require(
        type(v["resource_types"]) is dict and bool(v["resource_types"]),
        "resource_types must not be empty",
    )
    for name, record in v["resource_types"].items():
        string(name, "Resource Type", TYPE)
        mapping(record, (), ("operations", "conditions", "evidence"), name)
        for kind in ("operations", "conditions", "evidence"):
            record.setdefault(kind, {})
            require(type(record[kind]) is dict, f"{kind} must be an object")
            for cap, definition in record[kind].items():
                string(cap, "Capability", TYPE)
                mapping(definition, (), ("args_schema",), cap)
                definition.setdefault("args_schema", dict(EMPTY_ARGS))
                _schema(definition["args_schema"])
    return v, package


class Registry:
    def __init__(self):
        self.entries = {}
        self.owners = {}
        self._paths = []
        self._packages = []

    @classmethod
    def load(cls, app_dir):
        registry = cls()
        plugins = Path(app_dir) / "plugins"
        if not plugins.exists():
            return registry
        try:
            candidates = []
            packages = set()
            ids = set()
            types = set()
            for directory in sorted(p for p in plugins.iterdir() if p.is_dir()):
                try:
                    manifest, package = _manifest(directory / "gear-plugin.yaml")
                    require(
                        package not in packages
                        and package not in sys.stdlib_module_names
                        and importlib.util.find_spec(package) is None,
                        f"Package name collision: {package}",
                    )
                    require(
                        manifest["id"] not in ids,
                        f"Duplicate Plugin id: {manifest['id']}",
                    )
                    require(
                        not types.intersection(manifest["resource_types"]),
                        "Duplicate Resource Type ownership",
                    )
                    packages.add(package)
                    ids.add(manifest["id"])
                    types.update(manifest["resource_types"])
                    candidates.append((directory, manifest, package))
                except Exception as exc:
                    raise GearError(
                        "PLUGIN_LOAD_FAILED", f"{directory.name}: {exc}"
                    ) from exc
            for directory, manifest, package in candidates:
                directory = directory.resolve()
                registry._paths.append(str(directory))
                registry._packages.append(package)
                sys.path.insert(0, str(directory))
                try:
                    name, factory = manifest["entrypoints"]["runtime"].split(":")
                    before = set(sys.modules)
                    runtime = getattr(importlib.import_module(name), factory)()
                    registry.entries[manifest["id"]] = PluginEntry(
                        manifest["id"],
                        manifest["version"],
                        manifest,
                        runtime,
                        directory,
                        manifest["entrypoints"].get("workspace"),
                    )
                    require(
                        not any(
                            m.startswith(("PySide6", "PyQt"))
                            for m in set(sys.modules) - before
                        ),
                        "Runtime must not import Qt",
                    )
                    for method in (
                        "configure",
                        "validate_config",
                        "begin_run",
                        "invoke",
                        "evaluate",
                        "collect",
                        "end_run",
                        "close",
                    ):
                        require(
                            callable(getattr(runtime, method, None)),
                            f"Runtime missing method {method}",
                        )
                    for typ in manifest["resource_types"]:
                        registry.owners[typ] = manifest["id"]
                except Exception as exc:
                    raise GearError(
                        "PLUGIN_LOAD_FAILED", f"{manifest['id']}: {exc}"
                    ) from exc
            return registry
        except Exception:
            registry.close()
            raise

    def close(self):
        errors = []
        for entry in reversed(list(self.entries.values())):
            try:
                entry.runtime.close()
            except Exception as exc:
                errors.append(
                    exception_diagnostic(
                        "PLUGIN_CLOSE_FAILED", exc, "close", plugin_id=entry.id
                    )
                )
        self.entries.clear()
        for path in self._paths:
            if path in sys.path:
                sys.path.remove(path)
        for package in self._packages:
            for name in list(sys.modules):
                if name == package or name.startswith(package + "."):
                    sys.modules.pop(name, None)
        self._paths.clear()
        self._packages.clear()
        return errors
