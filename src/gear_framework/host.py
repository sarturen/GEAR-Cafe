"""Thread-safe FrameworkV1 coordinator with one persistent plugin worker."""

from __future__ import annotations
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import sys
import threading
import uuid
from gear_contracts.api import GearError
from .common import (
    Clock,
    StopToken,
    Subscription,
    diagnostic,
    exception_diagnostic,
    check_diagnostic,
)
from .documents import load_yaml, parse_environment, plugin_slice
from .executor import Executor
from .locking import HostLock
from .preflight import prepare
from .registry import Registry
from .store import RunStore, atomic_json, append_session_log
from .worker import Worker


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RunContext:
    run_id: str
    artifact_dir: str
    stop_token: StopToken
    _clock: object
    _sink: object

    def monotonic(self):
        return self._clock.monotonic()

    def emit(self, diagnostic):
        self._sink(diagnostic)


@dataclass(frozen=True)
class CallContext(RunContext):
    phase: str
    step_path: str
    iteration: int | None
    call_id: str


@dataclass
class Run:
    status: dict
    paths: dict
    submitted_at: str
    monotonic_start: float
    token: StopToken = field(default_factory=StopToken)
    store: object = None
    prepared: object = None
    sequence: int = 0
    call_number: int = 0
    archived: bool = False
    finalized: bool = False
    started_at: str | None = None
    execution_finished_at: str | None = None
    evidence: list = field(default_factory=list)
    cleanup: list = field(default_factory=list)
    attempted: list = field(default_factory=list)
    ready: set = field(default_factory=set)
    contexts: set = field(default_factory=set)
    pending: list = field(default_factory=list)


class Framework:
    def __init__(self, app_dir, environment, *, clock=None):
        self.app_dir = Path(app_dir).resolve()
        self.environment_path = Path(environment).resolve()
        self.clock = clock or Clock()
        self._mutex = threading.RLock()
        self._listeners = []
        self._contexts = []
        self._workspaces = []
        self._runs = {}
        self.session_diagnostics = []
        self._active = None
        self._pending_manual = 0
        self._blocked = False
        self._closed = False
        self._closing = False
        self._host_lock = HostLock()
        self._host_lock.acquire()
        self._worker = Worker()
        try:
            self._environment = parse_environment(load_yaml(self.environment_path))
            self._worker.submit(self._startup).result()
        except BaseException:
            if hasattr(self, "_registry"):
                self._worker.submit(self._registry.close).result()
            self._worker.close()
            self._host_lock.close()
            raise

    def _startup(self):
        self._registry = Registry.load(self.app_dir)
        for pid, entry in self._registry.entries.items():
            entry.runtime.configure(
                plugin_slice(
                    self._environment, pid, self._registry.owners.get("ADB") == pid
                )
            )

    def _check_idle(self):
        if self._closed or self._closing:
            raise GearError("CLOSED", "Framework is closing or closed")
        if self._blocked:
            raise GearError(
                "HOST_BLOCKED",
                "Resolve the finalization/configuration failure and restart GEAR",
            )
        if self._active is not None or self._pending_manual:
            raise GearError("BUSY", "A Run or manual/configuration operation is active")

    def submit(self, test_case, project, environment):
        paths = {
            "test_case": str(test_case),
            "project": str(project),
            "environment": str(environment),
        }
        if not all(Path(p).is_absolute() for p in paths.values()):
            raise GearError("INVALID_PATH", "submit requires absolute input file paths")
        with self._mutex:
            self._check_idle()
            rid = uuid.uuid4().hex
            status = {
                "run_id": rid,
                "phase": "PREFLIGHT",
                "outcome": None,
                "execution_result": None,
                "result": None,
                "execution_started": False,
                "active": True,
                "session_blocked": False,
                "preflight": None,
                "primary_failure": None,
                "finalization_errors": [],
                "report_path": None,
            }
            run = Run(status, paths, utc(), self.clock.monotonic())
            self._runs[rid] = run
            self._active = run
            self._worker.submit(
                lambda: self._preflight(run), lambda: self._after_task(run)
            )
            self._notify_workspaces("state")
            return rid

    def _get(self, rid):
        try:
            return self._runs[rid]
        except KeyError:
            raise GearError("UNKNOWN_RUN", f"Unknown Run {rid}") from None

    def confirm(self, run_id):
        with self._mutex:
            run = self._get(run_id)
            if run.status["phase"] != "WAITING_CONFIRMATION":
                raise GearError("INVALID_STATE", "Run is not awaiting confirmation")
            run.status["phase"] = "RUNNING"
            self._worker.submit(
                lambda: self._execute(run), lambda: self._after_task(run)
            )

    def stop(self, run_id):
        with self._mutex:
            run = self._get(run_id)
            if run.status["phase"] in ("FINISHED", "BLOCKED"):
                return
            run.token.request()
            if not run.status["execution_started"]:
                run.status["outcome"] = "DECLINED"
                if run.status["phase"] == "WAITING_CONFIRMATION":
                    run.status["phase"] = "FINALIZING"
                    self._worker.submit(
                        lambda: self._finalize(run), lambda: self._after_task(run)
                    )
            elif run.status["outcome"] is None:
                self._terminate(run, "STOPPED", None)

    def get_status(self, run_id):
        with self._mutex:
            return copy.deepcopy(self._get(run_id).status)

    def subscribe(self, listener):
        s = Subscription(listener)
        with self._mutex:
            self._listeners.append(s)
        return s

    def _emit(
        self, run, event, details, step_path=None, resource_id=None, *, persist=True
    ):
        with self._mutex:
            pending, run.pending = run.pending, []
            for pid, diag in pending:
                self._emit(
                    run,
                    "plugin.diagnostic",
                    {"plugin_id": pid, "diagnostic": diag},
                    persist=persist,
                )
            run.sequence += 1
            value = {
                "run_id": run.status["run_id"],
                "sequence": run.sequence,
                "timestamp": utc(),
                "elapsed_s": max(0, self.clock.monotonic() - run.monotonic_start),
                "phase": run.status["phase"],
                "event": event,
                "step_path": step_path,
                "resource_id": resource_id,
                "details": copy.deepcopy(details),
            }
            write_error = None
            if persist and run.store and not run.store.events_failed:
                try:
                    run.store.append_event(value)
                except Exception as exc:
                    run.store.events_failed = True
                    write_error = exc
            for listener in list(self._listeners):
                try:
                    listener.deliver(value)
                except Exception as exc:
                    print(f"GEAR event listener failed: {exc}", file=sys.stderr)
            if write_error is not None:
                self._finalization_failure(run, "event_write", write_error)

    def _state_event(self, run, persist=True):
        self._emit(
            run,
            "run.state",
            {k: run.status[k] for k in ("outcome", "result", "execution_started")},
            persist=persist,
        )

    def _preflight(self, run):
        try:
            run.store = RunStore(self.app_dir, run.status["run_id"])
            paths = run.store.archive(run.paths)
            run.archived = True
            try:
                run.store.start_events()
            except Exception as exc:
                run.store.events_failed = True
                self._finalization_failure(run, "event_write", exc)
                raise
            self._state_event(run)
            env = parse_environment(load_yaml(paths["environment"]))
            if env != self._environment:
                raise GearError(
                    "ENVIRONMENT_MISMATCH",
                    "Archived Environment differs from loaded session",
                )
            run.prepared = prepare(
                load_yaml(paths["test_case"]),
                load_yaml(paths["project"]),
                env,
                self._registry,
            )
            run.status["preflight"] = run.prepared.report
            atomic_json(run.store.directory / "preflight.json", run.prepared.report)
        except Exception as exc:
            code = exc.args[0] if isinstance(exc, GearError) else "PREFLIGHT_FAILED"
            run.status["preflight"] = {
                "ok": False,
                "diagnostics": [exception_diagnostic(code, exc, "preflight")],
                "bindings": {},
                "coverage": [],
            }
        with self._mutex:
            if run.token.is_requested():
                run.status["outcome"] = "DECLINED"
            elif not run.status["preflight"]["ok"]:
                run.status["outcome"] = "REJECTED"
            elif self._blocked:
                run.status["outcome"] = "REJECTED"
            else:
                run.status["phase"] = "WAITING_CONFIRMATION"
                self._state_event(run)
                return
        self._finalize(run)

    def _terminate(self, run, result, failure):
        with self._mutex:
            if run.status["outcome"] is None:
                run.status["outcome"] = result
                run.status["result"] = result
                run.status["execution_result"] = result
                run.status["primary_failure"] = copy.deepcopy(failure)
                run.execution_finished_at = utc()
            return run.status["result"]

    def _context(self, run, pid, phase=None, path=None, iteration=None):
        def sink(value):
            check_diagnostic(value)
            with self._mutex:
                if pid not in run.contexts:
                    raise GearError("INVALID_STATE", "Run context is no longer active")
                if threading.current_thread() is self._worker.thread:
                    self._emit(
                        run,
                        "plugin.diagnostic",
                        {"plugin_id": pid, "diagnostic": value},
                    )
                else:
                    run.pending.append((pid, copy.deepcopy(value)))

        values = (
            run.status["run_id"],
            str(run.store.directory),
            run.token,
            self.clock,
            sink,
        )
        if phase is None:
            return RunContext(*values)
        run.call_number += 1
        return CallContext(*values, phase, path, iteration, str(run.call_number))

    def _execute(self, run):
        executor = Executor(
            run.prepared,
            self._registry,
            lambda pid, phase, path, it: self._context(run, pid, phase, path, it),
            lambda event, details, **kw: self._emit(run, event, details, **kw),
            run.token,
            self.clock,
            lambda result, failure: self._terminate(run, result, failure),
        )
        executor.submitted_at = run.monotonic_start
        try:
            with self._mutex:
                if run.token.is_requested():
                    run.status["outcome"] = "DECLINED"
                    return
                run.status["execution_started"] = True
                run.started_at = utc()
            self._state_event(run)
            for pid in sorted(run.prepared.resource_ids):
                if run.token.is_requested():
                    break
                run.attempted.append(pid)
                run.contexts.add(pid)
                try:
                    self._registry.entries[pid].runtime.begin_run(
                        {
                            "plugin_slice": copy.deepcopy(run.prepared.slices[pid]),
                            "resource_ids": list(run.prepared.resource_ids[pid]),
                        },
                        self._context(run, pid),
                    )
                    run.ready.add(pid)
                except Exception as exc:
                    diag = exception_diagnostic(
                        "PLUGIN_BEGIN_FAILED", exc, "begin_run", plugin_id=pid
                    )
                    selected = self._terminate(
                        run,
                        "FAIL",
                        {
                            "phase": "prepare",
                            "step_path": None,
                            "resource_id": None,
                            "diagnostic": diag,
                        },
                    )
                    if selected == "STOPPED":
                        self._emit(
                            run,
                            "plugin.diagnostic",
                            {"plugin_id": pid, "diagnostic": diag},
                        )
            if run.status["outcome"] is None:
                executor.run()
            if run.status["outcome"] == "FAIL":
                run.status["phase"] = "FINALIZING"
                executor.prepared_plugins = run.ready
                run.evidence = executor.collect_evidence()
        except Exception as exc:
            self._terminate(
                run,
                "FAIL",
                {
                    "phase": "execution",
                    "step_path": None,
                    "resource_id": None,
                    "diagnostic": exception_diagnostic(
                        "FRAMEWORK_EXECUTION_FAILED", exc, "execute"
                    ),
                },
            )
        finally:
            self._finalize(run)

    def _finalization_failure(self, run, stage, exc, pid=None):
        codes = {
            "plugin_cleanup": "PLUGIN_CLEANUP_FAILED",
            "framework_cleanup": "FRAMEWORK_CLEANUP_FAILED",
            "event_write": "EVENT_WRITE_FAILED",
            "report_write": "REPORT_WRITE_FAILED",
        }
        diag = exception_diagnostic(
            codes[stage], exc, stage, **({"plugin_id": pid} if pid else {})
        )
        failure = {
            "stage": stage,
            "plugin_id": pid,
            "timestamp": utc(),
            "diagnostic": diag,
        }
        with self._mutex:
            self._blocked = True
            run.status["session_blocked"] = True
            run.status["finalization_errors"].append(failure)
            self._apply_finalization_result(run)
        record = {
            "run_id": run.status["run_id"],
            "execution_result": run.status["execution_result"],
            "result": run.status["result"],
            "session_blocked": True,
            "failure": failure,
        }
        try:
            append_session_log(self.app_dir, record)
        except Exception as log_exc:
            diag["details"]["session_log_error"] = exception_diagnostic(
                "SESSION_LOG_FAILED", log_exc, "append_session_log"
            )
            diag["details"]["persisted"] = False
            print(f"GEAR diagnostics were not persisted: {record}", file=sys.stderr)
        self._emit(run, "finalization.failure", record, persist=stage != "event_write")

    def _apply_finalization_result(self, run):
        if run.status["session_blocked"] and run.status["execution_result"] == "PASS":
            run.status["outcome"] = run.status["result"] = "FAIL"
            if run.status["primary_failure"] is None:
                run.status["primary_failure"] = {
                    "phase": "finalization",
                    "step_path": None,
                    "resource_id": None,
                    "diagnostic": copy.deepcopy(
                        run.status["finalization_errors"][0]["diagnostic"]
                    ),
                }

    def _finalize(self, run):
        run.status["phase"] = "FINALIZING"
        self._state_event(run)
        for pid in reversed(run.attempted):
            try:
                self._registry.entries[pid].runtime.end_run()
            except Exception as exc:
                self._finalization_failure(run, "plugin_cleanup", exc, pid)
                run.cleanup.append(
                    copy.deepcopy(run.status["finalization_errors"][-1]["diagnostic"])
                )
            finally:
                run.contexts.discard(pid)
        self._apply_finalization_result(run)
        self._state_event(run)
        if run.store:
            try:
                run.store.close_events()
            except Exception as exc:
                self._finalization_failure(run, "event_write", exc)
        if run.archived:
            try:
                report = {
                    "api": "gear.report/v1",
                    "run_id": run.status["run_id"],
                    **{
                        k: copy.deepcopy(run.status[k])
                        for k in (
                            "outcome",
                            "execution_result",
                            "result",
                            "session_blocked",
                            "execution_started",
                            "preflight",
                            "primary_failure",
                            "finalization_errors",
                        )
                    },
                    "inputs": {
                        "test_case": "input/test-case.yaml",
                        "project": "input/project.yaml",
                        "environment": "input/environment.yaml",
                    },
                    "coverage": (
                        copy.deepcopy(run.prepared.report["coverage"])
                        if run.prepared
                        else []
                    ),
                    "evidence": run.evidence,
                    "cleanup_diagnostics": run.cleanup,
                    "plugins": {
                        pid: entry.version
                        for pid, entry in self._registry.entries.items()
                    },
                    "submitted_at": run.submitted_at,
                    "started_at": run.started_at,
                    "execution_finished_at": run.execution_finished_at,
                    "reported_at": utc(),
                    "events_path": "events.jsonl",
                }
                run.status["report_path"] = run.store.publish_report(report)
            except Exception as exc:
                self._finalization_failure(run, "report_write", exc)
        run.finalized = True

    def _after_task(self, run):
        if not run.finalized:
            return
        with self._mutex:
            if run.status["session_blocked"]:
                run.status["phase"] = "BLOCKED"
            else:
                run.status["phase"] = "FINISHED"
                run.status["active"] = False
                self._active = None
            self._state_event(run, persist=False)
            self._notify_workspaces("state")

    def workspace_context(self, plugin_id, dispatch):
        from .workspace import WorkspaceContext

        with self._mutex:
            if plugin_id not in self._registry.entries:
                raise GearError("UNKNOWN_PLUGIN", plugin_id)
            context = WorkspaceContext(self, plugin_id, dispatch)
            self._contexts.append(context)
            return context

    def _notify_workspaces(self, kind):
        for context in list(self._contexts):
            context._notify(kind)

    def load_environment(self, path):
        data = parse_environment(load_yaml(path))
        with self._mutex:
            self._check_idle()
            self._pending_manual += 1
            self._environment = data
            self.environment_path = Path(path).resolve()

            def apply():
                try:
                    for pid, entry in self._registry.entries.items():
                        entry.runtime.configure(
                            plugin_slice(
                                data, pid, self._registry.owners.get("ADB") == pid
                            )
                        )
                except Exception as exc:
                    self._blocked = True
                    diag = exception_diagnostic(
                        "CONFIGURE_FAILED", exc, "load_environment"
                    )
                    self.session_diagnostics.append(diag)
                    print(
                        f"GEAR configuration failed; restart required: {diag}",
                        file=sys.stderr,
                    )
                    raise
                finally:
                    with self._mutex:
                        self._pending_manual -= 1
                    self._notify_workspaces("environment")
                    self._notify_workspaces("state")

            return self._worker.submit(apply)

    def flush(self):
        self._worker.flush()

    def close(self):
        with self._mutex:
            if self._closed:
                return
            if threading.current_thread() is self._worker.thread:
                raise GearError(
                    "INVALID_STATE", "Close must run on the host/GUI thread"
                )
            if any(c._gui_thread != threading.get_ident() for c in self._contexts):
                raise GearError(
                    "INVALID_THREAD", "Close must run on the Workspace GUI thread"
                )
            self._closing = True
            if self._active:
                self.stop(self._active.status["run_id"])
        self._worker.flush()
        errors = []
        for workspace in reversed(self._workspaces):
            try:
                workspace.dispose()
            except Exception as exc:
                errors.append(
                    exception_diagnostic("WORKSPACE_CLOSE_FAILED", exc, "dispose")
                )
        for context in self._contexts:
            context.dispose()
        errors.extend(self._worker.submit(self._registry.close).result())
        self._worker.close()
        self._closed = True
        if errors:
            # An unclosed plugin worker may still own hardware. Keep OS exclusion
            # until process exit instead of allowing a replacement session.
            self.session_diagnostics.extend(errors)
            raise GearError("SHUTDOWN_FAILED", str(errors))
        self._host_lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
