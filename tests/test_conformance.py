"""Contract scenarios beyond the happy-path lifecycle."""

import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace as NS
import pytest


@pytest.mark.parametrize("collision", ["id", "type", "package"])
def test_registry_rejects_duplicate_owners_and_packages(bench, collision):
    import copy
    import shutil
    from gear_framework.registry import Registry

    manifest = copy.deepcopy(bench["manifest"])
    original_package = manifest["entrypoints"]["runtime"].split(".")[0]
    directory = bench["app"] / "plugins" / "second"
    package = (
        original_package if collision == "package" else original_package + "_second"
    )
    shutil.copytree(bench["directory"] / original_package, directory / package)
    if collision != "id":
        manifest["id"] = "gear.second"
    if collision != "type":
        manifest["resource_types"] = {"OTHER": manifest["resource_types"]["SCREEN"]}
    manifest["entrypoints"]["runtime"] = package + ".runtime:create_plugin"
    (directory / "gear-plugin.yaml").write_text(json.dumps(manifest))
    with pytest.raises(GearError, match="PLUGIN_LOAD_FAILED"):
        Registry.load(bench["app"])


def test_stop_during_preflight_waits_for_static_callback(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        entered, release = threading.Event(), threading.Event()

        def validate(slice):
            entered.set()
            assert release.wait(3)
            return {"status": "VALID", "diagnostics": []}

        p.validate_config = validate
        rid = submit(host, bench)
        assert entered.wait(2)
        try:
            host.stop(rid)
            assert host.get_status(rid)["active"]
            with pytest.raises(GearError):
                submit(host, bench)
        finally:
            release.set()
        status = wait_phase(host, rid, "FINISHED")
        assert status["outcome"] == "DECLINED" and not status["execution_started"]
        assert "begin" not in p.calls


def test_manual_connection_survives_sequential_runs(bench):
    import queue

    notifications = queue.Queue()
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        context = host.workspace_context("gear.demo", notifications.put)

        def connect():
            p.connected = True
            p.open_count += 1

        results = []
        context.submit_manual(connect, results.append)
        notifications.get(timeout=2)()
        assert results[0]["ok"]
        for _ in range(2):
            _, status = run(host, bench)
            assert status["result"] == "PASS" and p.connected
        assert p.open_count == 1


def test_failed_configuration_is_visible_and_blocks_runs(bench):
    import queue

    callbacks = queue.Queue()
    with Framework(bench["app"], bench["environment"]) as host:
        context = host.workspace_context("gear.demo", callbacks.put)

        def fail(slice):
            raise ValueError("configuration apply failed")

        host._registry.entries["gear.demo"].runtime.configure = fail
        slice = context.current_slice()
        slice["plugin"]["config"] = {"changed": True}
        context.commit(slice, {"status": "VALID", "diagnostics": []})
        host.flush()
        assert context.run_state() == "ACTIVE"
        assert host.session_diagnostics[0]["code"] == "CONFIGURE_FAILED"
        with pytest.raises(GearError, match="HOST_BLOCKED"):
            submit(host, bench)


def test_event_file_open_failure_blocks_without_test_result(bench, monkeypatch):
    from gear_framework.store import RunStore

    def fail(self):
        raise OSError("cannot open event log")

    monkeypatch.setattr(RunStore, "start_events", fail)
    with Framework(bench["app"], bench["environment"]) as host:
        rid = submit(host, bench)
        status = wait_phase(host, rid, "FINISHED", "BLOCKED")
        assert status["phase"] == "BLOCKED"
        assert status["outcome"] == "REJECTED" and status["result"] is None
        assert status["finalization_errors"][0]["stage"] == "event_write"


def test_failed_shutdown_does_not_admit_another_control_session(bench):
    from gear_framework.locking import HostLock

    host = Framework(bench["app"], bench["environment"])

    def fail():
        raise OSError("service worker did not close")

    host._registry.entries["gear.demo"].runtime.close = fail
    try:
        with pytest.raises(GearError, match="SHUTDOWN_FAILED"):
            host.close()
        contender = HostLock()
        try:
            with pytest.raises(GearError, match="HOST_BUSY"):
                contender.acquire()
        finally:
            contender.close()
    finally:
        # Fixture has no background hardware worker; release the deliberately failed
        # session for the test process only. Production releases it at process exit.
        host._host_lock.close()


from gear_contracts.api import GearError, StopRequested
from gear_framework.common import validate_result
from gear_framework.documents import load_yaml
from gear_framework.host import Framework
from gear_framework.preflight import prepare
from gear_framework.registry import Registry
from test_host import submit, run, wait_phase
from test_executor import Clock, Token
from gear_framework.executor import Executor


def test_repeat_waits_and_capability_order_are_recorded(bench):
    case = {
        "api": "gear.dsl/v1",
        "name": "repeat",
        "body": [
            {
                "repeat": {
                    "count": 2,
                    "steps": [
                        {"do": {"resource": "SCREEN.a", "operation": "ON"}},
                        {"wait": {"random": {"min": "1ms", "max": "1ms"}}},
                    ],
                }
            }
        ],
    }
    bench["case"].write_text(json.dumps(case))
    with Framework(bench["app"], bench["environment"]) as host:
        rid, status = run(host, bench)
        assert status["result"] == "PASS"
        events = [
            json.loads(line)
            for line in (bench["app"] / "runs" / rid / "events.jsonl")
            .read_text()
            .splitlines()
        ]
        operations = [e for e in events if e["event"] == "operation.result"]
        waits = [e for e in events if e["event"] == "wait.chosen"]
        assert [e["details"]["iteration"] for e in operations] == [1, 2]
        assert [e["details"]["duration_s"] for e in waits] == [0.001, 0.001]
        assert operations[0]["step_path"] == "/body/0/repeat/steps/0"
        assert (
            operations[0]["details"]["call_id"] != operations[1]["details"]["call_id"]
        )
        report = json.loads(Path(status["report_path"]).read_text())
        assert report["coverage"][0]["executed"]


def test_stop_skips_unstarted_evidence_and_cannot_erase_failure(bench):
    case = {
        "api": "gear.dsl/v1",
        "name": "f",
        "body": [{"do": {"resource": "SCREEN.a", "operation": "ON"}}],
        "evidence_on_fail": [
            {"resource": "SCREEN.a", "evidence": "CAPTURE"},
            {"resource": "SCREEN.a", "evidence": "CAPTURE"},
        ],
    }
    bench["case"].write_text(json.dumps(case))
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        p.fail = True
        entered, release = threading.Event(), threading.Event()
        count = []

        def collect(*args):
            count.append(1)
            entered.set()
            assert release.wait(3)
            return {"ok": True, "diagnostic": None, "artifacts": []}

        p.collect = collect
        rid = submit(host, bench)
        wait_phase(host, rid, "WAITING_CONFIRMATION")
        host.confirm(rid)
        assert entered.wait(2)
        try:
            host.stop(rid)
        finally:
            release.set()
        status = wait_phase(host, rid, "FINISHED")
        assert status["result"] == "FAIL" and count == [1]
        report = json.loads(Path(status["report_path"]).read_text())
        assert (
            report["evidence"][1]["result"]["diagnostic"]["code"]
            == "EVIDENCE_CANCELLED"
        )


def test_cleanup_after_stop_preserves_stop_with_separate_error(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        p.block = True
        p.cleanup_fail = True
        rid = submit(host, bench)
        wait_phase(host, rid, "WAITING_CONFIRMATION")
        host.confirm(rid)
        assert p.entered.wait(2)
        host.stop(rid)
        p.release.set()
        status = wait_phase(host, rid, "BLOCKED")
        assert status["result"] == status["execution_result"] == "STOPPED"
        assert status["primary_failure"] is None and status["finalization_errors"]


def test_invalid_plugin_results_and_unrequested_stop_are_failures(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        p.invoke = lambda *args: {"ok": True}
        _, status = run(host, bench)
        assert status["result"] == "FAIL"

        def invalid_stop(*args):
            raise StopRequested()

        p.invoke = invalid_stop
        _, status = run(host, bench)
        assert status["result"] == "FAIL"
    for kind, result in (
        ("condition", {"ok": True, "satisfied": 1, "diagnostic": None, "details": {}}),
        (
            "operation",
            {"ok": True, "diagnostic": None, "details": {"bad": float("nan")}},
        ),
        ("evidence", {"ok": True, "diagnostic": None, "artifacts": [{"path": "x"}]}),
    ):
        with pytest.raises(GearError):
            validate_result(kind, result)


def test_subscriptions_are_detached_ordered_and_unsubscribe_is_effective(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        received = []

        def malicious(event):
            event["details"]["outcome"] = "mutated"
            raise ValueError("listener error")

        bad = host.subscribe(malicious)
        sub = host.subscribe(received.append)
        rid, status = run(host, bench)
        assert status["result"] == "PASS" and received
        assert all(e["details"].get("outcome") != "mutated" for e in received)
        assert {
            thread for thread in host._registry.entries["gear.demo"].runtime.threads
        } == {host._worker.thread.ident}
        count = len(received)
        sub.unsubscribe()
        bad.unsubscribe()
        run(host, bench)
        assert len(received) == count


def test_entire_involved_slice_validated_unrelated_plugin_does_not_gate(bench):
    registry = Registry.load(bench["app"])
    try:
        case = load_yaml(bench["case"])
        env = load_yaml(bench["environment"])
        env["resources"]["SCREEN.extra"] = {
            "type": "SCREEN",
            "plugin": "gear.demo",
            "config": {"unfinished": True},
        }
        seen = []
        plugin = registry.entries["gear.demo"].runtime

        def validate(slice):
            seen.append(slice)
            return {"status": "INCOMPLETE", "diagnostics": []}

        plugin.validate_config = validate
        prepared = prepare(case, load_yaml(bench["project"]), env, registry)
        assert not prepared.report["ok"] and "SCREEN.extra" in seen[0]["resources"]

        def fail_if_called(*args):
            raise AssertionError("Unrelated Plugin was validated")

        registry.entries["gear.unrelated"] = NS(
            runtime=NS(validate_config=fail_if_called, close=lambda: None)
        )
        plugin.validate_config = lambda slice: {"status": "VALID", "diagnostics": []}
        assert prepare(case, load_yaml(bench["project"]), env, registry).report["ok"]
    finally:
        registry.close()


def test_argument_schema_and_dangling_device_reject_without_execution(bench):
    r = Registry.load(bench["app"])
    try:
        case = load_yaml(bench["case"])
        case["body"][0]["do"]["args"] = {"undeclared": 1}
        p = prepare(
            case, load_yaml(bench["project"]), load_yaml(bench["environment"]), r
        )
        assert not p.report["ok"] and any(
            d["code"] == "INVALID_ARGUMENTS" for d in p.report["diagnostics"]
        )
        del case["body"][0]["do"]["args"]
        env = load_yaml(bench["environment"])
        env["resources"]["SCREEN.a"]["device"] = "unknown"
        assert not prepare(case, load_yaml(bench["project"]), env, r).report["ok"]
        assert r.entries["gear.demo"].runtime.open_count == 0
    finally:
        r.close()


def test_cycle_does_not_short_circuit_false_but_stops_after_late_return():
    for mode, cost, expected_calls in (
        ("immediate", 0, ["SCREEN.a", "SCREEN.b"]),
        ("within", 2, ["SCREEN.a"]),
    ):
        clock, token, calls = Clock(), Token(), []

        def evaluate(rid, *args):
            calls.append(rid)
            clock.now += cost
            return {
                "ok": True,
                "satisfied": rid == "SCREEN.b",
                "diagnostic": None,
                "details": {},
            }

        assertion = {
            "all": [
                {"resource": "SCREEN.a", "condition": "LIT"},
                {"resource": "SCREEN.b", "condition": "LIT"},
            ]
        }
        if mode == "within":
            assertion.update(within="1s", every="200ms")
        prepared = NS(
            case={
                "setup": [],
                "body": [{"assert": assertion}],
                "teardown": [],
                "evidence_on_fail": [],
            },
            report={"coverage": []},
            requests={
                f"/body/0/assert/all/{i}": [
                    {
                        "resource_id": rid,
                        "plugin_id": "gear.demo",
                        "kind": "condition",
                        "capability": "LIT",
                        "args": {},
                    }
                ]
                for i, rid in enumerate(("SCREEN.a", "SCREEN.b"))
            },
        )
        registry = NS(entries={"gear.demo": NS(runtime=NS(evaluate=evaluate))})
        ex = Executor(
            prepared,
            registry,
            lambda *a: NS(call_id="1"),
            lambda *a, **k: None,
            token,
            clock,
            lambda r, f: r,
        )
        assert ex.run()[0] == "FAIL" and calls == expected_calls


def test_persistence_failure_does_not_change_slice(bench, monkeypatch):
    import gear_framework.workspace as module

    with Framework(bench["app"], bench["environment"]) as host:
        context = host.workspace_context("gear.demo", lambda callback: None)
        before = context.current_slice()
        changed = copy.deepcopy(before)
        changed["plugin"]["config"] = {"new": 1}
        original = bench["environment"].read_bytes()

        def fail(*args):
            raise OSError("read only")

        monkeypatch.setattr(module, "atomic_json", fail)
        with pytest.raises(GearError, match="PERSISTENCE_ERROR"):
            context.commit(changed, {"status": "VALID", "diagnostics": []})
        assert (
            context.current_slice() == before
            and bench["environment"].read_bytes() == original
        )
