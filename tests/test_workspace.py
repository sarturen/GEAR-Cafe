import queue
import threading
import pytest
from gear_contracts.api import GearError
from gear_framework.host import Framework
from test_host import submit, wait_phase


def test_manual_action_and_notifications_use_dispatcher_and_shared_runtime(bench):
    callbacks = queue.Queue()
    with Framework(bench["app"], bench["environment"]) as host:
        context = host.workspace_context("gear.demo", callbacks.put)
        done = []
        owner = threading.get_ident()
        context.submit_manual(
            lambda: threading.get_ident(),
            lambda result: done.append((threading.get_ident(), result)),
        )
        callbacks.get(timeout=2)()
        assert done[0][0] == owner and done[0][1]["value"] != owner
        rid = submit(host, bench)
        wait_phase(host, rid, "WAITING_CONFIRMATION")
        with pytest.raises(GearError):
            context.submit_manual(lambda: 1, done.append)
        with pytest.raises(GearError):
            context.commit(
                context.current_slice(), {"status": "VALID", "diagnostics": []}
            )
        host.stop(rid)
        wait_phase(host, rid, "FINISHED")


def test_commit_persists_whole_slice_and_applies_configuration(bench):
    callbacks = queue.Queue()
    with Framework(bench["app"], bench["environment"]) as host:
        context = host.workspace_context("gear.demo", callbacks.put)
        data = context.current_slice()
        data["plugin"]["config"] = {"controller": "COM7"}
        context.commit(data, {"status": "INCOMPLETE", "diagnostics": []})
        # Worker barrier for assertion; Framework submit rejects while application is pending.
        host.flush()
        assert context.current_slice()["plugin"]["config"] == {"controller": "COM7"}
        assert host._registry.entries["gear.demo"].runtime.slice["plugin"][
            "config"
        ] == {"controller": "COM7"}
