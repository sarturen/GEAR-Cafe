# GEAR

GEAR 是面向车机单板测试的插件化执行框架。Framework 负责配置、资源绑定、用例执行、互斥、事件和报告；硬件能力由独立插件通过 `gear.plugin/v1` 合约接入。

## What is implemented

- Framework：严格校验 Project、Environment 和 DSL，用例完全串行且不重叠；启动前 Preflight 只检查配置，不探测硬件。
- 基础 GUI：即使没有插件也能启动，常驻展示当前用例、运行阶段、实时事件和报告路径；用例运行时禁止人工控制，资源状态仍持续显示。
- [ADB / fastboot](plugins/adb/README.md)：USB 设备登记、normal/recovery/sideload/fastboot 状态、命令、输出和日志取证。
- [8 路 Modbus RTU 继电器](plugins/relay/README.md)：一个控制器对应一个 COM 与八个物理通路，通路可绑定为单板的 KL30、KL15 或其他资源，支持吸合、释放和状态观察。
- [CH340 MCU/SOC 串口](plugins/console/README.md)：串口归属、持续输出、命令发送和快捷命令。
- [USB 摄像头](plugins/camera/README.md)：最多六路屏幕输入、预览、单板归属、屏幕用途、ROI 和快照；暂不实现黑屏、闪黑、冻屏或花屏判断。

## Install

需要 Python 3.11 或更高版本。在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ./doc/contracts -e ".[gui,test]"
```

## Double-click GUI startup

Windows 下双击根目录的 **[Start-GEAR.cmd](Start-GEAR.cmd)**。启动脚本始终以自身目录作为应用目录，并执行：

```text
".venv\Scripts\python.exe" -m gear_framework gui --app-dir .
```

也可以在终端运行同一命令。GUI 无需预先选择用例或项目；随后可在主界面载入。

## Resource configuration

GUI 只读取和保存仓库根目录的一份 `environment.yaml`。首次启动时文件不存在会创建空环境，以后始终复用，不提供环境选择器。插件页面将物理端口绑定为逻辑资源：

- 单板设备号是 `adb devices` 第一列的序列号，也是资源顶层 `device` 的逻辑主键。
- ADB、MCU 串口、SOC 串口和每路摄像头各自独占归属于一个单板，可缺省。
- 一个继电器控制器占用一个 COM；八个通路分别配置，某个通路可绑定给某块单板并命名为 `POWER.kl30`、`POWER.kl15` 等逻辑资源，也可不绑定。
- 配置页打开后，已连接的插件会话长期存在。Preflight 只验证配置完整性；真实可用性在实际操作时体现。

完整的四插件单板配置见 [board-demo](plugins/relay/examples/board-demo/README.md)。其中 `environment.example.yaml` 仅供复制参考，不会被 GUI 自动加载，也不会覆盖根 `environment.yaml`。

## Test cases and resource references

Environment 把逻辑资源 ID 绑定到插件、单板和物理配置；Project 声明本组用例允许使用的资源 ID 与类型；Case 用同一个 ID 发起操作、条件或取证。物理 COM、继电器通路和相机 ID 只写在 Environment，不写入用例。

例如 Project 声明：

```yaml
resources:
  POWER.kl30: {type: POWER}
```

Case 按语义引用它：

```yaml
body:
  - do: {resource: POWER.kl30, operation: ON}
  - assert:
      all:
        - {resource: POWER.kl30, condition: IS_ON}
```

完整示例包含 [环境绑定](plugins/relay/examples/board-demo/environment.example.yaml)、[Project](plugins/relay/examples/board-demo/project.yaml)、[继电器循环用例](plugins/relay/examples/board-demo/relay-cycle.case.yaml)、整板观察和串口命令用例。

## CLI

命令行执行示例：

```powershell
.\.venv\Scripts\gear.exe run --app-dir examples/bench --case examples/bench/case.yaml --project examples/bench/project.yaml --environment examples/bench/environment.yaml
```

CLI 先显示绑定、覆盖缺口和 Preflight 结果，输入 `yes` 后执行。报告与归档输入写入 `<app-dir>/runs/<run-id>/`。退出码：`0` PASS，`1` FAIL，`2` STOPPED/REJECTED/DECLINED，`3` 会话启动或关闭失败。

## Plugin development

插件放在 `<app-dir>/plugins/<name>/`，重启后由 manifest 发现。插件只能依赖公共 [Plugin v1](doc/cafe/plugin-contract/v1.md) 和 `gear_contracts`，不得导入 Framework 内部实现。Runtime 与 Workspace 共享插件自己的长期服务；导入、工厂、`configure` 和 Preflight 不打开或探测硬件。

## Documentation

[文档总索引](doc/README.md) 集中提供：

- `doc/cafe/`：架构、DSL、环境模型、运行时、插件合约、失败取证和报告规范。
- `doc/contracts/`：无 Framework、驱动或 Qt 依赖的精确 Python 接口。
- `doc/development/`：总体实现状态、设计和实施记录。
- `doc/plugins/`：四个硬件插件的实施说明、GUI 记录和图片。

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖 Framework、四个插件、真实 Qt 控件、模拟子进程/串口/相机、跨插件配置与报告流程。仓库示例可在无硬件环境进行解析和配置预检。

## Hardware validation boundary

自动化测试不会枚举、打开或操作现场 ADB、COM、继电器或摄像头。真实 PL2303GT/Modbus RTU 继电器、CH340 串口、USB 摄像头、驱动稳定性、USB 带宽、拔插和长期运行仍需在目标硬件上验收。
