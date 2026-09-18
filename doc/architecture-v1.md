# GEAR Architecture v1

Status: **Frozen for v1 implementation — 2026-09-18 revision**
Date: 2026-09-18

## 1. Design principle

Prefer explicit constraints and direct solutions. Do not accommodate imagined
future scenarios. Discuss complexity only for a concrete large benefit at small
cost, or when an agreed requirement cannot otherwise be satisfied.

GEAR is agent-assisted to build and agent-free to run. Agents may implement
Plugins and author tests; Runtime follows deterministic software rules.

## 2. Architecture

GEAR is a contract-decoupled layered monolith:

```text
GUI Shell / CLI
       |
       v
Framework API
       |
       v
Run Coordinator
  +-- Preflight
  +-- Executor
  +-- Plugin Registry
  +-- Run Store / Reporting
       |
       v
Long-lived Plugin sessions
       |
       v
Plugin-owned device services and connections
```

A Plugin Workspace uses that same Plugin session's private device services.
It does not create a second independent hardware owner.

One Control Host permits one GEAR control session: GUI or CLI. Startup acquires
host-wide OS exclusion before loading Plugins, and holds it until process exit,
including idle time. A second process exits with an explicit busy error.
v1 does not forward CLI requests into an existing GUI process.

GUI, Framework and Plugins may share one process and lifecycle. Logical module
separation is required. Daemonization, IPC, process isolation and background
execution after GUI exit are not required.

## 3. Normative contracts and shared package

- [Flow DSL v1](./dsl/v1.md)
- [Plugin v1](./plugin-contract/v1.md)
- [Project and Environment v1](./environment-model/v1.md)
- [Framework Runtime v1](./framework-runtime/v1.md)
- [Shared Python contract package](./contracts/README.md)
- [Confirmed revision decisions](./contract-decisions.md)

The `gear_contracts` package fixes exact types, interfaces, contexts and errors.
It contains no Framework implementation, hardware driver or Qt dependency.
The versioned documents define behavior. Code/prose disagreement is a contract
defect to fix, not permission for different agents to choose different meanings.

`conclusion.md` summarizes current contracts. `handover.md` preserves history
and is not an alternative implementation specification.

## 4. Two lifetimes

**Control-session lifetime**

- Each Plugin has one long-lived Runtime object and private device services.
- Configured COM/camera/relay connections may remain open across page changes
  and many Runs.
- Workspace manual controls and Runtime use those same services.
- Explicit disconnect, a changed connection binding, or application shutdown
  releases the affected connection.
- A Run ending does not close session-owned connections.

**Run lifetime**

- A Run owns its input snapshot, stop token, execution state, events, evidence
  tasks and temporary subscriptions.
- `begin_run` binds archived configuration and resets Run-local state.
- `end_run` finishes Run-local work while retaining session connections.
- No other Run or manual hardware command overlaps that Run.

Framework does not require recreation of device objects for each Test Case.
Plugins own their internal connection arrangement. There is no general
hardware-sharing graph or cross-Plugin service lookup.

## 5. Threading and GUI boundary

One fixed worker serves the entire control session. Plugin creation,
configuration, validation, Run calls and session close are serialized there.

GUI widgets, Workspace factories/disposal and Workspace listener callbacks run
on the GUI thread. The host translates worker notifications to GUI callbacks.
Manual actions are routed to the worker by the Workspace context, without
entering the Test Executor.

Plugins may own acquisition workers, but own their synchronization and exit.
GUI and Runtime consume the same Plugin-owned device service and synchronized
cache, rather than competing connections.

Framework Core imports no PySide6 type. GUI Shell provides Run pages, Workspace
hosting, Environment persistence, configured device-id choices, read-only state
propagation and callback-to-Qt adaptation. Plugin Workspace owns its configuration,
preview, status and manual controls; GUI Shell does not interpret those fields.

## 6. Framework API and complete exclusivity

GUI and CLI use the same API, precisely defined by Runtime v1:

```text
submit(test_case, project, environment) -> run_id
confirm(run_id)
stop(run_id)
get_status(run_id) -> RunStatus
subscribe(listener) -> Subscription
```

`submit` reserves the only Run slot, archives inputs and performs static
Preflight. Preflight checks configuration only. Neither fresh hardware probes
nor cached online/offline status gate acceptance.

Successful Preflight waits for explicit human confirmation. `stop` before
execution declines without a test result. After execution starts it requests
cooperative stop, without overwriting an already recorded FAIL.

The slot remains occupied throughout:

```text
archive -> Preflight -> confirmation -> execution
-> requested failure evidence -> Run-local cleanup
-> report write and closed Run event writer -> complete Run-task exit
-> IDLE
```

No queue or overlap is permitted. A known result does not release the slot.
Session connections may remain open after the Run has exited.
Cleanup/reporting failure blocks the session: a provisional PASS becomes FAIL,
while existing FAIL/STOPPED remain. Record original execution result, primary
failure and ordered finalization errors separately. No new Run/manual command
is accepted until the cause is handled and GEAR restarted.
Configuration changes and manual hardware commands stay disabled for the entire
ACTIVE interval. Status display may continue without injecting manual commands.

The public application API exposes no Plugin instance or Executor internals.
The separate GUI hosting path passes each Workspace only its own Plugin session.

## 7. Extension and independent development

Each Resource Type has exactly one Plugin owner. Extend ADB by modifying its
owning Plugin. An independent Plugin may declare a new Resource Type.
Plugins must not import/call one another or inject capabilities into another
Plugin's type. Shared physical grouping uses Environment data.

Plugins live under the application directory's fixed `plugins/` directory.
Startup loads immediate child Plugin directories. Copy a Plugin there and
restart to add it. Broken entries and conflicting ids/types are explicit errors.
There is no registration list or hot reload.

Framework and Plugin authors share only the contracts package and documents.
They can use fake counterparts to check the boundary without accessing one
another's implementation. Acceptance includes two sequential Runs reusing one
physical connection, Run-local state isolation and complete non-overlap.

## 8. Simplicity boundaries

v1 excludes:

- resident services, IPC, HTTP control planes and cross-process connection handoff;
- multiple control sessions, parallel Runs and automatic Run queues;
- Plugin process isolation and forced termination;
- Plugin dependency injection and cross-Plugin capability merging;
- automatic recovery/reconnection after connection failure;
- live hardware readiness checks in Preflight;
- general event buses, service locators and hardware ownership graphs;
- automatic Environment relationship maintenance and universal generated forms;
- configuration version coordination and Plugin package snapshots;
- automatic retries, campaign scheduling and Runtime Agents.
