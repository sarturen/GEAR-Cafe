"""Confirmed physical-controller and shared-board model; no device access."""

from copy import deepcopy
from types import SimpleNamespace
import threading
import time

import pytest
from gear_contracts.api import GearError
from gear_relay import bindings
from gear_relay.config import validate_slice
from gear_relay.runtime import RelayRuntime
from gear_relay.transport import RelayService, crc16


def data():
    return {
        "plugin": {
            "id": "gear.relay",
            "config": {
                "controllers": {
                    "front": {"port": "COM77", "poll_interval_ms": 0},
                    "rear": {"port": "COM78", "poll_interval_ms": 0},
                }
            },
        },
        "resources": {},
    }


def resource(controller="front", channel=1, device="board-serial", role="KL30"):
    return {
        "type": "POWER",
        "device": device,
        "config": {"controller": controller, "channel": channel, "role": role},
    }


def test_same_channel_on_two_independent_controllers_is_valid():
    saved = data()
    saved["resources"] = {
        "POWER.a": resource(),
        "POWER.b": resource("rear", role="reset"),
    }
    assert validate_slice(saved)["status"] == "VALID"
    saved["resources"]["POWER.b"]["config"]["controller"] = "front"
    assert validate_slice(saved)["status"] == "INVALID"


def test_device_role_unique_across_controllers_but_missing_role_allowed():
    saved = data()
    saved["resources"] = {"POWER.a": resource(), "POWER.b": resource("rear")}
    assert "RELAY_ROLE_DUPLICATE" in {
        d["code"] for d in validate_slice(saved)["diagnostics"]
    }
    saved["resources"]["POWER.b"]["config"].pop("role")
    assert validate_slice(saved)["status"] == "VALID"


def test_duplicate_com_cannot_open_two_services():
    saved = data()
    saved["plugin"]["config"]["controllers"]["rear"]["port"] = " com77 "
    assert "RELAY_PORT_DUPLICATE" in {
        d["code"] for d in validate_slice(saved)["diagnostics"]
    }


def test_legacy_placeholders_remain_incomplete_and_visible_until_explicit_removal():
    saved = {
        "plugin": {"id": "gear.relay", "config": {"port": "COM77"}},
        "resources": {
            "POWER.live": {"type": "POWER", "config": {"channel": 1}},
            "POWER.old": {
                "type": "POWER",
                "config": {"device_name": "old name", "terminal": "KL15"},
            },
        },
    }
    report = validate_slice(saved)
    assert report["status"] == "INCOMPLETE"
    assert any(d["code"] == "RELAY_CONFIG_INCOMPLETE" for d in report["diagnostics"])
    del saved["resources"]["POWER.old"]
    assert validate_slice(saved)["status"] == "VALID"


def test_assign_physical_channel_preserves_id_device_and_only_selected_record():
    saved = data()
    saved["resources"]["POWER.old"] = {
        "type": "POWER",
        "device": "board-serial",
        "config": {"channel": 2, "device_name": "not-a-serial", "terminal": "KL30"},
    }
    saved["plugin"]["config"] = {"port": "COM77"}
    bindings.migrate_controllers(saved)
    assert saved["resources"]["POWER.old"]["device"] == "board-serial"
    assert saved["resources"]["POWER.old"]["config"]["device_name"] == "not-a-serial"
    rid = bindings.assign_channel(saved, "main", 2, "board-serial", "reset")
    assert rid == "POWER.old"
    assert saved["resources"][rid] == resource("main", 2, role="reset")
    assert len(saved["resources"]) == 1


def test_new_assignment_does_not_create_other_roles_or_invent_device():
    saved = data()
    rid = bindings.assign_channel(saved, "front", 3, "board-serial", "custom")
    assert saved["resources"] == {rid: resource("front", 3, role="custom")}
    assert "devices" not in saved
    bindings.unassign_resource(saved, rid)
    assert saved["resources"][rid] == {
        "type": "POWER",
        "config": {"controller": "front", "channel": 3},
    }


def test_duplicate_assignment_rejected_atomically():
    saved = data()
    bindings.assign_channel(saved, "front", 1, "board-serial", "KL30")
    before = deepcopy(saved)
    with pytest.raises(GearError):
        bindings.assign_channel(saved, "rear", 2, "board-serial", "KL30")
    assert saved == before


class SimulatedSerial:
    def __init__(self, **config):
        self.config = config
        self.rx = b""
        self.states = [False] * 8
        self.frames = []
        self.opens = self.closes = 0
        self.overlap = False
        self.in_transaction = False
        self.failed = False

    def open(self):
        self.opens += 1

    def close(self):
        self.closes += 1

    def write(self, frame, timeout_s=None):
        self.overlap |= self.in_transaction
        self.in_transaction = True
        self.frames.append(frame)
        if self.failed:
            raise OSError("simulated disconnect")
        if frame[1] == 5:
            self.states[int.from_bytes(frame[2:4], "big")] = frame[4] == 255
            self.rx = frame
        elif frame[1] == 1:
            body = bytes([1, 1, 1, sum(int(v) << i for i, v in enumerate(self.states))])
            self.rx = body + crc16(body).to_bytes(2, "little")
        else:
            raise AssertionError(frame)
        time.sleep(0.001)
        return len(frame)

    def read(self, count, timeout_s=None):
        answer, self.rx = self.rx[:count], self.rx[count:]
        if not self.rx:
            self.in_transaction = False
        return answer


def runtime_with_serials():
    serials = []

    def factory(**config):
        serial = SimulatedSerial(**config)
        serials.append(serial)
        return serial

    runtime = RelayRuntime(service_factory=lambda: RelayService(serial_factory=factory))
    return runtime, serials


def context():
    return SimpleNamespace(
        run_id="one",
        phase="body",
        stop_token=SimpleNamespace(is_requested=lambda: False),
    )


def test_runtime_routes_archived_controller_keeps_others_connected():
    runtime, serials = runtime_with_serials()
    saved = data()
    saved["resources"] = {
        "POWER.a": resource(),
        "POWER.b": resource("rear", role="reset"),
    }
    runtime.configure(saved)
    assert serials == []
    runtime.connect("front")
    runtime.connect("rear")
    runtime.begin_run(
        {"plugin_slice": saved, "resource_ids": ["POWER.a", "POWER.b"]}, context()
    )
    saved["resources"]["POWER.b"]["config"]["controller"] = "front"
    assert runtime.invoke("POWER.b", "ON", {}, context())["ok"]
    assert serials[1].states[0] and not serials[0].states[0]
    assert runtime.snapshot("rear")["states"][0] is True
    runtime.end_run()
    saved["plugin"]["config"]["controllers"]["front"]["port"] = "COM79"
    runtime.configure(saved)
    assert serials[0].closes == 1 and serials[1].closes == 0
    runtime.close()
    assert serials[1].closes == 1


def test_polling_continues_during_run_serializes_io_and_stops_on_fault_and_close():
    runtime, serials = runtime_with_serials()
    saved = data()
    saved["plugin"]["config"]["controllers"]["front"]["poll_interval_ms"] = 20
    saved["resources"] = {"POWER.a": resource()}
    runtime.configure(saved)
    runtime.connect("front")
    runtime.begin_run({"plugin_slice": saved, "resource_ids": ["POWER.a"]}, context())
    try:
        with pytest.raises(GearError):
            runtime.set_channel(1, True, "front")
        for _ in range(10):
            assert runtime.invoke("POWER.a", "ON", {}, context())["ok"]
            assert runtime.invoke("POWER.a", "OFF", {}, context())["ok"]
        deadline = time.monotonic() + 1
        while (
            not any(f[1] == 1 for f in serials[0].frames)
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert any(f[1] == 1 for f in serials[0].frames)
        assert not serials[0].overlap
        serials[0].failed = True
        deadline = time.monotonic() + 1
        while (
            runtime.snapshot("front")["fault"] is None and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert runtime.snapshot("front")["fault"]
        count = len(serials[0].frames)
        time.sleep(0.06)
        assert len(serials[0].frames) == count and serials[0].opens == 1
    finally:
        runtime.close()
    count = len(serials[0].frames)
    time.sleep(0.04)
    assert len(serials[0].frames) == count
