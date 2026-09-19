"""One simulated bench shared by every plugin stub.

The bench owns the physical world the four plugins believe they are talking to:
relay coils, the board power rails those coils drive, the board's boot sequence,
and the ADB/screen/console state that follows from it. Cross-plugin causality
lives here rather than in four unrelated fakes, so a case that drops KL15 really
does lose ADB and really does go dark on screen.
"""

from __future__ import annotations

import queue
import re
import threading
import time

KL30 = "KL30"
KL15 = "KL15"
TICK_S = 0.05
FRAME_INTERVAL_S = 0.25
CHANNELS = 8

BOOT_LINES = (
    "[sim] {serial} bootloader 1.0",
    "[sim] power-on self test: OK",
    "[sim] kernel 6.1.0-gear-sim",
    "[sim] userspace ready",
)


def rail_name(alias, config):
    """Name the board rail a POWER resource drives; None means a plain switch."""
    role = config.get("role")
    if isinstance(role, str) and role.strip():
        return role.strip().upper()
    upper = alias.upper()
    return upper if re.fullmatch(r"KL\d+", upper) else None


def _text(value, default=""):
    return value if isinstance(value, str) and value else default


class Console:
    """One simulated COM port. Bytes queued here are what the plugin reads."""

    def __init__(self, port, device, role):
        self.port = port
        self.device = device
        self.role = role
        self.rx = queue.Queue()
        self.opened = False
        self.received = 0
        self.sent = 0

    def feed(self, data):
        """Queue bytes for the plugin; the counter is what it has been given."""
        self.received += len(data)
        self.rx.put(data)


class Screen:
    """One simulated capture input. `emit` is set by the camera backend."""

    def __init__(self, camera_id, device, role):
        self.camera_id = camera_id
        self.device = device
        self.role = role
        self.opened = False
        self.frames = 0
        self.lit = False
        self.emit = None


class Device:
    """One board: power rails in, boot state and peripherals out."""

    def __init__(self, serial, transport_id, default_rail):
        self.serial = serial
        self.transport_id = transport_id
        self._default = default_rail
        self.rails = {}
        self.booted = False
        self.boot_started = None
        self.boots = 0

    def rail(self, name):
        return self.rails.get(name, self._default)

    @property
    def powered(self):
        return self.rail(KL30) and self.rail(KL15)

    @property
    def adb_state(self):
        if not self.powered:
            return "missing"
        return "device" if self.booted else "offline"


class SimBench:
    """The simulated bench. One instance per verification session."""

    def __init__(
        self,
        environment,
        *,
        init_power=True,
        boot_delay_s=3.0,
        tick_s=TICK_S,
        clock=time.monotonic,
    ):
        self._lock = threading.RLock()
        self._clock = clock
        self._tick_s = tick_s
        self._stop = threading.Event()
        self._thread = None
        self._frames_at = 0.0
        self.boot_delay_s = float(boot_delay_s)
        self.devices = {}
        self.consoles = {}
        self.screens = {}
        self.coils = {}
        self.controllers = {}
        self.notes = []
        self._build(environment, bool(init_power))

    # -- construction ----------------------------------------------------

    def _controllers(self, environment):
        plugin = (environment.get("plugins") or {}).get("gear.relay") or {}
        config = plugin.get("config") or {}
        declared = config.get("controllers")
        if isinstance(declared, dict):
            return {
                cid: _text((record or {}).get("port"))
                for cid, record in declared.items()
                if isinstance(record, dict)
            }
        # Legacy flat relay configuration uses one implicit controller.
        return {"main": _text(config.get("port"))} if config.get("port") else {}

    def _build(self, environment, init_power):
        resources = environment.get("resources") or {}
        devices = set((environment.get("devices") or {}).keys())
        controllers = self._controllers(environment)

        for record in resources.values():
            device = (record or {}).get("device")
            if isinstance(device, str) and device:
                devices.add(device)
        for serial in sorted(devices):
            self.devices[serial] = Device(serial, len(self.devices) + 1, init_power)

        for resource_id, record in resources.items():
            record = record or {}
            kind = record.get("type")
            config = record.get("config") or {}
            device_id = _text(record.get("device"))
            alias = resource_id.split(".", 1)[1] if "." in resource_id else resource_id
            if kind == "POWER":
                port = controllers.get(_text(config.get("controller")), "")
                channel = config.get("channel")
                if not device_id:
                    self.notes.append(
                        f"{resource_id}: 未绑定单板，该继电器通路不会影响任何台架状态"
                    )
                    continue
                if not port or type(channel) is not int or not 1 <= channel <= CHANNELS:
                    self.notes.append(
                        f"{resource_id}: 没有可用的控制器/通路，该继电器通路不影响台架状态"
                    )
                    continue
                self.controllers.setdefault(port, {})[channel] = (
                    device_id,
                    rail_name(alias, config),
                )
            elif kind == "CONSOLE":
                port = _text(config.get("port"))
                if port:
                    self.consoles[port] = Console(port, device_id, _text(config.get("role"), "—"))
            elif kind == "SCREEN":
                camera_id = _text(config.get("camera_id"))
                if camera_id:
                    self.screens[camera_id] = Screen(
                        camera_id, device_id, _text(config.get("role"), "—")
                    )

        for port in self.controllers:
            self.coils[port] = {channel: init_power for channel in range(1, CHANNELS + 1)}
        for port, wiring in self.controllers.items():
            for channel, (device_id, rail) in wiring.items():
                if rail and device_id in self.devices:
                    self.devices[device_id].rails[rail] = init_power

        if init_power:
            for device in self.devices.values():
                if device.powered:
                    device.booted = True
                    for screen in device_screens(self, device.serial):
                        screen.lit = True

    # -- ticks -----------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        # A bench that is already running is already streaming.
        self._frames_at = self._clock() - FRAME_INTERVAL_S
        self._thread = threading.Thread(
            target=self._loop, name="gear-verify-bench", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)

    def _loop(self):
        while not self._stop.wait(self._tick_s):
            now = self._clock()
            with self._lock:
                self._advance(now)
                if now - self._frames_at >= FRAME_INTERVAL_S:
                    self._frames_at = now
                    self._pump_frames()

    def _sync_power(self, device):
        """Power loss is immediate; power-up starts the boot timer."""
        if device.powered:
            if not device.booted and device.boot_started is None:
                device.boot_started = self._clock()
            return
        if device.booted or device.boot_started is not None:
            device.booted = False
            device.boot_started = None
            for screen in device_screens(self, device.serial):
                screen.lit = False

    def _advance(self, now):
        for device in self.devices.values():
            if not device.powered or device.booted or device.boot_started is None:
                continue
            if now - device.boot_started >= self.boot_delay_s:
                device.booted = True
                device.boot_started = None
                device.boots += 1
                for screen in device_screens(self, device.serial):
                    screen.lit = True
                self._announce(device)

    def _announce(self, device):
        for console in self.consoles.values():
            if console.device == device.serial and console.opened:
                console.feed(
                    (
                        "".join(
                            line.format(serial=device.serial) + "\r\n"
                            for line in BOOT_LINES
                        )
                    ).encode("utf-8")
                )

    def _pump_frames(self):
        for screen in self.screens.values():
            if screen.opened and screen.emit is not None:
                screen.frames += 1
                try:
                    screen.emit(screen.lit)
                except Exception as exc:  # a broken consumer must not stop the bench
                    self.notes.append(f"screen {screen.camera_id}: {exc}")

    # -- plugin-facing operations ----------------------------------------

    def bind_console(self, port, opened):
        with self._lock:
            console = self.consoles.get(port)
            if console is None:
                console = self.consoles[port] = Console(port, "", "—")
            console.opened = opened
            return console

    def bind_screen(self, camera_id, opened):
        with self._lock:
            screen = self.screens.get(camera_id)
            if screen is not None:
                screen.opened = opened
            return screen

    def bind_screen_emit(self, camera_id, emit):
        with self._lock:
            screen = self.screens.get(camera_id)
            if screen is not None:
                screen.emit = emit

    def set_channel(self, port, channel, on):
        with self._lock:
            coils = self.coils.setdefault(
                port, {index: False for index in range(1, CHANNELS + 1)}
            )
            coils[channel] = on
            wiring = self.controllers.get(port, {}).get(channel)
            if wiring is None:
                return
            serial, rail = wiring
            device = self.devices.get(serial)
            if device is not None and rail:
                device.rails[rail] = on
                self._sync_power(device)

    def read_coils(self, port):
        with self._lock:
            return dict(
                self.coils.setdefault(
                    port, {index: False for index in range(1, CHANNELS + 1)}
                )
            )

    def adb_devices(self):
        """The ADB tool's view: only powered, booted boards are listed."""
        with self._lock:
            return [
                device
                for device in self.devices.values()
                if device.adb_state == "device"
            ]

    def device(self, serial):
        with self._lock:
            return self.devices.get(serial)

    def device_by_transport(self, transport_id):
        """ADB passes the transport id as text; the bench stores it as a number."""
        wanted = str(transport_id)
        with self._lock:
            for device in self.devices.values():
                if str(device.transport_id) == wanted:
                    return device
        return None

    # -- observation -----------------------------------------------------

    def snapshot(self):
        with self._lock:
            return {
                "devices": {
                    serial: {
                        "powered": device.powered,
                        "booted": device.booted,
                        "boots": device.boots,
                        "adb": device.adb_state,
                        "rails": {
                            name: bool(value) for name, value in device.rails.items()
                        },
                        "screens": {
                            screen.camera_id: {
                                "lit": screen.lit,
                                "open": screen.opened,
                                "frames": screen.frames,
                            }
                            for screen in self.screens.values()
                            if screen.device == serial
                        },
                        "consoles": {
                            console.port: {
                                "open": console.opened,
                                "rx_bytes": console.received,
                            }
                            for console in self.consoles.values()
                            if console.device == serial
                        },
                    }
                    for serial, device in self.devices.items()
                },
                "coils": {
                    port: dict(channels) for port, channels in self.coils.items()
                },
                "notes": list(self.notes),
            }


def device_screens(bench, serial):
    return [screen for screen in bench.screens.values() if screen.device == serial]
