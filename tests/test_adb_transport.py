"""Real child processes substitute only the physical adb executable boundary."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from gear_contracts.api import GearError

FAKE_ADB = r"""
import json, os, pathlib, sys, time
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
sys.stderr.reconfigure(encoding="utf-8", newline="\n")
cfg = json.loads(pathlib.Path(os.environ["GEAR_FAKE_ADB_CONFIG"]).read_text(encoding="utf-8"))
args = sys.argv[1:]
with open(os.environ["GEAR_FAKE_ADB_CALLS"], "a", encoding="utf-8") as record:
    record.write(json.dumps(args) + "\n")
if args == ["track-devices", "--proto-text"]:
    if cfg.get("discovery_error"):
        print(cfg["discovery_error"], file=sys.stderr, flush=True)
        sys.exit(2)
    if cfg.get("discovery_stall"):
        time.sleep(30)
    body = cfg.get("frame", "").encode("utf-8")
    header = cfg.get("header", f"{len(body):04x}").encode("ascii")
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()
    time.sleep(30)
elif args[:2] == ["-t", "11"] and args[2:5] == ["shell", "-n", "-T"]:
    print("output café", flush=True)
    print("remote warning", file=sys.stderr, flush=True)
    sys.exit(cfg.get("shell_exit", 7))
elif args[:3] == ["-t", "11", "pull"]:
    pathlib.Path(args[-1], "copied.txt").write_text("pulled café", encoding="utf-8")
    print("1 file pulled")
    sys.exit(cfg.get("pull_exit", 0))
elif args == ["-t", "11", "logcat", "-d", "-v", "threadtime"]:
    print("current log buffer café", flush=True)
    print("dump warning", file=sys.stderr, flush=True)
    sys.exit(cfg.get("dump_exit", 0))
elif args[:3] == ["-t", "11", "logcat"]:
    for i in range(cfg.get("log_lines", 3)):
        print(f"log café {i}", flush=True)
    if "log_exit" in cfg:
        print("device disconnected", file=sys.stderr, flush=True)
        sys.exit(cfg["log_exit"])
    time.sleep(30)
else:
    print("unexpected argv: " + repr(args), file=sys.stderr)
    sys.exit(88)
"""


USB_FRAME = """device {
  serial: "phone"
  state: DEVICE
  model: "Pixel_8"
  connection_type: USB
  negotiated_speed: 480
  max_speed: 5000
  transport_id: 11
}
"""


@pytest.fixture
def fake_adb(tmp_path, monkeypatch):
    # Per-test import scope avoids colliding with Registry's package ownership.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "plugins" / "adb"))
    helper = tmp_path / "fake_adb.py"
    helper.write_text(FAKE_ADB, encoding="utf-8")
    config_path = tmp_path / "config.json"
    calls_path = tmp_path / "calls.jsonl"
    config = {"frame": USB_FRAME}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("GEAR_FAKE_ADB_CONFIG", str(config_path))
    monkeypatch.setenv("GEAR_FAKE_ADB_CALLS", str(calls_path))
    real_popen = subprocess.Popen
    launched = []

    def popen(argv, **kwargs):
        assert argv[0] in {"fake-adb", "other-adb"}
        assert kwargs.get("shell", False) is False
        assert kwargs.get("stdin") is subprocess.DEVNULL
        if os.name == "nt":
            assert kwargs.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW
        proc = real_popen([sys.executable, "-u", str(helper), *argv[1:]], **kwargs)
        launched.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)

    def update(**values):
        config.update(values)
        config_path.write_text(json.dumps(config), encoding="utf-8")

    def calls():
        if not calls_path.exists():
            return []
        return [
            json.loads(line)
            for line in calls_path.read_text(encoding="utf-8").splitlines()
        ]

    yield update, calls, launched
    for proc in launched:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    for name in list(sys.modules):
        if name == "gear_adb" or name.startswith("gear_adb."):
            sys.modules.pop(name, None)


def service():
    module = importlib.import_module("gear_adb.transport")
    result = module.AdbService()
    result.configure("fake-adb")
    return result


def eventually(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    pytest.fail("background output did not arrive")


def test_constructor_configure_and_snapshot_do_no_io(fake_adb):
    adb = service()
    adb.configure("other-adb")
    assert adb.logcat_snapshot("phone") == {
        "running": False,
        "lines": [],
        "path": None,
        "error": None,
    }
    adb.close()
    adb.close()
    assert fake_adb[2] == []


def test_discovery_uses_explicit_usb_type_including_windows_and_offline(fake_adb):
    fake_adb[0](frame=USB_FRAME + """device {
      serial: "second"
      state: UNAUTHORIZED
      connection_type: USB
      bus_address: "usb:2-3"
      transport_id: 12
    }
    device { serial: "offline" state: OFFLINE connection_type: USB transport_id: 13 }
    device { serial: "192.168.1.7:5555" state: DEVICE connection_type: SOCKET transport_id: 14 }
    device { serial: "emulator-5554" state: DEVICE connection_type: SOCKET transport_id: 15 }
    """)
    adb = service()
    assert adb.discover() == [
        {
            "serial": "phone",
            "state": "device",
            "usb": "",
            "transport_id": "11",
            "model": "Pixel_8",
        },
        {
            "serial": "second",
            "state": "unauthorized",
            "usb": "usb:2-3",
            "transport_id": "12",
            "model": "",
        },
        {
            "serial": "offline",
            "state": "offline",
            "usb": "",
            "transport_id": "13",
            "model": "",
        },
    ]
    assert all(proc.poll() is not None for proc in fake_adb[2])
    adb.close()


def test_empty_discovery_is_not_a_query_failure(fake_adb):
    fake_adb[0](frame="")
    assert service().discover() == []


@pytest.mark.parametrize(
    "update",
    [
        {"frame": "not a protobuf device"},
        {
            "frame": 'device { serial: "a" state: DEVICE connection_type: UNKNOWN transport_id: 3 }'
        },
        {"frame": 'device { serial: "a" state: DEVICE connection_type: USB }'},
        {"header": "oops"},
        {"discovery_error": "adb server unavailable"},
    ],
)
def test_discovery_malformed_or_failed_is_an_error(fake_adb, update):
    fake_adb[0](**update)
    with pytest.raises(GearError) as caught:
        service().discover()
    assert caught.value.args[0] == "ADB_DISCOVERY_FAILED"
    assert all(proc.poll() is not None for proc in fake_adb[2])


def test_discovery_timeout_stops_and_joins_the_owned_client(fake_adb, monkeypatch):
    fake_adb[0](discovery_stall=True)
    adb = service()
    module = importlib.import_module("gear_adb.transport")
    monkeypatch.setattr(module, "_DISCOVERY_TIMEOUT", 0.1)
    with pytest.raises(GearError) as caught:
        adb.discover()
    assert caught.value.args[0] == "ADB_DISCOVERY_FAILED"
    assert all(proc.poll() is not None for proc in fake_adb[2])


def test_missing_local_tool_is_a_stable_error(fake_adb, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("adb executable was not found")

    monkeypatch.setattr(subprocess, "Popen", missing)
    with pytest.raises(GearError) as caught:
        service().discover()
    assert caught.value.args[0] == "ADB_TOOL_ERROR"


def test_shell_preserves_command_and_nonzero_remote_result(fake_adb):
    command = 'printf "hello"; printf warning >&2; exit 7'
    assert service().shell("phone", command) == {
        "exit_code": 7,
        "stdout": "output café\n",
        "stderr": "remote warning\n",
    }
    assert fake_adb[1]()[-1] == ["-t", "11", "shell", "-n", "-T", command]


@pytest.mark.parametrize(
    ("frame", "serial", "code"),
    [
        (USB_FRAME + USB_FRAME.replace("11", "12"), "phone", "ADB_AMBIGUOUS_DEVICE"),
        (USB_FRAME.replace("USB", "SOCKET"), "phone", "ADB_DEVICE_UNAVAILABLE"),
        (USB_FRAME.replace("DEVICE", "OFFLINE"), "phone", "ADB_DEVICE_UNAVAILABLE"),
        (
            USB_FRAME.replace("DEVICE", "UNAUTHORIZED"),
            "phone",
            "ADB_DEVICE_UNAVAILABLE",
        ),
        (USB_FRAME, "missing", "ADB_DEVICE_UNAVAILABLE"),
    ],
)
def test_shell_never_selects_ambiguous_network_or_unavailable_target(
    fake_adb, frame, serial, code
):
    fake_adb[0](frame=frame)
    with pytest.raises(GearError) as caught:
        service().shell(serial, "echo hello")
    assert caught.value.args[0] == code
    assert all(call == ["track-devices", "--proto-text"] for call in fake_adb[1]())


def test_pull_keeps_remote_path_as_one_argument_and_writes_supplied_folder(
    fake_adb, tmp_path
):
    result = service().pull("phone", "/sdcard/a b;echo bad", str(tmp_path))
    assert result == {"exit_code": 0, "stdout": "1 file pulled\n", "stderr": ""}
    assert (tmp_path / "copied.txt").read_text(encoding="utf-8") == "pulled café"
    assert fake_adb[1]()[-1] == [
        "-t",
        "11",
        "pull",
        "/sdcard/a b;echo bad",
        str(tmp_path),
    ]


def test_dump_logcat_uses_finite_dump_without_clearing_logs(fake_adb):
    fake_adb[0](dump_exit=3)
    assert service().dump_logcat("phone") == {
        "exit_code": 3,
        "stdout": "current log buffer café\n",
        "stderr": "dump warning\n",
    }
    assert fake_adb[1]()[-1] == ["-t", "11", "logcat", "-d", "-v", "threadtime"]
    assert all(proc.poll() is not None for proc in fake_adb[2])


def test_logcat_streams_file_and_bounded_tail_until_stop(fake_adb, tmp_path):
    fake_adb[0](log_lines=2100)
    adb = service()
    path = tmp_path / "capture.txt"
    try:
        assert adb.start_logcat("phone", str(path))["running"] is True
        eventually(lambda: "log café 2099" in adb.logcat_snapshot("phone")["lines"])
        assert len(adb.logcat_snapshot("phone")["lines"]) == 2000
        assert len(path.read_text(encoding="utf-8").splitlines()) == 2100
        snapshot = adb.logcat_snapshot("phone")
        snapshot["lines"].clear()
        assert len(adb.logcat_snapshot("phone")["lines"]) == 2000
        adb.configure("fake-adb")
        assert adb.logcat_snapshot("phone")["running"] is True
        stopped = adb.stop_logcat("phone")
        assert stopped["running"] is False
        assert stopped["error"] is None
        assert stopped["path"] == str(path)
        assert all(proc.poll() is not None for proc in fake_adb[2])
    finally:
        adb.close()


def test_logcat_refuses_existing_file(fake_adb, tmp_path):
    path = tmp_path / "existing.txt"
    path.write_text("keep this", encoding="utf-8")
    adb = service()
    with pytest.raises(GearError) as caught:
        adb.start_logcat("phone", str(path))
    assert caught.value.args[0] == "ADB_LOGCAT_FILE_ERROR"
    assert path.read_text(encoding="utf-8") == "keep this"
    adb.close()


def test_logcat_unexpected_exit_is_reported_without_restart(fake_adb, tmp_path):
    fake_adb[0](log_exit=3)
    adb = service()
    try:
        adb.start_logcat("phone", str(tmp_path / "capture.txt"))
        eventually(lambda: not adb.logcat_snapshot("phone")["running"])
        snapshot = adb.logcat_snapshot("phone")
        assert "3" in snapshot["error"]
        assert "device disconnected" in snapshot["lines"]
        assert len([call for call in fake_adb[1]() if "logcat" in call]) == 1
    finally:
        adb.close()


@pytest.mark.parametrize("action", ["configure", "close"])
def test_reconfigure_and_close_join_logcat_client_and_reader(
    fake_adb, tmp_path, action
):
    adb = service()
    path = tmp_path / "capture.txt"
    adb.start_logcat("phone", str(path))
    eventually(lambda: adb.logcat_snapshot("phone")["lines"])
    if action == "configure":
        adb.configure("other-adb")
    else:
        adb.close()
    assert adb.logcat_snapshot("phone")["running"] is False
    assert all(proc.poll() is not None for proc in fake_adb[2])
    # Renaming checks the reader released the output file on Windows.
    path.rename(tmp_path / "released.txt")
    adb.close()


def test_logcat_file_error_marks_capture_stopped_even_if_close_also_fails(
    fake_adb, tmp_path, monkeypatch
):
    destination = tmp_path / "full.txt"
    original_open = Path.open

    class FullDisk:
        def __init__(self, file):
            self.file = file

        def write(self, line):
            raise OSError("disk is full")

        def close(self):
            self.file.close()
            raise OSError("flush during close failed")

    def open_file(path, *args, **kwargs):
        file = original_open(path, *args, **kwargs)
        return (
            FullDisk(file) if path == destination and args and args[0] == "x" else file
        )

    monkeypatch.setattr(Path, "open", open_file)
    adb = service()
    try:
        adb.start_logcat("phone", str(destination))
        eventually(lambda: not adb.logcat_snapshot("phone")["running"], timeout=1)
        assert "disk is full" in adb.logcat_snapshot("phone")["error"]
        assert "close failed" in adb.logcat_snapshot("phone")["error"]
        assert all(proc.poll() is not None for proc in fake_adb[2])
    finally:
        adb.close()
