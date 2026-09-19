# GEAR 仓库目录整理设计

日期：2026-09-19  
状态：待用户书面确认后制定实施计划

## 目标

以 `framework-dev` 作为唯一技术项目和 Git 仓库根目录。仓库根目录只保留项目入口与工程目录；开发记录、GEAR Cafe 规范、公共合约、插件实施文档和图片统一归入根目录下的 `doc/`。整理完成后，根 README 能直接说明安装、启动、资源配置、用例运行、测试和文档入口，Windows 用户可从根目录双击启动 GUI。

此次整理不改变 Framework、插件合约、DSL、环境模型或硬件行为。文件移动后的 Python 包名、插件发现方式、默认环境文件及用例语义保持不变。

## 仓库边界

实际 Git 仓库为 `C:\dev\GEAR_cafe_docs\framework-dev`，当前分支为 `feat/adb-plugin`，远程为 `origin`。外层 `C:\dev\GEAR_cafe_docs` 不是 Git 仓库。

外层 `.gear-cafe-publish` 是一个干净的独立 Git checkout，分支为 `main`，本次不修改或删除。外层其余架构、DSL、环境、运行时、报告和插件合约文档与仓库 `doc/` 中的版本重复；外层 `contracts` 还包含构建产物。迁移结束并核对仓库版本完整后，清理这些非 Git 重复项，使外层只承担容纳技术项目和独立发布 checkout 的作用。

## 目标目录

```text
framework-dev/
├─ README.md
├─ Start-GEAR.cmd
├─ pyproject.toml
├─ src/
├─ plugins/
├─ examples/
├─ tests/
└─ doc/
   ├─ README.md
   ├─ contracts/
   ├─ cafe/
   ├─ development/
   └─ plugins/
```

具体归类如下：

- `doc/contracts/`：`gear_contracts` Python 包、构建配置和精确接口说明。路径保持不变，避免改变安装命令及测试 `pythonpath`。
- `doc/cafe/`：架构、决策记录、DSL、环境模型、失败取证、Framework Runtime、插件合约和报告规范。
- `doc/development/`：当前 `docs/` 下的总体实现状态、桌面与 ADB 状态、历史设计、实施计划和总体截图。
- `doc/plugins/<plugin>/`：当前各插件 `docs/` 下的实施报告、GUI 说明和图片。
- `plugins/<plugin>/README.md`：保留为插件就近使用入口，只承载安装、配置和常用命令，并链接到 `doc/plugins/<plugin>/` 的详细记录。

根目录不再保留 `docs/`。插件目录不再保留 `docs/`。

## 根入口

根 `README.md` 重写为整个技术项目的入口，内容包括：

1. GEAR 的职责及简单、独占、插件解耦原则。
2. 当前实现的 Framework、ADB、继电器、CH340 串口和摄像头能力。
3. Windows 安装与双击 `Start-GEAR.cmd` 的最短步骤。
4. 唯一 `environment.yaml`、单板资源配置、项目和用例的关系。
5. CLI、GUI、测试与四插件模拟联调命令。
6. `doc/README.md` 文档索引及各插件入口。
7. 无实机验证部分的准确边界。

`Start-GEAR.cmd` 保持在仓库根目录，继续从自身所在目录解析 `.venv`、`doc/contracts` 和应用目录。错误提示及 README 引用同步为整理后的路径。

## 引用迁移

所有版本化 Markdown、命令示例、`pyproject.toml`、启动脚本、测试和 Python 集成脚本都按最终路径更新。相对链接从文件的新位置重新计算，不保留兼容重定向或重复副本。

新增一个文档链接测试，扫描仓库内 Markdown 的本地相对链接，忽略 HTTP、邮件、纯锚点和代码块内容，确保目标文件或目录存在。它用于防止后续目录调整再次产生失效链接，不检查外部网站可用性。

## 用例与验证

验证按独立进程顺序执行，避免 Windows 主机锁和测试临时目录互相影响：

1. 根 Framework 全套测试。
2. ADB、继电器、串口、摄像头四套插件测试。
3. 四插件假硬件 GUI/Runtime 联调。
4. 根 `examples/bench` 的 CLI PASS 用例。
5. ADB、串口、继电器示例的配置解析与静态预检；硬件示例不访问真实 ADB、COM 或摄像头。
6. Python 3.11 目标格式检查、Markdown 本地链接检查和 `git diff --check`。
7. 从仓库外工作目录调用根 `Start-GEAR.cmd` 的路径解析测试；GUI 自动关闭，不访问真实硬件。

“所有用例能够正常运行”在无硬件环境中的含义是：纯内存示例真实执行通过，硬件用例能够载入并完成配置级预检，所有自动化测试与假硬件联调通过。它不声称完成真实设备验收。

## 提交与远程同步

保留当前工作区已有的 Framework GUI 与四插件实现，不回退或覆盖。目录迁移使用 Git 可识别的移动，完成验证后形成清晰提交，并推送当前 `feat/adb-plugin` 分支到 `origin`。推送前检查提交内容、分支和远程；不合并 `main`，也不修改独立的 `.gear-cafe-publish` checkout。

