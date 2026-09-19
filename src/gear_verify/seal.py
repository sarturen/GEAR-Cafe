"""Make real hardware unreachable while a verification session is running."""

from __future__ import annotations

import contextlib
import ctypes
import subprocess

MESSAGE = (
    "GEAR verification mode blocked a real hardware access ({name}). "
    "The simulated bench was not installed for this code path."
)


def _forbidden(*args, **kwargs):
    raise RuntimeError(MESSAGE.format(name="native call"))


def _blocked_popen(*args, **kwargs):
    raise RuntimeError(MESSAGE.format(name="subprocess"))


class Seal:
    """Process-wide blocks, installed in the two steps Framework startup allows."""

    def __init__(self):
        self._restore = []

    def ports(self):
        """Block native COM/Win32 access.

        Called only after the control-session lock is held: acquiring that lock
        is itself the last legitimate `ctypes.WinDLL` user, and it keeps the
        kernel handle it needs for its own release.
        """
        original = getattr(ctypes, "WinDLL", None)
        if original is not None:
            ctypes.WinDLL = _forbidden
            self._restore.append(("WinDLL", original))

    def release(self):
        for name, value in self._restore:
            setattr(ctypes, name, value)
        self._restore.clear()


@contextlib.contextmanager
def sealed():
    """Block tool processes immediately and native ports once the lock is held.

    Usage:

        with sealed() as seal:
            host = Framework(app_dir, environment)
            seal.ports()
    """
    original = subprocess.Popen
    subprocess.Popen = _blocked_popen
    seal = Seal()
    try:
        yield seal
    finally:
        seal.release()
        subprocess.Popen = original
