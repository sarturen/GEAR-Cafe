# ADB 单板接口实施报告

日期：2026-09-19。范围仅 `plugins/adb/**`，依据 `board-resources.md` 和已有 v1 公共合约实现。未修改核心、DSL、公共合约、根测试、依赖清单或 Git 元数据；未执行真实 adb/fastboot、扫描硬件、安装依赖或提交 Git。

## 已实现

- 沿用 `devices` 登记和资源顶层 `device` 作为单板主键，ADB/fastboot 使用同一编码。静态验证明确拒绝同板重复 ADB 资源，旧配置仍保留可修正。
- 独立缓存 ADB/fastboot 查询结果与错误；合并展示 device/recovery/sideload/fastboot/offline/unauthorized/missing。只有两工具成功且均无记录才判断 missing；工具失败不能冒充离线。
- 可选 `fastboot_path`。未配置/缺失独立报错，不破坏旧 SHELL/PULL/logcat/AVAILABLE/UNAVAILABLE/取证功能。
- `FASTBOOT(arguments, timeout_s=30)`：USB 唯一序列号确认，固定 `-s <device> --`，禁网络目标与用户全局选项，时间限制 `(0,300]` 秒，非零退出码和超时结构化失败，输出可查看。
- `STATE_IS(state)` 与 `OUTPUT_CONTAINS(command,text)` 均已声明 manifest 参数 schema；后者每次取当前 Shell stdout 做字面包含校验，无 DSL 改动。
- GUI 新增 fastboot 路径、命令输入/输出页、开始/停止观察。显式开启后会话线程约每秒查询，ACTIVE 期间继续；GUI timer 只读缓存。手动/用例查询与观察由同一锁串行。
- invoke/evaluate/手动命令更新同一服务状态与输出。configure/构造不发现设备；工具路径变更停止观察；close 停止并 join 观察，end_run 保留会话采集，不保留 Run context。
- GUI 私有 `resource_statuses()` 和 `select_resource(resource_id)` 已提供。前者只读切片/缓存，后者只导航，不发起 I/O。
- 按 AOSP 当前实现，fastboot 列表也会连接 `.fastboot/devices` 中的网络登记；为满足 USB-only，发现前只读检查，无文件/空白允许，非空/不可读/无法定位用户 Profile 时明确拒绝启动查询。Windows 使用与 AOSP 一致的 SHGetFolderPathW。绝不改写该文件；官方来源和跨进程限制见 README。

## 修改文件

- `gear-plugin.yaml`
- `gear_adb/config.py`
- `gear_adb/runtime.py`
- `gear_adb/transport.py`
- `gear_adb/workspace.py`
- `README.md`
- 新增 `.gitignore`，忽略本插件测试临时目录与 Python/pytest 缓存
- 新增 `tests/conftest.py`、`tests/test_board_modes.py`、`tests/test_board_workspace.py`
- 新增本报告 `docs/implementation-report.md`

原有 `docs/board-resources.md` 和主任务实施计划未修改。

## 验证

插件新增测试 **59 项通过**，全部外部进程用受控模拟替换，Qt 控件使用 offscreen。覆盖模式区分、跨模式缓存、新旧条件、退出码/超时、网络与目标覆盖防护、工具不可用、重复身份绑定、ACTIVE 只读、Run 输出展示、观察串行/关闭，以及网络登记不存在/空白/非空/不可读/用户目录无法确认。

执行命令：

```powershell
.venv/Scripts/python.exe -B -m pytest plugins/adb/tests --basetemp=plugins/adb/.test-tmp/final --capture=sys -p no:cacheprovider -q
```

TEMP/TMP/BLACK_CACHE_DIR 限于 `plugins/adb/.test-tmp`，无字节码或 pytest cache。已安装 Black 仅用于本插件变更文件格式化，目标 Python 3.11。根原测试及跨插件集成由主任务单独验证。最终验证后已核对 `.test-tmp` 的绝对路径位于本插件内并清理，避免根集成测试复制插件时包含临时产物。

## 必要限制

- 观察默认关闭，需要用户明确开启；不自动重连、重启 adb server、重启 logcat 或重试业务命令。观察周期本身会持续尝试列表查询并显示错误。
- SHELL/OUTPUT_CONTAINS 保留既有前台合作式停止语义，不新增强制终止；只能使用有限、非交互命令。ADB Shell/PULL/logcat 仍要求 device 模式。
- Fastboot 当前只支持子命令与位置参数，不支持全局选项。列表格式按官方 `devices -l` 的 fastboot 行解析；无法解析或无可用序列号的记录报错，不猜测设备身份。
- 外部进程不能在观察过程中修改 fastboot 网络登记；本插件不提供跨进程锁或隔离容器。网络登记检查失败不影响 ADB 接口使用。
- 状态为实际查询采样值，不能把命令成功回执当成模式切换完成。合并查询并非物理同时采样，切换瞬间的重复编码会显示 ambiguous，后续观察可更新。
- 未在本机执行真实硬件验收；仍需在目标单板和实际 Platform-Tools 上验证驱动/模式切换。
