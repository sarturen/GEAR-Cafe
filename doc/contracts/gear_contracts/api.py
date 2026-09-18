"""Exact synchronous GEAR v1 protocols. Implementations live elsewhere."""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol
from .data import (
    ConditionResult, Diagnostic, EvidenceResult, JSONDict, JSONValue,
    ManualResult, OperationResult, PluginSlice, RunBinding, RunEvent,
    RunStatus, ValidationReport,
)


class GearError(RuntimeError):
    """Construct as GearError(stable_code, readable_message), both strings.
    Consumers read args[0] as the machine-readable code.
    """


class StopRequested(Exception):
    """Cooperative cancellation, valid only after the token was requested."""


class Subscription(Protocol):
    def unsubscribe(self) -> None: ...


class StopToken(Protocol):
    def is_requested(self) -> bool: ...
    def wait(self, timeout_s: float) -> bool: ...


class RunContextV1(Protocol):
    run_id: str
    artifact_dir: str
    stop_token: StopToken
    def monotonic(self) -> float: ...
    def emit(self, diagnostic: Diagnostic) -> None: ...


class CallContextV1(RunContextV1, Protocol):
    phase: Literal["setup", "body", "teardown", "evidence"]
    step_path: str
    iteration: int | None
    call_id: str


class PluginRuntimeV1(Protocol):
    def configure(self, plugin_slice: PluginSlice) -> None: ...
    def validate_config(self, plugin_slice: PluginSlice) -> ValidationReport: ...
    def begin_run(self, binding: RunBinding, context: RunContextV1) -> None: ...
    def invoke(
        self, resource_id: str, operation: str, args: JSONDict,
        context: CallContextV1,
    ) -> OperationResult: ...
    def evaluate(
        self, resource_id: str, condition: str, args: JSONDict,
        context: CallContextV1,
    ) -> ConditionResult: ...
    def collect(
        self, resource_id: str, evidence: str, args: JSONDict,
        context: CallContextV1,
    ) -> EvidenceResult: ...
    def end_run(self) -> None: ...
    def close(self) -> None: ...


class WorkspaceContextV1(Protocol):
    plugin_id: str
    def current_slice(self) -> PluginSlice: ...
    def configured_device_ids(self) -> list[str]: ...
    def commit(
        self, full_slice: PluginSlice, validation_report: ValidationReport,
    ) -> None: ...
    def run_state(self) -> Literal["IDLE", "ACTIVE"]: ...
    def subscribe_run_state(
        self, listener: Callable[[Literal["IDLE", "ACTIVE"]], None],
    ) -> Subscription: ...
    def subscribe_environment(
        self, listener: Callable[[], None],
    ) -> Subscription: ...
    def submit_manual(
        self, action: Callable[[], JSONValue],
        listener: Callable[[ManualResult], None],
    ) -> None: ...


class WorkspaceV1(Protocol):
    # At the GUI boundary this MUST be a PySide6 QWidget.
    # object keeps the shared contracts importable without Qt.
    widget: object
    def dispose(self) -> None: ...


class FrameworkV1(Protocol):
    def submit(
        self, test_case: str, project: str, environment: str,
    ) -> str: ...
    def confirm(self, run_id: str) -> None: ...
    def stop(self, run_id: str) -> None: ...
    def get_status(self, run_id: str) -> RunStatus: ...
    def subscribe(
        self, listener: Callable[[RunEvent], None],
    ) -> Subscription: ...
