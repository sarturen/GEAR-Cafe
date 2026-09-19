"""GUI-neutral WorkspaceContextV1. The shell supplies a GUI-thread dispatcher."""

from __future__ import annotations
import copy
import sys
import threading
from gear_contracts.api import GearError
from .common import Subscription, ensure_json, exception_diagnostic, check_diagnostic
from .documents import plugin_slice, replace_slice
from .store import atomic_json


class WorkspaceContext:
    def __init__(self, host, plugin_id, dispatch):
        self.host, self.plugin_id, self.dispatch = host, plugin_id, dispatch
        self._gui_thread = threading.get_ident()
        self._listeners = {"state": [], "environment": []}
        self._disposed = False

    def _gui(self):
        if threading.get_ident() != self._gui_thread:
            raise GearError(
                "INVALID_THREAD", "Workspace methods must run on the GUI thread"
            )
        if self._disposed:
            raise GearError("CLOSED", "Workspace was disposed")

    def current_slice(self):
        self._gui()
        with self.host._mutex:
            return plugin_slice(
                self.host._environment,
                self.plugin_id,
                self.host._registry.owners.get("ADB") == self.plugin_id,
            )

    def configured_device_ids(self):
        self._gui()
        with self.host._mutex:
            return sorted(self.host._environment.get("devices", {}))

    def run_state(self):
        self._gui()
        with self.host._mutex:
            return "ACTIVE" if self.host._active or self.host._blocked else "IDLE"

    def subscribe_run_state(self, listener):
        self._gui()
        sub = Subscription(listener)
        self._listeners["state"].append(sub)
        return sub

    def subscribe_environment(self, listener):
        self._gui()
        sub = Subscription(lambda _: listener())
        self._listeners["environment"].append(sub)
        return sub

    def _post(self, callback):
        try:
            self.dispatch(callback)
        except Exception as exc:
            print(f"GEAR GUI dispatcher failed: {exc}", file=sys.stderr)

    def _notify(self, kind):
        state = "ACTIVE" if self.host._active or self.host._blocked else "IDLE"
        for sub in list(self._listeners[kind]):

            def deliver(sub=sub, value=state):
                if not self._disposed:
                    self._gui()
                    try:
                        sub.deliver(value)
                    except Exception as exc:
                        print(f"GEAR Workspace listener failed: {exc}", file=sys.stderr)

            self._post(deliver)

    def commit(self, full_slice, validation_report):
        self._gui()
        # Supplied validation is only immediate UI feedback, never an execution gate.
        ensure_json(validation_report)
        if (
            type(validation_report) is not dict
            or set(validation_report) != {"status", "diagnostics"}
            or validation_report["status"] not in ("VALID", "INCOMPLETE", "INVALID")
            or type(validation_report["diagnostics"]) is not list
        ):
            raise GearError("INVALID_RESULT", "Malformed ValidationReport")
        for diag in validation_report["diagnostics"]:
            check_diagnostic(diag)
        with self.host._mutex:
            self.host._check_idle()
            try:
                env = replace_slice(
                    self.host._environment,
                    self.plugin_id,
                    full_slice,
                    self.host._registry.owners.get("ADB") == self.plugin_id,
                )
                for record in full_slice["resources"].values():
                    if self.host._registry.owners.get(record["type"]) != self.plugin_id:
                        raise GearError(
                            "INVALID_SLICE", "Plugin does not own Resource Type"
                        )
            except GearError as exc:
                raise GearError("INVALID_SLICE", str(exc)) from exc
            try:
                atomic_json(self.host.environment_path, env)
            except Exception as exc:
                raise GearError("PERSISTENCE_ERROR", str(exc)) from exc
            self.host._environment = env
            self.host._pending_manual += 1
            slice = plugin_slice(
                env,
                self.plugin_id,
                self.host._registry.owners.get("ADB") == self.plugin_id,
            )

            def configure():
                try:
                    self.host._registry.entries[self.plugin_id].runtime.configure(slice)
                except Exception as exc:
                    self.host._blocked = True
                    diag = exception_diagnostic(
                        "CONFIGURE_FAILED", exc, "configure", plugin_id=self.plugin_id
                    )
                    self.host.session_diagnostics.append(diag)
                    print(
                        f"GEAR configuration failed; restart required: {diag}",
                        file=sys.stderr,
                    )
                finally:
                    with self.host._mutex:
                        self.host._pending_manual -= 1
                    self.host._notify_workspaces("environment")
                    self.host._notify_workspaces("state")

            self.host._worker.submit(configure)

    def submit_manual(self, action, listener):
        self._gui()
        with self.host._mutex:
            self.host._check_idle()
            self.host._pending_manual += 1

            def execute():
                try:
                    value = action()
                    ensure_json(value)
                    result = {
                        "ok": True,
                        "value": copy.deepcopy(value),
                        "diagnostic": None,
                    }
                except Exception as exc:
                    result = {
                        "ok": False,
                        "value": None,
                        "diagnostic": exception_diagnostic(
                            "MANUAL_FAILED", exc, "manual", plugin_id=self.plugin_id
                        ),
                    }
                return result

            # Release only after the action has returned to the worker dispatcher.
            future = self.host._worker.submit(execute)

            def finished(f):
                with self.host._mutex:
                    self.host._pending_manual -= 1

                def deliver():
                    if not self._disposed:
                        self._gui()
                        try:
                            listener(f.result())
                        except Exception as exc:
                            print(
                                f"GEAR manual listener failed: {exc}", file=sys.stderr
                            )

                self._post(deliver)

            future.add_done_callback(finished)

    def dispose(self):
        if self._disposed:
            return
        self._gui()
        for subs in self._listeners.values():
            for sub in subs:
                sub.unsubscribe()
        self._disposed = True
