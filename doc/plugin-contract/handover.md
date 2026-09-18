# GEAR Plugin Contract Cafe — Handover

Status: **Historical context; v1 contract frozen**
Date: 2026-09-18

The normative contract is [GEAR Plugin Contract v1](./v1.md). Later GUI Cafe
decisions replaced the configuration-editor model below with one complete
Plugin Workspace and real-time full-slice persistence.

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

## 5. GUI configuration ownership (superseded shape)

A later Framework/GUI Cafe asked how GUI should interact with low-level Plugins.

The accepted answer distinguishes Runtime from configuration:

```text
Test Runtime:
GUI -> Framework -> Plugin Runtime -> hardware

Environment configuration:
GUI host -> Plugin GUI Workspace -> Plugin-owned Environment slice
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

The originally proposed Framework/GUI responsibilities were:

- the logical resource identity being configured;
- editor hosting and persistence;
- persistence of the returned serializable payload;
- preventing configuration sessions during an Active Run.

The final v1 contract keeps the loading boundary but replaces Save/Cancel and a
configuration-only editor with one Plugin Workspace. It combines configuration,
preview, status, and manual controls and commits the complete Plugin slice after
semantic changes.

---

## 6. v1 outcome

Manifest/discovery, Runtime, validation, evidence, and Workspace context are now
frozen in `v1.md`. A universal configuration schema renderer remains rejected.
