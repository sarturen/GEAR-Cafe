from copy import deepcopy
from pathlib import Path
import pytest
from gear_contracts.api import GearError, StopRequested
from helpers import Factory, context, slice_for, wait_until


@pytest.fixture
def bench(tmp_path):
    from gear_console.runtime import ConsoleRuntime

    factory = Factory()
    runtime = ConsoleRuntime(serial_factory=factory, cache_chars=24)
    data, ctx = slice_for(), context(tmp_path)
    runtime.configure(data)
    yield runtime, factory, data, ctx
    runtime.close()


def begin(runtime, data, ctx):
    runtime.begin_run(
        {"plugin_slice": data, "resource_ids": list(data["resources"])}, ctx
    )


def test_preflight_configure_and_begin_are_pure_and_archived_data_is_detached(bench):
    runtime, factory, data, ctx = bench
    assert runtime.validate_config(data)["status"] == "VALID"
    begin(runtime, data, ctx)
    data["resources"]["CONSOLE.mcu"]["config"]["port"] = "COM78"
    assert factory.items == []
    result = runtime.invoke("CONSOLE.mcu", "SEND", {"command": "hello"}, ctx)
    assert result["ok"] and result["details"]["code"] == "CONSOLE_SENT"
    assert factory.items[0].settings["port"] == "COM77"


def test_manual_connection_run_and_next_run_reuse_one_receiver_reset_observation(bench):
    runtime, factory, data, ctx = bench
    runtime.connect("CONSOLE.mcu")
    port = factory.items[0]
    port.rx.put(b"previous")
    wait_until(lambda: runtime.snapshot("CONSOLE.mcu")["text"] == "previous")
    begin(runtime, data, ctx)
    assert not runtime.evaluate(
        "CONSOLE.mcu", "OUTPUT_CONTAINS", {"text": "previous"}, ctx
    )["satisfied"]
    port.rx.put(b"current")
    wait_until(lambda: "current" in runtime.snapshot("CONSOLE.mcu")["text"])
    assert runtime.evaluate("CONSOLE.mcu", "OUTPUT_CONTAINS", {"text": "current"}, ctx)[
        "satisfied"
    ]
    runtime.end_run()
    assert port.close_count == 0
    ctx.run_id = "run2"
    begin(runtime, data, ctx)
    assert not runtime.evaluate(
        "CONSOLE.mcu", "OUTPUT_CONTAINS", {"text": "current"}, ctx
    )["satisfied"]
    assert len(factory.items) == 1 and port.open_count == 1


def test_connected_predicate_is_cached_and_observer_can_lazy_connect(bench):
    runtime, factory, data, ctx = bench
    begin(runtime, data, ctx)
    result = runtime.evaluate("CONSOLE.mcu", "CONNECTED", {}, ctx)
    assert result["ok"] and not result["satisfied"] and not factory.items
    result = runtime.evaluate("CONSOLE.mcu", "OUTPUT_CONTAINS", {"text": "ready"}, ctx)
    assert result["ok"] and not result["satisfied"]
    assert runtime.evaluate("CONSOLE.mcu", "CONNECTED", {}, ctx)["satisfied"]


def test_condition_error_is_not_a_valid_false_predicate(bench):
    runtime, factory, data, ctx = bench
    begin(runtime, data, ctx)
    runtime.invoke("CONSOLE.mcu", "SEND", {"command": "start"}, ctx)
    factory.items[0].read_error = OSError("device lost")
    wait_until(lambda: runtime.snapshot("CONSOLE.mcu")["fault"] is not None)
    result = runtime.evaluate("CONSOLE.mcu", "OUTPUT_CONTAINS", {"text": "ready"}, ctx)
    assert not result["ok"] and not result["satisfied"]
    assert result["diagnostic"]["code"] == "CONSOLE_IO_ERROR"


def test_cache_gap_is_reported_and_transcript_stays_within_artifact_dir(bench):
    runtime, factory, data, ctx = bench
    begin(runtime, data, ctx)
    runtime.invoke("CONSOLE.mcu", "SEND", {"command": "start"}, ctx)
    factory.items[0].rx.put(b"0123456789abcdefghijklmnopqrstuvwxyz")
    wait_until(lambda: runtime.snapshot("CONSOLE.mcu")["end"] == 36)
    result = runtime.evaluate("CONSOLE.mcu", "OUTPUT_CONTAINS", {"text": "lost"}, ctx)
    assert not result["ok"] and result["diagnostic"]["code"] == "CONSOLE_CACHE_GAP"
    result = runtime.collect("CONSOLE.mcu", "TRANSCRIPT", {}, ctx)
    assert result["ok"] and len(result["artifacts"]) == 1
    path = Path(result["artifacts"][0]["path"])
    if not path.is_absolute():
        path = Path(ctx.artifact_dir) / path
    assert path.resolve().is_relative_to(Path(ctx.artifact_dir).resolve())
    text = path.read_text(encoding="utf-8")
    assert "truncated" in text and "cdefghijklmnopqrstuvwxyz" in text


def test_stop_body_and_teardown_semantics(bench):
    runtime, factory, data, ctx = bench
    begin(runtime, data, ctx)
    ctx.stop_token.event.set()
    with pytest.raises(StopRequested):
        runtime.invoke("CONSOLE.mcu", "SEND", {"command": "stop"}, ctx)
    assert not factory.items
    ctx.phase = "teardown"
    assert runtime.invoke("CONSOLE.mcu", "SEND", {"command": "cleanup"}, ctx)["ok"]
    assert factory.items[0].writes == [b"cleanup\n"]


def test_config_edit_has_no_io_and_requires_explicit_disconnect_before_settings_change(
    bench,
):
    runtime, factory, data, ctx = bench
    runtime.connect("CONSOLE.mcu")
    data["resources"]["CONSOLE.mcu"]["config"]["baudrate"] = 9600
    runtime.configure(data)
    assert factory.items[0].close_count == 0
    with pytest.raises(GearError, match="CONSOLE_CONFIG_CHANGED"):
        runtime.send("CONSOLE.mcu", "hello")
    runtime.disconnect("CONSOLE.mcu")
    runtime.connect("CONSOLE.mcu")
    assert factory.items[-1].settings["baudrate"] == 9600


@pytest.mark.parametrize(
    "method,name,args",
    [
        ("invoke", "SEND", {}),
        ("invoke", "SEND", {"command": 3}),
        ("invoke", "OTHER", {}),
        ("evaluate", "OUTPUT_CONTAINS", {"text": ""}),
        ("evaluate", "CONNECTED", {"extra": 1}),
        ("collect", "OTHER", {}),
    ],
)
def test_defensive_capability_validation(bench, method, name, args):
    runtime, factory, data, ctx = bench
    begin(runtime, data, ctx)
    result = getattr(runtime, method)("CONSOLE.mcu", name, args, ctx)
    assert not result["ok"] and result["diagnostic"]["code"].startswith("CONSOLE_")
    assert factory.items == []


def test_active_private_manual_methods_are_blocked(bench):
    runtime, factory, data, ctx = bench
    begin(runtime, data, ctx)
    for call in (
        lambda: runtime.connect("CONSOLE.mcu"),
        lambda: runtime.disconnect("CONSOLE.mcu"),
        lambda: runtime.send("CONSOLE.mcu", "manual"),
        lambda: runtime.configure(data),
    ):
        with pytest.raises(GearError, match="CONSOLE_BUSY"):
            call()
    assert factory.items == []
