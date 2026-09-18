# GEAR Framework

GEAR executes the [v1 contracts](doc/README.md) with one fixed worker and long-lived plugin sessions. The framework contains plugin discovery, strict YAML/DSL validation, configuration-only Preflight, execution, evidence, reporting, host exclusion and a GUI-neutral Workspace bridge. An optional Qt desktop now hosts the first [USB ADB plugin](plugins/adb/README.md).

## Install and run

Python 3.11 or later. From this repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ./doc/contracts -e ".[test]"
.\.venv\Scripts\gear run --app-dir examples/bench --case examples/bench/case.yaml --project examples/bench/project.yaml --environment examples/bench/environment.yaml
```

The CLI shows bindings and uncovered resources before asking for confirmation.
Enter `yes` to execute. EOF or any other answer declines. Ctrl-C requests cooperative stop; an in-flight plugin call must return before shutdown.
Exit codes: 0 PASS, 1 FAIL, 2 STOPPED/REJECTED/DECLINED, 3 blocked session/startup/shutdown failure.

The example plugin models an on/off state in memory. It exercises actual discovery and public interfaces without physical hardware. Its wildcard deliberately leaves DEMO.spare uncovered.

Reports and archived inputs are under `<app-dir>/runs/<run-id>/`. A report failure leaves `report_path` null and records diagnostics in current status plus `logs/session.log` when writable.

## Python API

```python
from gear_framework.host import Framework

with Framework(app_dir, environment_path) as framework:
    run_id = framework.submit(absolute_case_path, absolute_project_path, absolute_environment_path)
    # Read/subscribe until WAITING_CONFIRMATION, show Preflight, then obtain confirmation.
    framework.confirm(run_id)
    status = framework.get_status(run_id)
    # Remain alive until FINISHED or BLOCKED; close cooperatively waits for active work.
```

Subscribe before submit. Event listeners run on the worker and must return promptly.
All public FrameworkV1 data and protocol shapes come from the separate dependency-free `gear_contracts` package under `doc/contracts`.
`load_environment(path)` explicitly changes the idle session configuration and returns a Future for application completion.
`flush()` is a host-side worker barrier; never call it from a worker callback.
`close()` waits for execution and closes persistent connections; it is not a force-stop.

## GUI integration

Launch the included desktop and ADB Workspace from the repository root:

```powershell
.\.venv\Scripts\python -m pip install -e ".[gui]"
.\.venv\Scripts\gear gui --app-dir . --environment examples/adb/environment.yaml --project examples/adb/project.yaml --case examples/adb/case.yaml
```

The example starts with an unassigned `ADB.main`. Configure the ADB executable, register a USB serial and save its resource binding in the Workspace. Case/project arguments are optional; they can also be selected in the desktop.
The desktop presents Preflight before enabling confirmation, shows diagnostics and report paths, and waits cooperatively for in-flight work when closing.
See the [current ADB scope and pending decisions](docs/adb-status.md) and [desktop preview](docs/images/adb-workspace.png).

A GUI shell supplies a dispatcher that posts a zero-argument callback to its GUI thread.
Create contexts and Workspaces on that thread:

```python
context = framework.workspace_context("gear.example", post_to_gui)
# Or load the manifest's optional Workspace:
from gear_framework.gui import create_workspace
workspace = create_workspace(framework, "gear.example", post_to_gui)
```

The context implements WorkspaceContextV1. Widget factories, listeners and disposal stay on the GUI thread.
Manual actions go through `context.submit_manual(action, listener)` and use their own plugin's existing device service.
Persist complete slices with `context.commit(slice, validation_report)`; the host applies configuration on its worker.
The saved Environment is JSON-formatted YAML 1.2 after edits; comments/formatting are not retained.

A shell reads `framework.session_diagnostics` after environment notifications to display asynchronous configuration failures. Such failures block the session until restart.
Status and passive preview may remain visible during ACTIVE; manual commands and configuration edits are disabled and rejected by the host.

PySide6 is required only for the desktop/Workspace path, through the optional `gui` extra. Core execution and `gear run` remain independent of Qt.
Physical COM/camera/relay plugins are not implemented.

## Plugin development

Copy one plugin directory beneath `<app-dir>/plugins/` and restart. See [Plugin v1](doc/plugin-contract/v1.md) and the example.
Runtime imports, factories and configure must not open/probe hardware. Preflight never tests availability.
The plugin's Runtime and Workspace share the same private long-lived service; begin_run/end_run isolate only execution state.
No cross-plugin calls, automatic reconnection or queued Runs are provided.

## Verification

```powershell
.\.venv\Scripts\python -m pytest -q
```

Tests use deterministic clocks for timing semantics, blocking fake calls for cancellation/exclusivity, failure injection for finalization, a real separate process for OS exclusion, and subprocess CLI runs.
ADB tests substitute real child processes for the physical ADB executable, while GUI tests use actual Qt widgets.
Real device conformance remains the responsibility of each plugin's hardware tests.
