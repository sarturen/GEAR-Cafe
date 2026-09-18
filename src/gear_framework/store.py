"""Per-run archives and atomic report publication."""

from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import tempfile


def atomic_json(path, value):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            json.dump(value, file, ensure_ascii=False, allow_nan=False, indent=2)
            file.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class RunStore:
    def __init__(self, app_dir, run_id):
        self.directory = Path(app_dir) / "runs" / run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.events = None
        self.events_failed = False

    def archive(self, paths):
        directory = self.directory / "input"
        directory.mkdir()
        names = {
            "test_case": "test-case.yaml",
            "project": "project.yaml",
            "environment": "environment.yaml",
        }
        result = {}
        for key, name in names.items():
            shutil.copyfile(paths[key], directory / name)
            result[key] = str(directory / name)
        return result

    def start_events(self):
        self.events = (self.directory / "events.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        )

    def append_event(self, event):
        if self.events is not None and not self.events_failed:
            try:
                self.events.write(
                    json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
                )
                self.events.flush()
            except Exception:
                self.events_failed = True
                raise

    def close_events(self):
        if self.events is not None:
            stream, self.events = self.events, None
            stream.close()

    def publish_report(self, report):
        path = self.directory / "report.json"
        atomic_json(path, report)
        return str(path.resolve())


def append_session_log(app_dir, record):
    directory = Path(app_dir) / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "session.log").open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
