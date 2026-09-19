"""One OS-owned control session on this host, including idle time."""

from __future__ import annotations
import ctypes
import hashlib
import os
from pathlib import Path
import tempfile
from gear_contracts.api import GearError


class HostLock:
    def __init__(self, name=r"Global\GEAR.ControlSession.v1"):
        self.name = name
        self._handle = None

    def acquire(self):
        if self._handle is not None:
            raise GearError("HOST_BUSY", "Control session already acquired")
        if os.name == "nt":
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [
                ctypes.c_void_p,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            ]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            ctypes.set_last_error(0)
            handle = kernel.CreateMutexW(None, False, self.name)
            error = ctypes.get_last_error()
            if not handle or error == 183:
                if handle:
                    kernel.CloseHandle(handle)
                raise GearError(
                    "HOST_BUSY",
                    f"Another GEAR session owns this host (OS error {error})",
                )
            # Holding the named kernel object reserves the session without thread ownership.
            # Every contender uses CreateMutex's atomic already-exists result.
            self._kernel, self._handle = kernel, handle
        else:
            import fcntl

            path = Path(tempfile.gettempdir()) / (
                "gear-" + hashlib.sha256(self.name.encode()).hexdigest() + ".lock"
            )
            handle = path.open("a+b")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise GearError(
                    "HOST_BUSY", "Another GEAR session owns this host"
                ) from exc
            self._handle = handle

    def close(self):
        if self._handle is None:
            return
        if os.name == "nt":
            self._kernel.CloseHandle(self._handle)
        else:
            self._handle.close()
        self._handle = None

    release = close
