# ADB 插件实现记录

日期：2026-09-18；分支：feat/adb-plugin。

## 当前完成范围

- gear.adb 独立插件目录及 gear.plugin/v1 manifest，可复制到 app-dir/plugins 后装载。
- USB 类型确认、设备发现、序列号登记/移除、逻辑资源绑定和状态显示。
- SHELL：标准输出、错误输出、退出码；PULL：手动保存目录及 Run 内独立输出目录。
- 持续 logcat：实时保存、2000 行尾部缓存、手动启动/停止、意外退出和写盘错误显示。
- 长期 Runtime/服务复用：页面切换、end_run 不关闭日志服务；close 等待本插件进程和读取线程退出。
- 中文 PySide6 Workspace，完整配置切片落盘；ACTIVE 时锁定修改和手动操作，仍可切换设备查看缓存日志。
- 通用 gear gui 宿主：插件工作区、文件选择、预检、确认、停止、诊断、报告及异步等待在途工作后关闭。
- 示例环境、项目、用例和插件使用说明。示例 ADB.main 默认未绑定，须在页面补全。

插件生产代码只引用 gear_contracts 和自身模块；Runtime 无 Qt 依赖。GUI 专用依赖留在可选 gui extra。公共 v1 合约未修改。

## 尚需用户决定，未宣称完成

已有确认问题等待回答：

1. AVAILABLE 是否以 USB transport 状态 device 为准，offline / unauthorized / 缺失为 UNAVAILABLE；工具和 server 查询错误单独失败，不额外执行 Shell。
2. DIAGNOSTIC_LOGS 是否保存当前 logcat 并允许配置附加设备日志文件/目录，且不清空设备日志；或首版仅保存 logcat。

当前 manifest 仅声明 SHELL / PULL；这两项待决策功能尚未声明和实现。整个 ADB 功能集尚未完成验收。不得以当前测试通过推断这两项已经交付。

## 验证证据

本机 Windows、CPython 3.14、PySide6 6.11.2：

- 完整 pytest：106 项通过（原 Framework 47 项，新增 59 项）。
- ADB 传输测试通过真实子进程替代物理 adb 可执行文件，验证参数、USB/网络区分、重复序列号拒绝、离线/未授权、帧错误、查询超时、退出码、文件和日志生命周期。
- 真实 Qt 控件测试验证完整切片提交、无效配置可修正、设备身份删除保留绑定、ACTIVE 禁用手动操作、缓存查看、GUI 线程回调和安全关闭。
- 两次真实 Framework Run 复用同一插件服务；配置预检及取消不启动 ADB；单独进程确认 Runtime 不导入 Qt。
- 已复现并修复日志写入及关闭同时报错时状态不更新的问题，检查目录创建和无效已存配置加载。
- gear gui --help 通过；CLI 无 PySide6 时仍可运行，GUI 缺少依赖有明确提示。
- gear-framework wheel 构建通过，插件本身以独立目录交付。
- [完整桌面截图](images/adb-workspace.png) 已用实际 Framework、ADB Workspace 和未绑定示例环境渲染检查。
- 使用本机 Platform-Tools 35.0.2 对实际服务执行一次 USB 发现，返回空列表；当前无连接设备。未声称真实 USB Shell / 拉取 / logcat 已通过硬件验收。
- 聚焦代码审查未发现当前已实现范围的阻断问题；未将待定功能纳入“已完成”的评估。

## 实施选择

Windows 原生 USB 后端可能不给 devices -l 提供 USB 路径，因此读取 track-devices --proto-text 的首个长度帧，以 connection_type 确认 USB，并使用对应 transport ID。查询首帧等待最多 10 秒，失败明确报错，不作为设备离线；读取后关闭本插件列表客户端。

SHELL / PULL 仍遵循冻结的同步调用和合作式 Stop，不给远程命令增加强杀/自动重试。用户必须使用会自行结束的非交互前台命令。持续输出使用专用 logcat 服务。ADB server 由工具自身管理，插件不执行 kill-server。

配置失效仍可保存和编辑；设备身份不由发现结果自动增删。设备身份移除不级联修改任何资源绑定，悬空引用由预检拒绝并由用户修正。

部分并行工作曾再次遇到额度错误；已落盘代码保留并由主任务完成集成和验证，没有把代理中断视为实现完成。
