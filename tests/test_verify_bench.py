"""The simulated bench and its hardware stubs, without the Framework."""

import ctypes
import subprocess
import time

import pytest

from gear_verify.bench import SimBench
from gear_verify.seal import sealed
from gear_verify.stubs import SimCameraBackend, SimConsoleSerial, SimRelaySerial, crc16
from gear_verify.view import changes, flatten, render

ENVIRONMENT = {
    "devices": {"BOARD001": {}, "BOARD002": {}},
    "plugins": {
        "gear.relay": {"config": {"controllers": {"box": {"port": "COM79"}}}},
    },
    "resources": {
        "POWER.kl30": {
            "type": "POWER",
            "device": "BOARD001",
            "config": {"controller": "box", "channel": 1, "role": "KL30"},
        },
        "POWER.kl15": {
            "type": "POWER",
            "device": "BOARD001",
            "config": {"controller": "box", "channel": 2, "role": "KL15"},
        },
        "POWER.spare": {
            "type": "POWER",
            "device": "BOARD001",
            "config": {"controller": "box", "channel": 3},
        },
        "CONSOLE.mcu": {
            "type": "CONSOLE",
            "device": "BOARD001",
            "config": {"port": "COM77", "role": "MCU"},
        },
        "SCREEN.center": {
            "type": "SCREEN",
            "device": "BOARD001",
            "config": {"camera_id": "cam-a", "role": "中控屏"},
        },
    },
}


def request(unit, function, data):
    payload = bytes([unit, function]) + data
    return payload + crc16(payload).to_bytes(2, "little")


def read_response(serial):
    return serial.read(64, timeout_s=0.5)


@pytest.fixture
def bench():
    came_up = SimBench(ENVIRONMENT, init_power=False, boot_delay_s=0.2)
    came_up.start()
    yield came_up
    came_up.stop()


def test_environment_wiring_maps_channels_to_board_rails(bench):
    assert bench.controllers["COM79"][1] == ("BOARD001", "KL30")
    assert bench.controllers["COM79"][2] == ("BOARD001", "KL15")
    # A channel without a KL role drives nothing on the board.
    assert bench.controllers["COM79"][3] == ("BOARD001", None)
    assert set(bench.devices) == {"BOARD001", "BOARD002"}


def test_board_boots_only_after_power_and_delay(bench):
    device = bench.devices["BOARD001"]
    assert device.adb_state == "missing"

    bench.set_channel("COM79", 1, True)
    bench.set_channel("COM79", 2, True)
    assert device.powered and not device.booted
    assert device.adb_state == "offline"

    time.sleep(0.45)
    assert device.booted and device.boots == 1
    assert device.adb_state == "device"
    assert bench.screens["cam-a"].lit


def test_power_loss_is_immediate_and_costs_a_reboot(bench):
    bench.set_channel("COM79", 1, True)
    bench.set_channel("COM79", 2, True)
    time.sleep(0.45)
    assert bench.devices["BOARD001"].adb_state == "device"

    bench.set_channel("COM79", 2, False)
    assert bench.devices["BOARD001"].adb_state == "missing"
    assert not bench.devices["BOARD001"].booted
    assert not bench.screens["cam-a"].lit


def test_a_board_without_a_relay_binding_keeps_the_starting_power():
    # BOARD002 owns no resources, so no coil can switch it off.
    running = SimBench(ENVIRONMENT, init_power=True, boot_delay_s=0.2)
    try:
        assert running.devices["BOARD002"].powered
        assert running.devices["BOARD002"].adb_state == "device"
        # A bench that is already running is already showing a picture.
        assert running.screens["cam-a"].lit
        off = SimBench(ENVIRONMENT, init_power=False, boot_delay_s=0.2)
        assert not off.devices["BOARD002"].powered
        assert not off.screens["cam-a"].lit
    finally:
        running.stop()


def test_relay_stub_answers_read_coils_write_single_and_write_all(bench):
    serial = SimRelaySerial(bench, port="COM79")
    serial.open()

    assert serial.write(request(1, 0x0F, b"\x00\x00\x00\x08\x01\xff")) == 10
    echo = read_response(serial)
    assert echo[1] == 0x0F and echo[2:6] == b"\x00\x00\x00\x08"

    assert serial.write(request(1, 0x05, b"\x00\x02\x00\x00")) == 8
    assert read_response(serial)[:2] == b"\x01\x05"

    assert serial.write(request(1, 0x01, b"\x00\x00\x00\x08")) == 8
    read = read_response(serial)
    # Write-all closed everything; CH3 was then released, so only bit 3 is clear.
    assert read[2] == 1 and read[3] == 0b11111011


def test_relay_stub_rejects_a_corrupt_frame(bench):
    serial = SimRelaySerial(bench, port="COM79")
    serial.open()
    with pytest.raises(Exception):
        serial.write(request(1, 0x05, b"\x00\x02\x00\x00")[:-1] + b"\x00")


def test_relay_stub_read_reports_nothing_after_close(bench):
    serial = SimRelaySerial(bench, port="COM79")
    serial.open()
    serial.close()
    assert serial.read(8, timeout_s=0.05) == b""


def test_console_stub_echoes_writes_and_honours_the_read_timeout(bench):
    serial = SimConsoleSerial(bench, port="COM77")
    serial.open()
    assert serial.write(b"status\n") == 7
    assert serial.read(64, timeout_s=0.5) == b"status\n"

    started = time.monotonic()
    assert serial.read(64, timeout_s=0.1) == b""
    assert time.monotonic() - started >= 0.05


def test_console_stub_receives_boot_output_once_powered(bench):
    serial = SimConsoleSerial(bench, port="COM77")
    serial.open()
    assert serial.read(64, timeout_s=0.05) == b""
    bench.set_channel("COM79", 1, True)
    bench.set_channel("COM79", 2, True)
    deadline = time.monotonic() + 2
    output = b""
    while b"userspace ready" not in output and time.monotonic() < deadline:
        output += serial.read(4096, timeout_s=0.1)
    assert b"BOARD001" in output


def test_camera_backend_serves_frames_that_go_dark(bench):
    frames = []
    backend = SimCameraBackend(bench, frames.append)
    assert [item["id"] for item in backend.request("list")] == ["cam-a"]

    backend.request("open", camera_id="cam-a")
    assert frames[0] == {"event": "active", "camera_id": "cam-a", "active": True}

    bench.bind_screen_emit("cam-a", lambda lit: backend._frame("cam-a", lit))
    backend._frame("cam-a", False)
    backend._frame("cam-a", True)
    payloads = [item["jpeg"] for item in frames if item["event"] == "frame"]
    assert len(payloads) == 2 and payloads[0] != payloads[1]

    backend.request("stop", camera_id="cam-a")
    assert frames[-1]["active"] is False


def test_camera_backend_rejects_an_unknown_command_and_camera(bench):
    backend = SimCameraBackend(bench, lambda message: None)
    with pytest.raises(Exception):
        backend.request("rewind")
    with pytest.raises(Exception):
        backend.request("open", camera_id="missing")


def test_view_reports_only_what_moved(bench):
    before = flatten(bench.snapshot())
    assert changes(before, before) == []

    bench.set_channel("COM79", 2, True)
    after = flatten(bench.snapshot())
    moved = changes(before, after)
    assert any("KL15" in entry and "ON" in entry for entry in moved)
    assert "".join(render(bench.snapshot())).count("BOARD001") >= 1


def test_seal_blocks_tool_processes_and_restores_them():
    original = subprocess.Popen
    with sealed():
        with pytest.raises(RuntimeError):
            subprocess.Popen(["cmd"])
    assert subprocess.Popen is original


def test_seal_blocks_native_ports_after_the_control_session_lock():
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("Windows-only guard")
    native = ctypes.WinDLL
    with sealed() as seal:
        seal.ports()
        with pytest.raises(RuntimeError):
            ctypes.WinDLL("kernel32")
    assert ctypes.WinDLL is native
