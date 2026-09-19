# 摄像头与总 GUI 验证记录

2026-09-19，按用户批准范围交付；详细使用方法见 [README](../README.md)。

- 插件范围：gear.camera 的 manifest、纯配置验证、Runtime、长期采集服务、私有无窗口 Qt worker、GUI/ROI。
- 纯 GUI 范围：宿主 desktop.py 增加资源归属总览、导航、归档用例预览和既有 RunEvent 展示；未修改核心及公共合约。
- 相机测试和新增 GUI 测试：**31 passed**。包括假进程通讯、假相机精确 ID、JPEG 编码、旧回调丢弃、断管道退出、人工重开、资源独占/六路上限、ROI、Run 只读、排队操作固定目标和非法配置修复。
- 四插件集成脚本实际通过：一次配置预检取消、连续 2 PASS、一次预期 FAIL，报告含 SNAPSHOT 和 TRANSCRIPT，长期服务不重建，退出均关闭。
- 原有根回归 **141 passed**，其他插件最终计数见 [总交付记录](../../adb/docs/board-plugin-delivery.md)。
- 没有启动真实 Qt 采集子进程、没有枚举输入、没有打开 COM 或实际 ADB/fastboot。所有外部硬件边界均为模拟；真实 Qt 仅用于控件、ROI 和合成图像编码/显示。
- 摄像头算法按要求暂不实现。尚待真实相机/多路 USB 带宽及驱动兼容性验收，不能把模拟通过等同于硬件验收。

测试及临时产物限 plugins/camera 内。Qt 私有采集进程在实际使用时才启动，持有硬件并在关闭时回收；CLI 工厂/配置验证没有 Qt 和硬件依赖副作用。
