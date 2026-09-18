# GEAR Architecture v1

Status: **Frozen for v1 implementation**
Date: 2026-09-18

## 1. Architecture

GEAR v1 is a contract-decoupled, layered monolith:

```text
GUI Shell / CLI
       │
       ▼
Framework API
       │
       ▼
Run Coordinator
├── Preflight
├── Executor
├── Plugin Registry
└── Run Store / Reporting
       │
       ▼
Plugin Runtime Contract
       │
       ▼
Hardware and domain implementations
```

GUI, Framework, and Plugins may run in one Python process and may share one
lifecycle. v1 requires logical module separation, not daemonization, IPC,
process isolation, or survival after GUI exit.

## 2. Normative contracts

- [GEAR Flow DSL Contract v1](./dsl/v1.md)
- [GEAR Plugin Contract v1](./plugin-contract/v1.md)
- [GEAR Project and Environment Contract v1](./environment-model/v1.md)

The Runtime lifecycle and reporting rules in
[Framework Runtime Conclusion](./framework-runtime/conclusion.md) are also
binding for v1 until superseded by a later versioned contract.

## 3. Framework API

GUI and CLI use the same application-facing API:

```text
submit(test_case, project, environment) -> run_id
confirm(run_id)
stop(run_id)
get_status(run_id) -> RunStatus
subscribe(listener) -> Subscription
```

Semantics:

- `submit` archives the three inputs and performs static Preflight;
- successful Preflight enters `WAITING_CONFIRMATION`;
- `confirm` starts Runtime;
- `stop` requests cooperative stop;
- `get_status` is a read-only snapshot;
- `subscribe` supplies ordered process-local Run notifications;
- a second submission is rejected while any Run is waiting for confirmation or
  executing;
- the API never exposes Executor internals or Plugin instances.

Conceptual state machine:

```text
SUBMITTED
  ├── Preflight rejected ───────────────► REJECTED (no result)
  └── Preflight accepted ───────────────► WAITING_CONFIRMATION
          ├── declined ─────────────────► DECLINED (no result)
          └── confirmed ────────────────► RUNNING
                    ├── success ────────► PASS
                    ├── unexpected ─────► FAIL
                    └── human stop ─────► STOPPED
```

Framework cleanup and final report writing follow every terminal Runtime result.

## 4. GUI boundary

GUI Shell is a Framework-facing application layer, not part of Framework Core.
Framework Core MUST import no PySide6 type.

GUI Shell provides:

- Run submission, confirmation, stop, status, and report pages;
- Plugin discovery and one top-level Workspace page per Plugin;
- Environment loading and atomic persistence;
- configured ADB device-id projection to Workspaces;
- `IDLE`/`ACTIVE` read-only state propagation;
- callback-to-Qt-signal adaptation.

Plugin Workspace provides all Plugin-specific configuration, preview, status,
and manual control. GUI Shell does not understand Plugin-specific fields.

## 5. Development independence

Framework and Plugins can be developed independently after sharing only the
versioned contracts.

Framework development may use fake Plugins to test:

- manifest and registry loading;
- static Preflight and argument schemas;
- Executor timing and result mapping;
- Workspace hosting and Environment persistence;
- reporting and cleanup.

Plugin development may use fake Framework contexts to test:

- configuration validation;
- Runtime operations, conditions, evidence, and cleanup;
- one-Workspace GUI behavior;
- full-slice commits and Active-Run read-only behavior.

Different Plugins MUST NOT import or call each other. Shared physical grouping
passes only through configured Environment values.

This is complete source-level and team-level decoupling, not failure isolation:
because v1 is in-process, an unrecoverable process failure may terminate GUI,
Framework, and Plugins together.

## 6. Frozen simplicity boundaries

v1 intentionally excludes:

- resident services, IPC, and HTTP control planes;
- automatic Run queueing or parallel execution;
- Plugin process isolation and forced termination;
- general event buses;
- live hardware probing during Preflight;
- automatic Environment relationship maintenance;
- universal configuration-form generation;
- configuration locking/version coordination;
- automatic retry, recovery, campaign scheduling, or runtime Agents.
