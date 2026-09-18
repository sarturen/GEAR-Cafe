# ADB 插件实现记录

日期：2026-09-18；分支：feat/adb-plugin。

## 当前完成范围

- gear.adb 独立插件目录及 gear.plugin/v1 manifest，可复制到 app-dir/plugins 后装载。
- USB 类型确认、设备发现、序列号登记/移除、逻辑资源绑定和状态显示。
- SHELL：标准输出、错误输出、退出码；PULL：手动保存目录及 Run 内独立输出目录。
- AVAILABLE / UNAVAILABLE：读取 USB transport 状态；工具错误、查询错误和重复序列号分别返回诊断。
- DIAGNOSTIC_LOGS：当前 logcat 加资源配置的附加文件/目录，取证失败保留成功文件及原始用例失败。
- 持续 logcat：实时保存、2000 行尾部缓存、手动启动/停止、意外退出和写盘错误显示。
- 长期 Runtime/服务复用：页面切换、end_run 不关闭日志服务；close 等待本插件进程和读取线程退出。
- 中文 PySide6 Workspace，完整配置切片落盘；ACTIVE 时锁定修改和手动操作，仍可切换设备查看缓存日志。
- “失败取证”页面按逻辑资源编辑 log_paths，支持读取和修正无效配置；资源重命名/删除后同步选择。
- 通用 gear gui 宿主：插件工作区、文件选择、预检、确认、停止、诊断、报告及异步等待在途工作后关闭。
- 示例环境、项目、用例和插件使用说明。示例 ADB.main 默认未绑定，须在页面补全。

插件生产代码只引用 gear_contracts 和自身模块；Runtime 无 Qt 依赖。GUI 专用依赖留在可选 gui extra。公共 v1 合约未修改。

## 已确认决策

2026-09-18 用户回复“可以”，确认两项推荐方案；恢复写权限后完成实现。

1. ADB-D1：AVAILABLE 以 USB transport 状态 device 为准；其他状态及缺失为 UNAVAILABLE。工具和 server 查询错误单独失败，不额外执行 Shell，不等同于 Android 启动完成。
2. ADB-D2：DIAGNOSTIC_LOGS 保存当前 logcat，并允许配置附加设备日志文件/目录；不清空设备日志。log_paths 属于每个资源的 config，默认空列表。

manifest 已声明 SHELL / PULL、AVAILABLE / UNAVAILABLE 和 DIAGNOSTIC_LOGS。已批准的软件功能集完成；真实 USB 硬件验收仍待连接设备。

取证仅在用例 evidence_on_fail 声明对应能力时执行，GUI 配置不自动修改用例。读取归档资源配置，生成唯一目录内的文件 Artifact。命令失败可保留部分输出，其他路径继续尽力采集；Stop 等待已进入调用完成后跳过后续项。

## 验证证据

本机 Windows、CPython 3.14、PySide6 6.11.2：

- 完整 pytest：138 项通过（原 Framework 47 项，ADB / GUI 共新增 91 项）。
- ADB 传输测试通过真实子进程替代物理 adb 可执行文件，验证参数、USB/网络区分、重复序列号拒绝、离线/未授权、帧错误、查询超时、退出码、文件和日志生命周期。
- 真实 Qt 控件测试验证完整切片提交、无效配置可修正、设备身份删除保留绑定、ACTIVE 禁用手动操作、缓存查看、GUI 线程回调和安全关闭。
- 两次真实 Framework Run 复用同一插件服务；配置预检及取消不启动 ADB；单独进程确认 Runtime 不导入 Qt。
- 新增真实子进程的完整插件流程：可用性断言通过、Shell 失败、logcat 取证、附加文件部分拉取失败及最终报告；原始失败保持不变，部分文件可从报告定位读取。
- 验证离线/未授权/缺失、查询故障不冒充不可用、归档路径不受后续配置对象修改影响、取证取消和本地写入失败。
- 已复现并修复日志写入及关闭同时报错时状态不更新的问题，检查目录创建和无效已存配置加载。
- gear gui --help 通过；CLI 无 PySide6 时仍可运行，GUI 缺少依赖有明确提示。
- gear-framework wheel 构建通过，插件本身以独立目录交付。
- [完整桌面截图](images/adb-workspace.png) 与 [取证配置页](images/adb-evidence.png) 已用实际 Framework、ADB Workspace 和未绑定示例环境渲染检查。
- 使用本机 Platform-Tools 35.0.2 对实际服务执行一次 USB 发现，返回空列表；当前无连接设备。未声称真实 USB Shell / 拉取 / logcat 已通过硬件验收。
- 聚焦代码审查未发现阻断问题，覆盖可用性、归档配置、部分取证结果、取消、文件路径和 GUI 配置持久化。

## 实施选择

Windows 原生 USB 后端可能不给 devices -l 提供 USB 路径，因此读取 track-devices --proto-text 的首个长度帧，以 connection_type 确认 USB，并使用对应 transport ID。查询首帧等待最多 10 秒，失败明确报错，不作为设备离线；读取后关闭本插件列表客户端。

SHELL / PULL 仍遵循冻结的同步调用和合作式 Stop，不给远程命令增加强杀/自动重试。用户必须使用会自行结束的非交互前台命令。持续输出使用专用 logcat 服务。ADB server 由工具自身管理，插件不执行 kill-server。

配置失效仍可保存和编辑；设备身份不由发现结果自动增删。设备身份移除不级联修改任何资源绑定，悬空引用由预检拒绝并由用户修正。

部分并行工作曾再次遇到额度错误；已落盘代码保留并由主任务完成集成和验证，没有把代理中断视为实现完成。
