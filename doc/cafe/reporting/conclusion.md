# GEAR Reporting & Coverage — Current Conclusion

Status: **Summary; Runtime v1 defines report data**
Date: 2026-09-17

## 1. Result and coverage are separate

A test result answers:

> Did the assertions that were actually executed pass?

Coverage answers:

> What intended resources/capabilities were actually observable in this Environment?

Do not collapse them into one PASS/FAIL flag.

---

## 2. Missing observation coverage must be visible twice

Any Environment-dependent reduction in test coverage must be visible:

1. **before execution**;
2. **in the final report**.

This is a hard invariant.

---

## 3. Example

Project target:

```text
SCREEN.center
SCREEN.cluster
SCREEN.passenger
SCREEN.hud
SCREEN.rear_left
SCREEN.rear_right
```

Current Environment:

```text
center
cluster
passenger
```

Preflight must show:

```text
covered:
  center
  cluster
  passenger

not covered:
  hud
  rear_left
  rear_right
```

The report must preserve the same fact.

---

## 4. PASS does not imply complete product coverage

A report such as:

```text
Result: PASS
```

must never be interpreted as:

> every Project target was tested.

It means:

> the assertions that actually executed passed.

The coverage section states what was not exercised.

---

## 5. Record the execution Environment

The report should preserve enough information to explain the exact hardware/logical setup used for that run.

At minimum, likely candidates include:

```text
Environment identity
Archived Environment reference
resolved logical resources
Plugin versions/capability contract versions
actual covered targets
uncovered targets
```

Exact fields are defined by `RunReport` in
[gear_contracts.data](../../contracts/gear_contracts/data.py), with event/report
semantics in [Runtime v1](../framework-runtime/v1.md). No configuration hash or
revision-coordination mechanism is required.

---

## 6. Avoid fake precision

A synthetic percentage such as:

```text
Coverage: 73%
```

is not currently preferred.

Explicit sets/counts are more honest:

```text
requested observations: 6
executed observations: 3
uncovered observations: 3
```

---

## 7. Preflight rejection vs coverage gap

Current runtime rule:

### Missing operation

Reject the test.

Example:

```text
test requires POWER.kl30 OFF
Environment has no POWER.kl30
→ REJECT
```

### Missing observation

May reduce coverage.

Example:

```text
SCREEN.* target includes six screens
Environment can observe three
→ run three, report three uncovered
```

### Missing validation capability

Reject the test.

If Framework cannot deterministically validate what the test asks for, do not execute and guess.

---

## 8. Finalization diagnostics

Keep execution_result and final result separate. Each finalization failure records
stage, Plugin (or Framework), UTC time, code/message and original exception data.
Preserve the primary execution failure and clearly disclose blocked execution.
Do not call a partial/failed report write successful persistence.
When report writing fails, status and GUI/CLI retain the error; attempt one
ordinary session-log append and disclose if that also fails. Runtime v1 and
FinalizationFailure define the exact record.

## 9. Zero-binding wildcard

Resolved for v1:

> if an assertion targets a Resource Type wildcard and zero valid observations
> bind, Preflight rejects the Test Case.

There is no vacuous `PASS` and no separate top-level
`NOT-COVERED`/`NOT-APPLICABLE` result in v1.
