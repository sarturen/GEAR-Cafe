# ADB USB 插件

插件 ID：`gear.adb`；资源类型：`ADB`。通过 GEAR v1 合约装载，Runtime 不依赖 Qt 或 Framework 内部代码。

## 启动 GUI

安装 Python 3.11+ 和 Android SDK Platform-Tools。在仓库根目录执行：

```powershell
.\.venv\Scripts\python -m pip install -e ./doc/contracts -e ".[gui,test]"
.\.venv\Scripts\gear gui --app-dir . --environment examples/adb/environment.yaml --project examples/adb/project.yaml --case examples/adb/case.yaml
```

第一次打开后，在 ADB 页面填入 adb 可执行文件的路径（在 PATH 中时填 adb），显式刷新 USB 列表并登记序列号，然后把 ADB.main 绑定到该序列号。也可直接手动登记暂未连接的设备。

配置修改实时保存到 Environment；示例初始不指定设备，因此预检会提示补全绑定。预检只检查配置，确认执行后才发送命令。点击“停止”后，当前调用及收尾完成前不会开放下一次执行。

## 生命周期与 USB 范围

整个应用会话仅创建一个 ADB Runtime 和服务。Workspace 和所有用例共享该服务；切换页面、结束用例不会停止正在采集的 logcat。普通 adb 客户端命令会退出，但 USB 连接由长期运行的 ADB server 持有，不为每个用例重建连接。

只发现、选择和操作已确认是 USB 的 transport，不支持网络连接、无线配对或模拟器。设备离线、拔出后仍保留已登记身份和资源绑定。显式移除登记身份也不级联删除资源绑定，需要人工修正悬空引用。状态列表表示最近一次显式刷新结果。

使用 ADB 的 `track-devices --proto-text` 第一帧读取连接类型，再关闭本插件的列表客户端；不会维持后台设备轮询。选择设备时使用已核实的 USB transport ID。该方式依据 [ADB 官方连接类型定义](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/proto/adb_host.proto)，避免 Windows 下 `devices -l` 缺少 USB 路径带来的误判。已验证 Platform-Tools 35.0.2 支持此接口；不支持的 ADB 版本会报告查询错误。

本插件不自动重试命令、不自动重启日志、不执行 kill-server。应用关闭时停止并等待本插件拥有的客户端进程和读取线程退出，保留系统 ADB server。

## 能力和配置

插件配置：

```yaml
plugins:
  gear.adb:
    config:
      adb_path: adb
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

## 可用性与失败取证

AVAILABLE 表示绑定的 USB 设备处于 device 状态；UNAVAILABLE 表示设备缺失或处于其他状态（包括 offline / unauthorized）。两者都不执行额外 Shell，也不判断 Android 是否启动完成。ADB 程序、server 查询故障或重复 USB 序列号无法唯一定位时返回调用错误，不把工具故障当成“设备不可用”。断言的轮询与时间窗口由 Framework 按 DSL 执行。

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
