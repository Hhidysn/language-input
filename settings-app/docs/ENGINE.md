# 引擎溯源（Engine provenance）

本仓库只包含**设置应用**（`settings-app/`）。输入法引擎本身是一个独立的 Weasel/小狼毫(Rime) 分支，
**不包含在本仓库内**，也**不在本仓库里修改**。

## 引擎位置与来源

| 项 | 值 |
|---|---|
| 工作副本 | `F:\documents\software\languageInput` |
| fork | `https://github.com/Hhidysn/weasel` |
| upstream | `https://github.com/rime/weasel` |
| 基线版本 | Weasel **0.17.4**（git tag `0.17.4`） |
| 产品版本 | **0.17.4.3** |
| librime | **1.13.1**（git submodule，`rime/librime`） |
| 分支 | `codex/language-input-v0.2-local-ai` |
| 提交 | `a57d7f0`（`feat(language-input): add optional M2M100 backend`） |
| 子模块补丁 | `patches/librime-sensitive-mode.patch`（已应用 → 工作副本里 `librime` 显示为 ` M`） |

> 该分支唯一实质性的自有功能是 **"Language Input v0.2"**：一个 Rime Lua 译注过滤器
> （`gloss_filter.lua`）+ 一个 CTranslate2/SentencePiece 的 Python 模型宿主
> （`LanguageInputModelHost.exe`），通过仅回环、带 token 的 OpenAI 兼容接口被 WeaselServer 调用，
> 为候选词附上离线中→英/日/西 释义；另有 SAPI 朗读，以及一个在敏感输入域关闭学习/模型的 librime 补丁。

## 引擎安装位置（设置应用实际对接的对象）

| 项 | 值 |
|---|---|
| 安装根 | `C:\Program Files\Rime\weasel-0.1.0` |
| 用户数据 | `%APPDATA%\Rime`（可由 `HKCU\Software\Rime\Weasel\RimeUserDir` 重定向） |
| 模型目录 | `<RimeUserDir>\language_input\models` |

## 本次实测冻结的二进制哈希（SHA256）

用于确认"这个设置应用对应的是哪一份引擎构建"。若这些哈希变化，说明引擎被重装/升级过，
设置应用的行为假设可能需要重新验证。

| 文件 | 字节数 | SHA256 |
|---|---|---|
| `WeaselServer.exe` | 2,833,408 | `16A4572A046D6C45AEB121EDA29FC466D26B3D649DC68EC845A5EB19F277F294` |
| `weaselx64.dll` (TSF) | 1,068,544 | `6D330C17526E6FB21CF672009EF66311188933133653473DB29CC147D8EB7D57` |
| `rime.dll` | 3,544,928 | `3D30310CAE8414880A4227A19451318975010846D9935B72D87B092A391B9CD9` |
| `WeaselDeployer.exe` | 635,904 | `AB38EAEA412F503165F0D15286C328C8A4F68F4B65F2B59B2A28451698218250` |
| `LanguageInputModelHost.exe` | 4,892,238 | `84148537A96C5AE0444217AAA53205D513DF1B21DDF90BB464C774A98D3001D8` |

## 已实测确认的引擎行为（设置应用依赖这些）

- `WeaselServer.exe` 确含 `LANGUAGE_INPUT_REMOTE_*`（UTF-16）与 `language_input_model_m2m100` /
  `language_input_gloss` / `language_input_ai` 等选项（ASCII）。
- `rime.dll` 含 `LuaTranslator` / `user_dictionary` / `Memory`，**不含** `octagram` / `predictor` /
  `predict_translator`（→ 语法模型与下一词预测需要重编 librime，本项目**不做**）。
- 后端环境变量**仅在 WeaselServer 启动时读一次**；`absent` = 本地，真值 = 外接，假值 = 关闭译注。
- `WeaselDeployer.exe /deploy` 在配置非法时**仍返回 0**（错误只在 stderr）。

## 为什么不把引擎搬进本仓库

1. **独立上游历史**：它是 `rime/weasel` 的 fork，合并进来会重复上游历史并与 `librime`/`plum` 子模块纠缠。
2. **体积**：含 Boost 依赖树，GB 级。
3. **搬进来也编不了**：引擎需要 Visual Studio，而本项目的硬约束正是"零 VS 编译"。
4. **它已有托管**：fork 已在 `Hhidysn/weasel`。

如需更强的可复现性，可把它挂为 git submodule（例如 `engine/` → `Hhidysn/weasel` 的固定提交），
这样不重复历史又能钉住版本。**当前未挂。**

## 引擎重建路径（如将来必须）

需要 Visual Studio 2017+（含 ATL/MFC）、CMake、Boost；`git clone --recursive` 后
`build.bat all` 产出安装包到 `output\archives`。详见引擎仓库自己的 `INSTALL.md`。
本设置应用**不**依赖这条路径 —— 它只对接已安装的引擎。
