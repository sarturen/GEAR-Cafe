"""Pure camera binding validation; no Qt imports or device discovery."""

import math

PLUGIN_ID = "gear.camera"
FULL_ROI = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


def valid_roi(roi):
    if type(roi) is not dict or set(roi) != set(FULL_ROI):
        return False
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in roi.values()):
        return False
    return (
        0 <= roi["x"] < 1
        and 0 <= roi["y"] < 1
        and 0 < roi["width"] <= 1
        and 0 < roi["height"] <= 1
        and roi["x"] + roi["width"] <= 1.00000001
        and roi["y"] + roi["height"] <= 1.00000001
    )


def validate_slice(data):
    issues, cameras, roles, counts = [], {}, {}, {}

    def issue(code, message, path):
        issues.append({"code": code, "message": message, "details": {}, "path": path})

    if data["plugin"]["config"]:
        issue(
            "CAMERA_CONFIG_INVALID", "当前摄像头插件没有全局配置项。", "plugin.config"
        )
    for rid, record in data["resources"].items():
        config = record.get("config", {})
        path = f"resources.{rid}"
        if (
            record.get("type") != "SCREEN"
            or type(config) is not dict
            or set(config) - {"camera_id", "role", "roi"}
        ):
            issue(
                "CAMERA_CONFIG_INVALID",
                "资源应为 SCREEN，仅支持 camera_id、role、roi。",
                path,
            )
            continue
        camera, role, device = (
            config.get("camera_id"),
            config.get("role"),
            record.get("device"),
        )
        for key, value in (("camera_id", camera), ("role", role), ("device", device)):
            if value is None or value == "":
                issue(
                    "CAMERA_INCOMPLETE",
                    "请配置摄像头输入、单板设备号和屏幕用途。",
                    path + "." + key,
                )
            elif type(value) is not str or not value.strip():
                issue(
                    "CAMERA_CONFIG_INVALID", f"{key} 必须为非空文本。", path + "." + key
                )
        if "roi" in config and not valid_roi(config["roi"]):
            issue(
                "CAMERA_ROI_INVALID",
                "ROI 必须是图像内的归一化矩形，宽高应大于零。",
                path + ".config.roi",
            )
        if type(camera) is str and camera:
            if camera in cameras:
                issue("CAMERA_DUPLICATE", f"摄像头已被 {cameras[camera]} 独占。", path)
            cameras[camera] = rid
        if type(device) is str and device:
            counts[device] = counts.get(device, 0) + 1
            if counts[device] > 6:
                issue("CAMERA_LIMIT", "每块单板最多配置六个摄像头。", path)
            if type(role) is str and role:
                key = (device, role)
                if key in roles:
                    issue(
                        "CAMERA_ROLE_DUPLICATE",
                        f"该屏幕用途已分配给 {roles[key]}。",
                        path,
                    )
                roles[key] = rid
    invalid = any(i["code"] != "CAMERA_INCOMPLETE" for i in issues)
    return {
        "status": "INVALID" if invalid else "INCOMPLETE" if issues else "VALID",
        "diagnostics": issues,
    }
