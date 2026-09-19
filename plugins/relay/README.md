# GEAR 八路继电器插件

2026-09-19，按用户确认的 [单板与资源业务约定](../../doc/plugins/adb/board-resources.md) 重构。实现与模拟验证限定在本插件目录；没有扫描、探测或打开任何真实 COM 口，COM3 与本任务无关。

## 业务与身份

- `gear.relay` 提供 POWER 的 `ON`、`OFF`、`IS_ON`、`IS_OFF`，公共合约与 DSL 不变。
- 一个环境可配置多个独立的八路 Modbus RTU 控制器；每个控制器有自己的 COM 口和长期 RelayService。当前只针对 PL2303GT 接入的八路板，不增加其他板型。
- 单板主键是 ADB 登记的设备序列号；继电器使用资源顶层 `device` 关联，离线仍保留。继电器不创建 `devices`，不建立另一套 `device_name` 身份，不写其他插件切片。
- 物理定位是 `config.controller + config.channel`；同一物理通道至多一个资源。同一设备号的同一非空 `role` 至多一个资源，即使它们在不同控制器。
- `role` 是任意用途名；KL30、KL15 仅为快捷选项，可以缺省、只装其中一路或填写其他用途。未分配通道只显示“未配置”，不生成 POWER 占位。
- 操作使用“吸合 / 释放”，不推断线束通断电效果，不加入 KL30/KL15 执行顺序。

## 页面与配置

主页面按控制器显示一排 CH1–CH8 八个状态按钮：按下（绿色）为吸合，弹起为释放，按钮下显示用途与设备号。未知/未连接/故障标为“未知”并暂停切换，先连接并读取状态。命令排队时保持原缓存显示，收到回执后再更新，不把点击意图当成硬件结果。串口参数和资源绑定默认收起，正常控制区约 340px 高。

1. 展开“串口设置”，手工填写控制器名称与 COM 口。串口字段编辑后保存，不打开端口；点击“连接”才显式建立连接。“断开”“读取状态”以及全吸合/全释放只作用于当前控制器。
2. 展开“资源绑定”，在“查看通道”中选择 CH1–CH8（或右键通道按钮），选择已登记设备号并填写可选用途，点击“保存通道归属”。可指定新逻辑资源别名；已有资源 ID 不自动更名。设备号列表来自既有 WorkspaceContext，含离线登记。
3. “解绑单板与用途”保留逻辑资源及物理通道，只去除 `device`、`role` 和旧私有字段；“删除选中资源”明确删除该记录。未分配给单板的物理资源仍能用于调试或逻辑用例。
4. 仍有资源的控制器不能直接删除；先处理其资源，以免产生悬空物理定位。
5. 运行期间禁止人工控制与配置修改，仍可导航通道、查看缓存。手动动作经 `submit_manual` 调度；配置经宿主 `commit` 保存到唯一环境文件。

完整环境、项目和用例示例见 [board-demo](examples/board-demo/README.md)，实际环境仍只使用宿主根目录的 environment.yaml。

配置片段如下；`devices` 由 ADB 的登记入口维护，以下只示意已有登记与关联：

```yaml
devices:
  board-serial-A: {}
plugins:
  gear.relay:
    config:
      controllers:
        main:
          port: COM77
          baudrate: 9600
          parity: N
          stopbits: 1
          unit_id: 1
          timeout_s: 1.0
          poll_interval_ms: 500
        rear:
          port: COM78
          poll_interval_ms: 500
resources:
  POWER.main:
    type: POWER
    plugin: gear.relay
    device: board-serial-A
    config:
      controller: main
      channel: 1
      role: KL30
  POWER.reset:
    type: POWER
    plugin: gear.relay
    device: board-serial-A
    config:
      controller: rear
      channel: 1
      role: reset
```

默认 COM 留空，其他默认值为 9600 / 8 数据位 / N / 1 停止位 / 从站 1 / 超时 1 秒 / 后台读取间隔 500 毫秒。波特率允许 300–115200，从站 1–247，超时 0.05–30 秒，轮询间隔 0–60000 毫秒，0 关闭轮询。每个控制器需独立 COM；规范化后重复的 COM 会校验失败且不打开。默认参数不代表实机已验收。

## 旧配置迁移

- 旧 `plugin.config.port/...` 按 `main` 控制器解释，旧仅有 `channel` 的资源默认指向 `main`。加载、校验、导航不会改写环境。
- GUI 首次实际配置提交把单 COM 配置转换为 `controllers.main`，为缺少 controller 的旧资源标记 `main`；保留资源 ID 与已有顶层 `record.device`。
- 旧 `device_name` 只作为待核对的历史文本显示，绝不自动复制为设备号。已有 `record.device` 优先保留；没有它时必须人工选择登记设备号，或明确点击解绑。保存/解绑清理该资源的 `device_name`、`terminal`；旧 terminal 可作为用途建议。
- 旧缺少 channel 的占位资源在“旧数据与待处理资源”区域明确列出，下拉显示“未绑定”。它仍是 **INCOMPLETE**，不能伪装为有效资源。请分配实际通道，或点击删除；解绑单板本身不会补齐 channel。
- 当前核心对被引用插件的完整切片执行预检。因此，哪怕用例未引用旧占位，它也会使该插件预检未完成；需人工删除/补齐后再运行。本次没有绕过验证、静默删除资源或修改核心。新流程不再生成这种占位。

## 状态、协议与生命周期

| 能力 | 行为 | 状态来源 |
| --- | --- | --- |
| ON / OFF | 05 单线圈写；通道 1–8 对应地址 0–7，必须收到匹配回执 | `write_acknowledgement` |
| IS_ON / IS_OFF | 每次实际 01 读取八路线圈，再判断当前绑定通道 | `read_coils` |
| 手动全吸合 / 全释放 | 0F 设置当前控制器八路线圈 | `write_acknowledgement` |
| 后台读取 | 已连接后按间隔 01 读取 | `read_coils` |

写回执验证成功立即更新通道缓存，界面分别标记“回执 / 读回”。01 状态只是线圈寄存器，不是触点物理反馈。未知和故障不能当作 OFF；条件查询失败返回 `ok: false`，不会伪装为断言不满足。

同一服务的后台读取、手动操作和 Run 命令持有同一事务锁，完整请求/响应严格串行。快照使用独立缓存锁，GUI 不等待串口 I/O。轮询在 Run 中继续；其线程属于会话，不持有 RunContext、stop_token 或用例 emit 回调。GUI 每 100 毫秒读取缓存，不从 GUI 线程操作串口。

首次执行可以按已保存配置打开端口；故障后锁存状态、停止实际轮询，不自动重连，需人工“连接”。`end_run` 清除用例绑定但保持连接和轮询。改变某控制器的串口配置只释放该控制器连接；仅改资源归属不会断开。`close` 停止并 join 轮询线程，释放句柄；关闭、断开不发送继电器复位或全释放。

`validate_config` 和 GUI 保存计算为纯配置操作，不探测硬件；宿主随后调度 `configure` 时允许释放受影响的已有连接，不打开新连接。

## 实现入口与验证

- `bindings.py` / `config.py`：纯归属、迁移和校验。
- `transport.py`：RTU 帧/CRC、事务锁、轮询、缓存与连接。
- `runtime.py`：按归档 controller/channel 路由公共能力、会话生命周期与手控接口。
- `workspace.py`：Qt 物理页面；私有 `resource_statuses() -> dict[rid, str]` 只读取缓存，`select_resource(rid)` 只定位导航。
- `serial_win32.py`：标准库 Windows COM 适配，没有安装新依赖。Runtime 不导入 Qt。

详见 [实现报告](../../doc/plugins/relay/implementation-report.md) 与 [GUI 说明](../../doc/plugins/relay/gui-layout.md)。本次模拟测试覆盖真实串口 API 禁用、协议异常/超时/停止、多个控制器、归档绑定、运行中轮询与 Qt 行为；尚无实机验收。协议仍沿用已批准的 [Modbus 应用协议](https://modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf) 与 [串行规范](https://modbus.org/docs/Modbus_over_serial_line_V1_02.pdf) 映射，本次未扩展协议。

从 `framework-dev` 运行，测试临时文件保持在插件内：

```powershell
$env:TEMP = (Resolve-Path plugins/relay/.test-tmp).Path
$env:TMP = $env:TEMP
.venv/Scripts/python.exe -B -m pytest plugins/relay/tests --capture=sys -p no:cacheprovider --basetemp=plugins/relay/.test-tmp/local-cases
```
