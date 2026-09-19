"""Boundary checks, clocks and thread-safe subscriptions shared by the core."""

from __future__ import annotations
import copy
import math
import threading
import time
import traceback
from gear_contracts.api import GearError


def diagnostic(code, message, **details):
    return {"code": code, "message": str(message), "details": details}


def exception_diagnostic(code, exc, operation, **details):
    if isinstance(exc, GearError) and exc.args:
        details["original_code"] = str(exc.args[0])
    return diagnostic(
        code,
        str(exc),
        operation=operation,
        exception_type=type(exc).__name__,
        exception_message=str(exc),
        traceback="".join(traceback.format_exception(exc)),
        **details,
    )


def ensure_json(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            ensure_json(item)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            ensure_json(item)
        return
    raise GearError(
        "INVALID_DATA", "Expected JSON-compatible values with finite numbers"
    )


def check_diagnostic(value):
    if (
        not isinstance(value, dict)
        or not {"code", "message", "details"} <= value.keys()
    ):
        raise GearError("INVALID_RESULT", "Malformed diagnostic")
    if set(value) - {"code", "message", "details", "path"}:
        raise GearError("INVALID_RESULT", "Unknown diagnostic fields")
    if not all(
        isinstance(value[k], str) for k in ("code", "message")
    ) or not isinstance(value["details"], dict):
        raise GearError("INVALID_RESULT", "Malformed diagnostic fields")
    if "path" in value and not isinstance(value["path"], str):
        raise GearError("INVALID_RESULT", "Diagnostic path must be a string")
    ensure_json(value)


def validate_result(kind, value):
    fields = {
        "operation": {"ok", "diagnostic", "details"},
        "condition": {"ok", "diagnostic", "details", "satisfied"},
        "evidence": {"ok", "diagnostic", "artifacts"},
    }
    ensure_json(value)
    if (
        type(value) is not dict
        or set(value) != fields[kind]
        or type(value["ok"]) is not bool
    ):
        raise GearError("INVALID_RESULT", f"Malformed {kind} result")
    if value["diagnostic"] is not None:
        check_diagnostic(value["diagnostic"])
    if kind == "condition" and type(value["satisfied"]) is not bool:
        raise GearError("INVALID_RESULT", "satisfied must be boolean")
    if "details" in value and type(value["details"]) is not dict:
        raise GearError("INVALID_RESULT", "details must be an object")
    if kind == "evidence":
        if type(value["artifacts"]) is not list:
            raise GearError("INVALID_RESULT", "artifacts must be an array")
        for a in value["artifacts"]:
            if (
                type(a) is not dict
                or set(a) != {"path", "media_type", "description"}
                or not all(type(v) is str for v in a.values())
            ):
                raise GearError("INVALID_RESULT", "Malformed artifact")
    return copy.deepcopy(value)


class StopToken:
    def __init__(self):
        self._event = threading.Event()

    def request(self):
        self._event.set()

    def is_requested(self):
        return self._event.is_set()

    def wait(self, timeout_s):
        if timeout_s < 0:
            raise ValueError("timeout_s must be nonnegative")
        return self._event.wait(timeout_s)


class Clock:
    def monotonic(self):
        return time.monotonic()

    def wait(self, seconds, token):
        return token.wait(max(0, seconds))


class Subscription:
    def __init__(self, listener):
        self.listener = listener
        self._lock = threading.RLock()
        self._active = True

    def unsubscribe(self):
        with self._lock:
            self._active = False

    def deliver(self, value):
        with self._lock:
            if self._active:
                self.listener(copy.deepcopy(value))
