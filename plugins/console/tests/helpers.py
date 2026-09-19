from copy import deepcopy
import queue
import threading
import time
from types import SimpleNamespace


def slice_for(**changes):
    config = {"port": "COM77", "role": "MCU", **changes}
    return {
        "plugin": {"id": "gear.console", "config": {}},
        "resources": {
            "CONSOLE.mcu": {"type": "CONSOLE", "device": "ADB001", "config": config}
        },
    }


class Token:
    def __init__(self):
        self.event = threading.Event()

    def is_requested(self):
        return self.event.is_set()

    def wait(self, timeout_s):
        return self.event.wait(timeout_s)


def context(path, run_id="run1", phase="body"):
    return SimpleNamespace(
        run_id=run_id,
        phase=phase,
        artifact_dir=str(path),
        stop_token=Token(),
        monotonic=time.monotonic,
        emit=lambda d: None,
        step_path="body[0]",
        iteration=None,
        call_id="call1",
    )


def wait_until(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "Timed out waiting for fake serial receiver"


class FakeSerial:
    def __init__(self, **settings):
        self.settings = settings
        self.rx = queue.Queue()
        self.writes = []
        self.open_count = self.close_count = 0
        self.threads = []
        self.read_error = self.write_error = self.close_error = None
        self.read_gate = None
        self.short_write = False

    def open(self):
        self.threads.append(threading.get_ident())
        self.open_count += 1

    def read(self, size, timeout_s=None):
        self.threads.append(threading.get_ident())
        if self.read_gate:
            self.read_gate.wait(3)
        if self.read_error:
            raise self.read_error
        try:
            return self.rx.get(timeout=timeout_s)
        except queue.Empty:
            return b""

    def write(self, data, timeout_s=None):
        self.threads.append(threading.get_ident())
        if self.write_error:
            raise self.write_error
        self.writes.append(bytes(data))
        return len(data) - 1 if self.short_write else len(data)

    def close(self):
        self.threads.append(threading.get_ident())
        self.close_count += 1
        if self.close_error:
            raise self.close_error


class Factory:
    def __init__(self):
        self.items = []

    def __call__(self, **settings):
        item = FakeSerial(**settings)
        self.items.append(item)
        return item
