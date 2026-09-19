"""The hardware each plugin talks to, replaced by simulated equivalents.

Each stub satisfies exactly the duck type its plugin calls, so the plugin's own
code — Modbus framing, serial ownership, the ADB text-protobuf parser, the
capture cache — keeps running unchanged. Only the wire, the tool and the device
are simulated.
"""

from __future__ import annotations

import base64
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from gear_contracts.api import GearError

from .bench import CHANNELS
from .frames import HEIGHT, WIDTH, frame_bytes

LOG_CADENCE_S = 1.0
LOG_LINES = (
    "01-01 00:00:01.000  1000  1000 I gear-sim : boot",
    "01-01 00:00:02.000  1000  1200 I gear-sim : service ready",
    "01-01 00:00:03.000  1000  1400 W gear-sim : simulated warning",
)

SIM_PROPERTIES = {
    "ro.build.version.release": "13",
    "ro.product.model": "GEAR-SIM",
    "ro.product.device": "gear_sim",
}


def crc16(data):
    """The Modbus CRC-16 the real relay transport also computes."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _modbus_frame(payload):
    return payload + crc16(payload).to_bytes(2, "little")


# -- relay: one Modbus RTU slave per controller port ---------------------


class SimRelaySerial:
    """Eight coils behind a CRC-checked Modbus RTU serial link."""

    def __init__(self, bench, *, port, **params):
        self._bench = bench
        self.port = port
        self.params = params
        self.opened = False
        self.frames = 0
        self._pending = bytearray()
        self._cv = threading.Condition()

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False
        with self._cv:
            self._cv.notify_all()

    def _check(self, request):
        if len(request) < 4 or crc16(request[:-2]) != int.from_bytes(
            request[-2:], "little"
        ):
            raise GearError("RELAY_CRC", "模拟继电器收到的请求 CRC 不匹配。")
        function = request[1]
        expected = {0x01: 8, 0x05: 8, 0x0F: 10}.get(function)
        if expected is None:
            raise GearError(
                "RELAY_FUNCTION", f"模拟继电器不支持功能码 0x{function:02X}。"
            )
        if len(request) != expected:
            raise GearError("RELAY_LENGTH", "模拟继电器收到的请求长度不正确。")

    def write(self, data, timeout_s=None):
        if not self.opened:
            raise GearError("RELAY_IO", "模拟继电器串口未打开。")
        request = bytes(data)
        self._check(request)
        unit, function = request[0], request[1]
        if function == 0x05:
            channel = int.from_bytes(request[2:4], "big") + 1
            self._bench.set_channel(self.port, channel, request[4:6] == b"\xff\x00")
            response = _modbus_frame(request[:6])
        elif function == 0x0F:
            count = int.from_bytes(request[4:6], "big")
            on = request[7] == 0xFF
            for channel in range(1, count + 1):
                self._bench.set_channel(self.port, channel, on)
            response = _modbus_frame(request[:6])
        else:
            coils = self._bench.read_coils(self.port)
            mask = sum(1 << (channel - 1) for channel, on in coils.items() if on)
            response = _modbus_frame(bytes([unit, 0x01, 1, mask]))
        with self._cv:
            self._pending += response
            self._cv.notify_all()
        self.frames += 1
        return len(request)

    def read(self, size, timeout_s=None):
        if size <= 0:
            return b""
        deadline = time.monotonic() + (1.0 if timeout_s is None else timeout_s)
        with self._cv:
            while not self._pending and self.opened:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b""
                self._cv.wait(min(remaining, 0.05))
            chunk = bytes(self._pending[:size])
            del self._pending[: len(chunk)]
            return chunk


# -- console: one byte stream per COM port -------------------------------


class SimConsoleSerial:
    """A COM port that echoes what it is sent and talks while powered."""

    def __init__(self, bench, *, port, **params):
        self._bench = bench
        self.port = port
        self.params = params
        self.opened = False
        self.written = 0
        self._console = None
        self._pending = bytearray()
        self._cv = threading.Condition()

    def open(self):
        self.opened = True
        self._console = self._bench.bind_console(self.port, True)

    def close(self):
        self.opened = False
        self._bench.bind_console(self.port, False)
        with self._cv:
            self._cv.notify_all()

    def write(self, data, timeout_s=None):
        if not self.opened:
            raise GearError("CONSOLE_IO_ERROR", "模拟串口未打开。")
        chunk = bytes(data)
        self.written += len(chunk)
        # Terminal-like echo: what a case sends becomes observable output.
        self._console.feed(chunk)
        return len(chunk)

    def read(self, size, timeout_s=None):
        if size <= 0:
            return b""
        deadline = time.monotonic() + (1.0 if timeout_s is None else timeout_s)
        with self._cv:
            while not self._pending and self.opened:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b""
                try:
                    self._pending += self._console.rx.get(timeout=min(remaining, 0.05))
                except queue.Empty:
                    continue
            chunk = bytes(self._pending[:size])
            del self._pending[: len(chunk)]
            return chunk


# -- adb: the emulated `adb` tool and the process object around it -------


class Pipe:
    """A minimal read side of a process pipe, fed by the emulated tool."""

    def __init__(self, text=False):
        self.text = text
        self._cv = threading.Condition()
        self._buffer = bytearray()
        self._eof = False
        self._closed = False

    def feed(self, data):
        with self._cv:
            self._buffer += data
            self._cv.notify_all()

    def finish(self):
        with self._cv:
            self._eof = True
            self._cv.notify_all()

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def _decode(self, data):
        return data.decode("utf-8", "replace") if self.text else data

    def _take(self, size):
        chunk = bytes(self._buffer[:size])
        del self._buffer[: len(chunk)]
        return chunk

    def read(self, size=-1):
        with self._cv:
            if size is None or size < 0:
                while not self._eof and not self._closed:
                    self._cv.wait(0.05)
                return self._decode(self._take(len(self._buffer)))
            while len(self._buffer) < size and not self._eof and not self._closed:
                self._cv.wait(0.05)
            return self._decode(self._take(size))

    def readline(self):
        with self._cv:
            while b"\n" not in self._buffer and not self._eof and not self._closed:
                self._cv.wait(0.05)
            end = self._buffer.find(b"\n")
            return self._decode(self._take(len(self._buffer) if end < 0 else end + 1))

    def __iter__(self):
        while True:
            line = self.readline()
            if not line:
                return
            yield line


class SimProcess:
    """The subprocess.Popen surface the ADB plugin uses, over the emulated tool."""

    def __init__(self, bench, executable, arguments, **options):
        text = bool(options.get("text"))
        merged = options.get("stderr") is subprocess.STDOUT
        self.stdout = Pipe(text=text)
        self.stderr = self.stdout if merged else Pipe(text=text)
        self.returncode = None
        self._exited = threading.Event()
        self._stop = threading.Event()
        self._producer = None
        plan = _plan(bench, arguments)
        if plan[0] == "stream":
            self._stdout_rest = ""
            self._stderr_rest = ""
            self._producer = threading.Thread(
                target=self._stream, args=(plan[1],), daemon=True
            )
            self._producer.start()
        else:
            _, code, out, err = plan
            self._stdout_rest = out.decode("utf-8", "replace") if text else out
            self._stderr_rest = err.decode("utf-8", "replace") if text else err
            self.stdout.feed(out)
            self.stdout.finish()
            if self.stderr is not self.stdout:
                self.stderr.feed(err)
                self.stderr.finish()
            self.returncode = code
            self._exited.set()

    def _stream(self, lines):
        # A live log stream stays open until the client is stopped.
        index = 0
        while not self._stop.is_set():
            self.stdout.feed(lines[index % len(lines)])
            index += 1
            if self._stop.wait(LOG_CADENCE_S):
                break
        self.stdout.finish()
        if self.stderr is not self.stdout:
            self.stderr.finish()
        self.returncode = -9 if self._stop.is_set() else 0
        self._exited.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._exited.wait(timeout)
        return self.returncode

    def communicate(self, timeout=None):
        self._exited.wait(timeout)
        return self._stdout_rest, self._stderr_rest

    def terminate(self):
        self.kill()

    def kill(self):
        self._stop.set()
        if self._producer is not None:
            self._producer.join(2)
        self.stdout.finish()
        if self.stderr is not self.stdout:
            self.stderr.finish()
        if self.returncode is None:
            self.returncode = -9
        self._exited.set()


def _device_payload(bench):
    """ADB's flat Device text-protobuf snapshot: only live boards appear."""
    records = []
    for device in bench.adb_devices():
        records.append(
            "device {\n"
            f'serial: "{device.serial}"\n'
            "state: DEVICE\n"
            f"transport_id: {device.transport_id}\n"
            f'model: "GEAR-SIM {device.serial}"\n'
            "connection_type: USB\n"
            "}\n"
        )
    text = "".join(records)
    payload = text.encode("utf-8")
    return f"{len(payload):04x}".encode("ascii") + payload


def _shell(device, command):
    if device is None or device.adb_state != "device":
        return 1, b"", b"error: device offline\n"
    text = (command or "").strip()
    if text.startswith("echo "):
        output = text[5:].strip().strip("'\"") + "\n"
    elif text.startswith("getprop "):
        key = text[8:].strip()
        if key == "ro.serialno":
            output = device.serial + "\n"
        else:
            value = SIM_PROPERTIES.get(key, "")
            output = (value + "\n") if value else ""
    elif text == "id":
        output = "shell\n"
    else:
        output = ""
    return 0, output.encode("utf-8"), b""


def _pull(device, arguments):
    if device is None or device.adb_state != "device":
        return 1, b"", b"error: device offline\n"
    remote, destination = arguments[1], arguments[2]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", remote.rstrip("/").rsplit("/", 1)[-1])
    folder = Path(destination) / (name or "pull")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dump.txt").write_text(
        f"[sim] {device.serial} {remote}\n", encoding="utf-8"
    )
    return 0, f"{remote}: 1 file pulled.\n".encode("utf-8"), b""


def _plan(bench, arguments):
    """Emulate one `adb` invocation: ('finite', code, out, err) or ('stream', lines)."""
    arguments = list(arguments)
    index = 0
    transport_id = None
    while index < len(arguments) and arguments[index] == "-t":
        if index + 2 > len(arguments):
            break
        transport_id = arguments[index + 1]
        index += 2
    command = arguments[index:]
    device = None
    if transport_id is not None:
        device = bench.device_by_transport(transport_id)

    if command[:2] == ["track-devices", "--proto-text"]:
        return "finite", 0, _device_payload(bench), b""
    if command and command[0] == "shell":
        return "finite", *_shell(device, command[-1] if len(command) > 1 else "")
    if command and command[0] == "pull" and len(command) == 3:
        return "finite", *_pull(device, command)
    if command[:2] == ["logcat", "-d"]:
        if device is None or device.adb_state != "device":
            return "finite", 1, b"", b"error: device offline\n"
        body = "\n".join(LOG_LINES) + "\n"
        return "finite", 0, body.encode("utf-8"), b""
    if command and command[0] == "logcat":
        if device is None or device.adb_state != "device":
            return "finite", 1, b"", b"error: device offline\n"
        return "stream", [line.encode("utf-8") + b"\n" for line in LOG_LINES]
    return (
        "finite",
        1,
        b"",
        f"simulated adb: unsupported command {' '.join(arguments)}\n".encode("utf-8"),
    )


# -- camera: a capture input pointed at the board screen ------------------


class SimCameraBackend:
    """Frames keep arriving; they go dark when the board loses power."""

    def __init__(self, bench, emit):
        self._bench = bench
        self._emit = emit

    def request(self, command, **values):
        if command == "list":
            return [
                {
                    "id": screen.camera_id,
                    "description": f"模拟{screen.role}（{screen.device or '未归属'}）",
                }
                for screen in self._bench.screens.values()
            ]
        if command == "open":
            return self._open(values.get("camera_id"))
        if command == "stop":
            return self._stop(values.get("camera_id"))
        if command == "close":
            for camera_id in list(self._bench.screens):
                self._stop(camera_id)
            return None
        raise GearError(
            "CAMERA_BACKEND_ERROR", f"模拟摄像头不支持命令 {command}。"
        )

    def _open(self, camera_id):
        if camera_id not in self._bench.screens:
            raise GearError(
                "CAMERA_BACKEND_ERROR", f"模拟台架上没有摄像头 {camera_id}。"
            )
        self._bench.bind_screen(camera_id, True)
        self._bench.bind_screen_emit(
            camera_id, lambda lit: self._frame(camera_id, lit)
        )
        self._emit({"event": "active", "camera_id": camera_id, "active": True})

    def _stop(self, camera_id):
        self._bench.bind_screen(camera_id, False)
        self._bench.bind_screen_emit(camera_id, None)
        self._emit({"event": "active", "camera_id": camera_id, "active": False})

    def _frame(self, camera_id, lit):
        self._emit(
            {
                "event": "frame",
                "camera_id": camera_id,
                "width": WIDTH,
                "height": HEIGHT,
                "jpeg": base64.b64encode(frame_bytes(lit)).decode("ascii"),
            }
        )

    def close(self):
        for camera_id in list(self._bench.screens):
            self._bench.bind_screen_emit(camera_id, None)


def relay_serial_factory(bench):
    def make(**params):
        return SimRelaySerial(bench, **params)

    return make


def console_serial_factory(bench):
    def make(**params):
        return SimConsoleSerial(bench, **params)

    return make


def make_adb_service(bench):
    """The real ADB service, with every tool invocation going to the emulator.

    Only the two places that start a tool process are replaced; discovery
    parsing, target selection, command dispatch and evidence stay the plugin's.
    """
    from gear_adb.transport import AdbService

    class SimAdbService(AdbService):
        def __init__(self):
            super().__init__()
            self._bench = bench

        def _start(self, arguments, **options):
            return SimProcess(bench, self._adb_path, list(arguments), **options)

        def _discover_fastboot(self):
            # The simulated bench never exposes a board in fastboot mode, and the
            # host-side fastboot network registry is not this bench's business.
            return []

    return SimAdbService()


def camera_backend_factory(bench):
    def make(emit):
        return SimCameraBackend(bench, emit)

    return make
