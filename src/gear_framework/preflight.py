"""Resolve declared capabilities and configuration; never probe devices."""

from __future__ import annotations
from dataclasses import dataclass
import copy
from jsonschema import Draft202012Validator
from gear_contracts.api import GearError
from .documents import parse_case, parse_project, parse_environment, plugin_slice
from .common import diagnostic, exception_diagnostic, check_diagnostic, ensure_json


@dataclass
class PreparedRun:
    case: dict
    report: dict
    requests: dict
    slices: dict
    resource_ids: dict


def _requests(case):
    def steps(items, prefix):
        for i, step in enumerate(items):
            path = f"{prefix}/{i}"
            kind, v = next(iter(step.items()))
            if kind == "do":
                yield path + "/do", "operation", v
            elif kind == "assert":
                for n, item in enumerate(v["all"]):
                    yield f"{path}/assert/all/{n}", "condition", item
            elif kind == "repeat":
                yield from steps(v["steps"], path + "/repeat/steps")

    for phase in ("setup", "body", "teardown"):
        yield from steps(case[phase], "/" + phase)
    for i, item in enumerate(case["evidence_on_fail"]):
        yield f"/evidence_on_fail/{i}", "evidence", item


def prepare(case, project, environment, registry):
    report = {"ok": False, "diagnostics": [], "bindings": {}, "coverage": []}
    prepared = PreparedRun({}, report, {}, {}, {})
    try:
        case, project, env = (
            parse_case(case),
            parse_project(project),
            parse_environment(environment),
        )
        prepared.case = case
    except Exception as exc:
        report["diagnostics"].append(
            exception_diagnostic("INVALID_CONFIG", exc, "parse")
        )
        return prepared

    def reject(code, message, path):
        d = diagnostic(code, message)
        d["path"] = path
        report["diagnostics"].append(d)

    for source, kind, item in _requests(case):
        ref = item["resource"]
        typ = ref.split(".")[0]
        ids = (
            sorted(r for r, v in project["resources"].items() if v["type"] == typ)
            if ref.endswith(".*")
            else [ref]
        )
        prepared.requests[source] = []
        pid = registry.owners.get(typ)
        schema = None
        if pid:
            group = {
                "operation": "operations",
                "condition": "conditions",
                "evidence": "evidence",
            }[kind]
            definition = (
                registry.entries[pid]
                .manifest["resource_types"][typ][group]
                .get(item[kind])
            )
            if definition is not None:
                schema = definition["args_schema"]
        if schema is None:
            reject(
                "UNKNOWN_CAPABILITY",
                f"{typ} does not declare {kind} {item[kind]}",
                source,
            )
        else:
            try:
                errors = list(Draft202012Validator(schema).iter_errors(item["args"]))
                for error in errors:
                    reject("INVALID_ARGUMENTS", error.message, source)
            except Exception as exc:
                reject("INVALID_ARGUMENTS", str(exc), source)
        for rid in ids:
            bound = False
            if rid not in project["resources"]:
                reject("UNKNOWN_RESOURCE", f"{rid} is absent from Project", source)
            elif rid in env["resources"]:
                record = env["resources"][rid]
                if record["type"] != typ or record["plugin"] != pid:
                    reject(
                        "INVALID_BINDING",
                        f"{rid} has wrong type or Plugin owner",
                        source,
                    )
                elif (
                    record.get("device") is not None
                    and record["device"] not in env["devices"]
                ):
                    reject(
                        "DANGLING_DEVICE", f"{rid} refers to undeclared device", source
                    )
                else:
                    bound = True
                    report["bindings"][rid] = copy.deepcopy(record)
                    prepared.requests[source].append(
                        {
                            "resource_id": rid,
                            "plugin_id": pid,
                            "kind": kind,
                            "capability": item[kind],
                            "args": copy.deepcopy(item["args"]),
                        }
                    )
                    prepared.resource_ids.setdefault(pid, set()).add(rid)
            elif not ref.endswith(".*"):
                reject("MISSING_BINDING", f"{rid} is not bound", source)
            report["coverage"].append(
                {
                    "source": source,
                    "resource_id": rid,
                    "kind": kind,
                    "capability": item[kind],
                    "bound": bound,
                    "executed": False,
                }
            )
        if ref.endswith(".*") and not prepared.requests[source]:
            reject("ZERO_BINDINGS", f"{ref} has no valid bound resources", source)
    for pid in sorted(prepared.resource_ids):
        prepared.resource_ids[pid] = sorted(prepared.resource_ids[pid])
        slice = plugin_slice(env, pid, registry.owners.get("ADB") == pid)
        prepared.slices[pid] = slice
        try:
            result = registry.entries[pid].runtime.validate_config(copy.deepcopy(slice))
            ensure_json(result)
            if (
                type(result) is not dict
                or set(result) != {"status", "diagnostics"}
                or result["status"] not in ("VALID", "INCOMPLETE", "INVALID")
                or type(result["diagnostics"]) is not list
            ):
                raise GearError("INVALID_RESULT", "Malformed ValidationReport")
            for d in result["diagnostics"]:
                check_diagnostic(d)
            if result["status"] != "VALID":
                report["diagnostics"].extend(copy.deepcopy(result["diagnostics"]))
                reject(
                    "PLUGIN_CONFIG_INVALID",
                    f"{pid}: {result['status']}",
                    "/plugins/" + pid,
                )
        except Exception as exc:
            report["diagnostics"].append(
                exception_diagnostic(
                    "PLUGIN_VALIDATION_FAILED", exc, "validate_config", plugin_id=pid
                )
            )
    report["ok"] = not report["diagnostics"]
    return prepared
