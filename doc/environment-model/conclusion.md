# GEAR Environment Model — Current Conclusion

Status: **v1 frozen**
Date: 2026-09-18

Normative contract: [GEAR Project and Environment Contract v1](./v1.md).

## 1. Three distinct concerns

Do not mix:

```text
Project Definition
Environment Configuration
Test Case
```

They answer different questions.

### Project Definition

> What logical resources exist in this project?

### Environment Configuration

> How does this physical bench implement those logical resources?

### Test Case

> What does this test want to do with those logical resources?

---

## 2. Logical resource identity

Tests reference semantic logical identities:

```text
SCREEN.center
SCREEN.cluster
SCREEN.passenger
SCREEN.hud

POWER.kl30
POWER.kl15

ADB.main
CONSOLE.main
```

For screens, **screen purpose is the logical identity**.

Do not use numbered identities such as:

```text
SCREEN.1
SCREEN.2
```

as the test-facing abstraction.

---

## 3. Physical binding lives only in Environment

Example:

```text
SCREEN.center
  -> Camera 2
  -> ROI ...

SCREEN.cluster
  -> Camera 3
  -> ROI ...

ADB.main
  -> serial ABC123

CONSOLE.main
  -> COM7 / 115200

POWER.kl30
  -> RelayBoard A / Channel 3
```

Camera index, ROI, COM port, baudrate, ADB serial, relay board and relay channel are implementation details of the current bench.

They must not leak into Test Case DSL.

---

## 4. Real variation is mostly screen topology

The real bench situation is much simpler than a generic product-variant system.

Most non-screen resources are effectively stable.

Meaningful variation is mainly:

- number of screens;
- purpose/role of each screen.

Therefore do not build a broad product profile / variant hierarchy unless future reality proves it necessary.

---

## 5. No controlled resource groups/tags

Explicitly rejected:

```text
cockpit_display
driver_display
all_display
arbitrary tag system
```

If all screens are needed:

```text
SCREEN.*
```

If specific screens are needed:

```text
SCREEN.center
SCREEN.cluster
```

This is enough for current reality.

---

## 6. Resource Type wildcard and coverage

`SCREEN.*` means:

> all screen resources declared by the Project.

The current Environment may only bind a subset.

Example:

```text
Project:
  SCREEN.center
  SCREEN.cluster
  SCREEN.passenger
  SCREEN.hud
  SCREEN.rear_left
  SCREEN.rear_right

Environment:
  center
  cluster
  passenger
```

The test may still execute the three available observations, but the missing three must be explicitly reported as not covered.

---

## 7. No generic sharing topology

Do not introduce Framework concepts such as:

```text
shareable
exclusive
resource ownership graph
```

A relay board naturally has one serial connection and multiple channels. Different logical environments may use different channels while the same physical board connection remains open.

That is device semantics, not a reason to create a generic sharing abstraction.

Likewise, if a camera arrangement cannot reliably support several ROIs, it is acceptable to say the topology is unsupported and use more cameras.

---

## 8. Environment is human-configured

The physical bench layout is largely fixed and intentionally configured by humans.

The GUI is expected to support tasks such as:

- select which camera corresponds to which logical screen;
- draw/configure ROI;
- bind ADB serial to a logical resource;
- bind console port/settings;
- bind power rail to relay board/channel.

The runtime should consume this configuration deterministically.

Each Plugin owns one Workspace page for its complete configuration and resource
set. Workspace changes are persisted in real time as full Plugin-slice
replacements. Incomplete and invalid Environment states may be saved but cannot
pass Preflight.

The configured ADB serial is a paper device identity. It remains meaningful
when ADB is offline. Discovery never rewrites it, and changing or deleting it
does not cascade into other Plugin configurations.

---

## 9. Frozen v1 boundary

v1 defines the Project and Environment YAML shapes, Plugin-level and
resource-level configuration boundaries, Workspace persistence, Run snapshot,
and zero-binding rejection.

Do not expand this topic into a general hardware-topology or automatic
relationship-maintenance system unless a real requirement appears.
