# GEAR Plugin Contract — Current Conclusion

Status: **Active**  
Date: 2026-09-17

## 1. Plugin responsibility

A Plugin defines one or more Resource Types and their capabilities.

Conceptually, a Resource Type may expose:

```text
operations
observations
validations
evidence/capture capabilities
configuration schema
```

Not every Resource Type must look structurally identical.

---

## 2. Capability belongs to Resource Type

Hard rule:

> Capability belongs to Resource Type, never Resource Instance.

If:

```text
ADB.main
ADB.secondary
```

are both type `ADB`, they must expose the same capability contract.

Instances may differ only in configuration/binding/runtime connectivity.

Do not implement dynamic instance capability bitmaps.

---

## 3. Different resource types may have very different surfaces

Examples:

### POWER

Small finite operation set:

```text
ON
OFF
```

### SCREEN

Observation-heavy:

```text
LIT
BLACK
NOT_BLACK
FREEZE
HEALTHY
```

### ADB

Parameterized executor plus semantic operations:

```text
REBOOT
PULL
COLLECT_LOG
SHELL(command, ...)
```

### FASTBOOT

Potentially:

```text
EXEC(...)
FLASH(...)
REBOOT(...)
```

### CONSOLE

Potentially:

```text
SEND(...)
EXPECT(...)
project-specific semantic operations
```

Framework should not force all resources into one tiny finite enum shape.

---

## 4. Escape hatch

Low-level command surfaces are valid Plugin capabilities:

```text
ADB.SHELL(...)
FASTBOOT.EXEC(...)
CONSOLE.SEND(...)
```

Their purpose is rapid test support without forcing every new command to become a new Plugin release.

This is intentional architecture, not a failure.

---

## 5. Capability graduation

A healthy evolution path is:

```text
one-off raw command
↓
reused raw command pattern
↓
formal semantic Plugin capability
```

Example:

```text
ADB.SHELL("getprop ...")
```

may later become:

```text
DEVICE.GET_BOOT_MODE
```

if it becomes common and semantically stable.

---

## 6. Result validation belongs to Plugin

Framework DSL must not become a generic parser.

Plugin validations may internally use:

```text
regex
grep
structured parsing
filesystem inspection
log extraction
```

but those are implementation details.

Tests invoke a declared validation capability.

Example conceptual capability:

```text
ADB.OUTPUT_MATCHES(...)
LOG.HAS_NEW_KEYWORD(...)
DEVICE.BOOT_MODE_IS(...)
```

---

## 7. Strict contract declaration

Framework should not inspect Plugin source code or execute hardware to guess capabilities.

Plugin should declare its contract explicitly:

```text
Resource Type
Operations
Observations
Validations
Arguments/schema
```

Framework can then use that contract for:

- static DSL validation;
- preflight;
- authoring context;
- GUI/config integration later.

---

## 8. Plugin autonomy

Inside its contract boundary, Plugin implementation is free.

It may use:

```text
subprocess
pyserial
libraries
internal providers
multiple internal layers
caching
event-driven observation
polling
```

Framework should not care.

---

## 9. Plugin does not own DSL grammar

Hard boundary:

> Framework owns DSL grammar and temporal semantics.

Plugin may contribute vocabulary/capabilities, but may not invent new syntax or reinterpret:

```text
ASSERT
AND
WITHIN
FOR
EVERY
REPEAT
```

---

## 10. Open questions for later

Not yet designed:

- exact manifest/schema format;
- versioning/compatibility rules;
- Plugin discovery/registration;
- config-schema integration;
- optional custom GUI configuration editors;
- formal evidence/capture capability shape.
