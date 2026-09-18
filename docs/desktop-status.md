# 基础 GUI 实现与启动约定

日期：2026-09-18；分支：feat/adb-plugin。

## 用户要求与当前行为

用户明确要求：“搞一个基础的 GUI，哪怕没有任何插件也有入口 GUI。环境 yaml 只做一份，默认载入。”这项要求替代 a848cbe 中启动时选择环境文件的流程。

- 双击根目录 Start-GEAR.cmd，直接进入统一主窗口。首页始终存在，项目/用例选择、预检、确认、停止、诊断和报告属于基础 GUI。
- plugins 目录缺失或为空时仍可正常打开和关闭，首页显示尚未安装插件。插件工作区只作为额外标签页加入，不拥有应用入口。
- GUI 只使用 `<app-dir>/environment.yaml`，不再提供环境文件选择或 `gear gui --environment` 参数。
- 首次启动缺少该文件时，创建符合 gear.environment/v1 的空环境：name 为 GEAR，plugins、devices、resources 均为空映射；不预置 ADB 或任何其他插件资源。
- 已有文件不覆盖。各插件通过现有 WorkspaceContext 将配置保存到同一个文件，下次启动自动读取；已有格式错误按原诊断失败，不静默重置。
- 根目录 environment.yaml 是本机运行配置，已加入 Git 忽略。examples 中的 Environment 仅供独立 CLI/API 演示，GUI 不加载它们。
- `gear run` 和公共 Framework 合约的显式环境接口保持原样；本次变更不涉及长期设备服务、配置预检、执行独占或安全关闭语义。

## 使用

安装 README 中的基础及 GUI 依赖后，双击 Start-GEAR.cmd；或者运行：

```powershell
.\.venv\Scripts\gear gui --app-dir .
```

首次配置 ADB 时，切换 gear.adb 标签，设置程序路径、登记 USB 序列号并新增逻辑资源绑定。例如运行 examples/adb 中的项目和用例，需要在页面创建 ADB.main。

## 验证

Windows / CPython 3.14 / PySide6 6.11.2：

- 完整 pytest：141 项通过；基础桌面相关 11 项通过。
- 缺失及空 plugins 目录均实际构造 Framework 和 Qt 主窗口，首页及基础输入控件可用。
- 首次空配置创建、已有配置原样保留、无启动文件选择框、无效配置不被覆盖均有测试。
- 原插件工作区派发、预检确认、完全收尾前禁止后续操作、等待在途工作后关闭等回归通过。
- 从不相关工作目录实际执行 Start-GEAR.cmd，直接打开首页和 gear.adb 标签，加载根目录 environment.yaml 后正常关闭。
- 独立无插件目录实际启动、自动创建环境并正常关闭。两种真实 Qt 界面已渲染并检查：[基础首页](images/gear-home.png)、[无插件首页](images/gear-home-empty.png)。
- 聚焦代码审查未发现需要修复的重要问题。

这些验证未执行真实 USB 硬件命令；ADB 硬件验收范围仍见插件实现记录。
