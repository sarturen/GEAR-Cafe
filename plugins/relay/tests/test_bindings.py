import copy
import re

import pytest

from gear_contracts.api import GearError
from gear_relay import bindings
from gear_relay.config import validate_slice
from gear_relay.runtime import RelayRuntime


def data():
    return {
        "plugin": {"id": "gear.relay", "config": {"port": "COM77"}},
        "resources": {},
    }


def resource(channel=None, name=None, terminal=None, **extra):
    config = {}
    if channel is not None:
        config["channel"] = channel
    if name is not None:
        config["device_name"] = name
    if terminal is not None:
        config["terminal"] = terminal
    return {"type": "POWER", "config": config, **extra}


@pytest.mark.parametrize("channel", [0, 9, True, "1", None])
def test_bad_physical_channel_does_not_add_resources(channel):
    saved = data()
    before = copy.deepcopy(saved)
    with pytest.raises(GearError):
        bindings.assign_channel(saved, "main", channel, "board", "role")
    assert saved == before


def test_duplicate_channel_mapping_cannot_be_silently_adopted():
    saved = data()
    saved["resources"] = {"POWER.a": resource(1), "POWER.b": resource(1)}
    before = copy.deepcopy(saved)
    with pytest.raises(GearError):
        bindings.assign_channel(saved, "main", 1, "board", "KL30")
    assert saved == before


def test_move_resource_preserves_alias_and_other_records():
    saved = data()
    saved["resources"] = {
        "POWER.old": resource(1, device="board"),
        "POWER.other": resource(8),
    }
    bindings.assign_channel(saved, "main", 3, "board", "KL15", "POWER.old")
    assert saved["resources"]["POWER.old"] == {
        "type": "POWER",
        "device": "board",
        "config": {"controller": "main", "channel": 3, "role": "KL15"},
    }
    assert saved["resources"]["POWER.other"] == resource(8)
    before = copy.deepcopy(saved)
    with pytest.raises(GearError):
        bindings.assign_channel(saved, "main", 8, "board", "KL15", "POWER.old")
    assert saved == before


def test_grouping_uses_top_level_device_and_optional_roles_only():
    resources = {
        "POWER.a": resource(1, "private-name", "KL30", device="serial"),
        "POWER.b": resource(2, "private-name", "KL15"),
        "POWER.c": resource(3, device="serial"),
    }
    assert bindings.grouped_resources(resources) == {
        "serial": {"KL30": ["POWER.a"], "未指定用途": ["POWER.c"]}
    }


def test_migration_read_validation_never_modifies_original_or_promotes_private_name():
    saved = data()
    saved["resources"]["POWER.a"] = resource(1, "private-name", "KL30")
    before = copy.deepcopy(saved)
    assert validate_slice(saved)["status"] == "VALID"
    assert saved == before
    bindings.migrate_controllers(saved)
    assert "device" not in saved["resources"]["POWER.a"]
    assert saved["resources"]["POWER.a"]["config"]["device_name"] == "private-name"


class ManualService:
    def __init__(self):
        self.writes = []
        self.configures = 0

    def configure(self, config):
        self.configures += 1

    def set_channel(self, channel, on):
        self.writes.append((channel, on))

    def snapshot(self):
        return {"connected": True, "fault": None, "states": [None] * 8}


def test_manual_resource_control_uses_current_saved_channel():
    saved = data()
    saved["resources"]["POWER.a"] = resource(2, "独立设备", "KL30")
    service = ManualService()
    runtime = RelayRuntime(service)
    runtime.configure(saved)
    runtime.set_resource("POWER.a", True)
    saved["resources"]["POWER.a"]["config"]["channel"] = 8
    runtime.configure(saved)
    runtime.set_resource("POWER.a", False)
    assert service.writes == [(2, True), (8, False)]


@pytest.mark.parametrize(
    "rid,on,record",
    [
        ("POWER.missing", True, resource(1)),
        ("POWER.a", 1, resource(1)),
        ("POWER.a", False, resource()),
        ("POWER.a", True, {"type": "OTHER", "config": {"channel": 1}}),
        ("POWER.a", True, resource(True)),
    ],
)
def test_invalid_manual_targets_rejected_before_service_access(rid, on, record):
    saved = data()
    saved["resources"]["POWER.a"] = record
    service = ManualService()
    runtime = RelayRuntime(service)
    runtime.configure(saved)
    before = service.configures
    with pytest.raises(GearError):
        runtime.set_resource(rid, on)
    assert service.writes == []
    assert service.configures == before
