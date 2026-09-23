# 归属与许可（Attribution / Licensing）

本仓库 = **`rime/weasel`（小狼毫）的个人分支** + **一个自研的 Windows 设置应用**。
下面一次列清"哪些是我们写的、哪些来自开源项目、各自什么许可"。

---

## A. 本仓库自有部分（非上游）

| 路径 | 是什么 |
|---|---|
| `settings-app/` | **自研**的 PySide6 托盘设置应用（概览/翻译/模型/词库与记忆/外观/按键与开关/关于 七页） |
| `tools/` | 构建编排（`build-all.ps1`/`build-engine.ps1`）、模型宿主工具（`tools/model-host/`）、引擎二进制替换（`tools/engine/`） |
| `patches/` | `librime-sensitive-mode.patch`（敏感输入域门控）、`boost-1.84-msvc-14.4.patch` |
| `language-input/` | 本 fork 的**译注功能**：Rime schema、Lua 译注过滤器、GlossPack 清单、模型目录描述、设计与验证文档 |
| `scripts/`（其中 `language_input_model_host.py` 被我们直接改过） | 模型宿主 Python 源码 + 打包/评测脚本 |
| `RimeWithWeasel/LanguageInput*.{cpp,h}` 及 `language_input_*` 相关改动 | 候选栏译注、AI 后端选择、朗读 |
| `README.md` / `README-weasel.md` / `ATTRIBUTION.md` / `REPO-STRUCTURE.md` / `settings-app/docs/` | 本套文档 |

> ⚠️ `RimeWithWeasel/`、`WeaselTSF/` 等目录**同时**含上游代码与我们的改动。
> 我们的改动可用 `git log a57d7f0..HEAD` 精确区分（`a57d7f0` = 个人 fork 的基点）。

---

## B. 上游开源项目（本仓库**直接包含其代码**）

| 项目 | 在本仓库的位置 | 许可 |
|---|---|---|
| **rime/weasel**（小狼毫，Windows 前端） | 仓库根目录绝大部分：`WeaselTSF/` `WeaselServer/` `WeaselUI/` `WeaselIPC/` `WeaselDeployer/` `WeaselIME/` `WeaselSetup/` `RimeWithWeasel/` `include/` `build.bat` `weasel.sln` … | **GPL-3.0**（`LICENSE.txt`） |
| **rime/librime**（输入法引擎核心） | `librime/`（**submodule**：官方 `rime/librime` @ `1c23358`，版本 1.13.1） | **BSD-3-Clause**（`librime/LICENSE`） |
| **rime/plum**（Rime 包管理器/配方） | `plum/`（**submodule**：官方 `rime/plum` @ `cab9ed3`） | 见上游仓库 |

- 本仓库是 Weasel 的 **fork**，基点 = 上游 tag `0.17.4`（commit `9cc96e20…`）。
- `librime` 我们不直接改 submodule 内容，而是以 **补丁**形式加敏感域门控。

---

## C. 第三方数据与模型（NOTICE 随仓库附带于 `language-input/licenses/`）

| 组件 | 用途 | 许可 | NOTICE 文件 |
|---|---|---|---|
| **CC-CEDICT**（MDBG 汉英词典） | 固定词典译注 `en.tsv`（**197,865** 条） | **CC-BY-SA-4.0** | `CC-CEDICT-NOTICE.txt` |
| **QuickMT**（`quickmt/quickmt-zh-en`、`-en-ja`、`-en-es`） | 本地离线翻译模型包 | **CC-BY-4.0** | `QUICKMT-CC-BY-4.0-NOTICE.txt` |
| **facebook/m2m100_418M** | 可选多语直译模型（**权重未随仓库分发**） | **MIT** | `M2M100-MIT-LICENSE.txt`、`M2M100-NOTICE.txt` |
| **librime-lua** | Lua 插件（译注过滤器运行其上） | 见 NOTICE | `LIBRIME-LUA-NOTICE.txt` |
| **Lua 5.x** | Lua 运行时 | MIT（见 NOTICE） | `LUA-NOTICE.txt` |
| **RIME 双拼方案** | 小鹤双拼键位来源 | 见 NOTICE | `RIME-DOUBLE-PINYIN-NOTICE.txt` |
| 本地模型运行时（CTranslate2 / SentencePiece 等） | 模型宿主 | 见 NOTICE | `LOCAL-MODEL-RUNTIME-NOTICE.txt` |

> **模型权重本身不在仓库里**（每个约 400MB）。仓库内的 JSON 只是**目录与哈希清单**：
> `language-input/models/*.json` 是运行时目录，`settings-app/src/language_input_settings/data/components/*.json`
> 是离线安装用的**逐文件哈希（信任根）**。

---

## D. 运行时/构建依赖（第三方，**未**随仓库分发其代码）

| 组件 | 用在哪 | 许可 |
|---|---|---|
| **PySide6 / Qt for Python** | 设置应用 GUI | **LGPLv3** |
| **ruamel.yaml** | 设置应用读写 YAML | MIT |
| **PyInstaller** | 冻结 `LanguageInputSettings.exe` / `LanguageInputModelHost.exe` | GPL-2.0 **含例外条款**（允许打包非 GPL 程序） |
| **CTranslate2** | 模型宿主推理 | MIT |
| **SentencePiece** | 模型宿主分词 | Apache-2.0 |
| **Boost** | Weasel/librime 构建（本地安装，不入库） | BSL-1.0 |
| **OpenCC** | 简繁转换（Weasel 数据） | Apache-2.0 |
| **NSIS**（可选） | 安装器 | zlib/libpng |

---

## E. 仅作参考、**未使用其代码**的项目

| 项目 | 关系 |
|---|---|
| **metasequoiaime/MSIME-Windows**（GPL-3.0） | **灵感来源**（"候选栏后面显示翻译"的想法取自它）。我们**没有复制任何代码**：它是纯 TSF 从零实现，本项目是 Rime/Weasel 分支，架构完全不同 |
| PIME / WindInput / 微软 TSF 示例 | 仅在选型阶段调研，未采用 |

---

## F. 合规要点（若要分发给他人）

1. **本仓库含 GPL-3.0 代码**（Weasel）。分发二进制时需按 **GPL-3.0** 提供对应源码 —— 本仓库即是源码。
2. **CC-BY-4.0 / CC-BY-SA-4.0**（QuickMT 模型、CC-CEDICT 词典）要求**保留署名**；`language-input/licenses/` 下的 NOTICE 必须随分发保留。
3. **PySide6 是 LGPLv3**：以 onedir 方式分发 Qt DLL 属动态链接，满足要求；**不要静态链接 Qt**，并保留许可文本。
4. 仅**自用**不分发时基本不触发上述义务；但保留 NOTICE 是零成本的正确做法。
