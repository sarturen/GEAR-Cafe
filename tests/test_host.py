import json
import threading
import time
import pytest
from gear_contracts.api import GearError
from gear_framework.host import Framework


def wait_phase(host, rid, *phases):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = host.get_status(rid)
        if state["phase"] in phases:
            return state
        time.sleep(0.005)
    raise AssertionError(host.get_status(rid))


def submit(host, b):
    return host.submit(str(b["case"]), str(b["project"]), str(b["environment"]))


def run(host, b):
    rid = submit(host, b)
    wait_phase(host, rid, "WAITING_CONFIRMATION")
    host.confirm(rid)
    return rid, wait_phase(host, rid, "FINISHED", "BLOCKED")


def test_two_runs_reuse_session_and_single_worker(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        for _ in range(2):
            rid, status = run(host, bench)
            assert status["result"] == "PASS" and not status["active"]
            report = json.loads(open(status["report_path"], encoding="utf-8").read())
            assert (
                report["execution_result"] == "PASS" and not report["session_blocked"]
            )
        assert (
            p.open_count == 1
            and p.calls.count("begin") == 2
            and p.calls.count("end") == 2
        )
        assert len(set(p.threads)) == 1 and p.threads[0] != threading.get_ident()
    assert p.calls[-1] == "close"


def test_stop_waits_for_inflight_and_later_error_cannot_erase_stop(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        p.block = True
        p.fail = True
        rid = submit(host, bench)
        wait_phase(host, rid, "WAITING_CONFIRMATION")
        host.confirm(rid)
        assert p.entered.wait(2)
        host.stop(rid)
        assert host.get_status(rid)["active"]
        with pytest.raises(GearError):
            submit(host, bench)
        p.release.set()
        status = wait_phase(host, rid, "FINISHED")
        assert status["result"] == "STOPPED" and status["primary_failure"] is None


def test_decline_before_execution_and_reject_environment_mismatch(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        rid = submit(host, bench)
        wait_phase(host, rid, "WAITING_CONFIRMATION")
        host.stop(rid)
        status = wait_phase(host, rid, "FINISHED")
        assert status["outcome"] == "DECLINED" and status["result"] is None
        assert not status["execution_started"]
        env = json.loads(bench["environment"].read_text())
        env["name"] = "changed"
        bench["environment"].write_text(json.dumps(env))
        rid = submit(host, bench)
        status = wait_phase(host, rid, "FINISHED")
        assert status["outcome"] == "REJECTED"
        assert status["preflight"]["diagnostics"][0]["code"] == "ENVIRONMENT_MISMATCH"


def test_partial_begin_is_cleaned_and_primary_failure_preserved(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        p.begin_fail = True
        p.cleanup_fail = True
        rid, status = run(host, bench)
        assert status["phase"] == "BLOCKED" and status["result"] == "FAIL"
        assert "begin broke" in status["primary_failure"]["diagnostic"]["message"]
        assert p.calls.count("end") == 1
        assert status["finalization_errors"][0]["stage"] == "plugin_cleanup"
        with pytest.raises(GearError, match="HOST_BLOCKED"):
            submit(host, bench)


def test_report_failure_records_pass_to_fail_and_blocks(bench, monkeypatch):
    from gear_framework.store import RunStore

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(RunStore, "publish_report", fail)
    with Framework(bench["app"], bench["environment"]) as host:
        _, status = run(host, bench)
        assert (status["execution_result"], status["result"]) == ("PASS", "FAIL")
        assert status["report_path"] is None and status["session_blocked"]
        e = status["finalization_errors"][0]
        assert (
            e["stage"] == "report_write" and "traceback" in e["diagnostic"]["details"]
        )
        assert (bench["app"] / "logs/session.log").is_file()


def test_finalization_holds_slot_until_cleanup_returns(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        entered, release = threading.Event(), threading.Event()
        original = p.end_run

        def end():
            entered.set()
            assert release.wait(3)
            original()

        p.end_run = end
        rid = submit(host, bench)
        wait_phase(host, rid, "WAITING_CONFIRMATION")
        host.confirm(rid)
        assert entered.wait(2)
        try:
            assert host.get_status(rid)["active"]
            with pytest.raises(GearError):
                submit(host, bench)
        finally:
            release.set()
        assert wait_phase(host, rid, "FINISHED")["result"] == "PASS"


def test_cleanup_error_is_in_durable_events(bench):
    with Framework(bench["app"], bench["environment"]) as host:
        host._registry.entries["gear.demo"].runtime.cleanup_fail = True
        rid, status = run(host, bench)
        events = [
            json.loads(line)
            for line in (bench["app"] / "runs" / rid / "events.jsonl")
            .read_text()
            .splitlines()
        ]
        assert any(e["event"] == "finalization.failure" for e in events)
        assert status["execution_result"] == "PASS" and status["result"] == "FAIL"


def test_event_write_failure_keeps_subscription_sequence_order(bench, monkeypatch):
    from gear_framework.store import RunStore

    original = RunStore.append_event

    def fail_on_operation(self, event):
        if event["event"] == "operation.result" and not self.events_failed:
            self.events_failed = True
            raise OSError("event write failed")
        original(self, event)

    monkeypatch.setattr(RunStore, "append_event", fail_on_operation)
    with Framework(bench["app"], bench["environment"]) as host:
        sequences = []
        host.subscribe(lambda event: sequences.append(event["sequence"]))
        _, status = run(host, bench)
        assert sequences == sorted(set(sequences))
        assert status["phase"] == "BLOCKED"
        assert status["execution_result"] == "PASS" and status["result"] == "FAIL"


def test_report_and_fallback_log_failure_remain_visible(bench, monkeypatch, capsys):
    import gear_framework.host as module
    from gear_framework.store import RunStore

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(RunStore, "publish_report", fail)
    monkeypatch.setattr(module, "append_session_log", fail)
    with Framework(bench["app"], bench["environment"]) as host:
        _, status = run(host, bench)
        assert (
            status["finalization_errors"][0]["diagnostic"]["details"]["persisted"]
            is False
        )
        assert "not persisted" in capsys.readouterr().err


def test_failure_evidence_preserves_primary_and_skips_teardown(bench):
    case = {
        "api": "gear.dsl/v1",
        "name": "f",
        "body": [{"do": {"resource": "SCREEN.a", "operation": "ON"}}],
        "teardown": [{"do": {"resource": "SCREEN.a", "operation": "ON"}}],
        "evidence_on_fail": [{"resource": "SCREEN.a", "evidence": "CAPTURE"}],
    }
    bench["case"].write_text(json.dumps(case))
    with Framework(bench["app"], bench["environment"]) as host:
        p = host._registry.entries["gear.demo"].runtime
        p.fail = True

        def collect(*args):
            raise ValueError("evidence failed")

        p.collect = collect
        _, status = run(host, bench)
        assert status["result"] == "FAIL" and p.calls.count("invoke") == 1
        report = json.loads(open(status["report_path"]).read())
        assert report["primary_failure"]["diagnostic"]["code"] == "PLUGIN_CALL_FAILED"
        assert (
            report["evidence"][0]["result"]["diagnostic"]["code"] == "EVIDENCE_FAILED"
        )


def test_plugin_free_case_and_unknown_ids(bench):
    bench["case"].write_text(
        json.dumps(
            {
                "api": "gear.dsl/v1",
                "name": "wait",
                "body": [{"wait": {"duration": "1ms"}}],
            }
        )
    )
    with Framework(bench["app"], bench["environment"]) as host:
        _, status = run(host, bench)
        assert status["execution_started"] and status["result"] == "PASS"
        assert "begin" not in host._registry.entries["gear.demo"].runtime.calls
        with pytest.raises(GearError, match="UNKNOWN_RUN"):
            host.stop("missing")
