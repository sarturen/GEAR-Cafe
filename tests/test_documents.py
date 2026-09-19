import copy
import pytest
from gear_contracts.api import GearError
from gear_framework.documents import (
    load_yaml,
    parse_case,
    parse_environment,
    parse_project,
    duration,
    plugin_slice,
    replace_slice,
)


def test_yaml_11_directive_is_rejected(tmp_path):
    path = tmp_path / "wrong-version.yaml"
    path.write_text("%YAML 1.1\n---\nkey: OFF\n", encoding="utf-8")
    with pytest.raises(GearError):
        load_yaml(path)


def test_yaml_12_and_duplicate_or_unsafe_features(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("ON: OFF\ntruth: true\n", encoding="utf-8")
    assert load_yaml(p) == {"ON": "OFF", "truth": True}
    for text in (
        "a: 1\na: 2",
        "a: &x 1\nb: *x",
        "a: !!str hi",
        "a: .nan",
        "a: 2026-09-18",
        "1: value",
        "---\na: 1\n---\nb: 2",
    ):
        p.write_text(text, encoding="utf-8")
        with pytest.raises(GearError):
            load_yaml(p)


def test_case_defaults_and_grammar():
    c = parse_case(
        {"api": "gear.dsl/v1", "name": "a", "body": [{"wait": {"duration": "1ms"}}]}
    )
    assert c["setup"] == [] and c["evidence_on_fail"] == []
    assert duration("1.5m") == 90
    for value in ("0s", "-1s", "1d", "infms", True, " 1s"):
        with pytest.raises(GearError):
            duration(value)
    for step in (
        {"wait": {"duration": "1s", "random": {"min": "1s", "max": "2s"}}},
        {
            "assert": {
                "all": [{"resource": "SCREEN.a", "condition": "ON"}],
                "every": "1s",
            }
        },
        {"do": {"resource": "POWER.*", "operation": "ON"}},
        {"repeat": {"count": True, "steps": [{"wait": {"duration": "1s"}}]}},
        {
            "repeat": {
                "count": 1,
                "steps": [
                    {"repeat": {"count": 1, "steps": [{"wait": {"duration": "1s"}}]}}
                ],
            }
        },
    ):
        with pytest.raises(GearError):
            parse_case({"api": "gear.dsl/v1", "name": "a", "body": [step]})


def test_slice_is_detached_preserves_other_plugins_and_rejects_stealing():
    env = parse_environment(
        {
            "api": "gear.environment/v1",
            "name": "bench",
            "plugins": {"gear.a": {}, "gear.b": {}},
            "devices": {"serial": {}},
            "resources": {
                "A.one": {"type": "A", "plugin": "gear.a", "config": {"x": 1}},
                "B.two": {"type": "B", "plugin": "gear.b"},
            },
        }
    )
    before = copy.deepcopy(env)
    a = plugin_slice(env, "gear.a")
    a["resources"]["A.one"]["config"]["x"] = 2
    out = replace_slice(env, "gear.a", a)
    assert env == before and out["resources"]["B.two"] == env["resources"]["B.two"]
    assert out["resources"]["A.one"]["config"]["x"] == 2
    a["devices"] = {}
    with pytest.raises(GearError):
        replace_slice(env, "gear.a", a)
    del a["devices"]
    a["resources"]["B.two"] = {"type": "B", "config": {}}
    with pytest.raises(GearError):
        replace_slice(env, "gear.a", a)


def test_project_and_environment_envelopes():
    with pytest.raises(GearError):
        parse_project({"api": "gear.project/v1", "name": "p", "resources": {}})
    with pytest.raises(GearError):
        parse_environment(
            {
                "api": "gear.environment/v1",
                "name": "e",
                "plugins": {},
                "resources": {},
                "typo": 1,
            }
        )
    env = parse_environment(
        {"api": "gear.environment/v1", "name": "e", "plugins": {}, "resources": {}}
    )
    assert plugin_slice(env, "gear.new", True) == {
        "plugin": {"id": "gear.new", "config": {}},
        "resources": {},
        "devices": {},
    }
