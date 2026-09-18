# GEAR Environment Model Cafe — Handover

Status: **Historical context; v1 data model frozen**
Date: 2026-09-18

The normative data model is
[GEAR Project and Environment Contract v1](./v1.md).

## 1. Important correction during Cafe

The discussion initially started drifting toward a general model of resource sharing, product variants, optional/required resources, and controlled semantic groups.

The user provided the real situation:

> apart from screen count and screen purpose, almost everything else is basically unchanged.

That fact should dominate future design.

Do not generalize away from it.

---

## 2. Screen role became logical identity

The GUI already needs to configure:

> which camera observes which bench screen, and what the ROI is.

Therefore the natural logical abstraction is:

```text
SCREEN.center
SCREEN.cluster
...
```

The physical camera and ROI are Environment implementation details.

This is stronger and cleaner than:

```text
SCREEN.1 -> metadata role=center
```

The test should speak the domain role directly.

---

## 3. Why groups/tags were rejected

A controlled vocabulary for groups such as `cockpit_display` initially looked attractive.

The user later judged it unnecessary.

Current needs are covered by:

- all resources of one type (`SCREEN.*`);
- explicit named resources (`SCREEN.center`, `SCREEN.cluster`).

Do not reintroduce groups just because they are theoretically elegant.

---

## 4. Coverage concern

A key question arose:

> If the Project has six screens and the current Environment only instruments three, must there be different test cases?

Answer: no.

A wildcard target may represent the Project-wide intended observation scope, while runtime binds the subset that the current Environment can actually observe.

However, missing targets must be visible both:

- before execution;
- in the final report.

This is not silent optionality.

---

## 5. Distinguish action dependencies from observation coverage

The user clarified a strong runtime rule:

- operations cannot be missing;
- observations may be missing;
- validation capability cannot be missing.

Example:

```text
Power-cycle test + no relay
→ reject outright
```

Example:

```text
all-screen observation + only 3 of 6 screens instrumented
→ run 3, report 3 missing
```

This distinction is more useful than attaching a permanent `required/optional` property to every resource.

---

## 6. Next Cafe posture

Keep Environment focused on:

```text
logical resource -> physical implementation
```

Do not prematurely add:

- product family inheritance;
- optional/required profile trees;
- generic resource ownership;
- capability-per-instance;
- semantic tagging systems.
