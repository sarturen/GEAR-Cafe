import json
from gear_framework.documents import load_yaml
from gear_framework.registry import Registry
from gear_framework.preflight import prepare


def test_wildcards_preserve_gaps_and_no_hardware_calls(bench):
    registry = Registry.load(bench["app"])
    try:
        case = {
            "api": "gear.dsl/v1",
            "name": "wild",
            "body": [
                {"assert": {"all": [{"resource": "SCREEN.*", "condition": "LIT"}]}}
            ],
        }
        p = prepare(
            case, load_yaml(bench["project"]), load_yaml(bench["environment"]), registry
        )
        assert p.report["ok"]
        assert [(c["resource_id"], c["bound"]) for c in p.report["coverage"]] == [
            ("SCREEN.a", True),
            ("SCREEN.b", False),
        ]
        assert registry.entries["gear.demo"].runtime.calls == ["validate"]
        assert len(p.requests["/body/0/assert/all/0"]) == 1
    finally:
        registry.close()


def test_missing_concrete_and_zero_bound_wildcard_rejected(bench):
    r = Registry.load(bench["app"])
    try:
        env = load_yaml(bench["environment"])
        env["resources"] = {}
        for resource in ("SCREEN.a", "SCREEN.*"):
            case = {
                "api": "gear.dsl/v1",
                "name": "x",
                "body": [
                    {"assert": {"all": [{"resource": resource, "condition": "LIT"}]}}
                ],
            }
            p = prepare(case, load_yaml(bench["project"]), env, r)
            assert not p.report["ok"]
    finally:
        r.close()
