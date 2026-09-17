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

## 5. GUI configuration ownership

A later Framework/GUI Cafe asked how GUI should interact with low-level Plugins.

The accepted answer distinguishes Runtime from configuration:

```text
Test Runtime:
GUI -> Framework -> Plugin Runtime -> hardware

Environment configuration:
GUI host -> Plugin GUI Editor -> Plugin-owned config payload
```

The user explicitly chose:

> let every Plugin define its own configuration behavior and put that responsibility in the Plugin Contract.

Therefore Framework should not build a universal device-configuration form system.

Plugin owns:

- configuration payload format;
- configuration validation;
- device discovery needed by its editor;
- previews and interactive tools such as camera ROI selection;
- the PySide6 configuration editor presented inside the GUI host.

Framework/GUI owns:

- the logical resource identity being configured;
- editor hosting and Save/Cancel flow;
- persistence of the returned serializable payload;
- preventing configuration sessions during an Active Run.

The GUI editor is loaded only by GUI. CLI execution and Framework Runtime use the Plugin Runtime surface and saved Environment data without importing the editor.

---

## 6. Next Cafe posture

The next Plugin discussion may focus on manifest/discovery and the smallest editor factory/context contract.

Do not reintroduce a universal configuration schema renderer unless repeated Plugin implementations prove it useful.
