# GEAR DSL Cafe — Handover

Status: **Historical context; v1 questions resolved**
Date: 2026-09-18

The normative contract is [GEAR Flow DSL Contract v1](./v1.md). This handover
preserves the discussion path but no longer contains open v1 design work.

## 1. Why this Cafe happened

The existing GEAR Flow YAML has many device-specific step kinds and implicit temporal bookkeeping.

The redesign goal is not to preserve the old grammar. The goal is to find the smallest DSL that can naturally express real bench tests while keeping hardware details and parsing logic outside the language.

The Cafe repeatedly used real test scenarios as the architecture test.

---

## 2. Main direction chosen

The user strongly prefers a **declarative but very small DSL**.

The language should express test intent, while Framework provides deterministic time semantics and Plugin provides real-world capability.

The final mental model became:

```text
Project     says what logical resources exist
Environment says how this bench binds them
Plugin      says what each resource type can do/observe/validate
Test Case   says how this test operates/waits/checks/repeats
Framework   binds, validates, executes, captures evidence, reports
```

---

## 3. Key pruning decisions

Several ideas were considered and explicitly rejected because they created abstraction without demonstrated value:

- generic resource shareable/exclusive model;
- product variant/profile hierarchy;
- controlled semantic groups/tags such as `cockpit_display`;
- general `FOR EACH`;
- `IF PRESENT`;
- general IF/ELSE;
- variables and cross-step dataflow;
- cross-case state;
- Suite/Campaign automatic scheduling;
- runtime Agents;
- generic regex/text/file parsing in DSL;
- state-machine DSL;
- old `mark/since/watch/expect_watch` temporal bookkeeping.

The guiding attitude is:

> unsupported is better than guessed;
> add syntax only after a real scenario proves it necessary.

---

## 4. Why `AND` replaced an explicit WINDOW construct

A temporary idea introduced a `WINDOW` block to let several observations share the same time interval.

The user proposed the simpler:

```text
ASSERT XXX AND YYY ...
```

This is both more readable and closer to test intent.

The accepted result is:

```text
ASSERT A AND B WITHIN T EVERY Δ
ASSERT A AND B FOR T EVERY Δ
```

No separate `WINDOW` syntax.

---

## 5. Why `EVERY` became explicit

`FOR 1h` alone is underspecified.

Checking every 10 ms and checking every 10 s are materially different test strengths.

Therefore any temporal polling assertion must explicitly state its verification cadence:

```text
WITHIN 30s EVERY 200ms
FOR 1h EVERY 100ms
```

There is no hidden default interval.

---

## 6. Why validation belongs to Plugin

ADB shell, fastboot, serial, logs, etc. can return arbitrarily structured data.

Attempting to make Framework DSL provide generic:

```text
regex
contains
grep
split
parse number
file transforms
```

would quickly recreate a poor scripting language.

Decision:

> Framework DSL says that a result must be validated; Plugin defines how that validation works.

Low-level command surfaces remain available as escape hatches.

Repeated escape patterns should gradually be promoted into formal Plugin capabilities.

---

## 7. Why Test Cases must remain independent

The user explicitly rejected data passing and hidden dependencies between test cases.

Otherwise:

- execution-order hell appears;
- concurrency becomes dangerous;
- a test cannot be run independently;
- shared global state becomes implicit.

For now, a person decides which case to run next.

GEAR does not act as an autonomous test manager.

---

## 8. Setup / TearDown boundary

Setup establishes the current test case's initial state.

TearDown currently means **normal environment restoration**, not fault recovery.

Only simple recovery is wanted initially:

- normal power-off;
- normal power-on;
- stop extra control commands.

More aggressive actions such as restoring images, flashing a full build, restoring boardid, or rescue-mode recovery are intentionally postponed.

---

## 9. Failure policy

A normal functional assertion fails the current test case.

Current intended order:

```text
assertion violation
→ freeze/collect incident evidence
→ current case FAIL
→ stop current case
```

Do not add assertion-specific continue/retry/fatal policy yet.

If the goal is to collect a failure rate, create a dedicated observation/statistics test rather than making ordinary assertions multi-policy.

---

## 10. DSL-adjacent questions resolved by v1

### Failure evidence surface syntax

It is already clear that some tests need specific evidence:

```text
ADB pull xxxlog
screen screenshot
console dump
```

v1 uses a top-level declarative `evidence_on_fail` list. Each entry names a
resource, a Plugin evidence capability, and optional arguments.

It is not an arbitrary `on_failure` sub-flow.

### Zero-bind observation case

Observation resources may be partially absent and become coverage gaps.

If a wildcard resolves to zero valid bindings, Preflight rejects the Test Case.
Partial binding remains executable with explicit coverage gaps.

### Exact concrete grammar

v1 freezes a canonical YAML representation with `do`, `wait`, `assert`, and
`repeat` step mappings. The older uppercase examples remain semantic shorthand.

---

## 11. Useful reference scenarios

The current kernel was validated against four real-world scenarios:

1. KL30 boot, screen + ADB recovery, then 1h stability with failure evidence.
2. 10,000 KL15 sleep/wakeup cycles with expected off/on transitions.
3. 1000 repeated ADB shell executions followed by pull/unpack/log validation.
4. repeated fault injection requiring specific screen/reset behavior, bounded recovery, then command-result validation.

The current kernel handled all four without requiring IF, variables, state machines, generic parsing, or scheduler logic.

That is strong evidence the language is close to “just enough”.

---

## 12. Next Cafe posture

Do not immediately add syntax.

When a new test appears:

1. Try to express it using the current kernel.
2. If expression is awkward, ask whether the missing concept belongs to Framework grammar or to a Plugin capability.
3. Prefer Plugin capability when the concept is domain-specific.
4. Add Framework syntax only when the need is generic across resource types and cannot be expressed cleanly otherwise.
