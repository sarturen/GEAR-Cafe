# GEAR Failure & Evidence — Current Conclusion

Status: **v1 behavior frozen**
Date: 2026-09-18

Normative syntax and Plugin result shapes are defined by
[GEAR Flow DSL Contract v1](../dsl/v1.md) and
[GEAR Plugin Contract v1](../plugin-contract/v1.md).

## 1. Functional assertion failure

For an ordinary functional test:

```text
Assertion violation
→ Test Case FAIL
→ preserve/collect incident evidence
→ stop current Test Case
```

The assertion itself does not decide whether to continue, retry, or alter strategy.

---

## 2. No automatic test manager

GEAR does not currently decide:

```text
which test runs next
whether a campaign continues
whether to retry
which stress strategy to use
```

A person decides what to run.

---

## 3. Statistics should be a separate test intent

A long-running measurement such as:

> observe blackout frequency over one week

should be represented as a dedicated measurement/observation-oriented case.

Do not make ordinary functional assertions carry complicated:

```text
continue_on_failure
incident counters
rate thresholds
aggregate policies
```

unless future requirements force it.

---

## 4. Failure evidence is necessary

Real examples require:

```text
ADB pull xxxlog
screen screenshot
console dump
other incident artifacts
```

Therefore tests need a way to request incident evidence.

The evidence action should be supplied by Plugin capabilities.

---

## 5. Evidence collection is not a general failure workflow

Do not recreate the current arbitrary `on_failure` step list.

Failure evidence must not become:

```text
reboot DUT
restore image
flash build
modify state
continue normal test logic
```

Its job is to preserve the accident scene.

---

## 6. Preserve original failure cause

If evidence collection itself fails, it must not overwrite the original test failure.

Desired behavior:

```text
primary assertion failure remains primary
evidence collection failure is secondary/reportable
```

---

## 7. TearDown boundary

TearDown currently means normal environment restoration after successful completion.

Initial supported restoration:

```text
normal power-off
normal power-on
stop extra control actions
```

On failure, do not automatically execute DUT restoration that could destroy incident state.

---

## 8. Framework cleanup is separate

Framework must always clean up its own resources:

```text
watchers
subscriptions
processes
threads
file handles
```

This cleanup must not modify DUT state in a way that destroys evidence.

---

## 9. Deferred recovery problem

Explicitly postponed:

```text
restore old image
flash major build
restore boardid
enter programming mode
automatic DUT rescue
```

This is a different system and should not be smuggled into TearDown.

---

## 10. Frozen v1 declaration

Requested evidence is a top-level declarative `evidence_on_fail` list. Each
entry names a resource, a Plugin-declared evidence capability, and optional
schema-validated arguments.

It is deliberately not an executable failure sub-flow. Collection runs best
effort after `FAIL`, in declaration/expansion order, and never replaces the
original failure.
