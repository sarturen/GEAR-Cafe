# ADB USB 插件设计

## 已确认范围

用户已批准首个 ADB 插件及 GUI，连接仅涉及 USB。实现设备发现、身份登记、逻辑资源绑定、状态显示、Shell、文件拉取、持续 logcat 查看与保存，以及可用性断言和失败取证。后两项具体语义待用户答复已有问题后落地。

沿用已冻结合约：会话级 Runtime 和设备服务由 Workspace / Run 复用；configure 和 validate_config 不探测硬件；Run 全程独占，完成收尾退出后才允许下一次 Run 或手动操作。只增加当前功能需要的机制，不增加重连、重试、插件互调或网络 ADB。

## 实现结构

- plugins/adb/gear_adb：独立插件，只依赖 gear_contracts 和标准库；workspace 模块可选依赖 PySide6。
- transport.py：ADB 客户端进程与 USB 目标选择、持续日志服务。普通命令使用 argv 和有限前台命令；ADB server 持有 USB 连接，不随用例重建或停止全局 server。
- config.py / runtime.py：静态配置校验、逻辑资源到设备身份映射、合约结果与 Run 生命周期。设备断开不删除登记身份。
- workspace.py：设备与绑定编辑、手动 Shell / 拉取 / 日志；操作通过 submit_manual 排入框架工作线程，ACTIVE 时禁止编辑和手动命令。GUI 只读取日志缓存。
- gear_framework GUI shell：通用 Qt 宿主，动态装载 Workspace，提供用例/项目选择、预检、确认、停止及结果。插件不导入 Framework。

## 配置与命令

插件 ID gear.adb，资源类型 ADB。插件配置 adb_path（缺省 adb）；devices 中登记 USB 序列号；ADB.<alias> 资源以 device 字段引用序列号。资源 config 初始为空。

SHELL 参数 command 是非交互、有限前台 Android shell 命令，保留 stdout/stderr/exit_code。PULL 参数 remote_path，输出自动落入本次 Run 的 files/gear.adb/<唯一目录>；手动拉取由用户选本地目录。不会隐式创建后台远程任务，也不会自动重试。Stop 沿用合约，等待已进入调用返回。

持续 logcat 由用户显式启动并指定文件，边读取边保存，GUI 显示有限尾部缓存。日志跨用例和页面切换保留；显式停止或应用关闭时等待本插件客户端和读取线程退出。进程意外退出直接显示错误，不自动重启。

设备状态显示最近一次显式刷新结果；只有已确认的 USB transport 可以执行设备命令，排除网络与模拟器。

## 待用户确定的两项语义

1. AVAILABLE 是否仅表示 USB 设备在 devices 输出中为 device（建议），而不额外执行 Shell / 判断 Android 启动完成。
2. 失败取证是否使用当前 logcat 加可配置设备路径（建议），或首版只取 logcat。

在答复前不固化这两项契约，也不把相关功能标记为完成。

## 验证

静态校验无进程启动；模拟 ADB 进程验证参数、USB 过滤、退出码和日志生命周期；真实 Qt 控件验证配置落盘与 ACTIVE 禁用；Framework 集成验证重复 Run 复用、取证和无 Qt 的 CLI 路径。最终运行完整测试并检查界面截图。本机当前无 USB Android 设备，真实设备验收需另行执行并如实记录。
