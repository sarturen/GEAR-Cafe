"""Optional Qt hosting path. Import/use this from the GUI shell only."""

import importlib
from gear_contracts.api import GearError


def create_workspace(framework, plugin_id, dispatch):
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise GearError(
            "INVALID_THREAD", "Create Workspaces on the QApplication GUI thread"
        )
    with framework._mutex:
        framework._check_idle()
        if plugin_id not in framework._registry.entries:
            raise GearError("UNKNOWN_PLUGIN", plugin_id)
        entry = framework._registry.entries[plugin_id]
        if entry.workspace is None:
            return None
        context = framework.workspace_context(plugin_id, dispatch)
        module, factory = entry.workspace.split(":")
        workspace = getattr(importlib.import_module(module), factory)(
            context, entry.runtime
        )
        if not isinstance(workspace.widget, QWidget) or not callable(
            getattr(workspace, "dispose", None)
        ):
            context.dispose()
            raise GearError(
                "INVALID_WORKSPACE", "Workspace must expose a QWidget and dispose()"
            )
        framework._workspaces.append(workspace)
        return workspace
