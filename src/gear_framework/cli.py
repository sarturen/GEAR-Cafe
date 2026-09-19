"""Human-confirmed CLI using the same Framework API as a GUI shell."""

import argparse
import json
from pathlib import Path
import signal
import sys
import threading
from .host import Framework


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="gear", description="GEAR v1 plugin test runner"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="Preflight, confirm, execute and report one Test Case"
    )
    for flag in ("app-dir", "case", "project", "environment"):
        run.add_argument("--" + flag, required=True, type=Path)
    gui = commands.add_parser("gui", help="Open the optional Qt Workspace desktop")
    gui.add_argument("--app-dir", required=True, type=Path)
    for flag in ("case", "project"):
        gui.add_argument("--" + flag, type=Path)
    args = parser.parse_args(argv)
    if args.command == "gui":
        try:
            from .desktop import run_gui

            return run_gui(args.app_dir, case=args.case, project=args.project)
        except ModuleNotFoundError as exc:
            if exc.name == "PySide6":
                print(
                    "GEAR GUI requires: pip install 'gear-framework[gui]'",
                    file=sys.stderr,
                )
            else:
                print(f"GEAR: {exc}", file=sys.stderr)
            return 3
        except Exception as exc:
            print(f"GEAR: {exc}", file=sys.stderr)
            return 3
    previous = signal.getsignal(signal.SIGINT)
    try:
        with Framework(args.app_dir, args.environment) as host:
            changed = threading.Event()
            subscription = host.subscribe(lambda _: changed.set())
            rid = host.submit(
                str(args.case.resolve()),
                str(args.project.resolve()),
                str(args.environment.resolve()),
            )
            prompted = False
            while True:
                changed.clear()
                status = host.get_status(rid)
                if status["phase"] in ("FINISHED", "BLOCKED"):
                    break
                if status["phase"] == "WAITING_CONFIRMATION" and not prompted:
                    prompted = True
                    print(json.dumps(status["preflight"], ensure_ascii=False, indent=2))
                    try:
                        answer = (
                            input(
                                "Configuration checks passed. Execute this Test Case? [yes/no] "
                            )
                            .strip()
                            .lower()
                        )
                    except (EOFError, KeyboardInterrupt):
                        answer = "no"
                    if answer in ("y", "yes"):
                        host.confirm(rid)
                    else:
                        host.stop(rid)
                    # Subsequent Ctrl-C requests cooperative stop; no asynchronous task killing.
                    signal.signal(signal.SIGINT, lambda *_: host.stop(rid))
                try:
                    changed.wait(0.1)
                except KeyboardInterrupt:
                    host.stop(rid)
                    signal.signal(signal.SIGINT, lambda *_: host.stop(rid))
            subscription.unsubscribe()
            print(json.dumps(status, ensure_ascii=False, indent=2))
            if status["session_blocked"]:
                print(
                    "Finalization failed. Further execution is blocked. Resolve the cause and restart GEAR.",
                    file=sys.stderr,
                )
                return 3
            return {"PASS": 0, "FAIL": 1, "STOPPED": 2, "REJECTED": 2, "DECLINED": 2}[
                status["outcome"]
            ]
    except Exception as exc:
        print(f"GEAR: {exc}", file=sys.stderr)
        return 3
    finally:
        signal.signal(signal.SIGINT, previous)
