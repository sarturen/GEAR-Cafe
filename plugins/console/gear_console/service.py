"""One COM, one I/O thread: read, queued writes and close never overlap."""

from __future__ import annotations
import codecs
from dataclasses import dataclass, field
import queue
import threading
import time
import uuid
from gear_contracts.api import GearError, StopRequested
from .config import LINE_ENDINGS, normalize_config, error_diagnostic
from .serial_win32 import WindowsSerial


@dataclass
class _Write:
    data: bytes
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False
    error: Exception | None = None
    written: int = 0


class PortService:
    def __init__(
        self,
        config,
        *,
        serial_factory=WindowsSerial,
        cache_chars=262144,
        close_timeout_s=1.5,
    ):
        self.config = normalize_config(config)
        self._generation = uuid.uuid4().hex
        self._factory = serial_factory
        self._limit = max(1, cache_chars)
        self._close_timeout = close_timeout_s
        self._lock = threading.RLock()
        self._lifecycle = threading.Lock()
        self._jobs = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = self._serial = None
        self._connected = self._closing = False
        self._fault = self._close_error = None
        self._text, self._end = "", 0
        self._received_bytes = self._pending_writes = 0
        self._decoder = codecs.getincrementaldecoder(self.config["encoding"])(
            errors="replace"
        )

    def snapshot(self, cursor=None):
        with self._lock:
            start = self._end - len(self._text)
            requested = start if cursor is None else cursor
            return {
                "port": self.config["port"],
                "generation": self._generation,
                "connected": self._connected,
                "closing": self._closing,
                "worker_alive": bool(self._thread and self._thread.is_alive()),
                "fault": dict(self._fault) if self._fault else None,
                "text": self._text[max(0, requested - start) :],
                "start": max(start, min(requested, self._end)),
                "end": self._end,
                "received_bytes": self._received_bytes,
                "pending_writes": self._pending_writes,
                "truncated": requested < start,
            }

    def mark_run(self):
        with self._lock:
            # Exclude a character whose first bytes arrived before this Run.
            partial = bool(self._decoder.getstate()[0])
            return self._end + int(partial)

    def _append(self, text):
        if text:
            with self._lock:
                self._end += len(text)
                self._text = (self._text + text)[-self._limit :]

    def connect(self):
        with self._lifecycle:
            with self._lock:
                if self._closing:
                    raise GearError("CONSOLE_CLOSING", "串口关闭尚未完成。")
                if self._fault:
                    raise GearError(self._fault["code"], self._fault["message"])
                if self._connected:
                    return
                if self._thread and self._thread.is_alive():
                    raise GearError("CONSOLE_BUSY", "串口正在连接。")
                self._stop.clear()
                self._ready.clear()
                self._decoder.reset()
                self._thread = threading.Thread(
                    target=self._run, name="console-" + self.config["port"], daemon=True
                )
                self._thread.start()
            if not self._ready.wait(2):
                with self._lock:
                    self._closing = True
                    self._stop.set()
                raise GearError(
                    "CONSOLE_OPEN_TIMEOUT", "等待串口打开超时；请断开后重试。"
                )
            with self._lock:
                if self._fault:
                    raise GearError(self._fault["code"], self._fault["message"])
                if not self._connected:
                    raise GearError("CONSOLE_NOT_CONNECTED", "串口未连接。")

    def _run(self):
        try:
            config = self.config
            self._serial = self._factory(
                port=config["port"],
                baudrate=config["baudrate"],
                parity=config["parity"],
                stopbits=config["stopbits"],
                timeout_s=0.05,
            )
            self._serial.open()
            with self._lock:
                self._connected = True
            self._ready.set()
            while not self._stop.is_set():
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    job = None
                if job is not None:
                    try:
                        with self._lock:
                            cancelled = job.cancelled or self._stop.is_set()
                        if cancelled:
                            job.error = GearError(
                                "CONSOLE_WRITE_CANCELLED", "发送在执行前已取消。"
                            )
                        else:
                            job.written = self._serial.write(job.data, timeout_s=0.5)
                            if job.written != len(job.data):
                                raise GearError(
                                    "CONSOLE_SHORT_WRITE",
                                    "串口只写入了部分命令；不会自动重发。",
                                )
                    except Exception as exc:
                        job.error = exc
                        raise
                    finally:
                        with self._lock:
                            self._pending_writes -= 1
                        job.done.set()
                if not self._stop.is_set():
                    data = self._serial.read(256, timeout_s=0.05)
                    with self._lock:
                        self._received_bytes += len(data)
                        self._append(self._decoder.decode(data))
        except Exception as exc:
            with self._lock:
                self._fault = error_diagnostic(exc)
        finally:
            self._ready.set()
            with self._lock:
                self._append(self._decoder.decode(b"", final=True))
            self._close_on_worker()
            while True:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                with self._lock:
                    self._pending_writes -= 1
                job.error = GearError("CONSOLE_NOT_CONNECTED", "串口接收线程已退出。")
                job.done.set()

    def _close_on_worker(self):
        try:
            if self._serial is not None:
                self._serial.close()
        except Exception as exc:
            with self._lock:
                self._close_error = GearError("CONSOLE_CLOSE_FAILED", str(exc))
                self._fault = error_diagnostic(self._close_error)
                self._closing = True
        else:
            with self._lock:
                self._serial = None
                self._connected = self._closing = False
                self._close_error = None

    def send(self, command, token=None):
        if not isinstance(command, str) or not 1 <= len(command) <= 65536:
            raise GearError("CONSOLE_INVALID_ARGUMENT", "command 必须为 1–65536 字符。")
        if token and token.is_requested():
            raise StopRequested()
        try:
            data = (command + LINE_ENDINGS[self.config["line_ending"]]).encode(
                self.config["encoding"]
            )
        except UnicodeError as exc:
            raise GearError("CONSOLE_ENCODING_ERROR", str(exc)) from exc
        job = _Write(data)
        with self._lock:
            if self._fault:
                raise GearError(self._fault["code"], self._fault["message"])
            if not self._connected or self._closing or self._stop.is_set():
                raise GearError("CONSOLE_NOT_CONNECTED", "请先连接串口。")
            self._pending_writes += 1
            self._jobs.put(job)
        deadline = time.monotonic() + 1.5
        while not job.done.wait(0.02):
            if token and token.is_requested():
                # Cancel an unstarted job, but let any in-flight write complete
                # before returning control to Framework finalization/teardown.
                with self._lock:
                    job.cancelled = True
            if time.monotonic() >= deadline:
                with self._lock:
                    job.cancelled = True
                    self._stop.set()
                    self._closing = True
                    self._fault = error_diagnostic(
                        GearError(
                            "CONSOLE_WRITE_TIMEOUT", "发送超时，写入状态未知；请断开。"
                        )
                    )
                raise GearError(
                    "CONSOLE_WRITE_TIMEOUT", "发送超时，写入状态未知；请断开。"
                )
        if token and token.is_requested():
            raise StopRequested()
        if job.error:
            if isinstance(job.error, GearError):
                raise job.error
            raise GearError("CONSOLE_IO_ERROR", str(job.error)) from job.error
        return job.written

    def disconnect(self):
        with self._lifecycle:
            with self._lock:
                self._stop.set()
                thread = self._thread
                if thread and thread.is_alive():
                    self._closing = True
                elif self._serial is not None:
                    self._closing = True
                    thread = self._thread = threading.Thread(
                        target=self._close_on_worker,
                        name="console-close-" + self.config["port"],
                        daemon=True,
                    )
                    thread.start()
            if thread:
                thread.join(self._close_timeout)
            with self._lock:
                if thread and thread.is_alive():
                    raise GearError(
                        "CONSOLE_CLOSE_TIMEOUT",
                        "接收线程未在限定时间退出；串口尚未确认关闭。",
                    )
                if self._close_error:
                    raise self._close_error
                self._closing = False
                self._fault = None
