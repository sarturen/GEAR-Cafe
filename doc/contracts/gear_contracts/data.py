"""Canonical boundary data: ordinary dictionaries, never dataclass instances.
All keys are required unless marked NotRequired. Receivers do not mutate inputs.
"""
from typing import Literal, NotRequired, TypeAlias, TypedDict, Union

JSONValue: TypeAlias = Union[
    None, bool, int, float, str, list["JSONValue"], dict[str, "JSONValue"]
]
JSONDict: TypeAlias = dict[str, JSONValue]
Result: TypeAlias = Literal["PASS", "FAIL", "STOPPED"]
Outcome: TypeAlias = Literal["REJECTED", "DECLINED", "PASS", "FAIL", "STOPPED"]
RunPhase: TypeAlias = Literal[
    "PREFLIGHT", "WAITING_CONFIRMATION", "RUNNING", "FINALIZING", "FINISHED", "BLOCKED"
]
CapabilityKind: TypeAlias = Literal["operation", "condition", "evidence"]


class Diagnostic(TypedDict):
    code: str
    message: str
    details: JSONDict
    path: NotRequired[str]


class ValidationReport(TypedDict):
    status: Literal["VALID", "INCOMPLETE", "INVALID"]
    diagnostics: list[Diagnostic]


class PluginRecord(TypedDict):
    id: str
    config: JSONDict


class SliceResource(TypedDict):
    type: str
    config: JSONDict
    device: NotRequired[str]


class PluginSlice(TypedDict):
    plugin: PluginRecord
    resources: dict[str, SliceResource]
    devices: NotRequired[dict[str, JSONDict]]


class ResourceBinding(TypedDict):
    type: str
    plugin: str
    config: JSONDict
    device: NotRequired[str]


class RunBinding(TypedDict):
    plugin_slice: PluginSlice
    resource_ids: list[str]


class OperationResult(TypedDict):
    ok: bool
    diagnostic: Diagnostic | None
    details: JSONDict


class ConditionResult(TypedDict):
    ok: bool
    satisfied: bool
    diagnostic: Diagnostic | None
    details: JSONDict


class Artifact(TypedDict):
    path: str
    media_type: str
    description: str


class EvidenceResult(TypedDict):
    ok: bool
    artifacts: list[Artifact]
    diagnostic: Diagnostic | None


class ManualResult(TypedDict):
    ok: bool
    value: JSONValue
    diagnostic: Diagnostic | None


class CoverageItem(TypedDict):
    source: str
    resource_id: str
    kind: CapabilityKind
    capability: str
    bound: bool
    executed: bool


class PreflightReport(TypedDict):
    ok: bool
    diagnostics: list[Diagnostic]
    bindings: dict[str, ResourceBinding]
    coverage: list[CoverageItem]


class FailureRecord(TypedDict):
    phase: str
    step_path: str | None
    resource_id: str | None
    diagnostic: Diagnostic


class EvidenceRecord(TypedDict):
    source: str
    resource_id: str
    capability: str
    result: EvidenceResult


class InputArchive(TypedDict):
    test_case: str
    project: str
    environment: str


class FinalizationFailure(TypedDict):
    stage: Literal[
        "plugin_cleanup", "framework_cleanup", "event_write", "report_write"
    ]
    plugin_id: str | None
    timestamp: str
    diagnostic: Diagnostic


class RunStatus(TypedDict):
    run_id: str
    phase: RunPhase
    outcome: Outcome | None
    execution_result: Result | None
    result: Result | None
    execution_started: bool
    active: bool
    session_blocked: bool
    preflight: PreflightReport | None
    primary_failure: FailureRecord | None
    finalization_errors: list[FinalizationFailure]
    report_path: str | None


class RunEvent(TypedDict):
    run_id: str
    sequence: int
    timestamp: str
    elapsed_s: float
    phase: RunPhase
    event: str
    step_path: str | None
    resource_id: str | None
    details: JSONDict


class RunReport(TypedDict):
    api: Literal["gear.report/v1"]
    run_id: str
    outcome: Outcome
    execution_result: Result | None
    result: Result | None
    session_blocked: bool
    execution_started: bool
    inputs: InputArchive
    preflight: PreflightReport | None
    coverage: list[CoverageItem]
    primary_failure: FailureRecord | None
    evidence: list[EvidenceRecord]
    cleanup_diagnostics: list[Diagnostic]
    plugins: dict[str, str]
    submitted_at: str
    started_at: str | None
    execution_finished_at: str | None
    reported_at: str
    events_path: str
    finalization_errors: list[FinalizationFailure]
