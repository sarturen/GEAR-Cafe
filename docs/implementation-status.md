# Framework v1 实现与验收记录

日期：2026-09-18

## 交付范围

- 核心包 gear-framework，依赖独立 gear-contracts 合约包。
- 固定目录插件发现、入口和 schema 校验、类型唯一归属。
- YAML 1.2 严格解析、Project/Environment/DSL 配置预检和覆盖缺口。
- 单工作线程执行 do/wait/assert/repeat，within/for 按冻结的时间语义执行。
- 长期插件会话、长期设备服务复用；每个 Run 只建立独立执行状态。
- submit/confirm/stop/status/subscribe，真实主机独占锁，完整收尾前禁止下一次执行。
- 失败取证、输入归档、JSONL 事件、原子报告发布和阻断状态诊断。
- GUI 中立 WorkspaceContext：完整配置切片持久化、手动操作、GUI 线程派发。
- 独立可选 Qt Workspace 加载模块，核心和 CLI 无 Qt 依赖。
- 可安装的 CLI、确定性示例插件和运行说明。

## 验证证据

本机 Windows / CPython 3.14：

- 47 项 pytest 测试通过。
- 核心源码通过 Python 3.11 语法解析；没有声称已在 Python 3.11 解释器实跑。
- 两个包均已安装；非 editable wheel 构建通过。
- 安装后的 gear CLI 经过真实插件发现、预检、人为确认输入、执行和报告发布。
- 示例 PASS，DEMO.spare 的未绑定/未执行状态保留在报告中。
- RunReport、RunEvent 的字段集合与共享接口定义一致。
- 核心导入不加载 Qt。

测试包含真实子进程锁竞争、在途调用阻塞、手动连接跨 Run 复用、静态预检中取消、
时间边界、重复和随机等待、失败取证、配置/事件/报告故障注入和错误持久化失败说明。

## 已修正的具体问题

- 清理错误现在写入仍可写的事件文件，并保留会话日志和状态记录。
- 事件文件写入失败时，订阅回调仍按递增序号交付。
- YAML 1.1 指令不能绕过 YAML 1.2 的 ON/OFF 语义。
- 事件文件打开失败进入阻断状态，但未执行的提交不产生测试结果。
- 插件会话关闭失败后保留 OS 独占直到进程退出，防止残留服务与新会话重叠。
- Qt Widget 创建只在独立 GUI 加载路径内，且必须位于 QApplication 主线程。

## 边界

本记录对应 Framework v1 基线；后续 GUI Shell 和 ADB 插件进度见 [ADB 实现记录](adb-status.md)。
真实 COM、摄像头、继电器等驱动仍未实现。
Framework v1 基线的 Workspace 桥通过队列派发测试，后续真实 Qt 控件验证单独记录。
Unix 锁回退已实现，本轮执行证据来自 Windows。
Framework 以插件遵守合约为前提，不隔离恶意插件，也不强杀不返回的驱动调用。

此前并行任务因额度限制没有启动；实现、合约核对及故障修复由主任务完成。
