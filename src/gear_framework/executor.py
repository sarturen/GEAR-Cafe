"""Deterministic synchronous DSL execution; scheduling is owned by the host."""

from __future__ import annotations
import copy
import random
from pathlib import PurePosixPath, Path
from gear_contracts.api import StopRequested
from .common import diagnostic, exception_diagnostic, validate_result
from .documents import duration


class _End(Exception):
    def __init__(self, result, failure=None):
        self.result, self.failure = result, failure


class Executor:
    def __init__(
        self, prepared, registry, context_factory, emit, stop_token, clock, terminate
    ):
        self.prepared, self.registry = prepared, registry
        self.context_factory, self.emit = context_factory, emit
        self.token, self.clock, self.terminate = stop_token, clock, terminate
        self.submitted_at = 0.0
        self.prepared_plugins = set(registry.entries)

    def _stopped(self):
        if self.token.is_requested():
            result = self.terminate("STOPPED", None)
            raise _End(result)

    def _fail(self, phase, path, rid, diag):
        failure = {
            "phase": phase,
            "step_path": path,
            "resource_id": rid,
            "diagnostic": diag,
        }
        result = self.terminate("FAIL", failure)
        if result == "STOPPED":
            self.emit(
                "plugin.diagnostic",
                {"plugin_id": None, "diagnostic": diag},
                step_path=path,
                resource_id=rid,
            )
        raise _End(result, failure if result == "FAIL" else None)

    def _mark(self, source, rid):
        for item in self.prepared.report["coverage"]:
            if item["source"] == source and item["resource_id"] == rid:
                item["executed"] = True

    def _call(self, req, source, phase, path, iteration):
        self._stopped()
        pid, rid, kind = req["plugin_id"], req["resource_id"], req["kind"]
        context = self.context_factory(pid, phase, path, iteration)
        method = {"operation": "invoke", "condition": "evaluate"}[kind]
        started = self.clock.monotonic()
        self._mark(source, rid)
        try:
            result = getattr(self.registry.entries[pid].runtime, method)(
                rid, req["capability"], copy.deepcopy(req["args"]), context
            )
            result = validate_result(kind, result)
        except Exception as exc:
            if isinstance(exc, StopRequested) and self.token.is_requested():
                self._stopped()
            self._fail(
                phase,
                path,
                rid,
                exception_diagnostic("PLUGIN_CALL_FAILED", exc, method, plugin_id=pid),
            )
        finished = self.clock.monotonic()
        details = {
            kind: req["capability"],
            "iteration": iteration,
            "call_id": context.call_id,
            "result": result,
        }
        if kind == "condition":
            details.update(
                started_s=started - self.submitted_at,
                finished_s=finished - self.submitted_at,
            )
        self.emit(
            "condition.sample" if kind == "condition" else "operation.result",
            details,
            step_path=path,
            resource_id=rid,
        )
        if not result["ok"]:
            self._fail(
                phase,
                path,
                rid,
                result["diagnostic"]
                or diagnostic("PLUGIN_CALL_FAILED", f"{kind} returned ok=false"),
            )
        self._stopped()
        return result

    def _assert(self, value, phase, path, iteration):
        mode = (
            "within" if "within" in value else "for" if "for" in value else "immediate"
        )
        end = (
            self.clock.monotonic() + duration(value[mode])
            if mode != "immediate"
            else None
        )
        every = duration(value["every"]) if mode != "immediate" else None
        while True:
            self._stopped()
            start = self.clock.monotonic()
            if mode == "within" and start >= end:
                self._fail(
                    phase,
                    path,
                    None,
                    diagnostic(
                        "ASSERT_TIMEOUT", "No satisfying cycle completed by deadline"
                    ),
                )
            satisfied = True
            complete = True
            for i in range(len(value["all"])):
                source = f"{path}/assert/all/{i}"
                for request in self.prepared.requests[source]:
                    result = self._call(request, source, phase, path, iteration)
                    satisfied = satisfied and result["satisfied"]
                    if mode == "within" and self.clock.monotonic() > end:
                        complete = False
                        break
                if not complete:
                    break
            now = self.clock.monotonic()
            if mode == "within":
                if satisfied and complete and now <= end:
                    return
                if now >= end:
                    self._fail(
                        phase,
                        path,
                        None,
                        diagnostic(
                            "ASSERT_TIMEOUT",
                            "Assertion deadline elapsed",
                            deadline_s=end - self.submitted_at,
                            finished_s=now - self.submitted_at,
                        ),
                    )
            else:
                if not satisfied:
                    self._fail(
                        phase,
                        path,
                        None,
                        diagnostic(
                            "ASSERT_UNSATISFIED", "A condition was not satisfied"
                        ),
                    )
                if mode == "immediate" or now >= end:
                    return
            target = start + every
            if mode == "for":
                target = min(target, end)
            elif mode == "within":
                target = min(target, end)
            self.clock.wait(max(0, target - now), self.token)

    def _steps(self, steps, phase, prefix, iteration=None):
        for index, step in enumerate(steps):
            path = f"{prefix}/{index}"
            self._stopped()
            kind, value = next(iter(step.items()))
            self.emit(
                "step.start", {"kind": kind, "iteration": iteration}, step_path=path
            )
            try:
                if kind == "do":
                    source = path + "/do"
                    for req in self.prepared.requests[source]:
                        self._call(req, source, phase, path, iteration)
                elif kind == "wait":
                    if "duration" in value:
                        seconds = duration(value["duration"])
                    else:
                        seconds = random.uniform(
                            duration(value["random"]["min"]),
                            duration(value["random"]["max"]),
                        )
                    self.emit(
                        "wait.chosen",
                        {"duration_s": seconds, "iteration": iteration},
                        step_path=path,
                    )
                    self.clock.wait(seconds, self.token)
                    self._stopped()
                elif kind == "assert":
                    self._assert(value, phase, path, iteration)
                elif kind == "repeat":
                    for n in range(1, value["count"] + 1):
                        self._steps(value["steps"], phase, path + "/repeat/steps", n)
            except _End as end:
                self.emit(
                    "step.end",
                    {
                        "kind": kind,
                        "iteration": iteration,
                        "ok": False,
                        "diagnostic": (
                            end.failure["diagnostic"]
                            if end.failure
                            else diagnostic("STOPPED", "Execution stopped")
                        ),
                    },
                    step_path=path,
                )
                raise
            self.emit(
                "step.end",
                {"kind": kind, "iteration": iteration, "ok": True, "diagnostic": None},
                step_path=path,
            )

    def run(self):
        try:
            for phase in ("setup", "body", "teardown"):
                self._steps(self.prepared.case[phase], phase, "/" + phase)
            return self.terminate("PASS", None), None
        except _End as end:
            return end.result, end.failure

    def collect_evidence(self):
        records = []
        for i, _ in enumerate(self.prepared.case["evidence_on_fail"]):
            source = f"/evidence_on_fail/{i}"
            for req in self.prepared.requests[source]:
                pid, rid = req["plugin_id"], req["resource_id"]
                context = self.context_factory(pid, "evidence", source, None)
                if self.token.is_requested():
                    result = {
                        "ok": False,
                        "artifacts": [],
                        "diagnostic": diagnostic(
                            "EVIDENCE_CANCELLED", "Evidence cancelled before starting"
                        ),
                    }
                elif pid not in self.prepared_plugins:
                    result = {
                        "ok": False,
                        "artifacts": [],
                        "diagnostic": diagnostic(
                            "PLUGIN_NOT_PREPARED", "Evidence Plugin preparation failed"
                        ),
                    }
                else:
                    self._mark(source, rid)
                    try:
                        result = validate_result(
                            "evidence",
                            self.registry.entries[pid].runtime.collect(
                                rid,
                                req["capability"],
                                copy.deepcopy(req["args"]),
                                context,
                            ),
                        )
                        for artifact in result["artifacts"]:
                            p = PurePosixPath(artifact["path"])
                            base = Path(context.artifact_dir).resolve()
                            target = (base / Path(*p.parts)).resolve()
                            if (
                                p.is_absolute()
                                or "\\" in artifact["path"]
                                or ".." in p.parts
                                or p.parts[:2] != ("evidence", pid)
                                or not target.is_relative_to(base)
                                or not target.is_file()
                            ):
                                raise ValueError(
                                    "Artifact must name an existing file under evidence/<plugin-id>/"
                                )
                    except Exception as exc:
                        result = {
                            "ok": False,
                            "artifacts": [],
                            "diagnostic": exception_diagnostic(
                                "EVIDENCE_FAILED", exc, "collect", plugin_id=pid
                            ),
                        }
                records.append(
                    {
                        "source": source,
                        "resource_id": rid,
                        "capability": req["capability"],
                        "result": result,
                    }
                )
                self.emit(
                    "evidence.result",
                    {
                        "capability": req["capability"],
                        "call_id": context.call_id,
                        "result": result,
                    },
                    step_path=source,
                    resource_id=rid,
                )
        return records
