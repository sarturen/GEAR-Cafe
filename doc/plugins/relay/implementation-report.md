# 继电器重构实现报告

日期：2026-09-19。依据用户确认的 [单板与资源业务约定](../adb/board-resources.md)，只修改 `plugins/relay/**`。未修改 Framework 核心、DSL、公共合约、根 tests、根 environment、其他插件或 Git 元数据；未安装依赖、未提交 Git、未扫描或访问硬件。

## 完成内容

1. **统一单板身份与物理分配**：顶层 `record.device` 为登记设备号；不再创建私有 device_name 身份。资源按 `controller + channel` 唯一，设备号内非空用途 role 唯一。KL30/KL15 只是建议，支持其他用途或缺省；只保存实际选定的通道，不自动生成两路占位。
2. **独立多控制器**：`plugin.config.controllers` 字典，每控制器独立 COM、串口参数与长期 RelayService。规范化后的同 COM 冲突校验失败，阻止同端口双服务打开。修改配置只释放对应控制器连接；资源归属编辑不重开连接。
3. **兼容迁移**：旧单 COM / channel-only 只读解释为 main；GUI 首次实际提交转换控制器结构，保留全部资源 ID 和原顶层 device。旧私有名称在待处理区域展示，不能静默成为真实设备号；无真实归属时，保存要求人工选设备号，也可明确解绑。选中资源保存或解绑清理私有旧字段。
4. **物理主界面**：控制器选择、串口手填、连接/断开/读状态、CH1–CH8 状态与吸合/释放、归属编辑同页。旧无 channel / 冲突资源单列，明确提供修复、解绑、删除。已有 ID 在编辑器只读保留。运行中控制与编辑锁定，导航与状态继续。
5. **状态与线程**：写回执立即更新缓存，每通道标来源；连接后默认每约 500ms 后台 01 读回，可设 0 关闭。后台与手动/Run I/O 共用每控制器事务锁，完整RTU帧串行。GUI 100ms 只读缓存，缓存锁不包围串口 I/O。轮询不保存 RunContext，故障后不自动重连，close 停止并 join，end_run 保持长期连接与轮询。
6. **宿主私有观察入口**：Workspace `resource_statuses() -> dict[rid, str]` 只缓存；`select_resource(rid)` 只导航定位，不读取硬件、不提交任务。
7. **公共能力保持**：POWER ON/OFF/IS_ON/IS_OFF 接口未改。IS_ON/IS_OFF 仍主动01读回，故障返回错误而非假OFF。Stop仍允许teardown释放。关闭与断开不发送复位或全释放。

主要变更：`gear_relay/bindings.py`、`config.py`、`runtime.py`、`transport.py`、`workspace.py`；插件自有绑定/模型/运行时/协议/Qt/宿主集成测试；README 与 GUI 文档及新截图。`serial_win32.py` 的串口映射与底层实现未变。

## 验证结果

最终执行命令（工作目录 framework-dev）：

```powershell
$env:TEMP = (Resolve-Path plugins/relay/.test-tmp).Path
$env:TMP = $env:TEMP
$env:BLACK_CACHE_DIR = Join-Path $env:TEMP 'black-v2'
.venv/Scripts/python.exe -B -m black --target-version py311 plugins/relay/gear_relay plugins/relay/tests
.venv/Scripts/python.exe -B -m pytest plugins/relay/tests --capture=sys -p no:cacheprovider --basetemp=plugins/relay/.test-tmp/v2-final
```

**152 项通过，2.10 秒。** 分类如下：

| 范围 | 项数 |
| --- | ---: |
| 资源分配与兼容手控 | 15 |
| 新业务、多控制器、运行中轮询串行 | 9 |
| 运行时归档/条件/停止/长连接 | 29 |
| Modbus RTU | 36 |
| 假 Win32 串口 | 21 |
| 真实 Qt 控件 + 假运行时 | 37 |
| 独立进程真实 Framework + 假串口 | 5 |

- 五种宿主集成场景：旧 channel-only、旧私有名称兼容、两独立控制器且资源关联同一登记设备号、缺串口被预检拒绝、无 channel 旧占位被预检拒绝。两次执行复用同一模拟串口，每控制器仅打开一次并在会话关闭时释放。
- 新测试覆盖跨控制器通道复用、同板同用途冲突、COM冲突、ID/device迁移保留、不建立设备、不生成缺省通路、后台轮询在Run中持续、同COM无帧重叠、故障无重连、close后无继续写入、不同控制器连接隔离。
- 保留 CRC/功能码/回显/从站/异常帧/短写短读/超时/停止/关闭失败等回归，修复重构引入的“关闭失败后旧串口配置仍保留”问题。
- 全部插件测试禁止真实 `ctypes.WinDLL`。独立进程集成改用假 HostLock，不再加载真实 DLL；串口始终由模拟工厂注入。
- Black 针对 py311 检查，15 个 Python 文件最终无需改动。七个生产模块经 `ast.parse(feature_version=(3,11))` 通过；实际测试解释器为 Windows CPython 3.14.7，并未在 Python 3.11 解释器实跑。
- [物理页截图](images/relay-physical.png)、[旧数据页截图](images/relay-migration.png) 来自 1060×900 的真实 Qt 控件离屏渲染，模拟所有硬件状态；已实际查看图像，确认八路控制、来源文字、归属表单、未绑定与删除入口可读。
- 旧方案相关测试按新业务语义替换，不以旧“自动两路 + 私有名称”行为作为要求。协议、长期连接、停止、纯校验与重要GUI回归保留。
- 只读沙箱中的一次全套测试最初因无法创建 .test-tmp 子目录报三个 fixture PermissionError；限定插件临时目录的获准执行后全套通过，非产品故障。全部临时写入、Black缓存、模拟环境及报告均留在插件 `.test-tmp`。
- **根 tests 未在此子任务执行**：父任务要求由其统一顺序执行，避免 Windows 全局宿主锁冲突；根测试文件没有修改。

## 已知条件与限制

- **旧缺 channel 记录仍为 INCOMPLETE**。当前核心按被引用插件完整切片预检，即使某用例不引用旧占位，也需先在GUI明确补齐或删除它。没有静默清理、假标VALID或绕过预检。解绑单板不消除缺channel；新流程不再制造占位。
- 合法的旧 channel-only 可以继续执行；旧私有名称只是迁移提示，不能产生单板身份。资源归属使用登记设备号，不要求当下ADB在线。
- 串口参数保存与验证不打开设备；宿主后续 configure 可关闭受影响旧连接。运行时首次实际操作可打开已配置端口；故障后仍要求人工显式连接。
- GUI刷新显示当前最近缓存；比100ms更快的切换可在两次界面刷新之间发生。每次成功写回执都立即更新缓存，运行步骤/报告仍由宿主管理，不增加插件自己的事件或报告合约。
- `read_coils` 是继电器线圈寄存器读回，`write_acknowledgement` 是命令回执，均不能证明触点或线束电气状态。
- 没有硬件验收；真实八路板的地址、功能码支持、串口参数及接线效果仍须设备到位后确认。COM3 从未访问。


后续八按钮紧凑布局、Demo配置和327项复核记录见 [GUI调整记录](compact-gui-update.md)。
