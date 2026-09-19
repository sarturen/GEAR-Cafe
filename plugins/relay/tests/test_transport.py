"""Protocol tests use only scripted byte streams; no serial device exists."""

import threading
import pytest
from gear_contracts.api import GearError, StopRequested
from gear_relay.transport import RelayService, crc16

CONFIG = dict(
    port="COM77", baudrate=9600, parity="N", stopbits=1, unit_id=1, timeout_s=0.2
)
READ = bytes.fromhex("0101000000083dcc")
ALL_OFF = bytes.fromhex("010101005188")
ALL_ACK = bytes.fromhex("010f00000008540d")


class Clock:
    def __init__(self):
        self.now = 5.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration


class ScriptedSerial:
    def __init__(self, responses=(), chunk=256):
        self.responses = list(responses)
        self.chunk = chunk
        self.pending = b""
        self.open_count = 0
        self.close_count = 0
        self.writes = []
        self.read_timeouts = []
        self.on_read = None
        self.write_count = None
        self.open_error = None

    def open(self):
        self.open_count += 1
        if self.open_error:
            raise self.open_error

    def close(self):
        self.close_count += 1

    def write(self, data, timeout_s=None):
        self.writes.append(bytes(data))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            self.pending = response
        return len(data) if self.write_count is None else self.write_count

    def read(self, size, timeout_s=None):
        self.read_timeouts.append(timeout_s)
        if self.on_read:
            self.on_read()
        take = min(size, self.chunk)
        result, self.pending = self.pending[:take], self.pending[take:]
        return result


class Factory:
    def __init__(self, *serials):
        self.serials = list(serials)
        self.calls = []

    def __call__(self, **params):
        self.calls.append(params)
        return self.serials.pop(0)


def configured(*responses, chunk=256, **config):
    serial = ScriptedSerial(responses, chunk)
    factory = Factory(serial)
    clock = Clock()
    service = RelayService(serial_factory=factory, clock=clock, sleeper=clock.sleep)
    service.configure({**CONFIG, **config})
    return service, serial, factory, clock


def test_crc_uses_modbus_polynomial_and_low_byte_first_vector():
    assert crc16(b"123456789") == 0x4B37
    assert crc16(READ[:-2]) == 0xCC3D


def test_configure_and_snapshot_do_no_io_then_first_read_opens_once():
    service, serial, factory, _ = configured(
        bytes.fromhex("010101a591f3"), ALL_OFF, chunk=1
    )
    assert not factory.calls
    assert service.snapshot() == dict(
        connected=False, fault=None, states=[None] * 8, sources=[None] * 8
    )
    assert service.read_states() == [True, False, True, False, False, True, False, True]
    assert service.read_states() == [False] * 8
    assert serial.writes == [READ, READ]
    assert serial.open_count == 1
    assert factory.calls == [{k: v for k, v in CONFIG.items() if k != "unit_id"}]
    assert all(0 < timeout <= CONFIG["timeout_s"] for timeout in serial.read_timeouts)
    assert service.snapshot() == dict(
        connected=True, fault=None, states=[False] * 8, sources=["read_coils"] * 8
    )


@pytest.mark.parametrize(
    "channel,on,frame",
    [
        (1, True, "01050000ff008c3a"),
        (8, False, "0105000700007c0b"),
    ],
)
def test_single_coil_addresses_are_zero_based_and_write_requires_echo(
    channel, on, frame
):
    service, serial, _, _ = configured(bytes.fromhex(frame))
    service.set_channel(channel, on)
    assert serial.writes == [bytes.fromhex(frame)]
    expected = [None] * 8
    expected[channel - 1] = on
    assert service.snapshot()["states"] == expected


@pytest.mark.parametrize(
    "on,expected_request",
    [
        (True, "010f0000000801ffbed5"),
        (False, "010f000000080100fe95"),
    ],
)
def test_all_coils_uses_function_0f_exactly_eight_bits(on, expected_request):
    service, serial, _, _ = configured(ALL_ACK)
    service.set_all(on)
    assert serial.writes == [bytes.fromhex(expected_request)]
    assert service.snapshot()["states"] == [on] * 8


@pytest.mark.parametrize(
    "response,code",
    [
        ("010101005189", "RELAY_CRC"),
        ("0201010051cc", "RELAY_UNIT"),
        ("0102020000b9b8", "RELAY_FUNCTION"),
        ("0101020000b9fc", "RELAY_LENGTH"),
        ("018102c191", "RELAY_EXCEPTION"),
        ("01010100", "RELAY_TIMEOUT"),
        ("", "RELAY_TIMEOUT"),
    ],
)
def test_bad_response_disconnects_clears_cache_and_latches_manual_reconnect(
    response, code
):
    service, serial, factory, _ = configured(ALL_OFF, bytes.fromhex(response))
    service.read_states()
    with pytest.raises(GearError) as error:
        service.read_states()
    assert error.value.args[0] == code
    snap = service.snapshot()
    assert not snap["connected"]
    assert snap["states"] == [None] * 8
    assert snap["fault"]
    assert serial.close_count == 1
    for action in (service.read_states, lambda: service.set_all(False)):
        with pytest.raises(GearError) as blocked:
            action()
        assert blocked.value.args[0] == "RELAY_RECONNECT_REQUIRED"
    assert len(factory.calls) == 1
    assert serial.writes == [READ, READ]


@pytest.mark.parametrize(
    "action,response",
    [
        ("single", "0105000100009c0a"),
        ("all", "010f0001000805cd"),
    ],
)
def test_write_rejects_wrong_echo(action, response):
    service, _, _, _ = configured(bytes.fromhex(response))
    with pytest.raises(GearError) as error:
        service.set_channel(1, False) if action == "single" else service.set_all(False)
    assert error.value.args[0] == "RELAY_ECHO"
    assert service.snapshot()["states"] == [None] * 8


def test_partial_write_is_fault_and_is_not_retried():
    service, serial, _, _ = configured(ALL_OFF)
    serial.write_count = 3
    with pytest.raises(GearError) as error:
        service.read_states()
    assert error.value.args[0] == "RELAY_WRITE"
    assert len(serial.writes) == 1
    assert serial.close_count == 1


def test_io_failure_is_gear_error_and_requires_manual_open():
    bad, good = ScriptedSerial([OSError("unplugged")]), ScriptedSerial([ALL_OFF])
    factory = Factory(bad, good)
    service = RelayService(serial_factory=factory)
    service.configure(CONFIG)
    with pytest.raises(GearError) as error:
        service.read_states()
    assert error.value.args[0] == "RELAY_IO"
    service.configure(dict(CONFIG))
    with pytest.raises(GearError):
        service.read_states()
    service.open()
    assert service.snapshot()["fault"] is None
    assert service.read_states() == [False] * 8
    assert len(factory.calls) == 2


def test_failed_open_releases_partial_serial_and_does_not_retry():
    service, serial, factory, _ = configured()
    serial.open_error = OSError("access denied")
    with pytest.raises(GearError):
        service.read_states()
    assert serial.close_count == 1
    with pytest.raises(GearError) as error:
        service.read_states()
    assert error.value.args[0] == "RELAY_RECONNECT_REQUIRED"
    assert len(factory.calls) == 1


def test_same_configuration_retains_connection_changed_or_none_closes_without_writing():
    service, serial, factory, _ = configured(ALL_OFF)
    service.read_states()
    service.configure(dict(CONFIG))
    service.open()
    assert serial.open_count == 1
    assert serial.close_count == 0
    service.configure({**CONFIG, "baudrate": 19200})
    assert serial.close_count == 1
    assert service.snapshot() == dict(
        connected=False, fault=None, states=[None] * 8, sources=[None] * 8
    )
    service.configure(None)
    with pytest.raises(GearError) as error:
        service.open()
    assert error.value.args[0] == "RELAY_NOT_CONFIGURED"
    assert len(factory.calls) == 1
    assert serial.writes == [READ]


def test_close_and_disconnect_release_only_and_snapshot_is_detached():
    service, serial, _, _ = configured(ALL_OFF)
    service.read_states()
    snap = service.snapshot()
    snap["states"][0] = True
    assert service.snapshot()["states"][0] is False
    service.disconnect()
    service.close()
    assert serial.close_count == 1
    assert serial.writes == [READ]
    assert service.snapshot()["states"] == [None] * 8


@pytest.mark.parametrize(
    "baudrate,parity,stopbits,gap",
    [
        (9600, "N", 1, 3.5 * 10 / 9600),
        (9600, "E", 2, 3.5 * 12 / 9600),
        (19200, "O", 1, 3.5 * 11 / 19200),
        (38400, "N", 1, 0.00175),
    ],
)
def test_frame_spacing_is_enforced_between_request_response_exchanges(
    baudrate, parity, stopbits, gap
):
    service, serial, _, clock = configured(
        ALL_OFF, ALL_OFF, baudrate=baudrate, parity=parity, stopbits=stopbits
    )
    service.read_states()
    previous = clock.now
    service.read_states()
    assert clock.now - previous >= gap - 1e-12
    assert len(serial.writes) == 2


def test_read_deadline_shrinks_across_partial_bytes():
    service, serial, _, clock = configured(ALL_OFF, chunk=1)
    serial.on_read = lambda: setattr(clock, "now", clock.now + 0.05)
    with pytest.raises(GearError) as error:
        service.read_states()
    assert error.value.args[0] == "RELAY_TIMEOUT"
    assert serial.read_timeouts[-1] < serial.read_timeouts[0]
    assert len(serial.read_timeouts) <= 5


def test_snapshot_does_not_wait_for_blocked_device_io():
    service, serial, _, _ = configured(ALL_OFF)
    entered, release = threading.Event(), threading.Event()
    errors = []

    def block():
        entered.set()
        assert release.wait(2)

    def read():
        try:
            service.read_states()
        except Exception as exc:
            errors.append(exc)

    serial.on_read = block
    worker = threading.Thread(target=read)
    worker.start()
    try:
        assert entered.wait(1)
        assert service.snapshot()["connected"]
        assert service.snapshot()["states"] == [None] * 8
    finally:
        release.set()
        worker.join(2)
    assert not errors


class Token:
    def __init__(self, requested=False):
        self.requested = requested

    def is_requested(self):
        return self.requested


def test_stop_only_at_entry_and_does_not_interrupt_an_inflight_frame_or_teardown():
    service, serial, factory, _ = configured(ALL_OFF, ALL_ACK)
    token = Token(True)
    with pytest.raises(StopRequested):
        service.read_states(token)
    assert not factory.calls
    token.requested = False
    serial.on_read = lambda: setattr(token, "requested", True)
    assert service.read_states(token) == [False] * 8
    service.set_all(False)
    assert serial.writes == [READ, bytes.fromhex("010f000000080100fe95")]


@pytest.mark.parametrize("channel", [0, 9, -1, True, 1.0, "1"])
def test_invalid_channel_is_rejected_before_open(channel):
    service, _, factory, _ = configured()
    with pytest.raises(GearError):
        service.set_channel(channel, True)
    assert not factory.calls


class CleanupFailureSerial(ScriptedSerial):
    def __init__(self, responses=()):
        super().__init__(responses)
        self.close_error = True

    def close(self):
        self.close_count += 1
        if self.close_error:
            raise OSError("close failed")


def test_close_failure_remains_visible_and_can_be_retried():
    serial = CleanupFailureSerial([ALL_OFF])
    service = RelayService(serial_factory=Factory(serial))
    service.configure(CONFIG)
    service.read_states()
    with pytest.raises(GearError):
        service.close()
    assert service.snapshot()["fault"]
    assert not service.snapshot()["connected"]
    serial.close_error = False
    service.close()
    assert serial.close_count == 2
    assert service.snapshot()["fault"] is None


def test_manual_reconnect_releases_handle_left_by_a_failed_cleanup():
    bad = CleanupFailureSerial([OSError("unplugged")])
    good = ScriptedSerial([ALL_OFF])
    factory = Factory(bad, good)
    service = RelayService(serial_factory=factory)
    service.configure(CONFIG)
    with pytest.raises(GearError):
        service.read_states()
    assert service.snapshot()["fault"]
    bad.close_error = False
    service.open()
    assert bad.close_count == 2
    assert service.read_states() == [False] * 8
    assert len(factory.calls) == 2


def test_invalid_configuration_cannot_restore_old_port_after_close_failure():
    serial = CleanupFailureSerial([ALL_OFF])
    factory = Factory(serial)
    service = RelayService(serial_factory=factory)
    service.configure(CONFIG)
    service.read_states()
    with pytest.raises(GearError):
        service.configure(None)
    serial.close_error = False
    service.close()
    with pytest.raises(GearError) as error:
        service.open()
    assert error.value.args[0] == "RELAY_NOT_CONFIGURED"
    assert len(factory.calls) == 1
