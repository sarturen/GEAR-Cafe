# GEAR Failure & Evidence Cafe — Handover

Status: **Important open syntax remains**  
Date: 2026-09-17

## 1. Two motivating stability cases

### Case A — blackout during long stress

A blackout is a real violation, but the user may sometimes run a separate statistics-oriented test to measure blackout frequency.

This led to the decision not to burden every normal assertion with continue/aggregate policy.

### Case B — wakeup enters fastboot / whole-system reset

This is a functional failure where continued execution may be meaningless and may destroy the accident scene.

Therefore ordinary functional assertions stop the current case after evidence capture.

---

## 2. Important simplification

An earlier design separated:

```text
incident handling
continue/abort execution policy
```

The user simplified it further:

> first focus on functional testing; failed assertion stops the case and preserves evidence.

What happens next is a human decision.

Do not re-expand this into a scheduler yet.

---

## 3. TearDown discussion

Three possible recovery levels were discussed:

1. normal power-cycle and stop extra controls;
2. restore original image, then power-cycle;
3. enter flash mode and rebuild major image/boardid.

User explicitly requested:

> implement only level 1 for now.

Keep levels 2 and 3 out of current scope.

---

## 4. Next Cafe target

The most useful next question in this topic is likely:

> how should a test declare which evidence capabilities it wants on failure, without creating a second programming language inside `on_failure`?
