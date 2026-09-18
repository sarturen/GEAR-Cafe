# ADB Plugin Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans and test-driven-development. Independent transport and Workspace tasks may run in parallel using dispatching-parallel-agents.

**Goal:** Implement the approved USB ADB plugin, GUI and runnable generic GUI host.

**Architecture:** Session-scoped service behind gear_contracts; subprocess ADB transport; optional Qt Workspace. Framework remains hardware-neutral.

**Tech Stack:** Python >=3.11, gear_contracts v1, PySide6 >=6.8,<7 (GUI only), pytest.

**Spec:** ../specs/2026-09-18-adb-plugin-design.md

## Constraints

USB only. Preflight checks configuration only. Full Run exclusivity. No automatic retry/reconnect/server shutdown. Preserve registered identities when unplugged. Plugin imports no framework internals. Existing approvals cover implementation; pending semantics are recorded in the spec.

## Tasks

- [ ] Transport: create plugins/adb/gear_adb/transport.py and tests/test_adb_transport.py. Test USB discovery, explicit targets, shell output/exit, pull, persistent logcat and joined shutdown with a fake executable. Run the focused tests red before implementation and green afterwards.
- [ ] Configuration and runtime: create config.py, runtime.py and gear-plugin.yaml. Test pure static validation, Run resource mapping, command results, service lifetime and run-local pulled files. Finish AVAILABLE / UNAVAILABLE and evidence after user decisions arrive.
- [ ] Workspace: create workspace.py and tests/test_adb_workspace.py. Use real offscreen widgets; check complete slice commits, manual operation submission, ACTIVE control disabling, persistence of missing identities, log display and dispose behavior.
- [ ] GUI host: add desktop.py, gear gui CLI and optional GUI dependency. Test plugin discovery/hosting, GUI-thread dispatch, run confirmation and cooperative close.
- [ ] Integrate: add example environment/project/case and plugin README; verify real Registry/Framework with fake transport and two Runs, no Qt import for runtime, full pytest, packaging and screenshot.
- [ ] Record results and limitations; commit the verified feature branch.

## Internal interfaces

Transport service: AdbService(); configure(adb_path: str); discover() -> list[dict] (serial,state,usb,transport_id,model); shell(serial,command) -> dict (exit_code,stdout,stderr); pull(serial,remote_path,destination) -> same result; start_logcat(serial,destination) -> dict; stop_logcat(serial) -> dict; logcat_snapshot(serial) -> dict (running,lines,path,error); close(). All mutating calls execute on the host worker. Snapshot reads are thread-safe and I/O-free. Errors use GearError with stable ADB_* codes. Discovery only returns USB records and duplicate serials must not silently select a device.

Runtime helpers for Workspace: refresh_devices(), manual_shell(serial,command), manual_pull(serial,remote_path,destination), start_logcat(serial,destination), stop_logcat(serial), logcat_snapshot(serial). Helpers delegate the shared service; actions are submitted through WorkspaceContext.submit_manual. Config validate_slice(plugin_slice) returns ValidationReport without I/O. Runtime public methods follow PluginRuntimeV1.

Workspace factory create_workspace(context,runtime), widget QWidget, idempotent dispose stops GUI subscriptions/timer only. Full slices retain all registered devices and bindings when a discovered device disappears. Plugin config key adb_path; resources have type ADB, device serial and config {}.
