# 单板插件与 GUI 实施计划

2026-09-19，按用户批准的业务约定实施。

**目标：** 重构 ADB / 继电器，新增 CH340 串口和摄像头，并由宿主 GUI 展示单板归属及运行事件。

**边界：** 所有功能及新增测试在各 plugins 子目录；唯一允许改的插件外文件是纯 GUI 层 src/gear_framework/desktop.py。核心、公共合约、DSL、根测试、根环境和依赖配置不改。不访问任何实际硬件。不做 Git 提交。

**业务依据：** [board-resources.md](board-resources.md)。新增用户决定：摄像头本次不实现黑屏、闪黑、冻屏、花屏检测，只交付采集预览、绑定、ROI、快照和采集状态事件。

- [x] ADB：统一 device 编码，USB ADB / recovery / sideload / fastboot 查询与命令，保留旧能力，新增模式和输出校验。独立观察线程需人工开启，配置/构造无I/O。
- [x] Relay：按控制器 COM 的八个物理通道展示、任意用途+device归属；可多个控制器，各连接独占、长期复用。新流程不生成KL占位。旧未完成绑定保持可见可删除，不伪装成有效配置。
- [x] Console：CONSOLE资源，MCU/SOC可缺省；单COM接收线程串行I/O，发送/快捷命令/输出条件/日志取证，Run按游标区分观察窗口。
- [x] Camera：SCREEN资源，真实输入ID（不用易变的列表索引）+device+role+归一化ROI；每板最多6输入、各输入独占。复用已安装Qt Multimedia，在插件私有无窗口采集子进程内持有QCamera，父进程Runtime纯Python缓存，不引入新全局依赖。只有显式发现/打开或相应执行能力调用才访问相机。插件拥有并关闭采集子进程，不是第二个Framework或控制会话。
- [x] GUI：保持现有首页和插件标签，首页加单板资源归属总览并导航；加载用例文本可看，订阅Framework已有RunEvent实时输出时间、步骤、资源和结果，报告保持既有归档流程。各Workspace可提供GUI私有 resource_statuses()/select_resource()，不扩展核心合约。
- [x] 验证：各插件fake transport/backend与Qt控件测试；根原测试只读运行；实际Framework临时应用发现四插件、配置和模拟Run；截图检查GUI。所有tmp、缓存、产物限插件目录；设备API守卫禁止实际访问。

资源约定：已有resource.device为唯一单板归属；role为插件私有用途，POWER可任意角色，CONSOLE仅MCU/SOC，SCREEN为屏幕名称。配置均保存宿主唯一environment.yaml。后台观察只更缓存，GUI从缓存读取；ACTIVE禁止控制和编辑但观察继续。工厂、configure、validate均不探测硬件。

测试命令（各目录独立进程运行，避免test_runtime重名）：.venv/Scripts/python.exe -B -m pytest plugins/<plugin>/tests --capture=sys -p no:cacheprovider --basetemp plugins/<plugin>/.test-tmp/cases -q。TEMP/TMP/BLACK_CACHE_DIR也指定插件内。原有Framework集成测试不可并行，使用单独插件内临时根以避免复制递归。

最终验收：475 项 pytest 全通过；四插件真实Framework/Qt模拟联调通过（2 PASS + 1 预期 FAIL及取证）。范围审计无越界改动。详情见 board-plugin-delivery.md。
