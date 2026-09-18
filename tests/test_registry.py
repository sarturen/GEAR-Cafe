import json
import pytest
from gear_contracts.api import GearError
from gear_framework.registry import Registry


def test_discovery_creates_once_without_configuring(bench):
    r = Registry.load(bench["app"])
    try:
        assert r.owners == {"SCREEN": "gear.demo"}
        p = r.entries["gear.demo"].runtime
        assert p.calls == []
        assert p.open_count == 0
    finally:
        r.close()
    assert p.calls == ["close"]


def test_duplicate_type_and_remote_schema_rejected(bench):
    manifest = bench["manifest"]
    manifest["resource_types"]["SCREEN"]["operations"]["ON"] = {
        "args_schema": {"type": "object", "$ref": "https://example.com/schema"}
    }
    (bench["directory"] / "gear-plugin.yaml").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(GearError):
        Registry.load(bench["app"])


def test_missing_manifest_is_explicit_error(tmp_path):
    (tmp_path / "plugins" / "broken").mkdir(parents=True)
    with pytest.raises(GearError, match="broken"):
        Registry.load(tmp_path)
