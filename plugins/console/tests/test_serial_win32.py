"""Win32 tests inject a fake kernel API and never load a real driver."""

import ctypes
import pytest
from gear_console.serial_win32 import WindowsSerial, DCB, COMMTIMEOUTS


class FakeAPI:
    HANDLE = 0x1234567887654321

    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []
        self.dcb = None
        self.timeouts = []
        self.received = bytearray(b"abc")
        self.written = []
        self.write_count = None

    def CreateFileW(self, *args):
        self.calls.append(("create", args))
        return ctypes.c_void_p(-1).value if self.fail == "create" else self.HANDLE

    def GetCommState(self, handle, pointer):
        self.calls.append(("get", handle))
        return self.fail != "get"

    def SetCommState(self, handle, pointer):
        self.calls.append(("set", handle))
        self.dcb = DCB.from_buffer_copy(bytes(pointer._obj))
        return self.fail != "set"

    def SetCommTimeouts(self, handle, pointer):
        self.calls.append(("timeouts", handle))
        self.timeouts.append(COMMTIMEOUTS.from_buffer_copy(bytes(pointer._obj)))
        return self.fail != "timeouts"

    def PurgeComm(self, handle, flags):
        self.calls.append(("purge", handle, flags))
        return self.fail != "purge"

    def ReadFile(self, handle, buffer, count, pointer, overlapped):
        self.calls.append(("read", handle, count, overlapped))
        data = bytes(self.received[:count])
        del self.received[:count]
        ctypes.memmove(buffer, data, len(data))
        pointer._obj.value = len(data)
        return self.fail != "read"

    def WriteFile(self, handle, buffer, count, pointer, overlapped):
        self.calls.append(("write", handle, count, overlapped))
        self.written.append(ctypes.string_at(buffer, count))
        pointer._obj.value = count if self.write_count is None else self.write_count
        return self.fail != "write"

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return self.fail != "close"


def serial(api, **params):
    return WindowsSerial(
        port="COM77",
        baudrate=9600,
        parity="N",
        stopbits=1,
        timeout_s=0.2,
        api=api,
        **params,
    )


def test_constructor_never_loads_win32_or_opens_a_port(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("constructor must not load WinDLL")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    item = WindowsSerial(port="COM77")
    item.close()


@pytest.mark.parametrize(
    "parity,stopbits,parity_value,stop_value,flags",
    [
        ("N", 1, 0, 0, 1),
        ("E", 2, 2, 2, 3),
        ("O", 1, 1, 0, 3),
    ],
)
def test_open_uses_exclusive_existing_com_path_and_explicit_binary_settings(
    parity, stopbits, parity_value, stop_value, flags
):
    api = FakeAPI()
    item = WindowsSerial(
        port="COM77",
        baudrate=19200,
        parity=parity,
        stopbits=stopbits,
        timeout_s=0.2,
        api=api,
    )
    assert not api.calls
    item.open()
    assert api.calls[0] == ("create", (r"\\.\COM77", 0xC0000000, 0, None, 3, 0, None))
    assert api.dcb.DCBlength == 28
    assert api.dcb.BaudRate == 19200
    assert api.dcb.ByteSize == 8
    assert api.dcb.Parity == parity_value
    assert api.dcb.StopBits == stop_value
    assert api.dcb.flags == flags
    item.open()
    item.close()
    item.close()
    assert sum(call[0] == "create" for call in api.calls) == 1
    assert [c for c in api.calls if c[0] == "close"] == [("close", api.HANDLE)]


def test_read_uses_documented_first_byte_wait_and_finite_per_call_deadline():
    api = FakeAPI()
    item = serial(api)
    item.open()
    assert item.read(8, timeout_s=0.0121) == b"abc"
    timeout = api.timeouts[-1]
    assert timeout.ReadIntervalTimeout == 0xFFFFFFFF
    assert timeout.ReadTotalTimeoutMultiplier == 0xFFFFFFFF
    assert timeout.ReadTotalTimeoutConstant == 13
    assert timeout.WriteTotalTimeoutMultiplier == 0
    assert 0 < timeout.WriteTotalTimeoutConstant < 0xFFFFFFFF
    assert item.read(8, timeout_s=0.001) == b""
    item.close()


def test_write_preserves_binary_nuls_and_reports_short_write_without_retry():
    api = FakeAPI()
    item = serial(api)
    item.open()
    api.write_count = 2
    assert item.write(b"\x00\xff\x00", timeout_s=0.031) == 2
    assert api.written == [b"\x00\xff\x00"]
    assert api.timeouts[-1].WriteTotalTimeoutConstant == 31
    item.close()


@pytest.mark.parametrize("failure", ["get", "set", "timeouts", "purge"])
def test_failed_setup_closes_the_acquired_handle(failure):
    api = FakeAPI(failure)
    item = serial(api)
    with pytest.raises(OSError):
        item.open()
    item.close()
    assert [c for c in api.calls if c[0] == "close"] == [("close", api.HANDLE)]


def test_invalid_handle_is_not_closed():
    api = FakeAPI("create")
    item = serial(api)
    with pytest.raises(OSError):
        item.open()
    item.close()
    assert not [c for c in api.calls if c[0] == "close"]


@pytest.mark.parametrize("failure", ["read", "write"])
def test_false_io_result_surfaces_error_and_owner_can_release_handle(failure):
    api = FakeAPI()
    item = serial(api)
    item.open()
    api.fail = failure
    with pytest.raises(OSError):
        item.read(8) if failure == "read" else item.write(b"123")
    item.close()
    assert api.calls[-1] == ("close", api.HANDLE)


@pytest.mark.parametrize("port", ["", "COM0", "COM77/../x", r"\\.\COM77", "file.txt"])
def test_invalid_noncanonical_port_is_rejected_without_api_calls(port):
    api = FakeAPI()
    with pytest.raises(ValueError):
        WindowsSerial(port=port, api=api)
    assert not api.calls


def test_reads_and_writes_require_explicit_open():
    api = FakeAPI()
    item = serial(api)
    with pytest.raises(OSError):
        item.read(1)
    with pytest.raises(OSError):
        item.write(b"x")
    assert not api.calls


def test_failed_close_retains_handle_until_a_successful_retry():
    api = FakeAPI()
    item = serial(api)
    item.open()
    api.fail = "close"
    with pytest.raises(OSError):
        item.close()
    api.fail = None
    item.close()
    item.close()
    assert [c for c in api.calls if c[0] == "close"] == [("close", api.HANDLE)] * 2


def test_setup_and_cleanup_failure_retains_handle_for_owner_cleanup():
    class SetupAndCloseFailure(FakeAPI):
        def CloseHandle(self, handle):
            self.calls.append(("close", handle))
            return False

    api = SetupAndCloseFailure("set")
    item = serial(api)
    with pytest.raises(OSError):
        item.open()
    api.CloseHandle = lambda handle: api.calls.append(("retry_close", handle)) or True
    item.close()
    assert api.calls[-1] == ("retry_close", api.HANDLE)
