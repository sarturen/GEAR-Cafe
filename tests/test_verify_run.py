"""Real DSL cases through the real Framework, with only the hardware simulated."""

import json
from pathlib import Path

import pytest

from gear_verify.runner import run_case

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "examples" / "verify"
ENVIRONMENT = VERIFY / "environment.yaml"
PROJECT = VERIFY / "project.yaml"


def run(case, **kwargs):
    kwargs.setdefault("watch", False)
    return run_case(ROOT, case, PROJECT, ENVIRONMENT, **kwargs)


def sample(tmp_path, body, **extra):
    document = {"api": "gear.dsl/v1", "name": "verify-sample", "body": body, **extra}
    path = tmp_path / "sample.case.yaml"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_case_cannot_tell_it_is_not_on_a_real_bench():
    status, bench = run(VERIFY / "cases" / "board-observe.case.yaml")
    assert status["outcome"] == "PASS", status["primary_failure"]
    # All four plugins really drove their device: relays moved, ports opened,
    # a camera streamed, and the ADB tool answered.
    assert bench.consoles["COM77"].received > 0
    assert bench.screens["sim-center"].frames > 0
    assert bench.coils["COM79"][1] is False  # teardown released KL30


def test_power_cycle_propagates_from_the_relay_to_the_board():
    status, bench = run(VERIFY / "cases" / "power-cycle.case.yaml")
    assert status["outcome"] == "PASS", status["primary_failure"]
    assert bench.devices["BOARD001"].boots == 1  # one off/on cycle
    assert bench.devices["BOARD001"].adb_state == "device"


def test_a_timing_assumption_is_exposed_as_a_failure():
    status, _ = run(VERIFY / "cases" / "timing-mismatch.case.yaml")
    assert status["outcome"] == "FAIL"
    assert status["primary_failure"]["diagnostic"]["code"] == "ASSERT_TIMEOUT"


def test_the_same_case_passes_when_the_bench_really_is_that_fast():
    # Identical DSL; only the simulated board's boot time changes.
    status, _ = run(VERIFY / "cases" / "timing-mismatch.case.yaml", boot_delay=0.2)
    assert status["outcome"] == "PASS", status["primary_failure"]


def test_an_unpowered_bench_fails_what_the_powered_bench_passed():
    status, _ = run(VERIFY / "cases" / "board-observe.case.yaml", init_power=False)
    assert status["outcome"] == "FAIL"
    assert status["primary_failure"]["step_path"] == "/body/0"


def test_preflight_and_the_dsl_are_unchanged(tmp_path):
    case = sample(
        tmp_path,
        [{"do": {"resource": "POWER.kl30", "operation": "ON", "args": {"q": 1}}}],
    )
    status, bench = run(case)
    assert status["outcome"] == "REJECTED"
    assert bench.coils["COM79"][1] is True  # the initial bench state, untouched


def test_missing_capability_is_still_rejected(tmp_path):
    case = sample(
        tmp_path,
        [{"assert": {"all": [{"resource": "POWER.kl30", "condition": "GLOWING"}]}}],
    )
    status, _ = run(case)
    assert status["outcome"] == "REJECTED"


def test_failure_evidence_is_written_from_the_simulated_devices(tmp_path):
    case = sample(
        tmp_path,
        [{"assert": {"all": [{"resource": "ADB.main", "condition": "UNAVAILABLE"}]}}],
        evidence_on_fail=[
            {"resource": "CONSOLE.mcu", "evidence": "TRANSCRIPT"},
            {"resource": "SCREEN.center", "evidence": "SNAPSHOT"},
        ],
    )
    status, _ = run(case)
    assert status["outcome"] == "FAIL"
    report = json.loads(Path(status["report_path"]).read_text(encoding="utf-8"))
    assert report["api"] == "gear.report/v1"
    assert [entry["capability"] for entry in report["evidence"]] == [
        "TRANSCRIPT",
        "SNAPSHOT",
    ]
    artifacts = [
        item for entry in report["evidence"] for item in entry["result"]["artifacts"]
    ]
    assert len(artifacts) == 3  # console transcript, plus frame.jpg and frame.json
    for artifact in artifacts:
        assert (Path(status["report_path"]).parent / artifact["path"]).is_file()


def test_adb_shell_and_pull_reach_the_emulated_tool(tmp_path):
    case = sample(
        tmp_path,
        [
            {
                "do": {
                    "resource": "ADB.main",
                    "operation": "SHELL",
                    "args": {"command": "getprop ro.build.version.release"},
                }
            },
            {
                "assert": {
                    "all": [
                        {
                            "resource": "ADB.main",
                            "condition": "OUTPUT_CONTAINS",
                            "args": {
                                "command": "getprop ro.build.version.release",
                                "text": "13",
                            },
                        }
                    ]
                }
            },
        ],
    )
    status, _ = run(case)
    assert status["outcome"] == "PASS", status["primary_failure"]


def test_two_sequential_sessions_work_in_one_process():
    first, _ = run(VERIFY / "cases" / "power-cycle.case.yaml")
    second, bench = run(VERIFY / "cases" / "power-cycle.case.yaml")
    assert first["outcome"] == second["outcome"] == "PASS"
    assert bench.devices["BOARD001"].boots == 1
