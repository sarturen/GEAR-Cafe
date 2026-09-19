# Framework v1 implementation plan

**Goal:** Implement the approved v1 Framework, with executable acceptance tests.
**Architecture:** One fixed worker and long-lived plugin sessions. Core has no Qt dependency. A GUI dispatcher bridge serializes configuration/manual work; the CLI exercises the same Framework API.
**Tech stack:** Python 3.11+, ruamel.yaml (YAML 1.2), jsonschema Draft 2020-12, pytest; standard-library concurrency, filesystem and OS locking.
**Spec:** doc/cafe/architecture-v1.md, doc/cafe/framework-runtime/v1.md, doc/cafe/plugin-contract/v1.md, doc/cafe/dsl/v1.md, doc/cafe/environment-model/v1.md and doc/contracts/gear_contracts.

## Constraints and decisions
All eleven user-approved decisions remain binding. No readiness probes, overlapping Runs, per-run connection recreation, plugin-to-plugin calls, recovery, task queue or background daemon. All plugin runtime calls use the same worker. Passive acquisition belongs to each plugin.
The existing documents are the approved design; no new design approval is needed for implementation details. Full GUI pages and hardware plugins remain outside Framework; deliver their host bridge and a deterministic example plugin.
Development is isolated at framework-dev on feat/framework-v1; root documentation and main checkout remain unchanged.
Use parallel agents only for disjoint modules under the dispatching-parallel-agents skill. Parent owns shared helpers and integration. No subagent commits or shared-file edits.

## Task 1 — Strict documents and configuration-only Preflight
Files: src/gear_framework/documents.py, preflight.py; tests/test_documents.py, test_preflight.py.
- [x] Write failing tests for accepted YAML/DSL shapes, forbidden aliases/tags/duplicates/non-JSON values, duration/grammar constraints, immutable slice projection/replacement, wildcard coverage and static validation.
- [x] Implement load_yaml(path), parse_case(data), parse_project(data), parse_environment(data), duration(value), plugin_slice(environment, plugin_id, owns_adb=False), replace_slice(environment, plugin_id, replacement, owns_adb=False).
- [x] Implement prepare(case, project, environment, registry) -> PreparedRun with case, report, requests, slices, resource_ids attributes. Requests map source paths to expanded bound calls with resource_id/plugin_id/kind/capability/args. report contains all coverage gaps. validate complete involved slices only.
- [x] Run .venv/Scripts/python -m pytest tests/test_documents.py tests/test_preflight.py -q.
Test examples: ON is a string; duplicate body rejected; SCREEN.* expands lexically and retains unbound coverage; offline configuration is not probed.

## Task 2 — Sequential DSL executor
Files: src/gear_framework/executor.py; tests/test_executor.py.
- [x] Write failing fake-clock tests for immediate, within exact/late deadline, for boundary sampling, slow nonoverlapping evaluation, waits/repeat and stop precedence.
- [x] Implement Executor(prepared, registry, context_factory, emit, stop_token, clock, terminate).run() -> (result, primary_failure) and collect_evidence() -> list[EvidenceRecord].
context_factory(plugin_id, phase, step_path, iteration) returns CallContextV1.
emit(event, details, step_path=None, resource_id=None) is thread-safe.
clock.monotonic() and clock.wait(seconds, stop_token) support deterministic timing.
terminate(result, failure) atomically records first terminating cause and returns the selected result, respecting earlier stop. Executor calls terminate immediately on observed FAIL; STOPPED later errors are emitted as secondary diagnostics.
The executor marks prepared.report coverage executed when actually calling each request. Calls use registry.entries[plugin_id].runtime. It validates plugin result shapes. Evidence collection is best effort and does not rewrite primary failure.
- [x] Run executor tests, including full-cycle late true -> FAIL and failed first operation prevents teardown.

## Task 3 — Plugin registry, run store and host exclusion
Files: src/gear_framework/registry.py, store.py, locking.py; tests/test_registry.py, test_host.py, test_locking.py, test_conformance.py.
- [x] Test fixed-directory discovery, duplicate IDs/types/package names, invalid schemas/entrypoints, malformed runtime protocol; reject startup failures.
- [x] Registry.load(app_dir) produces entries mapping id -> PluginEntry(id, version, manifest, runtime, directory, workspace), owners mapping type -> plugin id. Factories invoked once on calling worker. Preflight uses the manifest resource_types mapping. No GUI import.
- [x] Test real separate-process host exclusion and lifecycle, atomic report persistence, error fallbacks.
- [x] Implement Windows named mutex and Unix file lock as appropriate; release on host shutdown/process exit. Core keeps the lock through worker exit.

## Task 4 — Framework coordinator and GUI host bridge
Files: src/gear_framework/host.py, common.py, worker.py, workspace.py, gui.py; tests/test_host.py, test_workspace.py.
- [x] Test submit/confirm/stop/status/subscriptions, full slot lifetime, before-start DECLINED, partial begin cleanup, same runtime reuse, worker affinity, blocked-session failure records.
- [x] Implement Framework(app_dir, environment, *, clock=None), all FrameworkV1 methods, close/context management and explicit idle environment loading. Factories/configuration/preflight/execution/cleanup/manual callbacks run on fixed worker.
- [x] Implement WorkspaceContext with injected GUI dispatcher; exact commit projection, atomic persistence, read-only ACTIVE, GUI callbacks and serialized manual closures. Optional GUI workspace creation is explicitly invoked by GUI host only.
- [x] Verify failure injection for cleanup/event/report writes, original vs final result, fallback log disclosure and no new execution before complete task exit.
Test examples: blocked end_run keeps next submit BUSY/HOST_BLOCKED; failing report changes execution PASS to final FAIL; stopped call returning error retains STOPPED.

## Task 5 — CLI, example plugin and integration
Files: src/gear_framework/cli.py, __main__.py, examples/, README.md, tests/test_cli.py.
- [x] Add interactive confirmation CLI using the same Framework API; EOF declines, Ctrl-C cooperatively stops and awaits finalization; predictable documented exit codes.
- [x] Add headless deterministic demo plugin and sample Project/Environment/Test Case; no hardware readiness claims.
- [x] Install both packages in the isolated venv, run CLI through real plugin loading and inspect event/report artifacts.
- [x] Run all tests and packaging/import checks; review contract conformance, fix concrete issues, record delivery limits.

## Progress
All five tasks completed. Implementation and contract review were performed by the parent after child tasks could not start because of usage limits. Validation: 47 pytest cases passed, both packages installed and wheels built, installed CLI demo PASS with explicit coverage gap. See doc/development/implementation-status.md for evidence and limits.
