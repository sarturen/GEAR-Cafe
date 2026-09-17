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
configuration contract
GUI configuration editor
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
- Environment config validation;
- GUI configuration-editor discovery.

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

## 10. Plugin owns Resource configuration

The configuration of a Resource Type belongs to its Plugin.

Framework Environment data owns only the outer binding identity:

```text
logical resource identity
Resource Type
Plugin identity
Plugin configuration payload
```

Conceptually:

```text
SCREEN.center:
    type: SCREEN
    plugin: camera_plugin
    config: <camera_plugin-owned data>
```

Framework stores and passes the `config` payload but does not interpret camera ids, ROI coordinates, COM settings, relay channels, ADB serials, or other Plugin-specific fields.

The Plugin contract must provide configuration validation for its own payload. Static Preflight may invoke this validation, but it does not probe live hardware.

Configuration output must be serializable so Environment Configuration can persist it and Run archival can copy it.

---

## 11. Plugin owns its GUI configuration editor

Each Plugin defines how its resources are configured in GUI.

Examples:

```text
ADB Plugin
    -> serial discovery and selection

CONSOLE Plugin
    -> COM discovery, baudrate and serial settings

RELAY Plugin
    -> board/channel selection

SCREEN/Camera Plugin
    -> camera preview and ROI drawing
```

The GUI provides the host/container and Save/Cancel workflow. The Plugin provides the actual editor behavior and returns its serializable configuration payload.

A Plugin may use shared GUI helper controls, but Framework does not generate or interpret a universal configuration form.

The GUI editor may depend on PySide6. It is a separate GUI-facing entry point and is loaded only by the GUI. Runtime contract loading and CLI execution must not require importing the GUI editor.

The editor may perform live configuration actions such as:

```text
device discovery
connection trial
camera preview
ROI selection
```

These actions are configuration tools, not Runtime Preflight guarantees.

The editor returns data; it does not contribute DSL grammar, execute Test Cases, or control an Active Run.

Configuration sessions are not opened while a Run is active, preventing GUI configuration tools and Runtime from competing for hardware.

---

## 12. Runtime and GUI surfaces remain separate

One Plugin package may contain both surfaces:

```text
Plugin Package
├── Runtime Contract / implementation
├── Configuration validation
└── GUI configuration editor
```

This does not make Framework Runtime depend on GUI.

The dependency direction is:

```text
GUI -> Plugin GUI Editor -> Plugin configuration data
Framework Runtime -> Plugin Runtime Contract -> hardware
```

Both sides meet only through persisted Environment configuration data and the declared Plugin identity.

---

## 13. Open questions for later

Not yet designed:

- exact manifest/schema format;
- versioning/compatibility rules;
- Plugin discovery/registration;
- exact GUI editor factory/context API;
- shared GUI helper SDK, if repeated editor patterns justify one;
- formal evidence/capture capability shape.
