# gear.console 实现报告

日期：2026-09-19。

## 交付范围

新增代码、manifest、测试、文档与示例均位于 `plugins/console/`。没有修改 Framework、DSL、公共合约、根环境文件、根测试、其他插件或 Git 元数据。未安装依赖，未操作任何真实 COM 口，未创建单板或资源占位。

实现遵循 `doc/plugin-contract/v1.md`、`gear_contracts` 的精确接口与 `plugins/adb/docs/board-resources.md` 的业务约定。运行时不依赖 Qt，GUI 只通过本插件服务及 WorkspaceContext 工作。

## 核心实现

- `gear_console/config.py`：纯静态配置验证、COM 独占、同单板 MCU/SOC 唯一、通信默认值和用户快捷命令。
- `gear_console/service.py`：每 COM 唯一 I/O 线程、有限超时读取、排队写入、增量 UTF-8/GBK 解码、线程安全有界缓存与关闭状态。
- `gear_console/serial_win32.py`：本插件自有 ctypes Windows COM 适配器，从现有串口适配器复制后独立维护；没有导入其他插件。
- `gear_console/runtime.py`：长期会话、归档绑定、Run 游标、SEND / OUTPUT_CONTAINS / CONNECTED / TRANSCRIPT，手动和 Run 复用服务。
- `gear_console/workspace.py`：真实 Qt 多端口终端、即时提交配置、用户快捷命令、ACTIVE 控件锁定、被动缓存刷新和本插件状态定位私有接口。

每 COM 的 open/read/write/close 均在其 I/O 线程执行，不会发生同一句柄上并发读写调整 SetCommTimeouts 的问题。无自动重连、设备扫描或预检探测。

停止时取消未开始的发送；已开始的发送等待结果。teardown 可以使用停止后的同一连接。若驱动超时失效导致写入仍运行，Run 清理明确失败，防止下一 Run 与其重叠。关闭线程限时等待且保留失败状态，不谎报关闭成功。

## 已验证

本插件测试：**92 passed**（Python 3.14.7，真实 PySide6 Qt，pytest，约 6.6 秒）。

覆盖：

- 空硬件配置合法，不生成占位；独占端口、同板角色唯一、缺省/非法配置及自定义快捷命令。
- Runtime 导入与预检无 Qt/硬件 I/O；归档数据脱离可变配置；直接调用参数校验。
- 双 COM 同时接收，各一个线程；分片 UTF-8/GBK、LF/CRLF/CR、短写和断线诊断。
- 手动连接与连续 Run 复用；Run 游标不混前次文本和跨 Run 半个字符。
- 资源持有具体 PortService 实例，不能通过历史 COM 名称引用后来替换的服务；仅准备 Run 不占连接持有关系，成功显式断开才撤销对应关系。故障接收线程退出并关闭句柄后，同一 COM 可为另一资源创建独立实例，同/不同配置均验证；旧资源的状态、连接及断开操作不能影响新所有者。关闭失败仍保留旧实例用于重试，未确认关闭前不能替换。
- 真实 Qt 回归还覆盖读取故障关闭 → 页面改 COM → 另一资源复用旧 COM → 原页面连接/断开的完整路径。通信参数修改重建服务后，GUI 按服务代次重置显示游标；新旧文本等长、连接完成回调前已接收数据和先前清空显示均正确刷新。
- 有界缓存淘汰、截断诊断及证据路径约束。
- 停止前/排队中/写入中与 teardown；异常未退出写入阻止 Run 清理。
- 关闭超时、关闭失败后重试、幂等会话关闭。
- 550px 高度的真实 Qt 宿主容器不被插件撑高，外层滚动可以到达快捷命令区。
- 真实 Qt 的提交、保存失败回滚、设备号悬空显示、明确手动任务、快捷发送、清空显示不清观察缓存、ACTIVE 继续显示日志、dispose 保留连接。
- 实际 Framework 发现 manifest、验证参数 Schema、纯静态拒绝不完整配置，连续两次模拟 PASS 和一次模拟 FAIL，后者报告实际收录 TRANSCRIPT；全程只有一个假串口连接。

测试全局 WinDLL guard 禁止真实设备 API。模拟 Framework 的子进程仅放行宿主所需的命名互斥锁，仍阻断所有串口文件和配置 API。TEMP/TMP/Black 缓存、复制应用和生成的报告均限制在 `plugins/console/.test-tmp`。

## 界面检查

已用 offscreen 真实 Qt、模拟双串口及本机字体生成并查看 [Workspace 截图](images/console-workspace.png)，确认中文文本、归属表单、命令输入和输出区域可读，无内容重叠。截图明确标注模拟数据；另有 [550px 高度滚动示例](images/console-workspace-550.png)。

## 限制

- 未做 CH340 真硬件、USB 拔插、驱动异常和长时间高吞吐测试。COM3 未被访问。
- 支持 Windows COM、8 数据位、N/E/O 校验、1/2 停止位；不提供流控和端口扫描。
- CONNECTED 表示缓存中句柄/服务仍连接，不代表单板响应或物理接线正常。
- 每 COM 默认仅保留最近 262144 字符；已淘汰文本无法恢复。若未找到子串且 Run 有缓存缺口，条件返回错误而非普通 false。
- 非法接收编码使用替换字符。发送成功只表示完整字节写入，不保证目标执行语义。
- GUI 采用独立终端标签页，用户切换查看各 COM；所有已连接端口后台持续接收。
