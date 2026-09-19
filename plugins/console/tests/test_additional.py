from copy import deepcopy
import threading
import time
import pytest
from gear_contracts.api import GearError
from helpers import Factory, context, slice_for, wait_until


def test_two_ports_receive_independently_and_keep_connections_between_runs(tmp_path):
    from gear_console.runtime import ConsoleRuntime

    factory = Factory()
    runtime = ConsoleRuntime(serial_factory=factory)
    data = slice_for()
    data["resources"]["CONSOLE.soc"] = {
        "type": "CONSOLE",
        "device": "ADB001",
        "config": {"port": "COM78", "role": "SOC"},
    }
    runtime.configure(data)
    try:
        runtime.connect("CONSOLE.mcu")
        runtime.connect("CONSOLE.soc")
        factory.items[0].rx.put(b"MCU output")
        factory.items[1].rx.put(b"SOC output")
        wait_until(
            lambda: runtime.snapshot("CONSOLE.mcu")["text"] == "MCU output"
            and runtime.snapshot("CONSOLE.soc")["text"] == "SOC output"
        )
        assert len({p.threads[0] for p in factory.items}) == 2
        runtime.begin_run(
            {"plugin_slice": data, "resource_ids": list(data["resources"])},
            context(tmp_path),
        )
        runtime.end_run()
        assert all(p.close_count == 0 for p in factory.items)
    finally:
        runtime.close()
    assert all(p.close_count == 1 for p in factory.items)


@pytest.mark.parametrize(
    "encoding,ending,wanted",
    [
        ("utf-8", "CR", "你好\r".encode("utf-8")),
        ("gbk", "CRLF", "你好\r\n".encode("gbk")),
    ],
)
def test_configured_encoding_and_line_endings(encoding, ending, wanted):
    from gear_console.service import PortService

    factory = Factory()
    service = PortService(
        {"port": "COM77", "role": "MCU", "encoding": encoding, "line_ending": ending},
        serial_factory=factory,
    )
    try:
        service.connect()
        service.send("你好")
        assert factory.items[0].writes == [wanted]
        data = "你好".encode(encoding)
        for byte in data:
            factory.items[0].rx.put(bytes([byte]))
        wait_until(lambda: service.snapshot()["text"] == "你好")
    finally:
        service.disconnect()


def test_operation_short_write_has_stable_diagnostic_and_no_automatic_resend(tmp_path):
    from gear_console.runtime import ConsoleRuntime

    factory = Factory()
    runtime = ConsoleRuntime(serial_factory=factory)
    data = slice_for()
    ctx = context(tmp_path)
    runtime.configure(data)
    runtime.connect("CONSOLE.mcu")
    factory.items[0].short_write = True
    runtime.begin_run({"plugin_slice": data, "resource_ids": ["CONSOLE.mcu"]}, ctx)
    try:
        result = runtime.invoke("CONSOLE.mcu", "SEND", {"command": "abc"}, ctx)
        assert (
            not result["ok"] and result["diagnostic"]["code"] == "CONSOLE_SHORT_WRITE"
        )
        assert factory.items[0].writes == [b"abc\n"]
    finally:
        runtime.close()


def test_evidence_returns_relative_path_in_contract_plugin_directory(tmp_path):
    from gear_console.runtime import ConsoleRuntime

    runtime = ConsoleRuntime(serial_factory=Factory())
    data = slice_for()
    ctx = context(tmp_path)
    runtime.configure(data)
    runtime.begin_run({"plugin_slice": data, "resource_ids": ["CONSOLE.mcu"]}, ctx)
    try:
        result = runtime.collect("CONSOLE.mcu", "TRANSCRIPT", {}, ctx)
        assert result["ok"]
        path = result["artifacts"][0]["path"]
        assert path.startswith("evidence/gear.console/") and "\\" not in path
        assert (tmp_path / path).is_file()
    finally:
        runtime.close()


def test_changing_com_requires_disconnect_of_previous_live_port():
    from gear_console.runtime import ConsoleRuntime

    factory = Factory()
    runtime = ConsoleRuntime(serial_factory=factory)
    data = slice_for()
    runtime.configure(data)
    runtime.connect("CONSOLE.mcu")
    data["resources"]["CONSOLE.mcu"]["config"]["port"] = "COM78"
    runtime.configure(data)
    try:
        with pytest.raises(GearError, match="CONSOLE_CONFIG_CHANGED"):
            runtime.connect("CONSOLE.mcu")
        assert len(factory.items) == 1
        runtime.disconnect("CONSOLE.mcu")
        runtime.connect("CONSOLE.mcu")
        assert factory.items[0].close_count == 1
        assert factory.items[1].settings["port"] == "COM78"
    finally:
        runtime.close()


def test_stop_waits_for_inflight_write_to_finish_before_returning():
    from gear_console.service import PortService
    from gear_contracts.api import StopRequested
    from helpers import FakeSerial, Token

    entered, release, returned = threading.Event(), threading.Event(), threading.Event()

    class BlockingWrite(FakeSerial):
        def write(self, data, timeout_s=None):
            entered.set()
            release.wait(2)
            return super().write(data, timeout_s)

    port = BlockingWrite()
    service = PortService(
        {"port": "COM77", "role": "MCU"}, serial_factory=lambda **kw: port
    )
    token, errors = Token(), []
    service.connect()

    def send():
        try:
            service.send("command", token)
        except BaseException as exc:
            errors.append(exc)
        finally:
            returned.set()

    thread = threading.Thread(target=send)
    thread.start()
    try:
        assert entered.wait(1)
        token.event.set()
        assert not returned.wait(
            0.1
        ), "A Run must not finish while its write is still active"
        release.set()
        assert returned.wait(1)
        assert len(errors) == 1 and isinstance(errors[0], StopRequested)
        assert port.writes == [b"command\n"]
    finally:
        release.set()
        thread.join(2)
        service.disconnect()


def test_incomplete_pre_run_multibyte_character_does_not_leak_into_run(tmp_path):
    from gear_console.runtime import ConsoleRuntime

    factory = Factory()
    runtime = ConsoleRuntime(serial_factory=factory)
    data = slice_for()
    ctx = context(tmp_path)
    runtime.configure(data)
    runtime.connect("CONSOLE.mcu")
    try:
        factory.items[0].rx.put(b"\xe4")
        wait_until(
            lambda: runtime.snapshot("CONSOLE.mcu").get("received_bytes", 0) == 1
        )
        runtime.begin_run({"plugin_slice": data, "resource_ids": ["CONSOLE.mcu"]}, ctx)
        factory.items[0].rx.put(b"\xbd\xa0fresh")
        wait_until(lambda: runtime.snapshot("CONSOLE.mcu")["text"] == "你fresh")
        assert not runtime.evaluate(
            "CONSOLE.mcu", "OUTPUT_CONTAINS", {"text": "你"}, ctx
        )["satisfied"]
        assert runtime.evaluate(
            "CONSOLE.mcu", "OUTPUT_CONTAINS", {"text": "fresh"}, ctx
        )["satisfied"]
    finally:
        runtime.close()


def test_old_port_status_remains_available_to_disconnect_after_external_config_edit():
    from gear_console.runtime import ConsoleRuntime

    factory = Factory()
    runtime = ConsoleRuntime(serial_factory=factory)
    data = slice_for()
    runtime.configure(data)
    runtime.connect("CONSOLE.mcu")
    data["resources"]["CONSOLE.mcu"]["config"]["port"] = "COM78"
    runtime.configure(data)
    try:
        view = runtime.snapshot("CONSOLE.mcu")
        assert view["connected"] and view["config_changed"] and view["port"] == "COM77"
    finally:
        runtime.close()


def test_stop_cancels_queued_write_without_disconnecting_receiver():
    from gear_console.service import PortService
    from gear_contracts.api import StopRequested
    from helpers import FakeSerial, Token

    reading, release, returned = threading.Event(), threading.Event(), threading.Event()

    class BlockingRead(FakeSerial):
        first = True

        def read(self, size, timeout_s=None):
            if self.first:
                self.first = False
                reading.set()
                release.wait(2)
            return super().read(size, timeout_s)

    port = BlockingRead()
    service = PortService(
        {"port": "COM77", "role": "MCU"}, serial_factory=lambda **kw: port
    )
    token, errors = Token(), []
    service.connect()

    def send():
        try:
            service.send("cancelled", token)
        except BaseException as exc:
            errors.append(exc)
        finally:
            returned.set()

    thread = threading.Thread(target=send)
    try:
        assert reading.wait(1)
        thread.start()
        wait_until(lambda: service.snapshot()["pending_writes"] == 1)
        token.event.set()
        time.sleep(0.06)
        release.set()
        assert returned.wait(1)
        assert len(errors) == 1 and isinstance(errors[0], StopRequested)
        assert not port.writes
        assert service.snapshot()["connected"] and service.snapshot()["fault"] is None
        assert port.close_count == 0
    finally:
        release.set()
        thread.join(2)
        service.disconnect()


def test_unfinished_write_prevents_run_cleanup_from_claiming_completion(tmp_path):
    from gear_console.runtime import ConsoleRuntime
    from helpers import FakeSerial

    entered, release = threading.Event(), threading.Event()

    class StuckWrite(FakeSerial):
        def write(self, data, timeout_s=None):
            entered.set()
            release.wait(3)
            return super().write(data, timeout_s)

    port = StuckWrite()
    runtime = ConsoleRuntime(serial_factory=lambda **kw: port)
    data = slice_for()
    ctx = context(tmp_path)
    runtime.configure(data)
    runtime.begin_run({"plugin_slice": data, "resource_ids": ["CONSOLE.mcu"]}, ctx)
    try:
        result = runtime.invoke("CONSOLE.mcu", "SEND", {"command": "slow"}, ctx)
        assert (
            not result["ok"] and result["diagnostic"]["code"] == "CONSOLE_WRITE_TIMEOUT"
        )
        with pytest.raises(GearError, match="CONSOLE_PENDING_WRITE"):
            runtime.end_run()
        release.set()
        wait_until(lambda: runtime.snapshot("CONSOLE.mcu")["pending_writes"] == 0)
        runtime.end_run()
    finally:
        release.set()
        runtime.close()


@pytest.mark.parametrize(
    "previous_use", ["connected_then_disconnected", "run_without_io"]
)
def test_reassigning_released_com_does_not_give_old_resource_control_of_new_owner(
    tmp_path, previous_use
):
    from gear_console.runtime import ConsoleRuntime

    factory = Factory()
    runtime = ConsoleRuntime(serial_factory=factory)
    data = slice_for()
    runtime.configure(data)
    try:
        if previous_use == "connected_then_disconnected":
            runtime.connect("CONSOLE.mcu")
            runtime.disconnect("CONSOLE.mcu")
        else:
            runtime.begin_run(
                {"plugin_slice": data, "resource_ids": ["CONSOLE.mcu"]},
                context(tmp_path),
            )
            runtime.end_run()
        data["resources"]["CONSOLE.mcu"]["config"]["port"] = "COM78"
        data["resources"]["CONSOLE.other"] = {
            "type": "CONSOLE",
            "device": "ADB002",
            "config": {"port": "COM77", "role": "MCU"},
        }
        assert runtime.validate_config(data)["status"] == "VALID"
        runtime.configure(data)
        runtime.connect("CONSOLE.other")
        other = factory.items[-1]
        old_view = runtime.snapshot("CONSOLE.mcu")
        assert old_view["port"] == "COM78"
        assert not old_view["connected"]
        assert not old_view["config_changed"]
        runtime.disconnect("CONSOLE.mcu")
        assert other.close_count == 0
        assert runtime.snapshot("CONSOLE.other")["connected"]
        runtime.connect("CONSOLE.mcu")
        assert runtime.snapshot("CONSOLE.mcu")["port"] == "COM78"
        assert runtime.snapshot("CONSOLE.mcu")["connected"]
        assert runtime.snapshot("CONSOLE.other")["connected"]
        assert other.close_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("replacement_role", ["MCU", "SOC"])
@pytest.mark.parametrize("first_action", ["connect", "disconnect"])
def test_fault_closed_service_cannot_alias_a_new_owner_of_its_old_com(
    replacement_role, first_action
):
    from gear_console.runtime import ConsoleRuntime

    factory = Factory()
    runtime = ConsoleRuntime(serial_factory=factory)
    data = slice_for()
    runtime.configure(data)
    runtime.connect("CONSOLE.mcu")
    failed_port = factory.items[0]
    try:
        failed_port.read_error = OSError("receiver lost")
        wait_until(
            lambda: runtime.snapshot("CONSOLE.mcu")["fault"] is not None
            and not runtime.snapshot("CONSOLE.mcu")["worker_alive"]
        )
        assert failed_port.close_count == 1
        assert not runtime.snapshot("CONSOLE.mcu")["connected"]
        data["resources"]["CONSOLE.mcu"]["config"]["port"] = "COM78"
        data["resources"]["CONSOLE.other"] = {
            "type": "CONSOLE",
            "device": "ADB002",
            "config": {"port": "COM77", "role": replacement_role},
        }
        assert runtime.validate_config(data)["status"] == "VALID"
        runtime.configure(data)
        runtime.connect("CONSOLE.other")
        other_port = factory.items[-1]
        assert other_port is not failed_port
        assert other_port.settings["port"] == "COM77"
        assert other_port.settings["baudrate"] == 115200
        view = runtime.snapshot("CONSOLE.mcu")
        assert view["port"] == "COM78"
        assert not view["connected"] and not view["config_changed"]

        getattr(runtime, first_action)("CONSOLE.mcu")
        assert other_port.close_count == 0
        assert runtime.snapshot("CONSOLE.other")["connected"]
        runtime.connect("CONSOLE.mcu")
        assert runtime.snapshot("CONSOLE.mcu")["port"] == "COM78"
        assert runtime.snapshot("CONSOLE.mcu")["connected"]
        runtime.disconnect("CONSOLE.mcu")
        assert other_port.close_count == 0
        assert runtime.snapshot("CONSOLE.other")["connected"]
        assert failed_port.close_count == 1
    finally:
        runtime.close()


def test_failed_close_keeps_actual_service_ownership_until_retry_succeeds():
    from gear_console.runtime import ConsoleRuntime

    factory = Factory()
    runtime = ConsoleRuntime(serial_factory=factory)
    data = slice_for()
    runtime.configure(data)
    runtime.connect("CONSOLE.mcu")
    original = factory.items[0]
    try:
        original.close_error = OSError("driver refused close")
        with pytest.raises(GearError, match="CONSOLE_CLOSE_FAILED"):
            runtime.disconnect("CONSOLE.mcu")
        data["resources"]["CONSOLE.mcu"]["config"]["port"] = "COM78"
        data["resources"]["CONSOLE.other"] = {
            "type": "CONSOLE",
            "device": "ADB002",
            "config": {"port": "COM77", "role": "SOC"},
        }
        runtime.configure(data)
        with pytest.raises(GearError, match="CONSOLE_CONFIG_CHANGED"):
            runtime.connect("CONSOLE.other")
        assert len(factory.items) == 1
        assert runtime.snapshot("CONSOLE.mcu")["port"] == "COM77"
        original.close_error = None
        runtime.disconnect("CONSOLE.mcu")
        runtime.connect("CONSOLE.other")
        assert runtime.snapshot("CONSOLE.other")["connected"]
        assert runtime.snapshot("CONSOLE.mcu")["port"] == "COM78"
        assert not runtime.snapshot("CONSOLE.mcu")["connected"]
    finally:
        original.close_error = None
        runtime.close()
