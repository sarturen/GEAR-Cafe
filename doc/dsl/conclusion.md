# GEAR DSL — Current Conclusion

Status: **Active / relatively mature**  
Date: 2026-09-17

## 1. Purpose

GEAR Flow DSL describes **one independent test case**.

It answers only:

> What logical resource should be operated, how long should the test wait, what should be validated, and how many times should the sequence repeat?

It is intentionally **not** a general-purpose programming language, dataflow language, state-machine language, or test scheduler.

---

## 2. Test Case Structure

```text
Test Case
├── setup
├── body
└── teardown
```

- `setup`: establishes this test case's required initial state.
- `body`: performs the actual test.
- `teardown`: performs simple environment restoration **only after normal completion**.

A test case must not depend on another test case having run first.

---

## 3. Core DSL Kernel

The currently accepted core is:

```text
DO
WAIT
WAIT RANDOM

ASSERT
AND
WITHIN ... EVERY ...
FOR ... EVERY ...

REPEAT

<Resource>
<ResourceType.*>
```

No other general control-flow or data-processing syntax is currently justified.

---

## 4. DO

`DO` invokes an operation supplied by a Plugin Resource Type.

Conceptual form:

```text
DO <resource> <operation> [arguments]
```

Examples:

```text
DO POWER.kl30 OFF
DO POWER.kl30 ON
DO ADB.main REBOOT
DO ADB.main SHELL "xxxcmd"
DO ADB.main PULL "xxxlog"
```

`DO` belongs to Framework grammar.

`OFF`, `REBOOT`, `SHELL`, `PULL`, etc. belong to Plugin vocabulary.

---

## 5. WAIT

Fixed delay:

```text
WAIT 5s
WAIT 30s
WAIT 1h
```

---

## 6. WAIT RANDOM

Randomized delay:

```text
WAIT RANDOM 5s..30s
```

The random interval is part of the test behavior, so it belongs to Framework DSL rather than Plugin capability.

Typical uses:

- randomized dwell time;
- avoiding perfectly periodic stress cycles;
- approximating variable user dwell.

---

## 7. ASSERT

Immediate validation:

```text
ASSERT <condition>
```

Examples:

```text
ASSERT SCREEN.center LIT
ASSERT ADB.main AVAILABLE
```

The condition vocabulary comes from the relevant Plugin.

Framework does not know how `LIT` or `AVAILABLE` is implemented.

---

## 8. AND

Multiple conditions may form one assertion:

```text
ASSERT A AND B AND C
```

Meaning:

> all conditions belong to the same assertion and must be satisfied together.

Do **not** introduce a general boolean-expression language yet.

Currently rejected:

```text
OR
complex parentheses
generic NOT
operator precedence rules
```

Negative semantics should preferably be explicit Plugin vocabulary:

```text
NOT_BLACK
UNAVAILABLE
NOT_ENTER_FASTBOOT
```

---

## 9. WITHIN ... EVERY ...

Conceptual form:

```text
ASSERT A AND B
WITHIN <duration>
EVERY <interval>
```

Semantics:

> Re-evaluate the complete assertion every `<interval>`. If all conditions are simultaneously satisfied at least once before `<duration>` expires, PASS. Otherwise FAIL.

Example:

```text
ASSERT SCREEN.center LIT
   AND ADB.main AVAILABLE
WITHIN 30s
EVERY 200ms
```

Typical uses:

- boot recovery;
- waiting for ADB to appear;
- fault recovery;
- waiting for a screen to become lit.

`EVERY` is mandatory. There is no hidden polling default.

---

## 10. FOR ... EVERY ...

Conceptual form:

```text
ASSERT A AND B
FOR <duration>
EVERY <interval>
```

Semantics:

> Re-evaluate the complete assertion every `<interval>` throughout the entire duration. Every check must pass.

Example:

```text
ASSERT SCREEN.center NOT_BLACK
   AND ADB.main AVAILABLE
FOR 1h
EVERY 100ms
```

Typical uses:

- no blackout during a stability window;
- ADB remains available;
- system remains healthy.

Again, `EVERY` is mandatory because polling cadence is itself part of test strength.

---

## 11. Plugin internals may be event-driven

`EVERY 100ms` defines the Framework validation cadence. It does **not** require the Plugin itself to sample only every 100 ms.

For example, a screen plugin may continuously process camera frames and cache events. Every 100 ms, Framework may ask whether an invalid event occurred since the previous check.

Framework owns the temporal contract; Plugin owns observation implementation.

---

## 12. REPEAT

Bounded repetition:

```text
REPEAT 10000:
    ...
```

Current scope is deliberately limited to an explicit count.

Not currently supported:

```text
WHILE
UNTIL
break
continue
general iterators
```

---

## 13. Resource references

A test refers only to **logical resources**:

```text
POWER.kl30
ADB.main
CONSOLE.main
SCREEN.center
SCREEN.cluster
```

It never references physical details such as:

```text
COM7
Camera 2
ROI coordinates
ADB serial number
relay board/channel
```

Those belong to Environment Configuration.

---

## 14. Resource Type wildcard

To target all Project resources of one type:

```text
SCREEN.*
```

This means:

> all `SCREEN` logical resources declared by the Project.

The actual Environment may only bind a subset. Missing observations become explicit coverage gaps; they are never silently treated as PASS.

No controlled group/tag system is currently needed.

---

## 15. Escape operations

Plugins may expose low-level command surfaces such as:

```text
ADB.SHELL(...)
FASTBOOT.EXEC(...)
CONSOLE.SEND(...)
```

These exist so a test can be supported quickly without immediately evolving the Plugin API.

Frequently reused escape patterns may later be promoted into formal semantic capabilities.

---

## 16. Result validation is NOT generic DSL syntax

Framework DSL does not provide general:

```text
stdout contains
stdout regex
exit_code comparison
grep
split
integer parsing
file contains
general text/file transformations
```

If command output must be checked, the corresponding Plugin must expose an appropriate validation capability.

Example idea:

```text
ASSERT ADB.main OUTPUT_MATCHES ...
```

The internal implementation may use regex, parser logic, or anything else; Framework does not care.

---

## 17. Preflight rules that affect DSL execution

### Operations cannot be missing

If the test says:

```text
DO POWER.kl30 OFF
```

but the current Environment cannot bind `POWER.kl30`, the test is rejected before execution.

Likewise, if the Resource Type does not provide the requested operation, reject.

### Observations may be missing

For:

```text
ASSERT SCREEN.* NOT_BLACK ...
```

the Project may declare six screens while the current Environment binds only three.

The available three may be tested, but the other three must be explicitly reported as **not covered**.

### Validation capability cannot be missing

If the test requires a particular result check and the Plugin has no corresponding validation capability, reject before execution.

---

## 18. Failure semantics

For an ordinary functional assertion:

```text
Assertion FAIL
→ current Test Case FAIL
→ collect incident evidence
→ stop current Test Case
```

The current DSL does not support assertion-level `continue`, auto-retry, auto-recovery, or adaptive test strategy.

If the purpose is statistical observation rather than functional pass/fail, write a dedicated measurement-oriented test case instead of overloading normal assertions.

---

## 19. TearDown

Initial TearDown scope is intentionally small:

```text
standard power-off
standard power-on
stop extra control actions started for the test
```

Do not yet implement:

```text
restore old image
auto-flash full build
restore boardid
automatic DUT rescue
```

Those are a separate recovery problem.

---

## 20. Explicitly rejected DSL features

Unless a real test case proves otherwise, do not reintroduce:

```text
variables
general dataflow
cross-case state
IF / ELSE
WHILE
break / continue
functions
general string/math processing
generic regex DSL
generic file-processing DSL
state-machine DSL
mark
since
watch
expect_watch
IF PRESENT
FOR EACH
controlled resource groups/tags
Suite/Campaign scheduling
automatic test-strategy selection
runtime Agent involvement
```

---

## 21. Four reference thought experiments

### Case 1 — boot then 1h stability

```text
DO POWER.kl30 ON

ASSERT SCREEN.center LIT
   AND ADB.main AVAILABLE
WITHIN startup_timeout
EVERY startup_interval

ASSERT SCREEN.center NOT_BLACK
   AND ADB.main AVAILABLE
FOR 1h
EVERY stability_interval
```

On failure: collect xxxlog + screenshot through evidence capabilities.

### Case 2 — 10,000 sleep/wakeup cycles

```text
REPEAT 10000:
    DO POWER.kl15 OFF

    ASSERT SCREEN.center BLACK
       AND ADB.main UNAVAILABLE
    WITHIN off_timeout
    EVERY check_interval

    WAIT 5s

    DO POWER.kl15 ON

    ASSERT SCREEN.center LIT
       AND ADB.main AVAILABLE
    WITHIN startup_timeout
    EVERY check_interval

    ASSERT SCREEN.center HEALTHY
       AND ADB.main AVAILABLE
    FOR dwell
    EVERY check_interval
```

### Case 3 — shell command 1000 times, then log validation

```text
REPEAT 1000:
    DO ADB.main SHELL "xxxcmd"
    WAIT 30s

DO ADB.main PULL "xxxlog"
DO LOG.xxx UNPACK

ASSERT LOG.xxx <project-specific-validation>
```

Unpack and keyword-delta validation belong to Plugin capabilities.

### Case 4 — repeated fault injection

```text
REPEAT N:
    DO FAULT.xxx INJECT

    ASSERT SYSTEM.xxx RESET_OCCURRED
       AND SCREEN.center BLACK
       AND SCREEN.cluster NOT_BLACK
    WITHIN fault_window
    EVERY check_interval

    ASSERT SYSTEM.xxx RECOVERED
    WITHIN recovery_timeout
    EVERY check_interval

    DO ADB.main SHELL "yyyycmd"

    ASSERT ADB.main <yyyycmd-result-validation>
```

No state-machine DSL is needed.

---

## 22. One-sentence definition

GEAR DSL is:

> A deterministic, intentionally small task-flow language for expressing **operation — wait — validation — repetition** over logical test resources, without exposing physical bench details or growing into a general programming language.
