# GEAR Cafe Handover — 2026-09-17

## 0. 用途

这是本轮 GEAR 架构 Cafe 的上下文 handover。

下一轮继续讨论时，应优先继承这里已经形成的边界，不要重新从“做一个万能测试平台”开始发散。

用户当前希望：

> 先充分 Cafe / 发散，随后再统一 prune。  
> 但一旦已经明确证明无用的抽象，应直接砍，不为了理论完整性保留。

---

# 1. 项目背景

GEAR 当前是一个硬件台架测试工具。

现有实现主要包括：

```text
继电器
串口
ADB
SSH
摄像头/屏幕检测
YAML Flow
PySide6 GUI
```

当前真实 Flow YAML 已经存在 15 种 step：

```text
log
mark
wait
relay
serial_write
serial_expect
watch
expect_watch
adb
adb_wait
ssh
expect_screen
repeat
snapshot
adb_logs
```

并存在 `mark / since / watch` 等隐式时间协议。fileciteturn7file3L141-L165

当前执行过程会在开跑前对当前设备资源做 `resolve_all` 预检，再顺序执行 step。fileciteturn7file2L105-L130

当前资源类型扩展仍偏手工：Flow step 的 `_handlers` 已经是相对成熟的扩展点，而新增设备类型需要继续修改 Bench 中的设备注册和 resolve/open/close 逻辑。fileciteturn6file7L314-L327

未来架构并不要求兼容当前 Flow DSL 的具体形式。

---

# 2. 用户真实目标

未来 GEAR 希望形成：

```text
GUI
用于台架硬件与逻辑资源配置、维护、预览、ROI 等

CLI / GUI
都可以运行一个 Test Case

Plugin
承载真实设备和项目能力

Flow DSL
只描述测试任务流

Agent
帮助开发 Framework、Plugin、编写测试
```

最关键边界：

> **Agent 永远不进入 Test Runtime。**

Agent 可以：

```text
开发 Framework
开发/迁移 Plugin
通过 Cafe 与人共同编写 Test Case
```

但正式测试执行必须由确定性传统软件完成。

一句话：

> GEAR is agent-assisted to build, but agent-free to run.

---

# 3. Agent 工作模式

## Framework Evolution

Human Cafe：

```text
需求
↓
讨论
↓
Spec
↓
Agent 修改 Framework
```

Framework 负责：

```text
DSL grammar
DSL temporal semantics
Plugin contract
runtime semantics
compatibility
contract tests
```

---

## Plugin Integration

公司真实环境里的 Agent 根据 Framework Contract 实现 Plugin。

Plugin 应通过正式 manifest / contract 告诉 Framework：

```text
有哪些 Resource Type
有哪些 Operation
有哪些 Observation / Validation
需要什么配置
```

如果现有 Framework Contract 无法表达需求：

```text
不要偷偷修改 Framework
↓
提出 Framework change request
↓
重新进入 Framework Cafe
```

---

## Test Authoring

Human 与 Authoring Agent 反复 Cafe：

```text
我要测什么
到底操作什么
观察什么
时间窗口是什么
失败意味着什么
循环多少次
```

直到人清楚理解 Test Plan。

然后 Agent 才生成 DSL。

不增加额外“Intent Verification Agent”。

人的 Test Plan 确认就是测试意图保真机制。

---

# 4. 核心分层

目前形成五层：

```text
Project Definition
Environment Configuration
Plugin Contract
Test Case DSL
Framework Runtime
```

---

# 5. Project Definition

Project 只描述逻辑资源。

例如：

```text
POWER.kl30
POWER.kl15
ADB.main
CONSOLE.main

SCREEN.center
SCREEN.cluster
SCREEN.passenger
SCREEN.hud
...
```

真实环境中主要变化来自：

```text
屏幕数量
屏幕用途
```

ADB、串口、电源等大部分配置通常比较固定。

因此不要继续设计复杂的 product variant/profile family。

---

## 已明确砍掉 Project Group / Tag

不要：

```text
cockpit_display
driver_display
all_display
tag system
group vocabulary
```

用户认为完全没必要。

所有某类型资源直接使用：

```text
SCREEN.*
```

指定资源直接写：

```text
SCREEN.center
SCREEN.cluster
```

---

# 6. Environment Configuration

Environment 由 GUI 维护真实台架映射。

例如：

```text
SCREEN.center
 -> Camera 2
 -> ROI

ADB.main
 -> device SN

CONSOLE.main
 -> COM + baudrate

POWER.kl30
 -> relay board + channel
```

屏幕的“用途”本身就是逻辑 Resource Identity。

因此 DSL 使用：

```text
SCREEN.center
```

而不是：

```text
SCREEN.1
Camera.2
```

Camera/ROI 只是 Environment 实现细节。

当前 GEAR 已经存在类似基础：screen 配置保存 camera/ROI，relay channel 保存 board/channel，console 保存 port/baudrate。fileciteturn7file7L331-L345

---

# 7. 不设计通用 Resource Sharing 模型

此前曾讨论 shared/exclusive resource，被用户明确否定。

例如继电器板天然就是：

```text
一个串口连接
多个 relay channel
```

不同 channel 可以服务不同台架环境。

这只是设备自身语义，不需要 Framework 再造：

```text
shareable
exclusive
resource ownership topology
```

摄像头如果现实里不支持一个 camera 很好地承担多个 ROI：

> 不支持就是不支持，可以多买 camera。

不要为了理论完整性建通用共享资源系统。

---

# 8. Plugin Kingdom

用户认可“Plugin 王国”模型。

Framework 只管理合同边界。

Plugin 内部：

```text
怎么实现
怎么拆 provider
怎么调用 subprocess
怎么解析输出
怎么进一步组织代码
```

都属于 Plugin 自治范围。

---

## Capability 属于 Resource Type

已经明确砍死：

> Capability belongs to Resource Type, never Resource Instance.

同类型所有实例能力完全统一。

不要动态：

```text
resource.supports(...)
instance capability bitmap
```

如果能力不同，就是不同 Resource Type。

---

# 9. Operation / Observation / Validation

不同 Resource Type 不要求长得一样。

例如：

```text
POWER
 -> ON / OFF

SCREEN
 -> LIT / BLACK / FREEZE ...

ADB
 -> REBOOT / SHELL / PULL ...

CONSOLE
 -> SEND / EXPECT / project-specific semantics

FASTBOOT
 -> EXEC / FLASH / ...
```

不要强行把所有东西抽成同一种 finite capability enum。

---

# 10. Command Executor / Escape Hatch

ADB shell、Fastboot、Console 等属于 parameterized command surface。

不要把：

```text
getprop
dumpsys
cat
setprop
...
```

每一条命令都注册成 Framework capability。

正确模型：

```text
ADB.SHELL(command)
FASTBOOT.EXEC(args)
CONSOLE.SEND(...)
```

作为 escape hatch。

高频稳定行为可以逐渐毕业为正式能力，例如：

```text
DEVICE.get_boot_mode()
DEVICE.enter_engineering_mode()
```

---

# 11. Result Validation 属于 Plugin

这是后来进一步明确的重要边界。

Framework DSL 不提供：

```text
stdout contains
regex
exit code comparison
grep
split
parser
通用文件内容检查
```

这些都属于 Plugin Validation Capability。

例如某测试要求：

```text
adb shell yyyycmd
返回值满足 regex
```

则 ADB / 项目 Plugin 可以提供：

```text
OUTPUT_MATCHES(...)
```

regex 本身是 Plugin 的参数/实现语义，而不是 Framework 通用语言能力。

如果某种检查越来越常用，就进一步抽正式能力。

---

# 12. DSL 不允许变成数据流语言

曾讨论保存 operation result：

```text
before = ...
after = ...
assert before == after
```

用户明确倾向不要。

Flow 就是任务流，不是数据流。

不要：

```text
变量
结果跨步骤传递
跨 Case 状态
通用计算
数据加工
```

否则很快出现：

```text
并发地狱
执行顺序地狱
```

---

# 13. Test Case 独立性

一个 Test Case 就是一个独立用例。

暂时不设计：

```text
Suite scheduler
Campaign scheduler
失败后自动决定下一个 Case
自动选择压测策略
```

人自己决定接下来运行什么。

用户原话的精神：

> 自己写的垃圾代码，还要求测试帮你选择压测策略🌚 我呸。

GEAR 是执行测试，不是替项目决定测试战略。

---

# 14. Setup / TearDown

允许：

```text
setup
body
teardown
```

Setup 建立当前 Case 自己需要的初态。

TearDown 只处理正常完成后的简单环境恢复。

第一阶段只实现最简单的：

```text
标准下电
标准上电
停止额外控制动作
```

暂不做：

```text
恢复旧镜像
自动重刷版本
恢复 boardid
故障救援
```

这些属于复杂 Recovery System，以后有真实需求再讨论。

---

# 15. Failure

功能性 Assertion 失败：

```text
FAIL
↓
保存事故现场
↓
当前 Case END
```

不要普通 Assertion 自己拥有：

```text
continue
retry
aggregate probability
复杂 severity/fatal policy
```

---

## 当前实现有一个值得继承的行为

现有 `on_failure` handler 即便自己执行失败，也不会覆盖原始失败原因。fileciteturn6file3L146-L154

未来应保留：

> Evidence collection failure must not erase the original test failure.

但当前任意 `on_failure` 子流程本身过于自由，未来更倾向限定成 Evidence Collection。

---

# 16. Failure Evidence

真实测试提出了：

```text
出现黑屏
或 ADB 丢失
↓
adb pull xxxlog
截图
保留现场
```

因此 Test Case 必须能表达/关联失败取证需求。

但：

> Failure Evidence 不是第二条万能 Flow。

只允许 Evidence/Capture Capability。

不允许：

```text
失败后 reboot
失败后刷版本
失败后继续跑
失败后修改 DUT
```

具体 DSL surface syntax 尚未最终决定。

---

# 17. Framework Cleanup 与 DUT TearDown 分开

Framework 自己产生的：

```text
watcher
thread
process
file handle
subscription
```

无论 PASS / FAIL 都应该清理。

但不能因为 cleanup 把 DUT 事故现场洗掉。

因此：

```text
Framework Cleanup
≠
DUT TearDown
```

---

# 18. Coverage / 不同硬件组网

用户明确要求：

> 环境差异导致的测试覆盖缩减，必须在两个地方明确展示。

第一处：

```text
执行前
```

显示：

```text
哪些能力能测
哪些目标不能测到
```

第二处：

```text
最终测试报告
```

明确记录：

```text
基于什么 Environment / Hardware Mapping
实际测了哪些能力
哪些资源没有覆盖
```

PASS 只代表：

> 实际执行过的 Assertion 通过。

PASS 不等于：

> 整个产品所有目标都覆盖了。

---

# 19. Runtime Preflight 已经简化

不要把 Runtime 设计得过于复杂。

目前核心规则只有：

## Operation 不能缺

例如 Flow：

```text
DO POWER.kl30 OFF
```

Environment 没 relay/POWER：

```text
REJECT
```

用户表达为：

> 跑电源用例，你没继电器，你爬🌚

---

## Observation 可以缺

例如项目定义六屏，当前 Environment 只配置三屏。

全量屏幕 Observation 允许只覆盖当前三屏。

但是必须显式报告 Coverage Gap。

绝不能静默当成完整 PASS。

---

## Result Validation Capability 不能缺

测试要求检查某结果，但 Plugin 没相应检查能力：

```text
REJECT
```

不能：

```text
先执行，再随便猜结果
```

---

# 20. Resource Type 全量选择

为了处理：

```text
项目六屏
台架 A 三屏
台架 B 六屏
```

允许：

```text
SCREEN.*
```

语义：

> Project 中全部 SCREEN 都是测试目标。

Environment 负责绑定当前真实可观察子集。

不要 `FOR EACH`，也不要 group/tag。

---

# 21. DSL 核心已经大幅 prune

当前目标 DSL Kernel：

```text
setup
body
teardown

DO

WAIT
WAIT RANDOM

ASSERT
AND
WITHIN ... EVERY ...
FOR ... EVERY ...

REPEAT

<Resource>
<ResourceType.*>
```

---

# 22. 时间语义

普通：

```text
ASSERT X
```

立即检查一次。

---

## WITHIN

```text
ASSERT X AND Y
WITHIN 30s
EVERY 200ms
```

截止之前周期检查。

某一次 X 与 Y 同时满足：

```text
PASS
```

超时仍未满足：

```text
FAIL
```

适用于：

```text
启动恢复
等待 ADB 出现
等待系统恢复
```

---

## FOR

```text
ASSERT X AND Y
FOR 1h
EVERY 100ms
```

持续整个时间段周期检查。

任何一次失败：

```text
FAIL
```

适用于：

```text
稳定性保持
一小时不能黑屏
ADB 一小时不能掉
```

---

## EVERY 必须显式

不提供默认 polling interval。

测试作者必须明确：

```text
EVERY 10ms
EVERY 100ms
EVERY 1s
```

因为这直接决定测试强度。

---

# 23. Plugin 可以内部事件驱动

DSL 的：

```text
EVERY 100ms
```

不意味着 camera 每 100ms 才采一帧。

SCREEN Plugin 可以一直实时检测事件。

Framework 每 100ms 只是调用一次 Validation：

> 从上次检查到现在是否保持符合条件？

因此：

```text
Framework 控制验证节拍
Plugin 控制 observation 实现
```

---

# 24. WAIT RANDOM

新增明确需求：

```text
WAIT RANDOM 5s..30s
```

用于：

```text
随机 dwell
打散测试周期
```

这是 Framework 时间原语，而不是 Plugin 能力。

---

# 25. AND 但不建通用布尔语言

支持：

```text
ASSERT A AND B AND C
```

暂不支持：

```text
OR
(A AND B) OR C
任意 NOT
复杂 precedence
```

负语义优先由 Plugin 定义：

```text
NOT_BLACK
UNAVAILABLE
```

防止 DSL 长成表达式语言。

---

# 26. REPEAT

只需要：

```text
REPEAT N
```

当前不需要：

```text
while
until
break
continue
iterator
```

长期压测可以直接：

```text
REPEAT 10000
```

---

# 27. 已做过四个思维实验

## Case 1

KL30 开机：

```text
屏幕在启动窗口内亮
ADB 在启动窗口内出现
```

随后一小时：

```text
屏幕不得黑
ADB 不得掉
```

异常：

```text
pull xxxlog
截图
FAIL
```

当前模型可以表达。

---

## Case 2

一万轮 KL15 休眠唤醒：

```text
OFF
↓
一定时间内屏幕黑 + ADB 掉

ON
↓
一定时间内屏幕亮 + ADB 恢复

保持健康
↓
下一轮
```

当前模型可以用：

```text
REPEAT
WITHIN ... EVERY
FOR ... EVERY
```

表达。

不需要状态机 DSL。

---

## Case 3

执行：

```text
adb shell xxxcmd
1000 次
每次间隔 30s
```

然后：

```text
pull xxxlog
解压
检查是否新增 xxx keyword
```

解压与日志内容验证都交 Plugin。

DSL 不新增通用文件/文本处理能力。

---

## Case 4

反复故障注入：

```text
系统 reset
中控黑
仪表不黑
短时间恢复
yyyycmd 返回结果满足某格式
↓
下一轮
```

同样可以通过：

```text
REPEAT
ASSERT ... AND ...
WITHIN ... EVERY ...
Plugin Validation
```

表达。

---

# 28. 明确砍掉的东西

下一轮除非出现真实反例，不要重新主动建议：

```text
Resource shareable/exclusive 抽象
Product variant/profile family
Project controlled group/tag
FOR EACH
IF PRESENT
通用 IF/ELSE
WHILE
变量
数据流
跨用例依赖
通用 stdout parser
通用 regex DSL
通用文件处理 DSL
状态机 DSL
mark/since/watch
generic capability bitmap
instance-level capability
Suite/Campaign 自动调度
自动测试策略
自动故障恢复
Agent runtime
```

---

# 29. 当前实现值得重构掉的东西

现有 Flow DSL 的 `mark / since / watch / expect_watch` 是当前命令式时间 bookkeeping 的实现泄露，应被未来：

```text
ASSERT ... WITHIN ... EVERY ...
ASSERT ... FOR ... EVERY ...
```

取代。

当前 DSL 还没有参数名白名单；例如错误写成 `timeouts` 或 `expects` 有可能静默使用默认行为甚至不执行预期断言。fileciteturn7file8L357-L361

未来 DSL 必须严格 schema：

> 未知字段直接拒绝，不允许静默忽略。

---

# 30. 用户的架构偏好

继续 Cafe 时要记住：

1. 不喜欢为了“未来可能有用”提前造抽象。
2. “不支持就是不支持”，宁可明确拒绝。
3. 喜欢协议化解耦，但协议必须解决真实问题。
4. Plugin 内部尽量自由。
5. Framework grammar/semantics 必须稳定、确定。
6. Agent 不进入 Runtime。
7. 测试作者不应该知道 COM/Camera/ROI 等物理细节。
8. Test Case 不应依赖其他 Test Case。
9. 人自己选择运行哪些用例，不要自动测试经理。
10. 先允许 Cafe 发散，但最终要狠狠干 prune。
11. 当某个讨论点还有真实语义没咬清楚时，可以继续同一个 Cafe，不需要为了“推进流程”强行换题。
12. 不确定时不要替真实环境脑补；应让现实场景来检验架构。

---

# 31. 下一轮最值得继续 Cafe 的开放点

目前 DSL Kernel 已经比较稳定。

尚未完全定稿、值得继续讨论的主要有：

### A. Failure Evidence 的最终 surface syntax

已经确定需要：

```text
失败时 pull log
截图
dump console
```

但还没决定：

```text
写在 Test Case 哪一层
如何引用 Evidence Capability
不同 Assertion 能否声明不同 Evidence
```

---

### B. Observation 缺失的极端情况

已经确定：

```text
Observation 可以缺
Coverage 必须显式
```

但尚未完全决定：

> 如果某条 Test Case 的 Observation 一个都绑定不到，是 REJECT、NOT COVERED，还是另一种明确状态？

唯一确定的是：

> 绝不能 PASS。

---

### C. Project / Plugin / Environment 的配置文件形态

逻辑边界已经清楚，但：

```text
manifest 长什么样
schema 如何表达
GUI 如何自动生成
camera ROI 是否 Plugin 自带 editor
```

尚未继续 Cafe。

用户此前主动要求：

> 先把 DSL 砍清楚，不急着下一个模块。

因此下一轮不要自动跳 GUI。

---

# 32. 当前一句话架构

> **Project 说“有什么”，Environment 说“怎么接”，Plugin 说“能干什么、怎么看结果”，Test Case 说“这次怎么测”，Framework 只负责确定性地绑定、校验、执行、取证和报告。**

Test DSL 则尽量保持为：

> **DO — WAIT — ASSERT — REPEAT。**