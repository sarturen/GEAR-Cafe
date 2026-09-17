# GEAR Framework Runtime — Current Conclusion

Status: **Active**  
Date: 2026-09-18

## 1. Purpose

Framework Runtime deterministically executes one submitted Test Case against the selected Project and Environment through declared Plugin capabilities.

It owns:

```text
submission
input archival
static preflight
human confirmation
execution
failure evidence
framework cleanup
reporting
```

It does not own test strategy, campaign scheduling, automatic retry/recovery, or runtime Agent decisions.

---

## 2. Runtime host terminology

Use the following terms consistently:

- **Control Host**: the jump/control computer that runs GEAR, GUI/CLI, Framework, and Plugins.
- **DUT**: the test board or system under test.
- **Bench**: the complete physical setup, including Control Host, DUT, cameras, relay hardware, power, and cabling.
- **Environment**: the logical-resource-to-physical-device binding configuration for one Bench.

Framework runs on the Control Host, not on the DUT.

---

## 3. Logical separation, not process separation

The accepted architecture is a layered monolith:

```text
GUI / CLI
    ↓
Framework API
    ↓
Run Coordinator
    ├── Preflight
    ├── Executor
    ├── Plugin Registry
    └── Run Store
```

The layers may all run in one Python process.

The GUI may share the Framework lifecycle and may terminate the current process when it closes. Current scope does not require:

```text
resident daemon
independent Runner service
IPC / HTTP API
disconnect/reconnect recovery
background execution after GUI exit
```

The architectural rule is:

> GUI is an interface to Framework, not the owner of test semantics or hardware behavior.

Framework Core must not depend on PySide6 widgets, windows, or GUI state. CLI and GUI call the same Framework API.

---

## 4. One Active Run

One Control Host and its connected hardware allow only one Active Run at a time.

This includes a submission waiting for human confirmation.

Do not implement:

```text
parallel Test Cases
resource-aware parallel scheduling
automatic Run queueing
separate GUI and CLI hardware ownership
```

A second submission while another is active is rejected as busy.

This rule belongs to Framework rather than GUI button state. The exact minimal cross-process locking mechanism is not yet fixed.

---

## 5. Framework API

GUI and CLI use one small application-facing API, conceptually:

```text
submit(test_case, project, environment)
confirm(run_id)
stop(run_id)
get_status(run_id)
subscribe(listener)
```

The API does not expose Plugin instances or Executor internals.

GUI updates use simple callbacks/listeners. A PySide6 adapter may translate callbacks into Qt signals. Do not introduce a general event bus or message broker.

### Configuration GUI path

Resource configuration is Plugin-owned and does not pass through the Test Executor.

Conceptually:

```text
GUI host
→ Plugin Registry discovers Plugin GUI Editor
→ Plugin Editor performs device-specific configuration/preview
→ Plugin Editor returns serializable config payload
→ Environment Configuration stores that payload
```

Framework does not interpret Plugin-specific configuration fields.

Plugin GUI Editors are loaded only by GUI. CLI execution and Framework Runtime do not import them.

Configuration mode is unavailable while a Run is active so configuration tools and Runtime cannot compete for hardware.

---

## 6. Core component responsibilities

### Run Coordinator

Owns the single Run lifecycle:

```text
archive inputs
→ preflight
→ wait for confirmation
→ execute
→ collect evidence on failure
→ framework cleanup
→ report
```

It is the single place where unexpected runtime failures are captured and converted into the Run result.

### Preflight

Performs static executability and coverage checks.

### Executor

Interprets the Test Case structure and Framework DSL semantics. It does not know about GUI, Plugin discovery, Run directory layout, or report rendering.

### Plugin Registry

Loads declared Plugin contracts, maps Resource Types to implementations, and creates logical resource instances from Environment bindings.

It does not infer per-instance capability differences.

### Run Store

Uses ordinary directories and files to archive inputs, events, evidence, and reports. A database is not currently justified.

---

## 7. Submission archive

On submission, Framework creates a Run directory and copies:

```text
Test Case
Project Definition
Environment Configuration
```

The archived copies are the inputs used for that Run.

Plugin implementations are not copied. The Run records Plugin names and versions.

Conceptual layout:

```text
runs/<run-id>/
├── input/
│   ├── test-case.yaml
│   ├── project.yaml
│   └── environment.yaml
├── preflight.json
├── events.log
├── evidence/
└── report.json
```

This is traceability, not a strong-consistency or configuration-versioning system.

Do not add:

```text
configuration file locks
hot-reload detection
cryptographic execution signatures
Plugin package snapshots
cross-process version coordination
persistent Execution Plan recovery
```

---

## 8. Preflight is static

Preflight checks:

- input schema validity;
- Project logical-resource existence;
- Environment bindings required by the Test Case;
- declared Plugin Operation and Validation capabilities;
- resolved Observation coverage and coverage gaps.

Preflight does not probe live hardware.

It does not attempt to prove:

```text
ADB is currently online
a COM port can currently open
a camera can currently capture
a relay board currently responds
the link will remain stable during execution
```

Hardware access begins in Runtime. Link and device failures during Runtime become normal Run failures.

---

## 9. Human confirmation is mandatory

After a successful static Preflight, GUI/CLI must show:

```text
requested resources and capabilities
resolved bindings
covered observations
uncovered observations
coverage gaps
```

Runtime starts only after explicit human confirmation.

If Preflight rejects the submission:

```text
execution_started: false
result: none
```

If the human declines or returns to editing, the submission ends without a test result.

The archived submission may remain for traceability.

---

## 10. Runtime sequence

After confirmation, Framework creates Plugin resource instances, opens required hardware, and executes:

```text
setup
body
teardown
```

DUT TearDown runs only after normal completion of setup and body.

Any unexpected condition after execution starts stops the Test Case and produces `FAIL`.

---

## 11. Result model

Executed Runs have only three top-level results:

```text
PASS
FAIL
STOPPED
```

### PASS

All expected execution and validation completed successfully.

### FAIL

Any unexpected condition after Runtime begins, including:

```text
assertion not satisfied
device unavailable
link interruption
operation failure
validation cannot complete
Plugin exception
Framework execution exception
```

Framework does not attempt to classify responsibility as DUT failure, infrastructure error, or Plugin error.

The report preserves the first failure point and original diagnostic information so a human can judge the cause.

There is no separate top-level `ERROR` result.

### STOPPED

The human explicitly stopped the Run.

This is not reported as `FAIL` and does not automatically trigger Failure Evidence.

Preflight rejection and declined confirmation are not test results because execution never started.

---

## 12. Stop semantics

Stop is cooperative:

- waits may end immediately;
- loops exit at the next Framework cancellation point;
- an in-progress Plugin call is not killed by force;
- Framework waits for that call to return before completing Stop.

Do not build forced thread termination or Plugin-process isolation in the first phase.

---

## 13. Failure Evidence

`FAIL` triggers only the declaratively requested Evidence capabilities.

Evidence collection is best effort:

- evidence failures never replace the original failure;
- each evidence result is recorded separately;
- evidence collection does not restore or modify the DUT beyond what the evidence capability itself requires.

`STOPPED` does not automatically trigger Failure Evidence.

---

## 14. Framework Cleanup

Framework Cleanup runs after `PASS`, `FAIL`, or `STOPPED` and releases Framework-owned resources:

```text
connections
watchers
subscriptions
threads/processes
file handles
temporary runtime objects
```

Cleanup is not DUT TearDown and must not perform automatic recovery that destroys the incident scene.

---

## 15. Minimum report facts

The final Run record should include:

```text
result: PASS | FAIL | STOPPED
archived input references
Preflight result
requested and actual coverage
step execution events
first failure point and original details
Evidence results
Cleanup diagnostics
Plugin names and versions
start/end timestamps
```

Coverage remains separate from the top-level result.

---

## 16. Explicitly rejected runtime complexity

Do not add without a demonstrated requirement:

```text
resident background service
GUI/Runtime independent lifetime
IPC or HTTP control plane
general event bus
parallel Run scheduler
automatic Run queue
hardware readiness probing in Preflight
FAIL versus infrastructure ERROR classification
strong configuration consistency machinery
forced Plugin-call termination
database-backed Run Store
runtime Agent involvement
```

---

## 17. One-sentence definition

GEAR Framework Runtime is:

> A Qt-independent, in-process-capable execution core that archives one submitted test, statically preflights it, waits for human confirmation, runs it through Plugin contracts, records PASS/FAIL/STOPPED, captures failure evidence, cleans up Framework resources, and writes a traceable report.
