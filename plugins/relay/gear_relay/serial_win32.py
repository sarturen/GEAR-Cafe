"""Minimal synchronous Windows COM I/O; construction and import perform no I/O.

Only open() loads kernel32 and opens the explicitly supplied port. The optional
API object is an injection seam for tests, never a device discovery mechanism.
"""

from __future__ import annotations

import ctypes
import math
import re

DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
BYTE = ctypes.c_ubyte
HANDLE = ctypes.c_void_p
BOOL = ctypes.c_int32


class DCB(ctypes.Structure):
    _fields_ = [
        ("DCBlength", DWORD),
        ("BaudRate", DWORD),
        ("flags", DWORD),
        ("wReserved", WORD),
        ("XonLim", WORD),
        ("XoffLim", WORD),
        ("ByteSize", BYTE),
        ("Parity", BYTE),
        ("StopBits", BYTE),
        ("XonChar", ctypes.c_char),
        ("XoffChar", ctypes.c_char),
        ("ErrorChar", ctypes.c_char),
        ("EofChar", ctypes.c_char),
        ("EvtChar", ctypes.c_char),
        ("wReserved1", WORD),
    ]


class COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [
        ("ReadIntervalTimeout", DWORD),
        ("ReadTotalTimeoutMultiplier", DWORD),
        ("ReadTotalTimeoutConstant", DWORD),
        ("WriteTotalTimeoutMultiplier", DWORD),
        ("WriteTotalTimeoutConstant", DWORD),
    ]


def _load_api():
    if not hasattr(ctypes, "WinDLL"):
        raise OSError("Windows COM ports are supported only on Windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateFileW": (
            [ctypes.c_wchar_p, DWORD, DWORD, ctypes.c_void_p, DWORD, DWORD, HANDLE],
            HANDLE,
        ),
        "GetCommState": ([HANDLE, ctypes.POINTER(DCB)], BOOL),
        "SetCommState": ([HANDLE, ctypes.POINTER(DCB)], BOOL),
        "SetCommTimeouts": ([HANDLE, ctypes.POINTER(COMMTIMEOUTS)], BOOL),
        "PurgeComm": ([HANDLE, DWORD], BOOL),
        "ReadFile": (
            [HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD), ctypes.c_void_p],
            BOOL,
        ),
        "WriteFile": (
            [HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD), ctypes.c_void_p],
            BOOL,
        ),
        "CloseHandle": ([HANDLE], BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = arguments
        function.restype = result
    return api


def _failure(operation):
    code = getattr(ctypes, "get_last_error", lambda: 0)()
    return OSError(code, f"{operation} failed (Win32 error {code})")


def _milliseconds(timeout_s):
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise ValueError("timeout_s must be a positive finite number")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be a positive finite number")
    milliseconds = max(1, math.ceil(timeout_s * 1000))
    if milliseconds >= 0xFFFFFFFF:
        raise ValueError("timeout_s exceeds the finite Win32 timeout range")
    return milliseconds


class WindowsSerial:
    def __init__(
        self, port, baudrate=9600, parity="N", stopbits=1, timeout_s=1.0, *, api=None
    ):
        if not isinstance(port, str) or not re.fullmatch(r"COM[1-9][0-9]*", port):
            raise ValueError("port must be an explicit canonical COM number")
        if (
            isinstance(baudrate, bool)
            or not isinstance(baudrate, int)
            or not 1 <= baudrate <= 0xFFFFFFFF
        ):
            raise ValueError("baudrate must be a positive integer")
        if parity not in ("N", "E", "O") or stopbits not in (1, 2):
            raise ValueError("8-bit serial requires N/E/O parity and 1 or 2 stop bits")
        _milliseconds(timeout_s)
        self.port = port
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self.timeout_s = timeout_s
        self._api = api
        self._handle = None

    def open(self):
        if self._handle is not None:
            return
        if self._api is None:
            self._api = _load_api()
        handle = self._api.CreateFileW(
            "\\\\.\\" + self.port, 0xC0000000, 0, None, 3, 0, None
        )
        if handle in (None, ctypes.c_void_p(-1).value, -1):
            raise _failure("CreateFileW")
        self._handle = handle
        try:
            dcb = DCB()
            dcb.DCBlength = ctypes.sizeof(DCB)
            if not self._api.GetCommState(handle, ctypes.byref(dcb)):
                raise _failure("GetCommState")
            dcb.BaudRate = self.baudrate
            # Binary, optional parity checking; no hardware/software flow control.
            dcb.flags = 1 | (2 if self.parity != "N" else 0)
            dcb.ByteSize = 8
            dcb.Parity = {"N": 0, "O": 1, "E": 2}[self.parity]
            dcb.StopBits = {1: 0, 2: 2}[self.stopbits]
            dcb.wReserved = dcb.wReserved1 = 0
            if not self._api.SetCommState(handle, ctypes.byref(dcb)):
                raise _failure("SetCommState")
            self._set_timeouts(self.timeout_s)
            # Discard stale bytes from a previous owner; never write relay commands.
            if not self._api.PurgeComm(handle, 0x0004 | 0x0008):
                raise _failure("PurgeComm")
        except Exception:
            try:
                self.close()
            except OSError:
                pass
            raise

    def _require_open(self):
        if self._handle is None:
            raise OSError("serial port is not open")

    def _set_timeouts(self, timeout_s):
        milliseconds = _milliseconds(timeout_s)
        # MAXDWORD/MAXDWORD/positive constant is the documented first-byte wait:
        # return buffered bytes immediately, otherwise wait at most the constant.
        timeouts = COMMTIMEOUTS(0xFFFFFFFF, 0xFFFFFFFF, milliseconds, 0, milliseconds)
        if not self._api.SetCommTimeouts(self._handle, ctypes.byref(timeouts)):
            raise _failure("SetCommTimeouts")

    def read(self, size, timeout_s=None):
        self._require_open()
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 256:
            raise ValueError("read size must be between 0 and 256")
        if not size:
            return b""
        self._set_timeouts(self.timeout_s if timeout_s is None else timeout_s)
        buffer = ctypes.create_string_buffer(size)
        received = DWORD()
        if not self._api.ReadFile(
            self._handle, buffer, size, ctypes.byref(received), None
        ):
            raise _failure("ReadFile")
        if received.value > size:
            raise OSError("ReadFile returned an invalid byte count")
        return buffer.raw[: received.value]

    def write(self, data, timeout_s=None):
        self._require_open()
        data = bytes(data)
        if not data:
            return 0
        self._set_timeouts(self.timeout_s if timeout_s is None else timeout_s)
        buffer = ctypes.create_string_buffer(data, len(data))
        written = DWORD()
        if not self._api.WriteFile(
            self._handle, buffer, len(data), ctypes.byref(written), None
        ):
            raise _failure("WriteFile")
        if written.value > len(data):
            raise OSError("WriteFile returned an invalid byte count")
        return written.value

    def close(self):
        if self._handle is not None:
            if not self._api.CloseHandle(self._handle):
                raise _failure("CloseHandle")
            self._handle = None
