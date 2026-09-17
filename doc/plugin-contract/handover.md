# GEAR Plugin Contract Cafe — Handover

Status: **Continue from Resource-Type capability contracts**  
Date: 2026-09-17

## 1. Why the original “capability” idea needed refinement

Simple resources are easy:

```text
POWER -> ON/OFF
SCREEN -> BLACK/FREEZE/etc.
```

But ADB, fastboot, serial and SSH can execute arbitrary parameterized commands.

Trying to enumerate every possible command as a capability would create a capability-list nightmare.

The resolution was:

> capability may be a parameterized command surface.

For example:

```text
ADB.SHELL(command)
```

`SHELL` is the Plugin operation. Individual shell commands are data.

---

## 2. Why raw commands are still allowed

Without raw command surfaces, every new one-off test would require Plugin development first.

That is too rigid.

Therefore raw executors are kept as escape hatches.

However, they must not cause Framework DSL to grow generic parsing or dataflow.

---

## 3. Why generic parsing was rejected

A proposed direction considered letting DSL inspect command results through generic:

```text
stdout
stderr
exit_code
contains
regex
```

The user explicitly preferred pushing result checking into Plugin capability instead.

This prevents DSL from slowly becoming a poor Python replacement.

---

## 4. Important fixed rule

The user explicitly chose:

> all instances of a Resource Type have exactly the same capability set.

No per-instance capability differences.

This rule should be treated as hard unless reality later proves it impossible.

---

## 5. Next Cafe posture

The next Plugin discussion should probably focus on contract shape and discovery only after DSL work is sufficiently settled.

Do not jump straight into GUI extension architecture merely because camera ROI editing will eventually need special UX.
