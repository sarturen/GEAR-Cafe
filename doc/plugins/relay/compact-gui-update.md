# 本轮 GUI 调整与 Demo 验证

2026-09-19，根据用户反馈实施。

- 总 GUI 四个页签改为 ADB / 继电器 / 串口 / 摄像头；插件注册 ID 和合约保持不变。
- 继电器八行表格改为一排八个 checkable 按钮，按下为吸合、弹起为释放；未知明确标注；操作等待期间保持缓存状态，失败不残留乐观状态。
- 串口参数、控制器管理和资源绑定默认折叠；完整设备号/资源 ID 可悬停查看。离屏截图检查常用控制区 1060×340。
- 添加 [完整资源 Demo](../../../plugins/relay/examples/board-demo/README.md)：六类资源环境样例、项目声明、继电器循环、整板观察、串口命令/输出校验。示例不覆盖默认环境。
- 修正摄像头 README 旧例的非 ASCII case name，保持现有 DSL 的名称约束。

验证：继电器155项 + 根回归141项 + 摄像头/总GUI31项，共 **327项通过**。四插件真实Framework/Qt模拟联调继续通过：2 PASS + 1预期FAIL及归档取证，所有硬件替换为fake。3个新示例均通过现有 Registry/prepare 静态预检，并显式禁止实际 WinDLL 和工具进程。git diff --check通过。

改动限插件目录与获准的 src/gear_framework/desktop.py。未改核心、DSL、根测试、根 environment.yaml；没有访问真实设备，没有Git提交或推送。
