# GEAR Reporting & Coverage Cafe — Handover

Status: **Core rule accepted, schema not designed**
Date: 2026-09-17

## 1. Why this became important

A single project-wide test may run against benches with different screen counts.

The user explicitly does not want one separate test case per bench topology.

Therefore the same test may have different observation coverage on different benches.

This makes coverage disclosure essential.

---

## 2. Strong user requirement

The user explicitly required two disclosure points:

> execution must clearly say what can and cannot be tested;

> the final report must clearly state the hardware/environment basis and what capabilities/resources were actually tested.

Treat this as a hard reporting requirement, not cosmetic logging.

---

## 3. Why coverage is not a score

The goal is not to invent a quality score.

It is to preserve factual execution scope.

Prefer:

```text
tested: A, B, C
not covered: D, E, F
```

over an abstract percentage.

---

## 4. Next Cafe posture

Do not design a huge reporting subsystem yet.

The next reporting Cafe should stay focused on:

- exact preflight summary;
- exact report minimum fields;
- Environment snapshot/versioning;
- evidence linkage.
