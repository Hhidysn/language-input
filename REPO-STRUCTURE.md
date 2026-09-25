# 仓库结构

这是**产品仓**：个人 fork `Hhidysn/weasel` 的引擎改动 + 自研 Windows 设置应用。
当前主分支为 `main`，引擎 fork 基点 = 提交 `a57d7f0`（基于上游 `rime/weasel` tag `0.17.4`）。

> 产品说明与效果 → [`README.md`](README.md)　|　**归属与许可（哪些是我们的、哪些是开源的）** → [`ATTRIBUTION.md`](ATTRIBUTION.md)
> 上游 Weasel 的原始说明 → [`README-weasel.md`](README-weasel.md)

## 目录归属

| 路径 | 归属 | 说明 |
|---|---|---|
| `WeaselTSF/` `WeaselServer/` `WeaselUI/` `WeaselIPC/` `WeaselDeployer/` `WeaselIME/` `WeaselSetup/` `RimeWithWeasel/` `include/` | 引擎（上游 weasel + 本 fork 改动） | TSF 输入法、常驻服务、候选窗、部署器、librime 桥接 |
| `language-input/` | 本 fork 自有功能 | Rime schema、Lua 译注过滤器、GlossPack、模型目录清单、设计与验证文档 |
| `scripts/` | 本 fork 自有 | 模型宿主 Python 源码（`language_input_model_host.py` 由我们在树内直接修改）+ 打包/评测脚本 |
| `patches/` | 本 fork 自有 | 上游构建补丁：`librime-sensitive-mode.patch`（librime 敏感域门控）、`boost-1.84-msvc-14.4.patch`；**模型宿主性能修复已改为源码内直接修改**（`scripts/language_input_model_host.py`），不再是补丁 |
| `librime/` `plum/` | 子模块（上游） | `rime/librime`、`rime/plum`；克隆需 `--recursive` |
| `settings-app/` | **本项目自研** | PySide6 托盘设置应用（与引擎仅通过注册表/可执行文件对接） |
| `tools/` | 本项目自研 | 构建编排（`build-all.ps1` / `build-engine.ps1`）+ 模型宿主工具（`tools/model-host/`：构建/安装/回滚/验证）+ 引擎二进制替换（`tools/engine/`：备份 + 提权替换 + 重启 `WeaselServer.exe`） |
| `third-party/` `.opencode/` `deps/` `output/` `.cache/` | 本地/外部 | 已忽略；Boost 等构建依赖不入库 |

## 构建顺序

1. `git clone --recursive <repo>` —— 取到 `librime` / `plum` 子模块
2. 给 `librime` 应用 `patches/` 中的敏感域补丁
3. 构建引擎 —— 需要 **Visual Studio + CMake + Boost**（`build.bat` / `xbuild.bat`）
4. 重建模型宿主 —— `tools/model-host/build.ps1`（直接从树内 `scripts/language_input_model_host.py` 构建）
5. 打包设置应用 —— `settings-app/packaging/build.ps1`
6. 单包安装程序 —— `scripts/package_release.ps1 -Version <版本号>`；GitHub Actions 见 `.github/workflows/release-ci.yml`

> “clone 就能编译”指的是**源码齐全、步骤明确**；引擎构建仍然需要上述工具链。

## 版本号口径

引擎的 `WEASEL_BUILD` 按 `git rev-list 0.17.4..HEAD --count` 计算，所以本分支 HEAD 得出 **`0.17.4.10`**。
早期文档里的 `0.17.4.3` 是“最后一次经完整验证的发布重建”，两者口径不同，不要混淆。

## 与原个人 fork、旧目录的关系

- 引擎改动来自个人 fork `Hhidysn/weasel` 分支 `codex/language-input-v0.2-local-ai`（提交 `a57d7f0`）。
- 本分支的引擎路径内容与该 fork HEAD **逐字节一致**（`git diff a57d7f0 HEAD -- RimeWithWeasel language-input scripts` 为空），
  因此该 fork 仅作为历史存档，**不再是构建或运行的必要依赖**。
- 旧的 `F:\documents\software\languageInput` 保持不变，不再是开发目标。
