"""Readable views of the simulated bench, for the live display."""

from __future__ import annotations


def flatten(snapshot):
    """Stable, human-labelled state pairs. Counters are left out on purpose."""
    flat = {}
    for serial, device in sorted(snapshot["devices"].items()):
        flat[f"{serial} 电源"] = device["powered"]
        flat[f"{serial} 已启动"] = device["booted"]
        flat[f"{serial} ADB"] = device["adb"]
        for name, on in sorted(device["rails"].items()):
            flat[f"{serial} 电源轨 {name}"] = on
        for camera_id, screen in sorted(device["screens"].items()):
            flat[f"{serial} 屏幕 {camera_id}"] = "亮" if screen["lit"] else "暗"
            flat[f"{serial} 屏幕 {camera_id} 采集"] = screen["open"]
        for port, console in sorted(device["consoles"].items()):
            flat[f"{serial} 串口 {port} 连接"] = console["open"]
    for port, channels in sorted(snapshot["coils"].items()):
        for channel, on in sorted(channels.items()):
            flat[f"继电器 {port} 通路 {channel}"] = on
    return flat


def changes(before, after):
    """One line per value that actually moved, in the bench's own terms."""
    return [
        f"{label}: {_show(before.get(label))} → {_show(value)}"
        for label, value in after.items()
        if before.get(label) != value
    ]


def _show(value):
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if value is None:
        return "—"
    return str(value)


def render(snapshot):
    """The whole bench at one instant."""
    lines = []
    for serial, device in sorted(snapshot["devices"].items()):
        if not device["powered"]:
            state = "断电"
        elif device["booted"]:
            state = "运行中"
        else:
            state = "启动中"
        lines.append(
            f"{serial}: {state}"
            f" · 本次会话上电启动 {device['boots']} 次"
            f" · ADB {device['adb']}"
        )
        for name, on in sorted(device["rails"].items()):
            lines.append(f"    电源轨 {name:<6} {'ON' if on else 'OFF'}")
        for camera_id, screen in sorted(device["screens"].items()):
            lines.append(
                f"    屏幕   {camera_id:<12} {'亮' if screen['lit'] else '暗'}"
                f" · {'采集中' if screen['open'] else '未采集'}"
                f" · 已出帧 {screen['frames']}"
            )
        for port, console in sorted(device["consoles"].items()):
            lines.append(
                f"    串口   {port:<12} {'已连接' if console['open'] else '未连接'}"
                f" · 收到 {console['rx_bytes']} 字节"
            )
    for port, channels in sorted(snapshot["coils"].items()):
        mask = "".join("1" if channels[key] else "0" for key in sorted(channels))
        lines.append(f"继电器 {port} 通路状态 {mask}（CH1..CH8）")
    for note in snapshot["notes"]:
        lines.append(f"注意：{note}")
    return lines
