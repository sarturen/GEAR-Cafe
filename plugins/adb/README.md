# ADB USB 插件

业务身份及跨插件资源归属以 [2026-09-19 业务约定](../../doc/plugins/adb/board-resources.md) 为准。设备号在 ADB / fastboot 间保持不变，作为现有 devices 登记与资源顶层 device 的唯一主键。每块单板最多一个 ADB 资源；旧重复绑定会显示 ADB_DEVICE_DUPLICATE，保留原数据供人工修正。

插件 ID：`gear.adb`；资源类型：`ADB`。通过 GEAR v1 合约装载，Runtime 不依赖 Qt 或 Framework 内部代码。

## 启动 GUI

安装 Python 3.11+ 和 Android SDK Platform-Tools。在仓库根目录执行：

```powershell
.\.venv\Scripts\python -m pip install -e ./doc/contracts -e ".[gui,test]"
.\.venv\Scripts\gear gui --app-dir . --project examples/adb/project.yaml --case examples/adb/case.yaml
```

也可以双击根目录 Start-GEAR.cmd，直接进入基础主窗口。GUI 默认读取根目录唯一的 environment.yaml；首次自动生成空配置。在 ADB 页面填入 adb 可执行文件的路径（在 PATH 中时填 adb），显式刷新 USB 列表并登记序列号，然后创建 main 资源别名，将 ADB.main 绑定到该序列号。也可直接手动登记暂未连接的设备。

配置修改实时保存到同一份 environment.yaml，下次启动继续使用；初始配置为空，须先补全资源绑定。预检只检查配置，确认执行后才发送命令。点击“停止”后，当前调用及收尾完成前不会开放下一次执行。

## 生命周期与 USB 范围

整个应用会话仅创建一个 ADB Runtime 和服务。Workspace 和所有用例共享该服务；切换页面、结束用例不会停止正在采集的 logcat。普通 adb 客户端命令会退出，但 USB 连接由长期运行的 ADB server 持有，不为每个用例重建连接。

只发现、选择和操作已确认是 USB 的 transport，不支持网络连接、无线配对或模拟器。设备离线、拔出后仍保留已登记身份和资源绑定。显式移除登记身份也不级联删除资源绑定，需要人工修正悬空引用。状态列表分别显示 device、recovery、sideload、fastboot、offline、unauthorized、missing。查询失败显示 unknown 和工具错误，不冒充 offline/missing。未启用观察时，列表表示最近一次刷新或能力调用取得的状态。

使用 ADB 的 `track-devices --proto-text` 第一帧读取连接类型，再关闭本插件的列表客户端。可在 GUI 显式“开始观察”，由会话线程约每秒查询 ADB 与 fastboot 列表；“停止观察”结束该线程。首次打开页面、configure 和静态预检不探测硬件。选择设备时使用已核实的 USB transport ID。该方式依据 [ADB 官方连接类型定义](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/proto/adb_host.proto)，避免 Windows 下 `devices -l` 缺少 USB 路径带来的误判。已验证 Platform-Tools 35.0.2 支持此接口；不支持的 ADB 版本会报告查询错误。

观察与手动/用例查询串行，GUI 的 300ms timer 只读缓存，不触发设备 I/O。观察和已启动 logcat 跨 Run 持续；ACTIVE 禁止人工命令、修改配置及切换观察开关。修改工具路径会停止观察，需要人工重新开启。

本插件不自动重试业务命令、不自动重启日志、不执行 kill-server。列表查询错误保留在缓存，下一观察周期仍按既定间隔查询；不执行工具/设备恢复。应用关闭时停止并等待本插件拥有的客户端进程和读取线程退出，保留系统 ADB server。

## 能力和配置

插件配置：

```yaml
plugins:
  gear.adb:
    config:
      adb_path: adb
      fastboot_path: fastboot  # 可选；留空或省略时显示查询不可用
devices:
  USB_SERIAL: {}
resources:
  ADB.main:
    type: ADB
    plugin: gear.adb
    device: USB_SERIAL
    config: {}
```

SHELL 执行 Android 上非交互、有限时长的前台 Shell 命令，记录 stdout、stderr 和 exit_code。返回非零退出码时操作失败。命令按 Android shell 语义处理，主机不使用 shell 拼接执行；命令中的管道、重定向等发生在 Android 端。

```yaml
- do:
    resource: ADB.main
    operation: SHELL
    args:
      command: getprop ro.product.model
- do:
    resource: ADB.main
    operation: PULL
    args:
      remote_path: /sdcard/device-log.txt
```

PULL 接受设备绝对路径，可拉取文件或目录。用例自动写入 `runs/<run-id>/files/gear.adb/<唯一目录>/`，事件结果中的 destination 是相对 Run 根目录的路径；手动拉取在页面指定本地目录。

持续 logcat 在 GUI 显式选择新文件并启动，边采集边保存，界面显示尾部缓存。已有文件不覆盖。Run 期间可以继续查看已启动日志，手动命令和配置编辑被禁用。Stop 遵循 GEAR 合约，等待已进入调用返回；不要用 SHELL 启动需要人工输入、无限输出或脱离会话的后台任务，持续日志使用专用 logcat 控件。

## Fastboot、模式与输出条件

`FASTBOOT` 的 `arguments` 是非空字符串列表，只填写子命令及位置参数。`timeout_s` 可选，默认 30 秒，必须大于 0 且不超过 300 秒；列表查询单次最多 10 秒。命令先核实 USB 发现列表中序列号唯一，再使用 `fastboot -s <device> -- ...` 执行。参数不能以 `-` 开头，不接受全局选项、覆盖 `-s`、`devices`、`connect` 或 `disconnect`。网络目标、空序列号、带空白或冒号的目标被拒绝。退出码非零为 `FASTBOOT_COMMAND_FAILED`，超时停止并回收本插件客户端，返回 `FASTBOOT_TIMEOUT`；stderr 的普通信息输出本身不代表失败。

为避免新版 fastboot 的列表命令主动连接历史网络目标，发现前会只读检查该工具用户目录的 `.fastboot/devices`。登记文件非空时返回 `FASTBOOT_NETWORK_CONFIGURED`；无法读取文件或确认用户目录时返回 `FASTBOOT_NETWORK_CHECK_FAILED`，均不启动 fastboot。不存在或仅有空白的文件允许发现。Windows 使用与官方工具一致的用户 Profile 查询；不会修改/删除该文件。请先在外部清理网络登记，并在本插件观察期间避免由其他进程修改登记；不提供跨进程配置锁或网络隔离容器。这项检查仅发生于显式发现、开启后的观察或 FASTBOOT 调用，不发生于静态验证/configure。

该限制依据 AOSP 的 [列表与网络连接实现](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fastboot/fastboot.cpp)、[登记文件位置](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fastboot/storage.cpp) 与 [用户目录解析](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fastboot/filesystem.cpp)。列表中的 tcp:/udp: 记录仍会过滤，目标命令也独立拒绝网络地址。

GUI 的 Fastboot 页支持子命令输入、退出码与 stdout/stderr 查看；含空格参数用引号，Windows 路径反斜杠原样保留。fastboot 路径可选，未配置/未安装时分别显示 `FASTBOOT_UNCONFIGURED` / `FASTBOOT_TOOL_ERROR`，ADB 命令仍可使用。

```yaml
- do:
    resource: ADB.main
    operation: FASTBOOT
    args:
      arguments: [getvar, product]
      timeout_s: 15
- assert:
    all:
      - resource: ADB.main
        condition: STATE_IS
        args: {state: fastboot}
    within: 10s
    every: 1s
- assert:
    all:
      - resource: ADB.main
        condition: OUTPUT_CONTAINS
        args: {command: getprop ro.product.model, text: GEAR}
```

`STATE_IS(state)` 每次 evaluate 重新查询两工具，观察当前采样点状态；state 可选 device、recovery、sideload、fastboot、offline、unauthorized、missing。只有两工具查询均成功且均无该编码时，才能确认 missing。已由一个工具确切观察到的模式不因另一个工具缺失而作废；无法确认状态时返回结构化查询错误。两工具同时报告同一编码或同工具重复编码时显示 ambiguous 并报错，需重新刷新核对。

`OUTPUT_CONTAINS(command, text)` 每次 evaluate 执行一次 Android Shell，仅检查本次 **stdout** 是否包含大小写敏感的字面文本，不是正则，也不重用旧命令输出。空文本不合法，空白文本可用。命令成功而文本不匹配时 `ok: true, satisfied: false`；非零退出码或执行错误时 `ok: false`。它和 SHELL 一样，只在 ADB device 模式执行，沿用前台命令合作式停止语义。不要用它反复执行有副作用或无限等待的命令。

命令结果与发现缓存属于同一个长期服务，Run 的 invoke/evaluate 结果会出现在 GUI 中。状态只是查询值，不表示命令后已经完成模式切换；开启观察或使用 STATE_IS 验证后续模式。此实现不修改 DSL，不提供通用变量或表达式解释器。

## 可用性与失败取证

AVAILABLE 表示绑定的 USB 设备处于 device 状态；UNAVAILABLE 表示设备缺失或处于其他状态（包括 offline / unauthorized）。两者都不执行额外 Shell，也不判断 Android 是否启动完成。ADB 程序、server 查询故障或重复 USB 序列号无法唯一定位时返回调用错误，不把工具故障当成“设备不可用”。这两个旧条件维持 ADB-only 语义（UNAVAILABLE 不等于合并模式 missing），不要求安装 fastboot。需要区分 bootloader/丢失状态时使用 STATE_IS。断言的轮询与时间窗口由 Framework 按 DSL 执行。

DIAGNOSTIC_LOGS 在用例失败后保存当前 logcat，不清空设备日志。可以在“失败取证”页选择逻辑资源，每行填写一个附加设备文件/目录的绝对路径并保存；路径写入该资源的 config.log_paths。留空时只取 logcat。取证能力需要在用例的 evidence_on_fail 中声明：

```yaml
api: gear.dsl/v1
name: adb_diagnostic_example
body:
  - assert:
      all:
        - resource: ADB.main
          condition: AVAILABLE
      within: 5s
      every: 200ms
evidence_on_fail:
  - resource: ADB.main
    evidence: DIAGNOSTIC_LOGS
```

Environment 中对应的可选配置：

```yaml
resources:
  ADB.main:
    type: ADB
    plugin: gear.adb
    device: USB_SERIAL
    config:
      log_paths:
        - /sdcard/Download/device.log
        - /data/local/tmp/logs
```

每次取证写入 Run 下独立的 evidence/gear.adb/<唯一目录>，logcat 为 UTF-8 文本，附加文件保持原内容。报告中的 Artifact 是相对 Run 根目录的文件路径。某一项失败时继续尽力采集其他项，保留已取得文件并单独记录错误，不改变用例原始失败；如果设备已断开，当前设备日志无法读取会如实报错。手动启动的持续 logcat 文件仍保留在其指定位置。Stop 等待当前调用返回后跳过尚未开始的采集。

## 验证与依赖

GUI 依赖可选 PySide6；无界面运行只需要 Framework、gear_contracts 和标准库。ADB 使用安装的 Platform-Tools，不将 ADB 二进制放入仓库。插件目录整体复制到目标 `<app-dir>/plugins/adb` 后重启 GEAR 即可发现。

测试使用模拟 ADB 客户端与真实 Qt 控件。本机当前没有连接 Android 设备；自动化测试不代表真实 USB 硬件验收。


## 宿主 GUI 私有接口

- `Workspace.resource_statuses() -> dict[resource_id, str]`：只读取保存的当前切片与服务缓存，包含未配置、绑定无效、模式及查询错误，供宿主单板总览展示。
- `Workspace.select_resource(resource_id)`：只选择已有绑定行并定位到设备页，不查询硬件、不修改配置。
- Runtime 的 `device_snapshot()` / `device_status(serial)` 均为锁保护的只读缓存；`start_monitor()` / `stop_monitor()` / `manual_fastboot(...)` 由宿主手动 worker 调用，ACTIVE 拒绝。

这些是插件与 GUI 的私有约定，不扩展公共 PluginRuntimeV1 / WorkspaceV1 合约。

新增测试位于 `plugins/adb/tests`，模拟所有硬件子进程，Qt 使用 offscreen。执行时将 TEMP、TMP、BLACK_CACHE_DIR 放到 `plugins/adb/.test-tmp`，并使用：

```powershell
.venv/Scripts/python.exe -B -m pytest plugins/adb/tests --capture=sys -p no:cacheprovider --basetemp plugins/adb/.test-tmp/cases -q
```

原有测试由主任务单独验证。本次不运行真实 adb/fastboot、不发现硬件，未安装新依赖；真实 Platform-Tools 与单板还需要现场验收。
