"""Run one Test Case against the simulated bench, through the real Framework.

Nothing here reimplements execution: the same `Framework` the GUI and CLI use
archives the inputs, runs Preflight, applies the DSL and publishes the report.
Only the plugins' hardware is simulated, so a case cannot tell the difference.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

from gear_framework.documents import load_yaml, parse_environment
from gear_framework.host import Framework

from .bench import SimBench
from .inject import install, prepare
from .seal import sealed
from .view import changes, flatten, render

EXIT_CODES = {"PASS": 0, "FAIL": 1, "STOPPED": 2, "REJECTED": 2, "DECLINED": 2}
LOOP_S = 0.1


def build_bench(environment, *, boot_delay=3.0, init_power=True):
    data = parse_environment(load_yaml(Path(environment)))
    return data, SimBench(data, init_power=init_power, boot_delay_s=boot_delay)


class StateLog:
    """Append-only record of every bench change, for after-the-fact inspection."""

    def __init__(self, path):
        self.path = Path(path)
        self._stream = None
        self._previous = None

    def __enter__(self):
        self._stream = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(self, *args):
        self._stream.close()

    def note(self, snapshot):
        flat = flatten(snapshot)
        if self._previous is not None:
            for entry in changes(self._previous, flat):
                self._stream.write(
                    json.dumps({"t": time.time(), "change": entry}, ensure_ascii=False)
                    + "\n"
                )
        self._previous = flat


def run_case(
    app_dir,
    case,
    project,
    environment,
    *,
    boot_delay=3.0,
    init_power=True,
    preconnect=True,
    watch=True,
    ask=False,
    state_log=None,
):
    """Drive one case; return (status, bench, state-log path or None)."""
    app_dir = Path(app_dir).resolve()
    environment = Path(environment).resolve()
    data, bench = build_bench(
        environment, boot_delay=boot_delay, init_power=init_power
    )
    log = StateLog(state_log) if state_log else None
    final = None
    with sealed() as seal, (log or contextlib.nullcontext()):
        host = Framework(app_dir, environment)
        try:
            seal.ports()
            installed = install(host, data, bench)
            bench.start()
            if watch:
                print(f"验证模式：已打桩 {', '.join(installed) or '（无插件）'}")
            if preconnect:
                _preconnect(host, data, watch)
            status = _drive(host, case, project, environment, watch, ask, bench, log)
            final = bench.snapshot()
        finally:
            bench.stop()
            host.close()
    if watch and final is not None:
        print("\n最终台架状态：")
        for line in render(final):
            print("  " + line)
    return status, bench


def _preconnect(host, data, watch):
    connected, failed = prepare(host, data)
    if not watch:
        return
    if connected:
        print(f"台架已就绪：{', '.join(connected)}")
    for entry in failed:
        print(f"台架连接失败：{entry}")


def _drive(host, case, project, environment, watch, ask, bench, log):
    subscription = host.subscribe(lambda event: _event(event, watch))
    run_id = host.submit(
        str(Path(case).resolve()), str(Path(project).resolve()), str(environment)
    )
    prompted = False
    previous = flatten(bench.snapshot())
    try:
        while True:
            status = host.get_status(run_id)
            if status["phase"] in ("FINISHED", "BLOCKED"):
                break
            if status["phase"] == "WAITING_CONFIRMATION" and not prompted:
                prompted = True
                print(json.dumps(status["preflight"], ensure_ascii=False, indent=2))
                if ask:
                    answer = input("配置检查通过。执行该用例？[yes/no] ").strip().lower()
                    host.confirm(run_id) if answer in ("y", "yes") else host.stop(run_id)
                else:
                    print("配置检查通过，开始执行。（--ask 可改为人工确认）")
                    host.confirm(run_id)
            previous = _show_changes(bench, previous, watch, log)
            time.sleep(LOOP_S)
        _show_changes(bench, previous, watch, log)
    finally:
        subscription.unsubscribe()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if status["session_blocked"]:
        print("收尾失败，会话已被阻塞。处理原因后重启 GEAR。", file=sys.stderr)
    return status


def _show_changes(bench, previous, watch, log):
    """Print and record what moved since the last look; return the new state."""
    snapshot = bench.snapshot()
    flat = flatten(snapshot)
    if log is not None:
        log.note(snapshot)
    if watch:
        for entry in changes(previous, flat):
            print(f"  [台架] {entry}")
    return flat


EVENT_NAMES = ("run.state", "operation.result", "condition.sample", "evidence.result")


def _event(event, watch):
    if not watch or event["event"] not in EVENT_NAMES:
        return
    details = event["details"]
    if event["event"] == "run.state":
        if details["outcome"] is not None:
            print(f"  [用例] 状态 {details['outcome']} ({event['phase']})")
    elif event["event"] == "operation.result":
        result = details["result"]
        print(
            f"  [用例] 操作 {details['operation']} on {event['resource_id']}: "
            f"{'成功' if result['ok'] else '失败'}"
        )
    elif event["event"] == "condition.sample":
        result = details["result"]
        print(
            f"  [用例] 条件 {details['condition']} on {event['resource_id']}: "
            f"{'满足' if result.get('satisfied') else '不满足'}"
        )
    else:
        result = details["result"]
        print(
            f"  [用例] 取证 {details['capability']} on {event['resource_id']}: "
            f"{'成功' if result['ok'] else '失败'}"
        )


def exit_code(status):
    if status["session_blocked"]:
        return 3
    return EXIT_CODES.get(status["outcome"], 3)
