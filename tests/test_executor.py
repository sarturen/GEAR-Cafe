from types import SimpleNamespace as NS
import pytest
from gear_framework.executor import Executor


class Token:
    requested = False

    def is_requested(self):
        return self.requested


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def wait(self, seconds, token):
        self.now += max(0, seconds)


def make_executor(assertion, results, costs=None):
    clock, token, events, calls = Clock(), Token(), [], []
    values = iter(results)
    costs = iter(costs or [0] * len(results))

    def evaluate(*args):
        calls.append(clock.now)
        clock.now += next(costs)
        return {
            "ok": True,
            "satisfied": next(values),
            "diagnostic": None,
            "details": {},
        }

    request = {
        "resource_id": "SCREEN.a",
        "plugin_id": "gear.demo",
        "kind": "condition",
        "capability": "LIT",
        "args": {},
    }
    prepared = NS(
        case={
            "setup": [],
            "body": [{"assert": assertion}],
            "teardown": [],
            "evidence_on_fail": [],
        },
        requests={"/body/0/assert/all/0": [request]},
        report={
            "coverage": [
                {
                    "source": "/body/0/assert/all/0",
                    "resource_id": "SCREEN.a",
                    "kind": "condition",
                    "capability": "LIT",
                    "bound": True,
                    "executed": False,
                }
            ]
        },
    )
    registry = NS(entries={"gear.demo": NS(runtime=NS(evaluate=evaluate))})

    def context(*args):
        return NS(call_id=str(len(calls)), iteration=args[-1])

    def terminate(result, failure):
        return "STOPPED" if token.requested else result

    executor = Executor(
        prepared,
        registry,
        context,
        lambda event, details, **kw: events.append((event, details)),
        token,
        clock,
        terminate,
    )
    return executor, clock, calls, prepared


def test_within_exact_deadline_passes_and_late_true_fails():
    assertion = {
        "all": [{"resource": "SCREEN.a", "condition": "LIT"}],
        "within": "1s",
        "every": "200ms",
    }
    executor, _, calls, _ = make_executor(assertion, [True], [1])
    assert executor.run()[0] == "PASS"
    assert len(calls) == 1
    executor, _, calls, _ = make_executor(assertion, [True], [2])
    assert executor.run()[0] == "FAIL"
    assert len(calls) == 1


def test_within_no_new_sample_at_deadline():
    a = {
        "all": [{"resource": "SCREEN.a", "condition": "LIT"}],
        "within": "1s",
        "every": "1s",
    }
    ex, clock, calls, _ = make_executor(a, [False])
    assert ex.run()[0] == "FAIL"
    assert calls == [0] and clock.now == 1


def test_for_requires_boundary_cycle_without_overlap():
    a = {
        "all": [{"resource": "SCREEN.a", "condition": "LIT"}],
        "for": "1s",
        "every": "700ms",
    }
    ex, clock, calls, p = make_executor(a, [True, True, True])
    assert ex.run()[0] == "PASS"
    assert calls == pytest.approx([0, 0.7, 1.0])
    assert p.report["coverage"][0]["executed"]
    ex, clock, calls, _ = make_executor(a, [True], [1.2])
    assert ex.run()[0] == "PASS" and calls == [0]


def test_immediate_false_fails():
    ex, *_ = make_executor(
        {"all": [{"resource": "SCREEN.a", "condition": "LIT"}]}, [False]
    )
    result, failure = ex.run()
    assert result == "FAIL" and failure["step_path"] == "/body/0"
