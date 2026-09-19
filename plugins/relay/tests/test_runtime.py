import copy
from types import SimpleNamespace

import pytest

from gear_contracts.api import GearError, StopRequested
from gear_relay.config import normalize_config, validate_slice
from gear_relay.runtime import RelayRuntime


def slice_data():
    return {
        "plugin": {"id": "gear.relay", "config": {"port": "COM77"}},
        "resources": {"POWER.main": {"type": "POWER", "config": {"channel": 1}}},
    }


class Service:
    def __init__(self):
        self.config = None
        self.connected = False
        self.states = [False] * 8
        self.opens = 0
        self.closes = 0
        self.writes = []
        self.reads = 0
        self.error = None

    def configure(self, config):
        if config != self.config:
            self.disconnect()
            self.config = copy.deepcopy(config)

    def open(self):
        if not self.connected:
            self.connected = True
            self.opens += 1

    def disconnect(self):
        if self.connected:
            self.closes += 1
        self.connected = False

    close = disconnect

    def snapshot(self):
        return {"connected": self.connected, "fault": None, "states": self.states[:]}

    def check(self, token):
        if token is not None and token.is_requested():
            raise StopRequested()
        if self.error:
            raise self.error
        self.open()

    def set_channel(self, channel, on, stop_token=None):
        self.check(stop_token)
        self.writes.append((channel, on))
        self.states[channel - 1] = on

    def set_all(self, on, stop_token=None):
        self.check(stop_token)
        self.writes.append(("all", on))
        self.states = [on] * 8

    def read_states(self, stop_token=None):
        self.check(stop_token)
        self.reads += 1
        return self.states[:]


def context(run_id="one", stopped=False, phase="body"):
    return SimpleNamespace(
        run_id=run_id,
        phase=phase,
        stop_token=SimpleNamespace(is_requested=lambda: stopped),
    )


def begin(runtime, data=None, ctx=None):
    data = data or slice_data()
    runtime.begin_run(
        {"plugin_slice": data, "resource_ids": ["POWER.main"]}, ctx or context()
    )


def test_empty_configuration_is_incomplete_and_never_opens():
    service = Service()
    runtime = RelayRuntime(service)
    empty = {"plugin": {"id": "gear.relay", "config": {}}, "resources": {}}
    runtime.configure(empty)
    assert runtime.validate_config(empty)["status"] == "INCOMPLETE"
    assert service.opens == 0
    runtime.close()


def test_config_defaults_are_explicit_and_port_is_normalized():
    assert normalize_config({"port": " com77 "}) == {
        "port": "COM77",
        "baudrate": 9600,
        "parity": "N",
        "stopbits": 1,
        "unit_id": 1,
        "timeout_s": 1.0,
        "poll_interval_ms": 500,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"port": "not-a-port"},
        {"port": 3},
        {"baudrate": True},
        {"baudrate": 0},
        {"parity": "X"},
        {"stopbits": 1.5},
        {"unit_id": 0},
        {"unit_id": 248},
        {"timeout_s": float("inf")},
        {"timeout_s": 0},
        {"unknown": 1},
    ],
)
def test_invalid_serial_configuration_is_rejected_without_io(change):
    data = slice_data()
    data["plugin"]["config"].update(change)
    assert validate_slice(data)["status"] == "INVALID"


@pytest.mark.parametrize("channel", [0, 9, True, "1"])
def test_invalid_channel_and_duplicate_channel_are_rejected(channel):
    data = slice_data()
    data["resources"]["POWER.main"]["config"]["channel"] = channel
    assert validate_slice(data)["status"] == "INVALID"


def test_duplicate_physical_channel_and_missing_channel_are_distinct():
    data = slice_data()
    data["resources"]["POWER.other"] = copy.deepcopy(data["resources"]["POWER.main"])
    assert validate_slice(data)["status"] == "INVALID"
    del data["resources"]["POWER.other"]
    data["resources"]["POWER.main"]["config"] = {}
    assert validate_slice(data)["status"] == "INCOMPLETE"


def test_manual_and_sequential_runs_reuse_session_connection():
    service = Service()
    runtime = RelayRuntime(service)
    runtime.configure(slice_data())
    runtime.connect()
    for run_id in ("one", "two"):
        ctx = context(run_id)
        begin(runtime, ctx=ctx)
        assert runtime.invoke("POWER.main", "ON", {}, ctx)["ok"]
        assert runtime.evaluate("POWER.main", "IS_ON", {}, ctx)["satisfied"]
        runtime.end_run()
        runtime.end_run()
        assert service.connected
    assert service.opens == 1
    assert service.reads == 2
    runtime.close()
    runtime.close()
    assert service.closes == 1
    assert service.writes == [(1, True), (1, True)]


def test_archived_channel_does_not_follow_later_mutation():
    runtime = RelayRuntime(Service())
    data = slice_data()
    runtime.configure(data)
    begin(runtime, data)
    data["resources"]["POWER.main"]["config"]["channel"] = 8
    result = runtime.invoke("POWER.main", "OFF", {}, context())
    assert result["ok"]
    assert runtime.service.writes == [(1, False)]
    runtime.close()


def test_unchanged_settings_keep_connection_but_invalid_settings_release_it():
    runtime = RelayRuntime(Service())
    data = slice_data()
    runtime.configure(data)
    runtime.connect()
    runtime.configure(copy.deepcopy(data))
    assert runtime.service.connected
    data["plugin"]["config"]["port"] = ""
    runtime.configure(data)
    assert not runtime.service.connected
    assert runtime.service.opens == 1


def test_conditions_read_current_state_and_protocol_errors_are_not_false_observations():
    runtime = RelayRuntime(Service())
    runtime.configure(slice_data())
    begin(runtime)
    result = runtime.evaluate("POWER.main", "IS_ON", {}, context())
    assert result["ok"] and not result["satisfied"]
    runtime.service.states[0] = True
    result = runtime.evaluate("POWER.main", "IS_ON", {}, context())
    assert result["ok"] and result["satisfied"]
    runtime.service.error = GearError("RELAY_CRC_ERROR", "bad CRC")
    result = runtime.evaluate("POWER.main", "IS_OFF", {}, context())
    assert not result["ok"] and not result["satisfied"]
    assert result["diagnostic"]["code"] == "RELAY_CRC_ERROR"
    runtime.close()


def test_stop_skips_new_body_command_but_teardown_can_release_relay():
    runtime = RelayRuntime(Service())
    runtime.configure(slice_data())
    begin(runtime)
    with pytest.raises(StopRequested):
        runtime.invoke("POWER.main", "ON", {}, context(stopped=True))
    assert runtime.service.writes == []
    result = runtime.invoke(
        "POWER.main", "OFF", {}, context(stopped=True, phase="teardown")
    )
    assert result["ok"]
    assert runtime.service.writes == [(1, False)]
    runtime.close()


@pytest.mark.parametrize(
    "kind,value,args",
    [
        ("invoke", "UNKNOWN", {}),
        ("invoke", "ON", {"extra": 1}),
        ("evaluate", "UNKNOWN", {}),
        ("evaluate", "IS_ON", {"extra": 1}),
    ],
)
def test_invalid_direct_capabilities_do_not_touch_service(kind, value, args):
    runtime = RelayRuntime(Service())
    runtime.configure(slice_data())
    begin(runtime)
    with pytest.raises(GearError):
        getattr(runtime, kind)("POWER.main", value, args, context())
    assert runtime.service.opens == 0
    runtime.close()


def test_out_of_run_or_unbound_call_is_rejected():
    runtime = RelayRuntime(Service())
    runtime.configure(slice_data())
    with pytest.raises(GearError):
        runtime.invoke("POWER.main", "ON", {}, context())
    begin(runtime)
    with pytest.raises(GearError):
        runtime.invoke("POWER.other", "ON", {}, context())
    with pytest.raises(GearError):
        runtime.invoke("POWER.main", "ON", {}, context("wrong-run"))
    assert runtime.service.opens == 0
    runtime.close()


def test_manual_all_channels_and_disconnect_return_contract_values():
    runtime = RelayRuntime(Service())
    runtime.configure(slice_data())
    assert runtime.set_all(True)["states"] == [True] * 8
    assert runtime.set_channel(8, False)["states"] == [True] * 7 + [False]
    assert runtime.read_states()["states"] == [True] * 7 + [False]
    assert runtime.disconnect()["connected"] is False
    runtime.close()
