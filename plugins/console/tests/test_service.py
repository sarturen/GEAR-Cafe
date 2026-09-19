import threading
import time
import pytest
from gear_contracts.api import GearError, StopRequested
from helpers import Factory, Token, wait_until


def make_service(**kw):
    from gear_console.service import PortService

    factory = Factory()
    service = PortService(
        {"port": "COM77", "role": "MCU", "encoding": "utf-8"},
        serial_factory=factory,
        **kw,
    )
    return service, factory


def test_incremental_decode_receive_send_and_close_share_one_io_thread():
    service, factory = make_service()
    assert factory.items == []
    try:
        service.connect()
        port = factory.items[0]
        port.rx.put(b"\xe4\xbd")
        port.rx.put(b"\xa0\xe5\xa5\xbd\n")
        wait_until(lambda: service.snapshot()["text"] == "你好\n")
        assert service.send("hello") == 6
        assert port.writes == [b"hello\n"]
        service.connect()
        assert port.open_count == 1
    finally:
        service.disconnect()
    assert len(set(port.threads)) == 1
    assert port.threads[0] != threading.get_ident()
    assert not service.snapshot()["connected"]


def test_bounded_cache_reports_lost_prefix_and_keeps_receiving():
    service, factory = make_service(cache_chars=8)
    try:
        service.connect()
        factory.items[0].rx.put(b"abcdefghijkl")
        wait_until(lambda: service.snapshot()["end"] == 12)
        view = service.snapshot(0)
        assert view["text"] == "efghijkl"
        assert view["start"] == 4 and view["truncated"]
        assert service.snapshot(8)["text"] == "ijkl"
        factory.items[0].rx.put(b"mn")
        wait_until(lambda: service.snapshot()["end"] == 14)
        assert service.snapshot()["text"] == "ghijklmn"
    finally:
        service.disconnect()


def test_close_timeout_reports_failure_and_does_not_pretend_thread_stopped():
    service, factory = make_service(close_timeout_s=0.02)
    gate = threading.Event()
    service.connect()
    factory.items[0].read_gate = gate
    time.sleep(0.08)
    try:
        with pytest.raises(GearError, match="CONSOLE_CLOSE_TIMEOUT"):
            service.disconnect()
        assert service.snapshot()["closing"]
        with pytest.raises(GearError):
            service.send("must not write")
    finally:
        gate.set()
        wait_until(lambda: not service.snapshot()["worker_alive"])
        service.disconnect()
    assert factory.items[0].writes == []


def test_failed_close_is_retryable_and_not_reported_as_closed():
    service, factory = make_service()
    service.connect()
    factory.items[0].close_error = OSError("close broken")
    with pytest.raises(GearError, match="CONSOLE_CLOSE_FAILED"):
        service.disconnect()
    assert service.snapshot()["connected"]
    factory.items[0].close_error = None
    service.disconnect()
    assert not service.snapshot()["connected"]


def test_io_failure_remains_distinct_from_empty_output():
    service, factory = make_service()
    service.connect()
    factory.items[0].read_error = OSError("unplugged")
    wait_until(lambda: service.snapshot()["fault"] is not None)
    with pytest.raises(GearError, match="CONSOLE_IO_ERROR"):
        service.send("hello")
    service.disconnect()


def test_stop_prevents_queued_write():
    service, factory = make_service()
    token = Token()
    try:
        service.connect()
        token.event.set()
        with pytest.raises(StopRequested):
            service.send("hello", token)
        assert factory.items[0].writes == []
    finally:
        service.disconnect()
