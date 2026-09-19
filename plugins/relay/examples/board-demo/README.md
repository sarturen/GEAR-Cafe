# 资源配置与用例引用 Demo

用例按逻辑资源 ID 找资源，不按 COM、通道、用途或设备号找资源：

`用例中的 POWER.kl30 → 项目声明 POWER.kl30 → 环境同名记录 → gear.relay → relay_box / CH1 → COM79`

- `BOARD001`：单板主键，实际填 ADB 第一列序列号；fastboot 使用相同编号。
- `POWER.kl30`：资源 ID，用例引用它；同一个 ID 在项目和环境中必须一致。
- `device: BOARD001`：资源归属于哪块单板，不代替资源 ID。
- `role: KL30`：给人看的用途。单独写 KL30 不会自动定位资源。
- `controller + channel`、`port`、`camera_id`：环境中的实际硬件定位。
- `gear.xxx`：配置/合约内的插件注册 ID。GUI 页签已用短名称，不改变注册 ID。
- 资源 ID 格式为 `TYPE.alias`，alias 以英文字母开头，可含字母、数字、短横线、下划线。多板可命名 `POWER.boardA_kl30`、`POWER.boardB_kl30`；每条再分别填写对应 device。

## 在 GUI 配置

1. **ADB**：刷新 USB 设备后登记序列号；也可手工登记离线设备号。新增 ADB 别名 `main`，绑定到设备号，形成 `ADB.main`。
2. **继电器**：展开“串口设置”，添加控制器 `relay_box`，手填 COM 和串口参数。展开“资源绑定”，查看 CH1，别名填 `kl30`，设备号选 BOARD001，用途选 KL30，保存；CH2 同样绑定别名 kl15 / 用途 KL15。没有接的通道不创建资源。右键八路按钮也能打开该通道归属。
3. **串口**：添加别名 mcu、soc；手填各自 COM，分配同一设备号和 MCU/SOC 用途，按真实参数设置编码/波特率/结束符。
4. **摄像头**：刷新输入、打开预览确认画面，再分配别名 center、设备号、用途“中控屏”，拖动选 ROI。
5. 这些页面都保存到根目录同一份 `environment.yaml`，首页可检查归属并双击导航。

## 文件分工

| 文件 | 含义 |
|---|---|
| [environment.example.yaml](environment.example.yaml) | 六条资源的完整环境样例；不自动加载，不覆盖你的环境 |
| [project.yaml](project.yaml) | 声明这套用例使用的逻辑资源名称和类型，不写 COM 等物理信息 |
| [relay-cycle.case.yaml](relay-cycle.case.yaml) | POWER.kl30 吸合/释放各1秒，重复3次；不会操作 KL15 |
| [board-observe.case.yaml](board-observe.case.yaml) | 观察 ADB 正常模式、两串口连接、相机采集，再执行 echo；失败时收取各类证据 |
| [serial-command.case.yaml](serial-command.case.yaml) | 发送 MCU 命令并检查本次 Run 输出，两个 REPLACE_* 文本须由你换成真实协议命令/输出 |

修改环境里的 COM 或 channel，不需要改用例；改了资源 ID，就必须同步修改项目和用例。同板可有缺省资源，删除不需要的记录和项目声明，并只运行不引用它们的用例即可。

## 运行

保持 GEAR 使用默认的唯一环境文件。在 GUI 上方“项目配置”选择本目录 project.yaml，“测试用例”选择其中一个 .case.yaml；开始预检、查看归档输入、确认执行。

样例中的 BOARD001、COM77/78/79、相机 ID 都是示意值。请先用自己的硬件完成上述 GUI 配置；本次开发没有访问这些 COM。已有环境不要直接被样例覆盖。

继电器循环和串口 SEND 可在执行时首次建立连接，也可以复用已经打开的连接。board-observe 用例的 CONNECTED / STREAMING 只检查缓存：运行前必须已连接两路串口并打开相机预览。STREAMING 只表示有新画面，不表示亮屏或图像正常。串口命令示例无需预连接，SEND 会按保存配置建立连接。

预检只检查配置与能力声明，不能证明硬件可用。用例期间人工控制锁定，八路按钮跟随实际缓存更新；报告仍由宿主归档到 runs/<run_id>/report.json。断言失败会停止后续步骤；本例不承诺失败后自动释放继电器，释放应由操作者确认处理。未修改 DSL 或执行规则。
