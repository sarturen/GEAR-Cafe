"""Deterministic demo only: no physical hardware or GUI dependencies."""

import json
from pathlib import Path
from gear_contracts.api import GearError


class Demo:
    def __init__(self):
        self.connected = False
        self.state = False
        self.context = None
        self.resources = set()

    def configure(self, plugin_slice):
        self.slice = plugin_slice

    def validate_config(self, plugin_slice):
        diagnostics = []
        if plugin_slice["plugin"]["config"] or any(
            r["config"] for r in plugin_slice["resources"].values()
        ):
            diagnostics = [
                {
                    "code": "UNSUPPORTED_CONFIG",
                    "message": "Demo accepts empty configuration only",
                    "details": {},
                }
            ]
        return {
            "status": "INVALID" if diagnostics else "VALID",
            "diagnostics": diagnostics,
        }

    def begin_run(self, binding, context):
        self.resources = set(binding["resource_ids"])
        self.context = context

    def _check(self, resource, args):
        if resource not in self.resources or args:
            raise GearError(
                "INVALID_CALL", "Use a bound demo resource with empty arguments"
            )
        self.connected = True

    def invoke(self, resource, operation, args, context):
        self._check(resource, args)
        if operation not in ("ON", "OFF"):
            raise GearError("UNKNOWN_CAPABILITY", operation)
        self.state = operation == "ON"
        return {"ok": True, "diagnostic": None, "details": {"state": self.state}}

    def evaluate(self, resource, condition, args, context):
        self._check(resource, args)
        if condition != "IS_ON":
            raise GearError("UNKNOWN_CAPABILITY", condition)
        return {"ok": True, "satisfied": self.state, "diagnostic": None, "details": {}}

    def collect(self, resource, evidence, args, context):
        self._check(resource, args)
        if evidence != "STATE":
            raise GearError("UNKNOWN_CAPABILITY", evidence)
        relative = f"evidence/gear.demo/state-{context.call_id}.json"
        path = Path(context.artifact_dir) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"state": self.state}), encoding="utf-8")
        return {
            "ok": True,
            "diagnostic": None,
            "artifacts": [
                {
                    "path": relative,
                    "media_type": "application/json",
                    "description": "Demo state",
                }
            ],
        }

    def end_run(self):
        self.context = None
        self.resources.clear()

    def close(self):
        self.end_run()
        self.connected = False


def create_plugin():
    return Demo()
