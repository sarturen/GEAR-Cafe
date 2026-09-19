"""Command line entry point for verification mode."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runner import build_bench, exit_code, run_case


def _common(parser):
    parser.add_argument(
        "--boot-delay",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="单板上电到启动完成的时间，默认 3.0 秒",
    )
    parser.add_argument(
        "--powered-off",
        action="store_true",
        help="台架起始为断电状态，默认保持已上电运行",
    )
    parser.add_argument(
        "--no-preconnect",
        action="store_true",
        help="不预先连接串口与相机，用于验证“必须先连接”这类失败路径",
    )


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        # The live view is Chinese; do not let a legacy console codepage garble it.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="gear-verify",
        description="GEAR 验证模式：用真实用例驱动模拟台架，不改框架、插件和 DSL。",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="对模拟台架执行一条测试用例")
    for flag in ("app-dir", "case", "project", "environment"):
        run.add_argument("--" + flag, required=True, type=Path)
    _common(run)
    run.add_argument("--ask", action="store_true", help="执行前人工确认")
    run.add_argument("--quiet", action="store_true", help="不打印实时台架状态")
    run.add_argument("--state-log", type=Path, help="把台架状态变化写成 JSONL")

    gui = commands.add_parser("gui", help="用模拟硬件打开桌面界面")
    gui.add_argument("--app-dir", required=True, type=Path)
    gui.add_argument("--environment", required=True, type=Path)
    gui.add_argument("--case", type=Path)
    gui.add_argument("--project", type=Path)
    _common(gui)

    args = parser.parse_args(argv)
    if args.command == "gui":
        return _gui(args)
    status, _ = run_case(
        args.app_dir,
        args.case,
        args.project,
        args.environment,
        boot_delay=args.boot_delay,
        init_power=not args.powered_off,
        preconnect=not args.no_preconnect,
        watch=not args.quiet,
        ask=args.ask,
        state_log=args.state_log,
    )
    return exit_code(status)


def _gui(args):
    try:
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError:
        print(
            "GEAR 验证模式 GUI 需要 PySide6：pip install 'gear-framework[gui]'",
            file=sys.stderr,
        )
        return 3
    from gear_framework.desktop import DesktopWindow
    from gear_framework.host import Framework

    from .inject import install, prepare
    from .seal import sealed

    data, bench = build_bench(
        args.environment, boot_delay=args.boot_delay, init_power=not args.powered_off
    )
    with sealed() as seal:
        host = Framework(args.app_dir, args.environment)
        try:
            seal.ports()
            install(host, data, bench)
            bench.start()
            if not args.no_preconnect:
                prepare(host, data)
            app = QApplication.instance() or QApplication(sys.argv[:1])
            window = DesktopWindow(host, case=args.case, project=args.project)
            window.show()
            return app.exec()
        finally:
            bench.stop()
            host.close()


if __name__ == "__main__":
    sys.exit(main())
