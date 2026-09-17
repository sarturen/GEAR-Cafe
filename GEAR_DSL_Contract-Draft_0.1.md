# GEAR DSL Contract — Cafe Draft 0.1

## 0. 目标

本文件现作为 GEAR Cafe 的唯一当前结论输出，不再仅限于 DSL；DSL 是当前最成熟的结论部分。

GEAR 的 Flow DSL 用于描述一个**独立、可单独执行的测试用例**。

它只回答：

> 对哪些逻辑资源，执行什么操作，等待多久，验证什么，重复多少次。

DSL 不描述物理连接，不承担数据处理语言、脚本语言、状态机语言、测试调度系统的职责。

设计原则：

> 能少一个语法原语就少一个。  
> Framework 负责时序和执行；Plugin 负责业务能力；Environment 负责真实硬件绑定。

---

# 1. 整体分层

GEAR 当前目标模型分为五层。

## 1.1 Project Definition

定义：

> 这个项目逻辑上有什么资源。

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
SCREEN.rear_left
SCREEN.rear_right
```

Project Definition 不包含任何真实硬件参数。

不出现：

```text
COM7
Camera 2
ROI
ADB serial number
Relay channel
```

### 不支持受控资源集合

明确不引入：

```text
cockpit_display
driver_display
all_display
tag/group
```

如果测试需要所有某类型资源，直接选择 Resource Type 的全集：

```text
SCREEN.*
```

如果只测试特定资源，则明确引用：

```text
SCREEN.center
SCREEN.cluster
```

---

# 2. Environment Configuration

Environment 定义：

> 当前这一套真实台架，如何实现 Project 中的逻辑资源。

例如：

```text
SCREEN.center
    -> Camera 2
    -> ROI ...

SCREEN.cluster
    -> Camera 3
    -> ROI ...

ADB.main
    -> serial ABC123

CONSOLE.main
    -> COM7
    -> 115200

POWER.kl30
    -> RelayBoard A
    -> Channel 3
```

Environment 由 GUI 维护。

因此 Flow 永远不需要知道：

```text
Camera 编号
ROI
COM 口
波特率
ADB SN
继电器板号
继电器通道
```

---

# 3. Plugin Contract

Plugin 定义：

> 一种 Resource Type 能执行什么、能观察什么、能怎样验证结果。

Capability 属于 **Resource Type**，不属于 Resource Instance。

也就是说：

```text
ADB.main
ADB.secondary
```

只要类型都是 `ADB`，Capability 集合必须完全一致。

实例之间只能在以下方面不同：

```text
物理绑定
配置参数
当前是否在线
```

不允许：

```text
ADB.main 支持 reboot
ADB.secondary 不支持 reboot
```

如果两个实例实际具有不同能力，它们就不应该冒充同一个 Resource Type。

---

## 3.1 Operation

例如：

```text
POWER:
    ON
    OFF

ADB:
    REBOOT
    SHELL
    PULL
    COLLECT_LOG

FASTBOOT:
    ...
```

Flow 通过统一的 `DO` 调用 Operation。

Framework 不理解 Operation 的具体业务意义。

---

## 3.2 Observation / Validation

例如：

```text
SCREEN:
    LIT
    BLACK
    NOT_BLACK
    FREEZE
    HEALTHY

ADB:
    AVAILABLE
    UNAVAILABLE
    COMMAND_SUCCESS
    某种输出格式验证
    某种业务状态验证
```

具体如何检测、解析、判断，由 Plugin 实现。

Framework 不提供通用：

```text
stdout contains
stdout regex
exit_code == ...
grep
split
parse int
file contains
```

如果某个测试需要 regex 检查，可以由对应 Plugin 暴露例如：

```text
OUTPUT_MATCHES(...)
```

至于内部是否使用 regex，是 Plugin 的事情。

---

## 3.3 Escape Operation

允许 Plugin 提供底层逃生接口，用于“不更新插件也能快速支持临时用例”。

典型例子：

```text
ADB.SHELL(...)
FASTBOOT.EXEC(...)
CONSOLE.SEND(...)
```

Escape Operation 解决：

> 快速把命令发出去。

它不意味着 Framework 获得通用结果解析能力。

若结果需要验证，对应 Validation Capability 仍必须由 Plugin 提供。

高频、稳定、业务意义明确的逃生用法，可以后续“毕业”为正式 Operation / Validation。

---

# 4. Test Case

一个 Test Case 就是一个完整、独立的测试用例。

结构：

```text
Test Case
├── setup
├── body
└── teardown
```

Test Case 不依赖其他 Test Case。

禁止：

```text
Case A 输出变量给 Case B
Case B 要求 Case A 先跑
共享全局测试状态
根据上一条 Case 结果决定下一条 Case
```

任何 Case 都应该可以被人单独选择执行。

---

# 5. Setup

`setup` 用于建立本用例所需要的初始状态。

例如：

```text
标准上电
进入测试模式
准备故障注入条件
```

Setup 属于当前 Test Case，不依赖前一个用例留下的状态。

---

# 6. Body

`body` 是真正的测试过程。

目前核心 DSL 原语只保留：

```text
DO
WAIT
WAIT RANDOM
ASSERT
AND
WITHIN ... EVERY ...
FOR ... EVERY ...
REPEAT
```

---

# 7. TearDown

`teardown` 目前只负责：

> Test Case 正常完成后的简单环境恢复。

第一阶段只考虑最简单的正常恢复，例如：

```text
标准下电
标准上电
停止测试额外启动的控制动作
```

暂不支持自动故障恢复：

```text
失败后替换回旧镜像
自动刷大版本
自动恢复 boardid
自动进入烧录模式
自动修复 DUT
```

如果 Test Case 已经异常失败，应优先保留事故现场，而不是自动执行可能破坏现场的 DUT 恢复动作。

Framework 自己的内部资源清理不属于 TearDown。

---

# 8. DO

调用 Resource 的 Operation。

概念形式：

```text
DO <resource> <operation> [arguments]
```

例如：

```text
DO POWER.kl30 OFF
DO POWER.kl30 ON

DO ADB.main REBOOT
DO ADB.main SHELL "xxxcmd"
DO ADB.main PULL "xxxlog"
```

`DO` 属于 Framework Grammar。

`OFF / REBOOT / SHELL / PULL` 属于 Plugin Vocabulary。

---

# 9. WAIT

固定时间等待：

```text
WAIT 5s
WAIT 30s
WAIT 1h
```

这是纯 Framework 时间语义。

---

# 10. WAIT RANDOM

随机时间等待：

```text
WAIT RANDOM 5s..30s
```

随机等待属于测试流程本身，因此由 Framework 提供。

典型用途：

```text
随机 dwell
打散固定测试周期
模拟不同停留时间
```

---

# 11. ASSERT

瞬时检查：

```text
ASSERT <condition>
```

例如：

```text
ASSERT SCREEN.center LIT
ASSERT ADB.main AVAILABLE
```

Condition 本身来自 Plugin。

Framework 不理解 `LIT`、`AVAILABLE` 的具体实现。

---

# 12. AND

允许多个 Condition 组成同一个 Assertion：

```text
ASSERT A AND B AND C
```

含义：

> 这一组条件共同构成一个测试要求。

目前只需要简单 `AND`。

暂不引入：

```text
OR
复杂括号
通用 NOT 运算符
布尔表达式优先级
```

需要负语义时，优先由 Plugin 提供明确的 Validation：

```text
NOT_BLACK
UNAVAILABLE
NOT_ENTER_FASTBOOT
```

---

# 13. WITHIN ... EVERY ...

语义：

> 在指定截止时间内，按照明确周期反复检查；只要某一次整个 Assertion 成立，即 PASS。

概念形式：

```text
ASSERT A AND B
WITHIN <duration>
EVERY <interval>
```

例如：

```text
ASSERT SCREEN.center LIT
   AND ADB.main AVAILABLE
WITHIN 30s
EVERY 200ms
```

近似底层语义：

```text
deadline = now + 30s

while now < deadline:
    check A
    check B

    if A AND B:
        PASS

    delay 200ms

FAIL
```

适合：

```text
开机后一定时间内亮屏
ADB 一定时间内出现
故障后短时间内恢复
```

---

# 14. FOR ... EVERY ...

语义：

> 在整个持续时间内，按照明确周期检查；每一次都必须满足。

概念形式：

```text
ASSERT A AND B
FOR <duration>
EVERY <interval>
```

例如：

```text
ASSERT SCREEN.center NOT_BLACK
   AND ADB.main AVAILABLE
FOR 1h
EVERY 100ms
```

近似底层语义：

```text
deadline = now + 1h

while now < deadline:
    check A
    check B

    if NOT (A AND B):
        FAIL

    delay 100ms

PASS
```

适合：

```text
一小时不能黑屏
一小时 ADB 不能消失
持续保持健康状态
```

### EVERY 必须显式声明

只要使用：

```text
WITHIN
FOR
```

就必须同时明确：

```text
EVERY
```

不提供隐藏默认值。

原因：

```text
FOR 1h EVERY 10ms
```

与：

```text
FOR 1h EVERY 10s
```

代表完全不同的测试强度。

---

# 15. Observation 的内部实现

`EVERY` 表示 Framework 的 Validation 节拍，不要求 Plugin 本身使用轮询。

例如 SCREEN Plugin 可以一直实时处理摄像头帧并缓存事件。

那么：

```text
ASSERT SCREEN.center NOT_BLACK
FOR 1h
EVERY 100ms
```

可以解释为：

> Framework 每 100ms 向 Plugin 查询一次，自上次检查以来是否发生过不允许的黑屏事件。

Plugin 内部可以：

```text
轮询
事件订阅
缓存状态
持续检测
```

Framework 不关心。

---

# 16. REPEAT

有界重复：

```text
REPEAT 10000:
    ...
```

用于：

```text
上下电压测
休眠唤醒压测
故障注入循环
命令重复执行
```

目前只支持明确的次数。

暂不引入：

```text
WHILE
break
continue
无限循环
复杂 iterator
```

---

# 17. Resource Type 全量选择

允许直接表达 Project 中某 Resource Type 的完整目标集合：

```text
SCREEN.*
```

其全集来自 Project Definition，而不是当前 Environment。

例如 Project 有：

```text
SCREEN.center
SCREEN.cluster
SCREEN.passenger
SCREEN.hud
SCREEN.rear_left
SCREEN.rear_right
```

当前 Environment 只配置前三块，则 Framework 可以实际验证：

```text
center
cluster
passenger
```

并明确报告未覆盖：

```text
hud
rear_left
rear_right
```

不需要：

```text
IF PRESENT
FOR EACH
资源 tag/group
```

---

# 18. Preflight

Runtime 在执行前做能力检查。

## 18.1 Operation 不能缺

如果 Flow 要执行：

```text
DO POWER.kl30 OFF
```

而：

```text
POWER.kl30 不存在
```

或 Plugin 根本没有 `OFF` Operation：

```text
REJECT
```

测试无法成立，不允许硬跑。

---

## 18.2 Observation 可以缺

Observation 对应的 Environment 覆盖可以不完整。

例如全量屏幕测试目标为六屏，而当前 Environment 只接三屏。

允许执行当前可覆盖部分，但必须：

1. 执行前明确显示哪些目标能测、哪些不能测。
2. 报告永久记录本次 Environment 和实际 Coverage。
3. 未覆盖目标绝不能被包装成 PASS。

对于“一个 Observation 都绑定不到”的最终行为，目前尚未单独定死；至少绝不能静默 PASS。

---

## 18.3 Validation Capability 不能缺

如果 Test Case 要求某种结果检查，而 Plugin 没有相应 Validation Capability：

```text
REJECT
```

不能：

```text
命令执行成功
→ 不知道结果对不对
→ 姑且 PASS
```

---

# 19. Failure

普通功能性 Assertion 一旦失败：

```text
Assertion FAIL
↓
当前 Test Case FAIL
↓
收集事故现场
↓
停止当前 Test Case
```

目前不支持：

```text
Assertion 失败以后继续当前 Test Case
自动 retry
自动修改测试策略
自动选择下一条 Case
```

如果测试目的本身是统计某种异常概率，应写一个专门的统计/观察型用例，而不是把普通功能 Assertion 变成万能策略系统。

---

# 20. Failure Evidence

测试可以要求失败后保存特定事故证据，例如：

```text
ADB pull xxxlog
SCREEN screenshot
CONSOLE dump
```

这些动作应由 Plugin 提供专门的 evidence/capture capability。

Failure Evidence 不是任意 `on_failure` Flow。

不允许借它执行：

```text
刷机
重启恢复
修改 DUT 状态
继续测试
```

目的只有一个：

> 保存事故现场。

具体 surface syntax 尚未最终确定。

---

# 21. Framework Cleanup

Framework Cleanup 不进入 DSL。

无论 PASS / FAIL，Framework 都必须清理自己创建的运行资源，例如：

```text
watcher
内部订阅
辅助进程
线程
文件句柄
临时对象
```

但 Cleanup 不应该破坏 DUT 事故现场。

---

# 22. 明确不支持的 DSL 能力

目前明确砍掉：

```text
变量
跨步骤通用数据流
跨用例数据
IF / ELSE
WHILE
break / continue
函数
数组/map 通用操作
字符串加工语言
数学表达式语言
通用 regex DSL
通用文件处理 DSL
通用 stdout/stderr 解析
状态机 DSL
mark
since
watch
expect_watch
受控资源 group/tag
Suite/Campaign 自动调度
测试策略自动选择
故障自动恢复
Instance-level Capability
Plugin 自定义 DSL grammar
```

如果以后现实需求真正证明其中某项必要，再单独 Cafe。

---

# 23. 核心 DSL 最终骨架

目前可以压缩为：

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

Plugin 提供 Vocabulary。

Project 提供 Logical Resources。

Environment 提供 Physical Binding。

Framework 提供确定性的时序与执行语义。

---

# 24. 四个思维实验的覆盖情况

## Case 1：KL30 上电 + 一小时稳定

可表达：

```text
DO POWER.kl30 ON

ASSERT SCREEN.center LIT
   AND ADB.main AVAILABLE
WITHIN startup_timeout
EVERY startup_interval

ASSERT SCREEN.center NOT_BLACK
   AND ADB.main AVAILABLE
FOR 1h
EVERY stability_interval
```

失败后：

```text
ADB xxxlog evidence
SCREEN screenshot
```

由 Failure Evidence Capability 完成。

---

## Case 2：一万轮休眠唤醒

可表达：

```text
REPEAT 10000:
    DO POWER.kl15 OFF

    ASSERT SCREEN.center BLACK
       AND ADB.main UNAVAILABLE
    WITHIN off_timeout
    EVERY check_interval

    WAIT 5s

    DO POWER.kl15 ON

    ASSERT SCREEN.center LIT
       AND ADB.main AVAILABLE
    WITHIN startup_timeout
    EVERY check_interval

    ASSERT SCREEN.center HEALTHY
       AND ADB.main AVAILABLE
    FOR dwell
    EVERY check_interval
```

不需要状态机 DSL。

---

## Case 3：shell 1000 次 + 日志校验

可表达：

```text
REPEAT 1000:
    DO ADB.main SHELL "xxxcmd"
    WAIT 30s

DO ADB.main PULL "xxxlog"
DO LOG.xxx UNPACK

ASSERT LOG.xxx <project-specific-validation>
```

解压、日志新增关键字、格式判断都属于 Plugin Capability，不增加通用 DSL。

---

## Case 4：故障注入恢复

可表达：

```text
REPEAT N:
    DO FAULT.xxx INJECT

    ASSERT SYSTEM.xxx RESET_OCCURRED
       AND SCREEN.center BLACK
       AND SCREEN.cluster NOT_BLACK
    WITHIN fault_window
    EVERY check_interval

    ASSERT SYSTEM.xxx RECOVERED
    WITHIN recovery_timeout
    EVERY check_interval

    DO ADB.main SHELL "yyyycmd"

    ASSERT ADB.main <yyyycmd-result-validation>
```

下一轮只有在当前轮全部通过后才自然进入。

---

# 25. 一句话定义

GEAR DSL 是：

> **一个不携带物理硬件细节、不承担通用编程能力，只用于描述独立测试用例中“操作—等待—验证—重复”的确定性任务流语言。**
