import json
import os
import subprocess
import sys


def test_cli_runs_through_real_discovery_confirmation_and_report(bench):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(["src", "doc/contracts"]))
    args = [
        sys.executable,
        "-m",
        "gear_framework",
        "run",
        "--app-dir",
        str(bench["app"]),
        "--case",
        str(bench["case"]),
        "--project",
        str(bench["project"]),
        "--environment",
        str(bench["environment"]),
    ]
    result = subprocess.run(
        args, input="yes\n", capture_output=True, text=True, env=env, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert '"result": "PASS"' in result.stdout
    reports = list((bench["app"] / "runs").glob("*/report.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["execution_started"]


def test_cli_eof_declines(bench):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(["src", "doc/contracts"]))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gear_framework",
            "run",
            "--app-dir",
            str(bench["app"]),
            "--case",
            str(bench["case"]),
            "--project",
            str(bench["project"]),
            "--environment",
            str(bench["environment"]),
        ],
        input="",
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 2
    assert '"outcome": "DECLINED"' in result.stdout
