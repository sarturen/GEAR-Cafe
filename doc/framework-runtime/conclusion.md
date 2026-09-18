# GEAR Framework Runtime — Current Conclusion

Status: **Summary; Runtime v1 is normative**
Date: 2026-09-18

See [Runtime v1](./v1.md), [Architecture v1](../architecture-v1.md) and the
[shared contract package](../contracts/README.md) for precise behavior and types.

## Accepted model

GEAR is a Qt-independent execution core inside one control process. GUI or CLI
owns one Control Host session; a second GEAR process is refused even while idle.
There is no service, IPC, parallel scheduler or automatic Run queue.

One fixed worker serializes Plugin calls. GUI Workspaces and their callbacks
run on the GUI thread. Plugins own private acquisition workers and synchronization.

## Long-lived hardware, isolated Run state

Plugin sessions and configured COM/camera/relay connections remain available
across Test Cases and page changes. Workspace and Runtime use the same Plugin
device services. A new case resets its stop token, observation cursors and
temporary state through begin_run; end_run cleans only that Run.
Session close and physical connection release happen on explicit disconnect,
affected configuration changes or application shutdown.

## Complete exclusive sequence

```text
submit -> archive -> static Preflight -> human confirmation
-> setup/body/normal-success teardown
-> requested evidence after failure
-> Run-local cleanup -> report -> complete Run-task exit -> IDLE
```

No next Run or manual command overlaps this sequence. A known PASS/FAIL/STOPPED
does not mean the slot is free. Rejected/declined submissions also finish their
existing submission work before release.

Finalization failure blocks the session until the cause is handled and GEAR
restarted. Provisional PASS becomes FAIL; existing FAIL/STOPPED remain.
Record execution_result separately from final result, preserve original failures,
and list all finalization errors with stage, Plugin, timestamp and exception data.
Report-write failure stays visible even if no report can be saved.

Preflight validates configuration only, never actual/cached hardware availability.
Runtime discovers device failures. A stop before execution declines without a
test result; a stop during execution is cooperative and cannot erase an already
recorded FAIL.

## Boundaries

Executor owns DSL execution, not GUI, discovery or report rendering.
Registry owns manifest discovery and long-lived Plugin sessions.
Run Store owns ordinary files containing archived inputs, events and reports.
Coordinator owns exclusivity, lifecycle, termination precedence and finalization.

Use fixed plugins/ discovery and the shared gear_contracts definitions.
Do not add process isolation, forced termination, automatic retry/recovery,
generic resource ownership graphs, event buses or Runtime Agents.
