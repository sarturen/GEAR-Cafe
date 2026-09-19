# USB 摄像头插件（gear.camera）

本插件实现 GEAR v1 的 SCREEN 资源。每个输入独占归属于一个已登记单板设备号，单板最多六个摄像头；屏幕用途由用户填写，例如中控屏、仪表屏、副驾屏。没有连接摄像头的单板无需创建占位资源。

本次提供：输入发现、长期采集预览、资源归属、鼠标 ROI 选择、采集状态事件、STREAMING 条件和 SNAPSHOT 取证。**不包含黑屏、闪黑、冻屏或花屏检测，也不从画面推断测试结果。**

## 使用

插件随现有 GEAR GUI 的插件发现机制加载，不需要修改核心或 DSL。运行环境沿用 GUI 已有的 PySide6 / Qt Multimedia；无新增全局依赖。默认唯一 environment.yaml 由宿主加载和保存。

1. 在 ADB 页登记单板设备号（可离线登记）。编号本身是主键，与 ADB 是否在线无关。
2. 摄像头页点击“刷新输入”；页面构造、保存配置、预检均不枚举相机。
3. 选择输入，点击“打开预览”，确认真实屏幕。未绑定的输入也可以先预览。
4. 填写 SCREEN 别名，选择单板，填写屏幕用途，点击“分配资源”。已保存资源的归属、用途和 ROI 修改自动保存。
5. 在预览画面上拖动矩形选择 ROI；“ROI 恢复全画面”重置。ROI 按图像归一化坐标保存，黑边不会算入 ROI。
6. “已存资源”可选择未完成或重复的记录，修正或移除；保留 INCOMPLETE/INVALID 的明确诊断，不隐式丢弃记录。
7. Run 期间禁止人工开关、发现和编辑，页面仍可切换观察输入，预览及采集事件继续更新。

打开后采集跨多个用例持续存在，end_run 不关闭相机。关闭输入、移除最后一个使用该输入的绑定、退出 GEAR 时释放对应输入。设备故障显示原因并清除旧画面，业务调用不会自动重连；用户明确点击“打开预览”可重开，采集进程退出也可人工恢复。

## 配置（写入同一份宿主环境）

插件 config 为 {}。资源示意：

```yaml
SCREEN.center:
  type: SCREEN
  plugin: gear.camera
  device: BOARD001
  config:
    camera_id: <由“刷新输入”取得的完整输入ID>
    role: 中控屏
    roi: {x: 0.1, y: 0.1, width: 0.8, height: 0.8}
```

camera_id 是 Qt 原始输入 ID 字节的十六进制字符串，不是扫描顺序或摄像头索引。换接口、驱动或系统后应重新核对输入绑定。role 在同一单板内唯一；camera_id 不能重复分配；每板最多六条 SCREEN 资源。device、camera_id、role 缺失为 INCOMPLETE，非法 ROI、重复输入或超过六路为 INVALID。未配置 roi 时使用全画面。

## 合约能力

| 类别 | 名称 | 语义 |
|---|---|---|
| 条件 | STREAMING | 无参数。只检查共享缓存是否在最近 3 秒有有效画面；不会隐式开启输入。未打开为不满足，采集故障为错误。 |
| 取证 | SNAPSHOT | 无参数。复用现有输入；未打开时才开启，最多等待 3 秒取得画面。输出完整 frame.jpg 与包含 device、role、camera_id、尺寸、ROI 的 frame.json。 |
| 操作 | 无 | 本版不提供图像判断、设备重启或控制命令。 |

取证文件放在本次报告目录的 evidence/gear.camera/<唯一ID>/，供既有 evidence_on_fail 使用。ROI 本次作为配置和取证元数据，不裁剪图片、不执行检测。

```yaml
api: gear.dsl/v1
name: screen-input-check
description: 检查已打开的屏幕输入
body:
  - assert:
      all:
        - resource: SCREEN.center
          condition: STREAMING
      within: 3s
      every: 200ms
evidence_on_fail:
  - resource: SCREEN.center
    evidence: SNAPSHOT
```

## 实现边界

Runtime、配置校验和缓存层只依赖标准库；工厂不导入 Qt、不启动进程。显式发现/打开或快照取证时，插件才启动一个私有无窗口 Qt 采集进程，在其事件循环中持有 QCamera / QMediaCaptureSession / QVideoSink。多输入共用该进程，JPEG 预览每路最多 5 帧/秒；父进程后台读帧，GUI 只读缓存。它是插件自己的采集实现，不是第二个 Framework 或另一套资源合约。

关闭会等待采集进程和读线程退出；正常关闭失败时终止本插件拥有的采集进程，仍无法收尾会明确报错。父进程管道断开也会清理全部输入并退出。配置和 Runtime 私有接口均留在本插件。

Qt 使用依据：[QMediaDevices](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaDevices.html)、[QMediaCaptureSession](https://doc.qt.io/qtforpython-6.10/PySide6/QtMultimedia/QMediaCaptureSession.html)、[QVideoSink](https://doc.qt.io/qtforpython-6.10/PySide6/QtMultimedia/QVideoSink.html)。

## 验证和待现场确认

测试采用假进程、假相机、合成图像及实际 Qt 控件，不枚举或打开现场设备。覆盖设备独占/六路限制、ROI、目标捕获、GUI Run 锁、缓存生命周期、故障恢复、父管道关闭及取证目录。

```powershell
# 从 framework-dev 执行；TEMP/TMP 指到 plugins/camera/.test-tmp
.venv/Scripts/python.exe -B -m pytest plugins/camera/tests --capture=sys -p no:cacheprovider --basetemp plugins/camera/.test-tmp/unit
.venv/Scripts/python.exe -B plugins/camera/integration/simulate_board.py plugins/camera/.test-tmp/board-app
```

联调脚本复制四插件生产文件到插件目录内的临时应用，使用真实 Framework、GUI 和报告流程，但所有硬件边界替换为模拟实现。脚本显示 COM77/78/79 仅作为模拟配置，不访问任何 COM；截图中的画面也是合成图像。

尚未做真实摄像头、六路并发吞吐及设备拔插验收；需现场按实际 USB 带宽/驱动确认。本版不承诺实时检测延迟或图像分析准确率。
