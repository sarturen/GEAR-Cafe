"""One eight-coil Modbus RTU session with explicit reconnect after any fault."""

from __future__ import annotations

import threading
from functools import wraps
import time

from gear_contracts.api import GearError, StopRequested

from .serial_win32 import WindowsSerial


def crc16(data: bytes) -> int:
    """Return the Modbus CRC-16; the wire encodes its low byte first."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _frame(payload):
    return payload + crc16(payload).to_bytes(2, "little")


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._io_lock:
            return method(self, *args, **kwargs)

    return call


class RelayService:
    """One serial owner with serialized manual, run and background transactions.

    serial_factory receives serial configuration keyword arguments and returns
    an unopened adapter with open/read/write/close. Read and write accept a
    timeout_s keyword so one transaction shares a bounded deadline.
    """

    def __init__(
        self, serial_factory=WindowsSerial, clock=time.monotonic, sleeper=time.sleep
    ):
        self._serial_factory = serial_factory
        self._clock = clock
        self._sleep = sleeper
        self._config = None
        self._serial = None
        self._needs_reconnect = False
        self._last_activity = None
        self._io_lock = threading.RLock()
        self._poll_stop = threading.Event()
        self._poll_thread = None
        self._cache_lock = threading.Lock()
        self._cache = dict(
            connected=False, fault=None, states=[None] * 8, sources=[None] * 8
        )

    def snapshot(self):
        with self._cache_lock:
            return {
                **self._cache,
                "states": list(self._cache["states"]),
                "sources": list(self._cache["sources"]),
            }

    def _publish(self, **changes):
        # This lock is never held while waiting, opening, reading or closing.
        with self._cache_lock:
            self._cache.update(changes)

    def configure(self, config_or_none):
        config = dict(config_or_none) if config_or_none is not None else None
        if config == self._config:
            return
        try:
            self.disconnect()
        finally:
            # A failed close must not restore the obsolete serial configuration.
            with self._io_lock:
                self._config = config

    def _start_poll(self):
        if not self._config.get("poll_interval_ms", 0):
            return
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll, name="relay-poll-" + self._config["port"], daemon=True
        )
        self._poll_thread.start()

    def _poll(self):
        interval = self._config["poll_interval_ms"] / 1000
        while not self._poll_stop.wait(interval):
            with self._io_lock:
                if self._poll_stop.is_set() or not self.snapshot()["connected"]:
                    continue
                try:
                    self.read_states()
                except GearError:
                    # The fault latch stays set; no implicit reconnect or retry.
                    pass

    @_serialized
    def open(self):
        """Explicit operator connection; the only method that clears a fault latch."""
        if self._config is None:
            raise GearError(
                "RELAY_NOT_CONFIGURED", "Relay serial configuration is not valid"
            )
        if self._serial is not None and self.snapshot()["connected"]:
            return
        try:
            # A failed close retains ownership until an explicit cleanup succeeds.
            self._release()
            params = {
                key: self._config[key]
                for key in ("port", "baudrate", "parity", "stopbits", "timeout_s")
            }
            self._serial = self._serial_factory(**params)
            self._serial.open()
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, GearError)
                else GearError("RELAY_IO", f"Could not open relay serial port: {exc}")
            )
            self._fail(error)
            raise error from exc
        self._needs_reconnect = False
        self._last_activity = self._clock()
        self._publish(connected=True, fault=None, states=[None] * 8, sources=[None] * 8)
        self._start_poll()

    def _release(self):
        self._last_activity = None
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def disconnect(self):
        self._poll_stop.set()
        thread = self._poll_thread
        try:
            with self._io_lock:
                try:
                    self._release()
                except Exception as exc:
                    self._needs_reconnect = True
                    self._publish(
                        connected=False,
                        fault=str(exc),
                        states=[None] * 8,
                        sources=[None] * 8,
                    )
                    raise GearError(
                        "RELAY_IO", f"Could not close relay serial port: {exc}"
                    ) from exc
                self._publish(
                    connected=False, fault=None, states=[None] * 8, sources=[None] * 8
                )
        finally:
            # Never join with the transaction lock held.
            if thread is not None and thread is not threading.current_thread():
                thread.join()
            self._poll_thread = None

    def close(self):
        """Release the port only; never restore or switch any relay."""
        self.disconnect()

    def _fail(self, error):
        self._needs_reconnect = True
        message = str(error.args[-1]) if error.args else str(error)
        self._publish(
            connected=False, fault=message, states=[None] * 8, sources=[None] * 8
        )
        try:
            self._release()
        except Exception as cleanup:
            self._publish(fault=f"{message}; closing port also failed: {cleanup}")

    @staticmethod
    def _check_stop(stop_token):
        if stop_token is not None and stop_token.is_requested():
            raise StopRequested()

    def _ensure_connected(self):
        if self._needs_reconnect:
            raise GearError(
                "RELAY_RECONNECT_REQUIRED",
                "Relay communication failed; connect manually before continuing",
            )
        if self._serial is None:
            self.open()

    def _frame_gap(self):
        baudrate = self._config["baudrate"]
        if baudrate > 19200:
            return 0.00175
        character_bits = (
            1 + 8 + (self._config["parity"] != "N") + self._config["stopbits"]
        )
        return 3.5 * character_bits / baudrate

    def _wait_frame_gap(self):
        if self._last_activity is not None:
            remaining = self._frame_gap() - (self._clock() - self._last_activity)
            if remaining > 0:
                self._sleep(remaining)

    def _remaining(self, deadline):
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise GearError(
                "RELAY_TIMEOUT", "Timed out waiting for a complete relay response"
            )
        return min(remaining, self._config["timeout_s"])

    def _read_exact(self, count, deadline):
        result = bytearray()
        while len(result) < count:
            chunk = self._serial.read(
                count - len(result), timeout_s=self._remaining(deadline)
            )
            now = self._clock()
            if not chunk or now > deadline:
                raise GearError(
                    "RELAY_TIMEOUT", "Timed out waiting for a complete relay response"
                )
            self._last_activity = now
            if len(chunk) > count - len(result):
                raise GearError(
                    "RELAY_LENGTH", "Relay response contained too many bytes"
                )
            result.extend(chunk)
        return bytes(result)

    def _exchange(self, function, data):
        self._ensure_connected()
        request = _frame(bytes([self._config["unit_id"], function]) + data)
        try:
            self._wait_frame_gap()
            deadline = self._clock() + self._config["timeout_s"]
            if self._serial.write(request, timeout_s=self._remaining(deadline)) != len(
                request
            ):
                raise GearError(
                    "RELAY_WRITE", "Relay request was not completely written"
                )
            self._last_activity = self._clock()
            header = self._read_exact(3, deadline)
            if header[1] == (function | 0x80):
                response = header + self._read_exact(2, deadline)
            elif header[1] != function:
                raise GearError(
                    "RELAY_FUNCTION",
                    "Relay response function did not match the request",
                )
            elif function == 0x01:
                if header[2] != 1:
                    raise GearError(
                        "RELAY_LENGTH",
                        "Eight relay coils require exactly one data byte",
                    )
                response = header + self._read_exact(3, deadline)
            else:
                response = header + self._read_exact(5, deadline)
            if crc16(response[:-2]) != int.from_bytes(response[-2:], "little"):
                raise GearError("RELAY_CRC", "Relay response CRC is invalid")
            if response[0] != self._config["unit_id"]:
                raise GearError(
                    "RELAY_UNIT", "Relay response came from a different slave"
                )
            if response[1] & 0x80:
                raise GearError(
                    "RELAY_EXCEPTION",
                    f"Relay returned Modbus exception 0x{response[2]:02X}",
                )
            if function in (0x05, 0x0F) and response[2:6] != request[2:6]:
                raise GearError(
                    "RELAY_ECHO",
                    "Relay write acknowledgement did not match the request",
                )
            return response
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, GearError)
                else GearError("RELAY_IO", f"Relay serial communication failed: {exc}")
            )
            self._fail(error)
            if error is exc:
                raise
            raise error from exc

    @_serialized
    def read_states(self, stop_token=None):
        self._check_stop(stop_token)
        response = self._exchange(0x01, b"\x00\x00\x00\x08")
        states = [bool(response[3] & (1 << index)) for index in range(8)]
        self._publish(states=states, sources=["read_coils"] * 8)
        return list(states)

    @_serialized
    def set_channel(self, channel, on, stop_token=None):
        self._check_stop(stop_token)
        if type(channel) is not int or not 1 <= channel <= 8:
            raise GearError(
                "RELAY_CHANNEL", "Relay channel must be an integer from 1 to 8"
            )
        if type(on) is not bool:
            raise GearError("RELAY_VALUE", "Relay state must be a boolean")
        data = (channel - 1).to_bytes(2, "big") + (b"\xff\x00" if on else b"\x00\x00")
        self._exchange(0x05, data)
        states = self.snapshot()["states"]
        states[channel - 1] = on
        sources = self.snapshot()["sources"]
        sources[channel - 1] = "write_acknowledgement"
        self._publish(states=states, sources=sources)

    @_serialized
    def set_all(self, on, stop_token=None):
        self._check_stop(stop_token)
        if type(on) is not bool:
            raise GearError("RELAY_VALUE", "Relay state must be a boolean")
        self._exchange(0x0F, b"\x00\x00\x00\x08\x01" + (b"\xff" if on else b"\x00"))
        self._publish(states=[on] * 8, sources=["write_acknowledgement"] * 8)
