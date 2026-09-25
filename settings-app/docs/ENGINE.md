# 引擎行为与实测（Engine behaviour & measurements）

> 本文件只记录**设置应用所依赖的引擎行为**与**实测证据**。
> 引擎源码、fork/上游、`librime`/`plum` 子模块与 librime 补丁的**位置与仓库结构**，
> 见仓库根目录的 [`REPO-STRUCTURE.md`](../../REPO-STRUCTURE.md)。
>
> 背景：引擎源码**就在本产品仓内**（分支 `language-input-settings`，基点 = fork 提交
> `a57d7f0`）。设置应用**不编译**引擎，只通过注册表与已安装的可执行文件对接，因此下文
> 记录的"引擎行为"仍是设置应用需要遵守的契约。

## 引擎安装位置（设置应用实际对接的对象）

| 项 | 值 |
|---|---|
| 安装根 | `C:\Program Files\Rime\weasel-0.1.0` |
| 用户数据 | `%APPDATA%\Rime`（可由 `HKCU\Software\Rime\Weasel\RimeUserDir` 重定向） |
| 模型目录 | `<RimeUserDir>\language_input\models` |

## 本次实测的二进制哈希（SHA256，2026-09-26）

用于确认"这个设置应用对应的是哪一份引擎构建"。若这些哈希变化，说明引擎被重装/升级过，
设置应用的行为假设可能需要重新验证。

| 文件 | 字节数 | SHA256 |
|---|---|---|
| `WeaselServer.exe` | 2,864,640 | `5DA0DA4CCA399864CCFEFF8D40B195172B2FA7B8E8D7DEF70EA722E557B52FF3` |
| `weaselx64.dll` (TSF) | 1,068,544 | `899C1106E66E26211EA6E2EC4A6EBAA79E74A3E7A28D549DEEBEFDED3365D98E` |
| `rime.dll` | 3,544,928 | `3D30310CAE8414880A4227A19451318975010846D9935B72D87B092A391B9CD9` |
| `WeaselDeployer.exe` | 635,904 | `1AF8CC1C52E8FDC5DFE63A89680ECB8F0EB5C717EA47B5B31B7D9E2B06414704` |
| `LanguageInputModelHost.exe` | 4,894,758 | `1D75392968CA31EA313AD6805FD624A08E54DE88AA5024A11A1D930CBA9E03C4` |

## 已实测确认的引擎行为（设置应用依赖这些）

- `WeaselServer.exe` 确含 `LANGUAGE_INPUT_REMOTE_*`（UTF-16）与
  `language_input_gloss` / `language_input_ai` 等选项（ASCII）；本地译注固定使用 QuickMT 路线。
- `rime.dll` 含 `LuaTranslator` / `user_dictionary` / `Memory`，**不含** `octagram` / `predictor` /
  `predict_translator`（→ 语法模型与下一词预测需要重编 librime，本项目**不做**）。
- 后端环境变量**仅在 WeaselServer 启动时读一次**；`absent` / `local` = 本地，真值 = 外接，假值 = 关闭译注。
- `WeaselDeployer.exe /deploy` 在配置非法时**仍返回 0**（错误只在 stderr）。

## 引擎重建路径（如将来必须）

引擎源码已在本仓（`Weasel*`、`RimeWithWeasel` 等目录），但重建需要 Visual Studio 2017+
（含 ATL/MFC）、CMake、Boost；`git clone --recursive` 后按 `REPO-STRUCTURE.md` 的构建顺序执行
`build.bat all`，产出安装包到 `output\archives`。详见引擎自己的 `INSTALL.md`。
本设置应用**不**依赖这条路径 —— 它只对接已安装的引擎。
