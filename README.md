# 译注输入法（Language Input）

一个**自用**的 Windows 输入法项目：打字时在**候选栏后面显示另一种语言的释义**，帮助语言学习。
底座是 **[rime/weasel（小狼毫）](https://github.com/rime/weasel)** 的个人分支，外加一个**自研的 PySide6 托盘设置应用**。

> - 上游 Weasel 的原始说明已原样保留 → [`README-weasel.md`](README-weasel.md)
> - **哪些是我们的、哪些是开源的、各自什么许可** → [`ATTRIBUTION.md`](ATTRIBUTION.md)
> - **每个目录归谁、版本号口径** → [`REPO-STRUCTURE.md`](REPO-STRUCTURE.md)

## 效果

输入拼音，候选栏每位候选后面跟一条目标语言释义（中→**英 / 日 / 西**）。
**固定词典（CC-CEDICT，197,865 条）优先，词典查不到的词由本地小模型补上**；
候选窗里**不显示任何来源标记**，干净地只有「中文 + 释义」。

```
da zi
 1. 打造   Build; Build up
 2. 打字   Typing; Typography
 3. 搭子   companion
 …
```

## 哪些是我们的、哪些是上游的

| 归属 | 内容 |
|---|---|
| **上游 rime/weasel**（GPL-3.0） | 整个 Windows 前端：TSF 输入法、常驻服务、候选窗、部署器、构建系统。仓库根目录绝大多数文件 |
| **上游 rime/librime**（BSD-3-Clause） | `librime/` —— 输入法引擎核心（**submodule**） |
| **我们写的** | `settings-app/`（设置应用）、`tools/`（构建与安装脚本）、`patches/`（librime 敏感域补丁等）、`language-input/`（译注功能的 schema / Lua / 词典清单）、`RimeWithWeasel/LanguageInput*`、本套文档 |
| **第三方数据/模型** | CC-CEDICT 词典（CC-BY-SA-4.0）、QuickMT 模型（CC-BY-4.0）、M2M100（MIT）—— NOTICE 见 `language-input/licenses/` |

> 我们的改动可用 `git log a57d7f0..HEAD` 精确区分（`a57d7f0` = 个人 fork 的基点）。

## 目录

```
langInput/
├─ WeaselTSF/ WeaselServer/ WeaselUI/ WeaselIPC/ WeaselDeployer/ WeaselIME/ WeaselSetup/
│                                   ← 上游 Weasel（含我们的 language-input 改动）
├─ RimeWithWeasel/                  ← librime 桥接 + 译注/AI 后端（我们改过）
├─ language-input/                  ← 译注功能：Rime schema、Lua 过滤器、词典/模型清单、设计文档
├─ scripts/                         ← 模型宿主 Python 源码 + 打包/评测脚本
├─ patches/                         ← librime 敏感域补丁、Boost 补丁
├─ librime/ · plum/                 ← submodule（官方 rime/librime、rime/plum）
├─ settings-app/                    ← ★ 自研设置应用（PySide6）
│   ├─ src/language_input_settings/ · assets/ · packaging/ · docs/
├─ tools/                           ← ★ 构建编排 / 模型宿主 / 引擎替换
├─ ATTRIBUTION.md · REPO-STRUCTURE.md · README-weasel.md
└─ third-party/ · deps/ · output/ · msbuild/   （已忽略：本地/构建产物）
```

## 构建

**引擎**需要 Visual Studio（C++ 桌面）+ CMake + Boost；**模型宿主/设置应用**需要 Python 3.11。

```powershell
git clone --recursive <repo>                       # 1. 取 librime / plum 子模块
git -C librime apply ../patches/librime-sensitive-mode.patch   # 2. 打引擎补丁
.\build.bat weasel                                 # 3. 构建引擎（或 msbuild weasel.sln /p:Platform=x64）
.\tools\model-host\build.ps1                       # 4. 重建模型宿主
.\settings-app\packaging\build.ps1                 # 5. 打包设置应用
.\tools\build-all.ps1                              # 6. 一键编排（含 TODO：合并安装包）
```

> “clone 就能编译”指**源码齐全、步骤明确**；引擎构建仍需要上述工具链。
> 本机已验证的路径是：复用已构建好的 `librime`（`dist_x64\lib\rime.dll` + `rime.lib` + 头文件），
> 只单独编 `WeaselServer.exe`，详见 `tools/engine/`。

## 安装与使用

- **引擎**：安装后由 TSF 自动挂载。模型宿主 `LanguageInputModelHost.exe` 由 `WeaselServer.exe`
  **按需拉起**，空闲 **600 秒**自动退出 —— **不需要常驻、不需要开机自启**。
  实测延迟：热请求 ~30ms、冷启动后首次 ~1.7s（详见 `settings-app/docs/MODEL-HOST-LIFECYCLE.md`）。
- **设置应用**：`settings-app\dist\LanguageInputSettings\LanguageInputSettings.exe`（托盘图标 → 设置窗口）
  - ⚠️ 首次运行 Windows 会把新托盘图标放进**溢出区**（点任务栏 `^` 才看得到），需手动拖到任务栏固定
  - 可选开机自启：`settings-app\packaging\autostart.ps1 -Enable`
- **引擎二进制替换**（改了 C++ 之后）：`tools\engine\apply-weaselserver.ps1`（备份 + 提权替换 + 重启）

## 文档地图

| 文档 | 回答什么问题 |
|---|---|
| [`ATTRIBUTION.md`](ATTRIBUTION.md) | 哪些是我们的、哪些是开源的、各自什么许可 |
| [`REPO-STRUCTURE.md`](REPO-STRUCTURE.md) | 每个目录归谁、构建顺序、版本号口径、与个人 fork 的关系 |
| [`README-weasel.md`](README-weasel.md) | 上游 Weasel 的原始说明 |
| [`settings-app/README.md`](settings-app/README.md) | 设置应用怎么用、怎么打包、CLI 有哪些命令 |
| [`settings-app/docs/2026-09-20-…-design.md`](settings-app/docs/) | 设置应用的完整设计 + **三轮独立审阅**记录 + 风险表 |
| [`settings-app/docs/ENGINE.md`](settings-app/docs/ENGINE.md) | 引擎溯源、已实测的引擎行为、冻结二进制哈希 |
| [`settings-app/docs/MODEL-HOST-LIFECYCLE.md`](settings-app/docs/MODEL-HOST-LIFECYCLE.md) | 模型服务何时起/退、实测延迟、"每次请求重算哈希"的发现与修复 |
| [`tools/model-host/README.md`](tools/model-host/README.md) | 模型宿主的构建/安装/回滚 + A/B 证据 |
| [`language-input/README.zh-CN.md`](language-input/README.zh-CN.md) | 译注功能本身的说明（schema / 词典 / 模型包） |

## 状态与已知限制

个人自用、持续开发中。**完整**的风险与未验证项清单见
[`settings-app/docs/2026-09-20-…-design.md`](settings-app/docs/) 的 §11 / §13。要点：

- 模型**权重不在仓库**（每个约 400MB）；内网无外网时需手动导入 `.limodel`。
- M2M100 路线的**权重未分发**，且本地缺少 byte-exact 的 `LICENSE.txt`/`NOTICE.txt`，因此当前不可安装。
- 设置应用里"从 HF 下载"的能力已实现并做过小文件实测，但**完整包的真实下载/安装链路只做过桩验证**。
- 设置界面中的"词频列表"是**有意不做**的（LevelDB 无法在纯 Python 下安全读取）。

## 许可

本仓库含 **GPL-3.0**（Weasel）代码，整体按 GPL-3.0 提供源码。第三方组件的许可与署名要求见
[`ATTRIBUTION.md`](ATTRIBUTION.md)。
