# GEAR Framework Runtime Cafe — Handover

Status: **Core runtime shape accepted; concrete schemas and code structure remain open**  
Date: 2026-09-18

## 1. Why this Cafe happened

DSL discussion was considered mature enough to pause. The next goal was to establish the Framework boundary before discussing GUI details.

The Cafe deliberately focused on actual runtime behavior rather than class diagrams or a general distributed architecture.

---

## 2. Important correction: logical decoupling is enough

The discussion initially moved toward a persistent Framework service that would outlive GUI failure.

The user rejected that complexity.

Accepted direction:

> GUI and Framework may live and die together; they must be separated logically, not necessarily by process.

Therefore do not restart from daemon, IPC, reconnection, or service-hosting design.

---

## 3. Control Host terminology

An ambiguity appeared around “bench machine”.

Use:

```text
Control Host = jump/control computer running GEAR
DUT          = tested board/system
Bench        = complete physical setup
Environment  = logical-to-physical binding configuration
```

Framework runs on the Control Host.

---

## 4. One Run only

The user considered concurrent GUI/CLI Test Cases obviously invalid for the same Control Host and hardware.

Accepted rule:

> only one submitted/active Run at a time; no queue and no resource-aware concurrency.

The rule belongs to Framework, but the exact smallest cross-process enforcement mechanism was not selected.

---

## 5. Preflight confirmation

The user explicitly required a human confirmation after Preflight and before Runtime.

Preflight must make Coverage Gap visible before execution.

An early proposal added immutable Execution Plans, fingerprints, and invalidation behavior. The user considered this unnecessary.

The simpler accepted model is:

```text
submit
→ copy inputs into Run archive
→ preflight archived inputs
→ human confirms
→ execute archived inputs
```

The goal is traceability, not strong consistency.

---

## 6. Preflight does not probe hardware

The user rejected live-device probing because a successful probe cannot guarantee that an unstable link remains healthy.

Preflight is therefore static. Hardware failures are observed during Runtime.

Do not reintroduce readiness probes merely to make Preflight appear stronger.

---

## 7. Why there is no ERROR result

A proposed result model separated functional `FAIL` from infrastructure or execution `ERROR`.

The user rejected it because Framework often cannot reliably decide whether the cause belongs to the DUT, link, bench, Plugin, or Framework.

Accepted result model:

```text
PASS
FAIL
STOPPED
```

Anything unexpected after execution starts is `FAIL`.

Preserve detailed raw facts, but do not pretend to assign responsibility.

Preflight rejection is not a test result because execution did not start.

---

## 8. Why STOPPED remains separate

The user accepted a separate `STOPPED` result for explicit human cancellation.

It is not a product/test failure and does not automatically trigger failure evidence.

Cancellation remains cooperative; forced Plugin termination is out of scope.

---

## 9. Accepted component shape

The accepted direction is a simple layered monolith:

```text
GUI / CLI
→ Framework API
→ Run Coordinator
→ Preflight / Executor / Plugin Registry / Run Store
```

Use direct calls and simple callbacks. Do not introduce an event bus or separate service.

---

## 10. Next useful Framework Cafe questions

The following remain open and should be discussed only when needed:

- exact Run status/event/report schemas;
- exact minimal Control Host lock used across GUI and CLI processes;
- resource instance creation/open/close lifecycle details;
- Framework callback threading rules needed by PySide6;
- Run archive retention and deletion policy;
- how cleanup failure is represented without introducing a separate ERROR result;
- the still-open zero-bound wildcard Observation outcome from Reporting.

Do not jump into Plugin process isolation, distributed execution, daemon hosting, or parallel scheduling.

---

## 11. Next Cafe posture

Continue from the accepted minimal runtime path:

```text
SUBMIT
→ ARCHIVE
→ STATIC PREFLIGHT
→ HUMAN CONFIRM
→ RUN
→ EVIDENCE ON FAIL
→ FRAMEWORK CLEANUP
→ REPORT
```

When choosing concrete APIs or schemas, prefer the smallest representation that supports this path and the existing accepted DSL/Plugin/Environment contracts.
