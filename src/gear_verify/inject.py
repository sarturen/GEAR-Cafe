"""Point each installed plugin's hardware services at the simulated bench.

Injection happens after `Framework` startup rather than before plugin discovery:
`Registry.load` rejects a plugin package that is already imported, and it evicts
plugin modules on close. Swapping the services on the worker — the same private
access the repository's four-plugin integration harness uses — is order
independent and repeatable.
"""

from __future__ import annotations

from gear_framework.documents import plugin_slice

from .stubs import (
    camera_backend_factory,
    console_serial_factory,
    make_adb_service,
    relay_serial_factory,
)


def _install_relay(host, environment, bench):
    from gear_relay.transport import RelayService

    runtime = host._registry.entries["gear.relay"].runtime
    # Startup `configure` already built one real service per controller.
    for service in list(runtime.services.values()):
        service.close()
    runtime.services.clear()
    runtime._service_factory = lambda: RelayService(
        serial_factory=relay_serial_factory(bench)
    )
    runtime.configure(plugin_slice(environment, "gear.relay"))


def _install_console(host, environment, bench):
    runtime = host._registry.entries["gear.console"].runtime
    runtime._factory = console_serial_factory(bench)


def _install_adb(host, environment, bench):
    pid = "gear.adb"
    runtime = host._registry.entries[pid].runtime
    runtime.service = make_adb_service(bench)
    runtime.configure(
        plugin_slice(environment, pid, host._registry.owners.get("ADB") == pid)
    )


def _install_camera(host, environment, bench):
    from gear_camera.service import CameraService

    runtime = host._registry.entries["gear.camera"].runtime
    runtime.service = CameraService(camera_backend_factory(bench))


INSTALLERS = {
    "gear.relay": _install_relay,
    "gear.console": _install_console,
    "gear.adb": _install_adb,
    "gear.camera": _install_camera,
}


def install(host, environment, bench):
    """Install every applicable stub on the host worker; return their plugin ids."""

    def apply():
        installed = []
        for plugin_id, installer in INSTALLERS.items():
            if plugin_id in host._registry.entries:
                installer(host, environment, bench)
                installed.append(plugin_id)
        return installed

    return host._worker.submit(apply).result()


def prepare(host, environment):
    """Bring the bench to the state the documented workflow calls ready.

    The board-demo workflow assumes the operator has already connected the
    consoles and opened the camera preview before running a case. Repeating that
    here exercises each plugin's real connect path against the bench. Returns the
    connections made and any that failed.
    """
    entries = host._registry.entries
    connected, failed = [], []

    def attempt(label, action):
        try:
            action()
            connected.append(label)
        except Exception as exc:  # a misconfigured binding is a bench fact
            failed.append(f"{label}: {exc}")

    def apply():
        relay = entries.get("gear.relay")
        if relay is not None:
            controllers = (environment.get("plugins") or {}).get("gear.relay", {}).get(
                "config", {}
            ).get("controllers") or {}
            for controller in sorted(controllers) or ["main"]:
                attempt(f"继电器 {controller}", lambda c=controller: relay.runtime.connect(c))

        console = entries.get("gear.console")
        if console is not None:
            for resource_id, record in _resources(environment, "gear.console"):
                if record.get("type") == "CONSOLE":
                    attempt(
                        f"串口 {resource_id}",
                        lambda rid=resource_id: console.runtime.connect(rid),
                    )

        camera = entries.get("gear.camera")
        if camera is not None:
            for resource_id, record in _resources(environment, "gear.camera"):
                camera_id = (record.get("config") or {}).get("camera_id")
                if record.get("type") == "SCREEN" and camera_id:
                    attempt(
                        f"摄像头 {camera_id}",
                        lambda cid=camera_id: camera.runtime.open(cid),
                    )

        adb = entries.get("gear.adb")
        if adb is not None:
            attempt("ADB 设备发现", adb.runtime.refresh_devices)

    host._worker.submit(apply).result()
    return connected, failed


def _resources(environment, plugin_id):
    return sorted(plugin_slice(environment, plugin_id)["resources"].items())
