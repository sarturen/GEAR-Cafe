"""Best-effort, synchronous Run evidence; no session context is retained."""

from pathlib import Path
import uuid

from gear_contracts.api import GearError
from .config import PLUGIN_ID, diagnostic


def collect_logs(service, serial, log_paths, context):
    base = Path(context.artifact_dir).resolve()
    directory = base / "evidence" / PLUGIN_ID / uuid.uuid4().hex
    artifacts, failures = [], []
    cancelled = False

    def stopped():
        nonlocal cancelled
        cancelled = context.stop_token.is_requested()
        return cancelled

    def failure(stage, exc):
        code = exc.args[0] if isinstance(exc, GearError) else "ADB_FILE_ERROR"
        failures.append({"stage": stage, "code": code, "message": str(exc)})

    def command_result(stage, result):
        if result["exit_code"] != 0:
            failures.append(
                {
                    "stage": stage,
                    "code": "ADB_COMMAND_FAILED",
                    "message": "ADB 取证命令执行失败。",
                    "exit_code": result["exit_code"],
                    "stderr": result["stderr"],
                }
            )

    def artifact(path, media_type, description):
        if not path.resolve().is_relative_to(directory):
            raise GearError(
                "ADB_ARTIFACT_PATH_INVALID", "日志文件指向本次取证目录之外。"
            )
        artifacts.append(
            {
                "path": path.relative_to(base).as_posix(),
                "media_type": media_type,
                "description": description,
            }
        )

    def result():
        ok = not failures and not cancelled
        return {
            "ok": ok,
            "artifacts": artifacts,
            "diagnostic": (
                None
                if ok
                else diagnostic(
                    "ADB_EVIDENCE_CANCELLED" if cancelled else "ADB_EVIDENCE_FAILED",
                    (
                        "取证已停止，保留已取得的文件。"
                        if cancelled
                        else "部分日志取证失败，保留已取得的文件。"
                    ),
                    failures=failures,
                )
            ),
        }

    if stopped():
        return result()
    try:
        directory.mkdir(parents=True)
    except OSError as exc:
        failure("create_directory", exc)
        return result()

    try:
        dump = service.dump_logcat(serial)
        command_result("logcat", dump)
        # A successful empty buffer is valid; failures only keep actual partial output.
        if dump["exit_code"] == 0 or dump["stdout"]:
            path = directory / "logcat.txt"
            path.write_text(dump["stdout"], encoding="utf-8")
            artifact(
                path,
                "text/plain",
                "当前 logcat" if dump["exit_code"] == 0 else "当前 logcat（部分输出）",
            )
    except (GearError, OSError) as exc:
        failure("logcat", exc)

    for index, remote in enumerate(log_paths, 1):
        if stopped():
            break
        target = directory / f"extra-{index}"
        try:
            target.mkdir()
            pulled = service.pull(serial, remote, str(target))
            command_result(remote, pulled)
            # Keep partial downloads even when adb reports a failure.
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    artifact(path, "application/octet-stream", f"设备日志：{remote}")
        except (GearError, OSError) as exc:
            failure(remote, exc)
    return result()
