"""Pure configuration checks; never resolve executables or contact ADB."""

PLUGIN_ID = "gear.adb"


def diagnostic(code, message, **details):
    return {"code": code, "message": message, "details": details}


def validate_slice(plugin_slice):
    issues = []
    incomplete = False
    invalid = False

    def issue(code, message, path, unfinished=False):
        nonlocal incomplete, invalid
        entry = diagnostic(code, message)
        entry["path"] = path
        issues.append(entry)
        incomplete |= unfinished
        invalid |= not unfinished

    config = plugin_slice["plugin"]["config"]
    if set(config) - {"adb_path"}:
        issue("ADB_CONFIG_INVALID", "ADB 配置仅支持 adb_path。", "plugin.config")
    path = config.get("adb_path", "adb")
    if type(path) is not str or "\0" in path:
        issue(
            "ADB_CONFIG_INVALID",
            "ADB 路径必须是不含空字符的字符串。",
            "plugin.config.adb_path",
        )
    elif not path.strip():
        issue(
            "ADB_PATH_REQUIRED",
            "请填写 adb 可执行文件路径或命令名称。",
            "plugin.config.adb_path",
            True,
        )
    devices = plugin_slice.get("devices", {})
    for serial in devices:
        if not serial.strip() or any(c.isspace() or c == "\0" for c in serial):
            issue(
                "ADB_CONFIG_INVALID",
                "设备序列号不能包含空白或空字符。",
                "devices." + serial,
            )
    for rid, record in plugin_slice["resources"].items():
        resource_config = record["config"]
        if set(resource_config) - {"log_paths"}:
            issue(
                "ADB_CONFIG_INVALID",
                "ADB 资源配置仅支持 log_paths。",
                f"resources.{rid}.config",
            )
        paths = resource_config.get("log_paths", [])
        if type(paths) is not list or any(
            type(path) is not str or not path.startswith("/") or "\0" in path
            for path in paths
        ):
            issue(
                "ADB_LOG_PATHS_INVALID",
                "附加日志路径必须是设备绝对路径字符串列表，且不含空字符。",
                f"resources.{rid}.config.log_paths",
            )
        serial = record.get("device")
        if not serial:
            issue(
                "ADB_DEVICE_REQUIRED",
                "请为 ADB 资源选择已登记的 USB 设备序列号。",
                f"resources.{rid}.device",
                True,
            )
        elif serial not in devices:
            issue(
                "ADB_DEVICE_UNKNOWN",
                "绑定的设备序列号尚未登记。",
                f"resources.{rid}.device",
            )
    return {
        "status": "INVALID" if invalid else "INCOMPLETE" if incomplete else "VALID",
        "diagnostics": issues,
    }
