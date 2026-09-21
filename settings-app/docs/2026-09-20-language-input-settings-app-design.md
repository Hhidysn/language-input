# Language Input 设置应用 设计文档（rev3）

- 日期：2026-09-20（rev3 修订于同日）
- 状态：**已修订，待实施**；已完成两轮独立审阅
- 目标平台：Windows 10/11 x64
- 范围：在既有 Weasel/Rime fork（`F:\documents\software\languageInput`）之上新增**独立设置应用**，补齐模型下载/管理与词频记忆。**不编译任何 C++**。

---

## 0. 审阅记录（务必先读）

| 轮次 | 审阅者 | 结果 |
|---|---|---|
| 1 | 多模型 council（4 席） | 跑通 **3/4**（gemini 席位 `Not Found`）。裁决 sound-with-fixes，指出 rev1 机制错误 |
| 2 | **Codex CLI**（命令行 `codex exec --model gpt-6-astra`，只读沙箱） | **成功**。裁决 **REVISE**，指出 rev2 仍有 6 项 behavior-breaking 错误 |
| 3 | **GLM**（独立第三方审阅，审阅**当前 tree**） | **成功**。提出 B1–B4 / S1–S8 / N1–N4。逐条复核：**全部 CONFIRMED 并修复**（无 NOT-REPRODUCED）；另有 D1 暂缓。见下方「GLM 审阅处置」。 |

**Codex 用文件行号更正了 council 的 5 处过度断言**（本 rev3 已采纳）：
1. `custom_phrase` **确实存在**（`output\data\luna_pinyin.schema.yaml:69` 注册 `table_translator@custom_phrase`，`stabledb`）——council 说"仅见于 CHANGELOG"是错的。
2. "Rime 非标准 YAML 必然破坏往返" **不成立**：`__include`/`__patch`/斜杠路径是**普通 YAML 键/字符串值**，Rime 自己用 `YAML::Load`（`config_data.cc:39`）解析。→ 应**测试保持性**，而非一概改用文本替换。
3. `installed.json` 的**精确序列化不是 host 接受条件**（`language_input_model_host.py:499` 用 `json.loads` + 结构校验）；排序/缩进/尾 LF 是一致性约定，**非字节要求**。
4. LevelDB **不妨碍**文本快照视图：`export_user_dict` 可把 userdb 导出为文本（`DictManagementDialog.cpp:190`）。
5. 引用更正：是 `librime/src/rime/switcher.cc`，不是 `.../rime/gear/switcher.cc`。

**仍未完成**：council 的 gemini 席位未运行（可选补第 5 席）。

> rev3 的所有机制以**两轮审阅给出的源码行号**为准。

### GLM 审阅处置（rev7 追加）

GLM 审阅的是**当前 tree**（此前已加入 `winproc.py` 的免控制台重构），因此其行号与旧审阅不同、部分行号已位移。逐条复核结论如下：**全部 CONFIRMED 并已修复，无 NOT-REPRODUCED 项**。

| 编号 | 结论 | 修复 |
|---|---|---|
| **B1** | ✅ CONFIRMED：`apply_backend` 的 `expected` 只收 `new_env` 中存在的键；`local` 删除全部 `LANGUAGE_INPUT_REMOTE_*`，于是 `expected == {}`、校验恒真；`result["started"]` 也无条件置真 | 对**全部** `REMOTE_VARS` 取值（缺失记 `None` = 必须不存在）；`_verify_env` 读不到环境块时不判成功；必须有 PID 才 `started`/`ok` |
| **B2** | ✅ CONFIRMED：`set_switches` 在 server 运行中写 `user.yaml`，无停止/守护/重启 | 抽出可复用上下文管理器 `server.server_stopped`；开关写入走 停→写→重启 事务 |
| **B3** | ✅ CONFIRMED：模型安装只*断言*已停止，不停 server/host、不持互斥体 | 整个 backup/swap/rollback（含 `recover_interrupted`）包进 `server.maintenance_guard()`；先停 server+host；保留 `assert_host_stopped` 作最终校验 |
| **B4** | ✅ CONFIRMED：`maintenance_guard` 的返回值被调用方丢弃，`CreateMutexW` 失败仍照常运行 | Windows 上获取失败改为致命（抛 `ServerError`）；非 Windows 仍返回 bool |
| **S1** | ✅ CONFIRMED：`save_config` 覆盖 `config.json` 无备份 | 内容变化时先备份 |
| **S2** | ✅ CONFIRMED：`gloss_badge._write_shadow` 无备份 | 内容变化时先备份（与 `revert_plain_gloss` 一致） |
| **S3** | ✅ CONFIRMED：关于页 `_on_redeploy` 只看退出码 0 | 要求 stderr 为空才算成功 |
| **S4** | ✅ CONFIRMED：`start_minimized` 无人读取，自启动会弹窗 | 新增 `--start-minimized`（隐藏到托盘）并用于 `packaging\autostart.ps1`；持久设置亦被读取 |
| **S5** | ✅ CONFIRMED：卸载器不清理应用的 `<schema>.custom.yaml`（学习可能保持关闭），且 `*.bak-*` 递归清扫过宽 | 只移除应用写入的键（`switches/@N/reset`、`translator/enable_user_dict`），空文件删除；`*.bak-*` 仅清扫应用自己的备份目标 |
| **S6** | ✅ CONFIRMED：`weasel.custom.yaml` 的嵌套 `patch:` 读得到、写/删却只认扁平键（静默失败） | 改用 ruamel round-trip 文档，按**扁平路径**解析读与写/删（同 `schema_patch.py`） |
| **S7** | ✅ CONFIRMED：`resume_from` 未以 `expected_size` 为界，过期/超大的 `.part` 会发出越界 `Range`（416）并永久重试 | `resume_from >= expected_size` 时丢弃 `.part` 重新下载 |
| **S8** | ✅ CONFIRMED：容器校验用**未解析**的 root 对比已解析路径，相对 `--model-root` 会被判「逃逸」 | 先解析 root |
| **N1** | ✅ CONFIRMED：`cmd_set_style` 用恒为 dict 的 `changes` 判成败 | 改用 `changed` / `clean` |
| **N2** | ✅ CONFIRMED：`--m2m100` 无 QuickMT catalog 时会把字面量 `"None"` 当 `--catalog` | 先校验 QuickMT catalog |
| **N3** | ✅ CONFIRMED：`_read_text` 用 `Path.read_text`（已归一化换行），正则里的 `\r?` 与「CRLF 源」注释是死代码 | 删除 `\r?` 与相关注释 |
| **N4** | ✅ CONFIRMED：`set_user_yaml_option` 先备份再做 no-op 判断，重复应用堆积备份 | 先判断再备份 |

**D1（已完成，随 R26 落地）**：所有实机操作（apply/sync/neutralize/install）都已移出 Qt 主线程：`TransactionWorker(QObject)` 被移入每次新建的 `QThread`，只发 `started` / `finished` / `failed` 信号；所有控件更新都在主线程槽里完成。各页共享 `BusyStrip`（不确定进度条 + 状态行）呈现「正在…」。修复方向与验收见 §11 R26。

**行尾**：本项目已统一 **LF**（不再保留 CRLF）。`--selftest` 与所有写入路径均输出 UTF-8 无 BOM + LF；`.gitattributes` 移除了 PowerShell 的 CRLF 例外。

### M0 去风险实验结果（rev4 追加）

| 探针 | 结果 |
|---|---|
| **V0 冻结二进制** | ✅ 已记录 `WeaselServer.exe` / `weaselx64.dll` / `rime.dll` / `WeaselDeployer.exe` / `LanguageInputModelHost.exe` 的 SHA256；`WeaselServer.exe` **含** `LANGUAGE_INPUT_REMOTE_*`（UTF-16）与 `language_input_model_m2m100` / `language_input_gloss` / `language_input_ai`（ASCII）→ **R1 关闭** |
| **合成安装布局** | ✅ host `--list` **接受**自合成的 `<id>\installed.json + model\*` → **R9 验证通过** |
| **踩坑记录** | ⚠️ PowerShell 5.1 的 `Set-Content -Encoding utf8` **会写 BOM** → `installed.json` 解析失败并被 `installed_components` **静默跳过**（表现为 `--list` 返回 `{}`）。再次印证"UTF-8 无 BOM"是硬要求 |
| **环境/代理（rev5 更正）** | 本机配了 `HTTP(S)_PROXY = ALL_PROXY = http://127.0.0.1:7898`（WinINET 启用、git 亦用；WinHTTP 直连、pip 无配置）。实测 HF 407MB 权重的头 12 秒：**直连 8.8 MB/s** vs **走代理 42.7 KB/s（差 ~200×）** → 之前"PySide6 装不下"是**代理限速造成的误判**（直连下 195MB 约 30 秒）。**下载器必须默认绕过代理或可配置**。 |
| **本地模型（rev5）** | ✅ 本地已存在与 catalog **精确对应**的包：`.i-wish-research\language-input-ai-models-20260826\artifacts\quickmt-limodel-v2\*.limodel`（3 个）。→ **模型不需要下载** |
| **M2M100 阻塞** | ⚠️ 本地缺 `LICENSE.txt`/`NOTICE.txt`（哈希不匹配），且无 `m2m100-*.limodel` → 暂不可装；QuickMT 路线已可用 |

**已安装 Weasel 实例**：`C:\Program Files\Rime\weasel-0.1.0`；用户目录 `%APPDATA%\Rime`（**尚无** `language_input\models`）。

**M0 状态（rev6 —— 全部跑完）**

| 探针 | 结果 |
|---|---|
| **A env 三态重启** | ✅ 机制通过：三种注入 env 都能让 server 干净重启，且**注入的环境变量确实到达 server 进程**（实测读取进程环境块确认）。⚠️ **但"哪个后端生效"不可观测**：模型 host 按请求懒启动、启动时不拉起；server 日志也无后端/HTTP 行 → 语义**未验证**（需真实输入会话） |
| **B `user.yaml` 存活** | ✅ **未被覆盖**：server 运行中写 `var/option/language_input_model_m2m100: true` → `/deploy`（exit 0, 0.75s）→ 读回 **survived = True**；`last_build_time` / `previously_selected_schema` / `schema_access_time` 均保留。⚠️ 探针无法创建真实 IME 会话，所以 council 担心的"切方案时整档回写"**未被证伪，只是未复现** |
| **C 坏 YAML 的 `/deploy`** | ✅ **实锤 V3**：语法错误 YAML 下 `/deploy` **仍返回 exit 0**，解析错误**只在 stderr**（`config_data.cc:78`），且 Rime **没有**把文件移进 `trash\`。⚠️ `zzz_*.custom.yaml` 不会被加载（无对应基名 schema），必须写在 `default.custom.yaml` 这类会被自动 patch 的文件上才有效 |
| **D schema `reset` 修法** | ✅ **有效且无需改源码**：`build\language_input_flypy.schema.yaml` 中 @4–@8 确实带 `reset:`（gloss=1, ai=0, m2m100=0, 语言组=0, speech=1）。写 `<schema>.custom.yaml` 的 `patch: {"switches/@4/reset": null, ...}` → 重新部署后**编译 schema 中这些 reset 被移除**（7→2，仅剩 ascii_mode / zh_hant）。机制与 librime 源码一致（无 `reset` → `reset_value()=-1` → `engine.cc:402` 跳过）。⚠️ 真实跨会话保持**未在运行时验证**（无会话） |
| **未验证** | 运行时实际后端、跨会话选项保持、TSF 在 stop 窗口自动拉起（V11） |

**探针产物**：`settings-app\.m0\`（备份 + 原始输出）。`user.yaml` 已按字节还原（SHA256 `1360fc15…`），`WeaselServer.exe` 运行中（LOCAL 态），3 个 QuickMT 模型仍在。

---

## 1. 目标

1. **词典 + 本地模型 + 兼容外接 API**：候选栏译注可用本地离线模型，也可切外接大模型 API（可联网环境）。
2. **设置界面**：托盘图标 → 常规设置窗口；把只能靠 F4 按键切换的隐藏开关变成可视化配置。
3. **常用打字记忆**：验证"常用词排前"确实生效并可调优（**不做**词频可视化，见 §8）。

## 2. 硬约束

| 编号 | 约束 | 影响 |
|---|---|---|
| C1 | **不编译 C++**：目标机不装 VS，不编译 TSF/候选窗/Server/Deployer | C++ 层冻结，只用 `output\` 现成二进制 |
| C1a | **已定**：C1 指"不编译 C++/不装 VS"，**允许** `PyInstaller`、`makensis` 等非 VS 打包工具 | 打包/安装器可做 |
| C2 | 只用 `output\` 里已构建好的二进制 | 一切改动落在 Rime 配置 / Lua / Python / 独立前端 |
| C3 | 内网无外网 | 外接 API 与 HF 下载仅联网机器可用；内网走手动导入 |
| C4 | 个人使用 | GPL-3.0 / LGPL 无分发顾虑（保留第三方通知） |

## 3. 现状盘点（已核实）

### 3.1 已存在且可用
- 完整可运行 x64 部署：`output\`（`WeaselServer.exe`、`weaselx64.dll`(TSF)、`rime.dll`、`WeaselDeployer.exe`）+ 安装包 `output\archives\weasel-0.17.4.3-installer.exe`。
- 本地 AI：`LanguageInputModelHost.exe`（CTranslate2 + SentencePiece，PyInstaller `--onedir --windowed`）+ Lua 译注过滤器 `gloss_filter.lua` + 固定词典 `en.tsv`（CC-CEDICT 7.7MB）。
- librime 学习已开启：`%APPDATA%\Rime\luna_pinyin.userdb\`（LevelDB）已存在。
- `custom_phrase` 已注册（`stabledb`，只读、无动态调频）。

### 3.2 缺失 / 不足
| 项 | 现状 |
|---|---|
| 本地模型包 | `%APPDATA%\Rime\language_input\` **不存在** → 未导入任何 `.limodel`，本地模型链路**未启用** |
| 模型下载 | 两个 catalog **无下载 URL**；host **无任何网络代码** |
| 设置界面 | 无 GUI；开关靠 F4 选项菜单 + `user.yaml` |
| octagram / predict | `rime.dll` **不含**（实测无 `octagram`/`predictor`/`predict_translator` 字符串）→ 需重编，**排除** |
| 免编译扩展点 | `rime.dll` **含** `librime-lua`（`LuaTranslator`）→ **Lua 是唯一免编译扩展点** |

## 4. 架构

```
┌─ 冻结层（绝不重编，只用 output\ 产物）────────────────────┐
│ WeaselServer.exe · weaselx64.dll(TSF) · rime.dll          │
│ WeaselDeployer.exe  ← /deploy = "应用更改"                │
└───────────────────────────────────────────────────────────┘
        ▲ 读 %APPDATA%\Rime\*.yaml ；env 仅在进程启动时读一次
┌─ 免编译扩展层（本设计全部工作在层内）──────────────────────┐
│ 1) Rime 配置  : *.custom.yaml / weasel.yaml ；user.yaml 的 var/option/* │
│ 2) Lua 过滤器 : gloss_filter.lua 等（librime-lua 已内置）  │
│ 3) Python AI  : LanguageInputModelHost.exe（本地模型）     │
│ 4) 新设置应用 : 独立 Python 托盘程序（本次主交付）          │
└───────────────────────────────────────────────────────────┘
```

设置应用是**独立进程**，不注册 TSF、不进 System32，故**自身可完全便携**。

## 5. 关键机制（rev3：以两轮审阅的源码行号为准）

### 5.1 配置读取与生效
- 用户目录：`HKCU\Software\Rime\Weasel\RimeUserDir`，否则 `%APPDATA%\Rime`。
- **不热重载**：`*.custom.yaml`/`weasel.yaml` 改动需 `WeaselDeployer.exe /deploy`；**选项**改动见 §5.5（并非只需重启）。

### 5.2 翻译后端（rev3 再修正）

**env 只控制"传输层"**（是否外接、端点、Key、外接**模型**、语言码可用性），**不控制语言选择**。

事实（`LanguageInputRemote.cpp:36-39, 504-517, 535-544, 978`；`RimeWithWeasel.cpp:125-129`）：
- env **仅在 WeaselServer 进程启动时读一次**。
- 真值只认 `1/true/yes/on`。
- **变量不存在** 且本地 host 文件存在 → **本地**；真值 → **外接**；**假值（`0`/`false`）→ AI 传输禁用**。
  ⚠️ rev1 的"不设/假 → 本地"是错的；正确做法是**删除**变量来选本地。
- ⚠️ **rev3 更正（rev2 写错）**：语言**不是**全局固定的。`LanguageInputRemote.cpp:978` 对**两种后端**都执行 `request_config.language = job.language` → **语言是逐会话选项**（`language_input_en/ja/es`）。只有**外接模型**是 env 控制。
- 外接还需 `LANGUAGE_INPUT_REMOTE_LANGUAGE` 为合法语言码（`IsUsable`），否则外接不可用。

| 目标 | 做法 |
|---|---|
| 本地模型 | **删除** `LANGUAGE_INPUT_REMOTE_ENABLED`（及其它 `LANGUAGE_INPUT_REMOTE_*`） |
| 外接 API | `=1` + `URL` + `API_KEY`（+ `LANGUAGE` 作初始可用性） |
| AI 传输关闭 | `=0`。注意：**词典译注仍可能显示**，见 §5.7 |

**缓存（rev3 更正）**：缓存键用 `job.model`，取自 Rime 选项（QuickMT/M2M100），**不是**实际外接模型。且切换/重启**不会**清缓存。
→ 改**端点 / 外接模型 / 后端**后，必须**在 server 停止后**清 `ai_cache_v3.json`；不要指望改 `LANGUAGE_INPUT_REMOTE_MODEL` 就能隔离缓存（冻结 C++ 不把它用于缓存键）。

**env 注入**：写 `HKCU\Environment` **不会**更新已运行进程；子进程继承**父进程**环境块。
→ 必须：应用内 `env = os.environ.copy()` → 按上表 set/remove → 用该 env 启动 `WeaselServer.exe`。用 `REG_SZ`（非 `REG_EXPAND_SZ`）。

**重启**：发 `/q`（异步）→ **轮询**至进程退出 + 管道消失（带超时）→ 以注入 env 启动（无参数即自替换：关闭旧实例、重试 10×50ms）→ **验证**（IPC / `/health` / 真实译注）。

**⚠️ rev3 新增（Codex #4）**：仅靠 HKCU 自启动**无法独占启动权**。TSF 在重连失败后会自行执行 `start_service.bat` 拉起 server（`WeaselTSF.cpp:242-258`，除非 `WeaselDeployerExclusiveMutex` 已存在）→ 会在我们的 stop/写入/安装窗口内**重新引入陈旧 env 与文件占用**。
→ 事务期间必须**持有 `WeaselDeployerExclusiveMutex`** 抑制 TSF 自动恢复；可控启动路径尽量经由带 env 的启动器；并在整个生命周期内**核对**被替换的进程。

### 5.3 本地模型路径
- host：`<WeaselServer 同目录>\LanguageInputModelHost.exe`
- catalog：`<exe 目录>\data\language_input\models\packs-v2.json`、`m2m100-packs-v1.json`
- 模型根：`<RimeUserDir>\language_input\models\`
- AI 缓存：`<RimeUserDir>\language_input\ai_cache_v3.json`

### 5.4 用户数据目录重定向（事务化）
- 改 `RimeUserDir` 后新目录**为空**；词典、`.userdb`、`build\`、模型、缓存**不会自动迁移**。
- Rime 的 C++ 侧**不展开环境变量**且按 `MAX_PATH` 截断；host 的 Python 侧**会** `expandvars` → **绝不写 `%APPDATA%` 这类路径**。
- **迁移必须是事务**（rev3）：**停止 server → 静置 → 复制到 staging → 校验 → 改注册表 → 部署 → 重启并验证**；**原目录在成功前保留**。定义目标已存在时的冲突处理与**跨盘**行为。仅"重启并重建"不能保护**运行中的 LevelDB 拷贝**。

### 5.5 选项与本地后端（rev3 关键修正）
- 选项持久化在 `user.yaml` 的 **`var/option/<name>`**（`switch_translator.cc:64,132`；`librime/src/rime/switcher.cc:44`）；`user.yaml` 是普通 user_config，**不是** patch 文件。
- ⚠️ **rev3 新增（Codex #1，承重）**：`engine.cc:87` 先 `RestoreSavedOptions()`，随后 `engine.cc:394 InitializeOptions()` 会应用 schema 中**显式 `reset:` 值**。两个 Language Input schema 对 **AI / model / language / gloss / speech** 开关都写了 `reset` → 通过 `user.yaml` 选的 M2M100/日语**可能在会话创建时立刻被重置**。
  → 必须：**先通过 schema/config 改动去掉这些开关的显式 `reset`**（或明确管理其默认值），**部署该改动**，再验证：跨新会话、跨方案切换都能保持。
  → **语言是单选组**，必须写成**互斥组**。
- 本地后端：选项 `language_input_model_m2m100` 假 → `quickmt-gloss-route-v2`；真 → `m2m100-418m-int8`。

### 5.6 配置写入规范（rev3 收敛）
- 写前备份 + 原子写（temp+`os.replace`）+ UTF-8 无 BOM + LF + 2 空格。
- **行尾统一 LF（rev7）**：**不保留 CRLF**。应用写出的每一个文件都是 LF；`.gitattributes` 亦不再给 PowerShell 保留 CRLF 例外。
- **`weasel.custom.yaml` 用 ruamel round-trip 编辑**：读写都按**扁平路径**解析（`style/font_point` 与嵌套 `style:\n  font_point:` 等价），保留注释与无关结构；`<schema>.custom.yaml` 同法。`user.yaml` 仍用定点文本编辑（见下）。
- **实机写 `user.yaml` 必须在停 server 的窗口内**：由 `server.server_stopped` 事务完成（持 `WeaselDeployerExclusiveMutex` → 停 server/等 host → 原子写 → 以原 env 重启）。
- **`user.yaml` 有覆盖竞态**：Server 以 `auto_save=true` 在内存持有并会整档回写 → **停 server → 等退出 → 原子写 → 启动**；写入**必须保留** `var/last_build_time`、`var/previously_selected_schema`、`var/schema_access_time` 及既有 `var/option/*`。
- **修正（Codex）**：不要一概断言"往返解析必然破坏"。`__include`/`__patch`/斜杠路径是**普通 YAML**（`config_data.cc:39` 用 `YAML::Load`）→ 应**实测保持性**（section 往返对比），仅在实测失败时改用定点块替换。定点替换还需处理**流式映射、带引号键、既有嵌套映射**。
- **配置归属要有落点**：应用自有的 YAML 文件若不被现有配置 `__include`/`__patch` 引用则**无效**；必须写明挂接点与优先级。
- `/deploy`：**带超时**运行；**退出码 0 ≠ 成功**（`Configurator.cpp:144` 分别部署 Rime 与 Weasel 配置且忽略两者返回值）；两个互斥体冲突返回 1。

### 5.7 译注开关的真实语义（rev3 新增，Codex #3）
- env 只控制 **C++ AI 服务**。**词典译注由独立的 Lua 过滤器产出**：当 `language_input_gloss=true` 且 `language_input_ai=false` 时**仍会**显示英文注释（`output\data\lua\language_input\gloss_filter.lua:142`）。
- 且该 Lua 的 option 通知器会在**日语/西语**下**强制把 AI 打开**。
- → **不能**把开关当独立布尔量。UI 必须建模为**受约束的选择**：
  - "总注释开关" 应控制 **`language_input_gloss`**；
  - model / AI / backend / language 之间要**遵守 Lua 的强制关系**，不显示运行时会拒绝的状态。

## 6. 设置应用设计

- **技术栈（rev4 调整）**：Python 3.11 + **tkinter（内置）+ pystray + Pillow**。
  - 原因：本机到 PyPI ~0.02 MB/s、镜像不可达 → **PySide6 实际无法安装**。tkinter 随 Python 内置；`pystray`+`Pillow` 仅数 MB（实测 9 秒装完）。
  - 代价：造型能力弱于 QSS（改用 ttk 主题 + 自绘）；若日后拿到 PySide6 wheel 可切回（GUI 层已隔离）。
  - 许可：tkinter/Tk（BSD 类）、Pillow（MIT-CMU）、**pystray（LGPLv3）**——C4 个人使用无碍。
- **常驻**：`setQuitOnLastWindowClosed(False)`；关窗=隐藏到托盘；**单实例**用命名互斥体 + `QLocalServer`。
- **托盘**：独立图标。⚠️ `output\data\weasel.yaml` 中 `display_tray_icon: false`，需实测 WeaselServer 自带图标是否默认关闭（§13）。
- **自启动**：注册 `HKCU\...\Run`（**必要但不充分**，见 §5.2 的独占启动问题）。
- **依赖说明**：应用**捆绑**运行库，但底层前提（MSVC 运行时）仍需澄清（rev3 nit）。
- **页面**：
  1. 概览 — 方案 / 译注开关 / 后端 / 模型状态
  2. 翻译 — 本地 ↔ 外接 API（按 §5.2 语义表）+ 端点/Key/模型/语言 + 连通性测试
  3. 模型 — 包列表、HF 下载、依赖联动、从源安装、后端切换
  4. 词库与记忆 — 学习开关 + 同步/导出（**不含**词频列表，见 §8）
  5. 外观 — 候选窗配色 / 字号（写 `weasel.custom.yaml`）
  6. 按键与开关 — F4 隐藏开关 → 受约束的可视化配置（按 §5.7）
  7. 关于 + 重新部署按钮

## 7. 模型下载与管理设计

### 7.1 上游来源（已核实，公开、无需 token）
| 组件 | HF 仓库 | 固定 revision | 可下载 |
|---|---|---|---|
| quickmt-zh-en | `quickmt/quickmt-zh-en` | `c27cc802…` | ✅ 已是 CT2 格式 |
| quickmt-en-ja | `quickmt/quickmt-en-ja` | `c09e98b8…` | ✅ |
| quickmt-en-es | `quickmt/quickmt-en-es` | `430b7889…` | ✅ |
| m2m100-418m-int8 | `facebook/m2m100_418M` | `55c2e61b…` | ❌ **不能从 HF 直接得到** |

- **M2M100 不可由 HF 复现**：catalog 钉的是 **CT2 INT8 转换产物**（`model.bin` a18269…、`runtime_bytes 499,724,092`），HF 只有 `pytorch_model.bin`（1,935,796,948 B），转换命令已丢失（R7）、非字节可复现 → **只走已验证 `.limodel` 导入**。

### 7.2 语言路由
- **QuickMT = 英语中转**：`ja = [zh-en, en-ja]`、`es = [zh-en, en-es]` → **zh→en→目标**，必须先装 zh-en。
- **M2M100 = 直译**。
- 设置界面须**依赖联动**并显示当前后端。

### 7.3 下载与安装实现（rev3 修正）

**信任根**：
- `packs-v2.json` **只有整包**哈希、**没有 `files[]`**；QuickMT 的**逐文件**清单只在 `language-input\models\components\quickmt-*.json`（**未** stage 到 `output\data`）。
- → **必须把该清单打进设置应用**作为校验依据。
- ⚠️ **绝不给 catalog 加字段**（host `load_pack_catalog` 要求精确键集合，多键 `ValueError` 且破坏运行中的 host）；下载元数据放**独立文件**。

**安装布局合成**：
- 目录名 **必须** == catalog `component_id` == `manifest.component_id`。
- `installed.json` 顶层键**恰好** `{format, component_id, pack_size, pack_sha256, manifest}`；`format` = `language-input-installed-component-v1`（M2M100 为 `...-installed-m2m100-component-v1`）。
- **修正（Codex）**：host 用 `json.loads` + 结构校验（`language_input_model_host.py:499`）→ 排序/缩进/尾 LF 是**一致性约定，非字节要求**；仍建议与 host 写法一致以减少混淆。
- QuickMT：`installed.json` + `model/<files>`。M2M100：另需组件根 **byte-exact** 的 `LICENSE.txt`/`NOTICE.txt`。
- host 加载时**只**逐文件校验 `manifest.files[].size/sha256`（路径须在 `model/` 下），**不**校验整包 `pack_sha256`。

**替换已存在组件（rev3 新增，Codex #5）**：
- 仅 staging + `os.replace` **不够** —— 目标是**已存在且非空**的目录时会失败。**必须复用 host 的 backup/swap/rollback**（`language_input_model_host.py:583`：把 target 改名为 backup → 装 staging → 失败恢复），并**为两次 rename 之间被中断**加恢复。
- **必须等 host 进程退出**，不只是 server：`LocalHostProcess::Stop()` 只关闭 kill-on-close job，**不等待子进程终止**（`LanguageInputRemote.cpp:434`）。

**离线/手动导入**：host `--import-pack` 要求与 catalog **精确匹配**（`audit_pack`）→ 用户自打包会被拒；离线走"**指向源文件 + 清单 → 合成布局**"。

**下载健壮性**：磁盘空间预检、断点续传、HF LFS/Xet 重定向、代理/TLS、超时、半成品清理、**输入时不下载**。
**性能**：host 在**每次** `translate()` 都重算所有模型文件哈希（约 400MB–1.2GB 读/请求）→ V4/V6 时延预期需按此重估。

### 7.4 体积
QuickMT 三件套 ≈ 1.22GB；M2M100 ≈ 500MB。

## 8. 词频记忆设计（rev3）

- **学习已默认开启**（`luna_pinyin.userdb` 已存在）；本项 = **验证 + 调优**。
- **已决定：设置界面不列出"常用词 / 次数 / 权重"。**
  - **理由修正（Codex）**：这不是"技术上不可能"——`export_user_dict` 能把 userdb 导出成文本（`DictManagementDialog.cpp:190`）。这是**主动取舍**（省工程量）。
- **学习开关文案要准确**：`translator/enable_user_dict=false` 会**连"使用"已学词条一起禁用**，不只是停止新学习（`user_dictionary.cc:580`）→ 文案须写"完全停用用户词典"。
- 置顶词可用 **`custom_phrase`**（已确认存在，`luna_pinyin.schema.yaml:69`，`db_class: stabledb` → **只读、无动态调频**）。
- octagram / predict **不做**（需重编）。

## 9. 打包与安装设计

- **设置应用 → exe**：PyInstaller `--onedir --windowed`（`build_model_host.ps1:74-79` 即此法，免 VS）。用户机器**无需装 Python**。rev4 改用 tkinter+pystray 后**不再打包 Qt DLL**，体积显著更小。
- **自定义安装目录**：现有 NSIS 含 `MUI_PAGE_DIRECTORY`，文件落 `$INSTDIR\weasel-<版本>\`；静默可用 `/D=`。
- **重打安装器**：`makensis`（**非 VS**；注意会**丢掉 CI 签名步骤**）。
- **数据目录**：见 §5.4 的**事务化迁移**。
- **卸载完整性**：现有卸载器只删 `$INSTDIR` 顶层与显式 `RMDir`，**不会**删：嵌套 onedir 应用目录、`HKCU\Run`、应用写的 env、备份、DPAPI 密钥、以及**重定向后的** `<userdir>\language_input\models\`；且卸载器**以管理员运行**，清理 HKCU 会落到管理员 hive。
- **真·绿色免安装版：不可行**（TSF 必须 `regsvr32` + `ITfInputProcessorProfiles` + System32 + HKLM）。
- **其他**：本地化（zh-Hans/zh-Hant/en）、SmartScreen/杀软（onedir 优于 onefile；建议签名）、VC++ 运行时前提、`--windowed` 下日志需落文件。rev4 移除 PySide6/Qt（无 Qt 体积与 LGPL 负担）；保留 Pillow（MIT-CMU）与 pystray（LGPLv3）的许可通知。

## 10. 验证计划（rev3）

| # | 项 | 证据 |
|---|---|---|
| V0 | **二进制与行为**（强化） | 记录冻结二进制**哈希** + **行为探针**验证具体语义（仅 `strings` 只是线索，不足以证明与源码等价） |
| V1 | 词频重排 | 同拼音连续上屏非首选词 N 次 → 记录候选前移 + userdb commits/tick 增长 |
| V2 | 设置应用 | 改一项 → 原子写 → 备份存在 → 生效；判定见 V3 |
| V3 | **部署成败**（重写） | **非零 = 失败/忙**；**零仍需**：校验生成配置中目标键、限定本次调用的日志、运行时行为。回滚必须**恢复源配置并成功重新部署**，或**在停止态**恢复一致的生成快照 |
| V4 | 译注 | 候选栏出现目标语言注释（截图级）；按 §7.3 性能注意重估时延 |
| V5 | **后端与开关**（重写） | env 三态（absent→本地 / `0`→AI 传输禁用 / `1`+URL+KEY→外接）；**区分"AI 传输禁用"与"所有注释隐藏"**；验证语言**单选组**跨会话保持 |
| V6 | 模型安装 | 合成布局被 host 接受 → 译注生效；翻转 1 字节 → 被拒 |
| V7 | 免编译 | 全链路不调用 msbuild/VS 任何工具 |
| V8 | 打包 | onedir 产物在**未装 Python** 环境（VM）可运行 |
| V11 | **事务期抑制**（新） | 持 `WeaselDeployerExclusiveMutex` 时，TSF 不会在 stop 窗口内自行拉起 server |
| V12 | **组件替换**（新） | backup/swap/rollback 正确；两次 rename 间中断可恢复；host 进程确已退出 |
| V13 | **选项保持**（新） | 去掉 schema 显式 `reset` 后，`var/option/*` 的后端/语言在**新会话**与**跨方案**都保持 |

## 11. 风险与未决问题

| # | 风险 / 未决 | 处置 |
|---|---|---|
| R1 | 冻结二进制与源码不一致 | **V0**（哈希 + 行为探针） |
| R2 | ~~installed.json 格式~~ **已解决** | 5 键 schema 已核实（§7.3） |
| R3 | API Key 明文 | **已定：DPAPI + 仅注入子进程 env**；不写 `HKCU\Environment` |
| R4 | env 改动需重启 server | §5.2 停→轮询→注入启动→验证 |
| R5 | 部署互斥（返回 1） | 串行化 + 超时；**不看退出码** |
| R6 | 托盘图标默认/重复 | M0 实测 `display_tray_icon` |
| R7 | M2M100 转换命令缺失 | 只走已验证 `.limodel`（§7.1） |
| R8 | 内网无外网 | 保留手动导入 |
| R9 | 合成布局一致性 | V6 实测 |
| R10 | QuickMT 逐文件信任根 | 打包 `components\quickmt-*.json`（§7.3） |
| R11 | `user.yaml` 覆盖竞态 | 停 server → 原子写 → 启动（§5.6） |
| R12 | YAML 往返保持性 | **改为实测保持性**（section 往返对比），而非一概文本替换 |
| R13 | LevelDB 词库 | **已定：不做词频列表**（主动取舍） |
| R14 | `RimeUserDir` 重定向丢数据 | **事务化迁移**（§5.4） |
| R15 | ~~外接语言全局固定~~ **rev3 更正** | 语言是**逐会话**（`LanguageInputRemote.cpp:978`） |
| R16 | `--import-pack` 拒绝自打包 | 离线走"从源合成"（§7.3） |
| R17 | 卸载残留 | §9 |
| R18 | host 每次调用重算全模型哈希 | 时延预期重估 |
| **R19** | **schema 显式 `reset` 会覆盖保存的选项** | **先改 schema 去掉 reset 再部署**（§5.5，V13） |
| **R20** | **缓存键用 Rime 选项而非外接模型** | 改端点/模型/后端后**停止态清缓存**（§5.2） |
| **R21** | **TSF 会在事务窗口自行拉起 server** | 事务期持 `WeaselDeployerExclusiveMutex`（§5.2，V11） |
| **R22** | **替换已存在组件需 backup/swap/rollback** | 复用 host 逻辑 + 等待 host 进程退出（§7.3，V12） |
| **R23** | **词典译注与 AI 开关相互独立（Lua 强制）** | UI 建模为受约束选择（§5.7，V5） |
| **R24** | 配置归属缺少挂接点 | 写明 `__include`/`__patch` 挂接点与优先级（§5.6） |
| **R25** | **GLM 第三方审阅**（审阅当前 tree）发现的实机缺陷 | ✅ 全部核实为真并修复（B1–B4 / S1–S8 / N1–N4，见 §0「GLM 审阅处置」） |
| **R26** | **实机事务阻塞 Qt 主线程**（apply / sync / neutralize / install 期间 UI 冻结） | ✅ **已完成（D1）**：`TransactionWorker(QObject)` 移入每次新建的 `QThread`，只发 `started(str)` / `finished(object)` / `failed(str)`，主线程槽更新 UI（worker 绝不碰 `QWidget`）。各页共享 `BusyStrip`（不确定 `QProgressBar` + 状态行），事务期间禁用本页控件、完成后在既有状态条显示结果；**无「取消」按钮**（中止会留下停掉的输入法服务）。托盘「退出」在事务期间拒绝退出并提示。 |

## 12. 里程碑（先验证，再写码）

0. **M0 去风险实验（无 UI/无打包）**
   1. **env/重启探针**：absent / `0` / `1` 三态 + 停→轮询→注入启动→验证（§5.2）
   2. **合成布局探针**：`components\quickmt-zh-en.json` + 真实文件 + 合成 `installed.json` → host `--list`/`--serve` 通过；翻转 1 字节 → 被拒（§7.3）
   3. **`user.yaml` 存活探针**：写 `var/option/...` + `/deploy` vs 停→写→启动；喂坏 YAML 确认仍返回 0（§5.5/§5.6/V3）
   4. **二进制特性探针（V0）**：哈希 + `strings`
   5. **选项 reset 探针（V13/R19）**：确认 schema `reset` 是否覆盖保存值；验证去掉 reset 后保持
   6. **事务期抑制探针（V11/R21）**：持 `WeaselDeployerExclusiveMutex` 时 TSF 是否停止自动拉起
1. **M1 骨架**：tkinter+pystray 托盘 + 设置窗 + 单实例 + 配置读写 + `/deploy`（V2/V3/V7）
2. **M2 翻译页**：后端切换（§5.2 表）+ env 注入 + 重启 + 停止态清缓存 + 受约束开关（§5.7）（V5）
3. **M3 模型页**：HF 下载（QuickMT）+ 逐文件校验 + 从源合成安装 + backup/swap/rollback + 依赖联动 + M2M100 `.limodel` 导入（V6/V12）
4. **M4 记忆页**：学习开关（文案准确）+ 同步/导出（**无词频列表**）；V1 验证"常用词排前"
5. **M5 外观 + 按键页** + 美化（交 @designer，QSS）
6. **M6 打包**：PyInstaller onedir + 自启动 + 卸载完整性 + NSIS 集成（V4/V8）

## 13. 决策记录

| # | 决策 | 状态 |
|---|---|---|
| 1 | C1a：允许 `makensis` / `PyInstaller` 等**非 VS** 打包工具 | ✅ 已定 |
| 2 | 凭据：**DPAPI + 仅注入 WeaselServer 子进程 env** | ✅ 已定 |
| 3 | 词库：**不做词频列表**（主动取舍，非技术限制） | ✅ 已定 |
| 4 | 第 1 轮 council（3/4 席）+ 第 2 轮 **Codex CLI 审阅已完成** | ✅ 已定 |
| 5 | 托盘 `display_tray_icon` 默认值与自启动顺序 | ⏳ M0 实测 |
| 6 | gemini 席位未运行（可选补第 5 席） | ⏳ 可选 |
| 7 | §5.5 的 schema `reset` 改动（R19）需先落地并部署 | ⏳ M0 验证后 |
| 8 | **GUI 栈由 PySide6 改为 tkinter + pystray + Pillow**（网络装不下 PySide6） | ✅ 已定（rev4） |
| 9 | 实现位置：`F:\documents\software\langInput\settings-app\`（当前目录） | ✅ 已定 |
| 10 | M0 与实现进展：V0 ✅（R1 关闭）、合成布局 ✅（R9 通过）、M1 骨架 ✅（`--selftest` 通过） | ✅ 已完成 |
| 11 | **模型来源 = 本地 artifacts 离线导入**（无需下载）；已导入 3 个 QuickMT 包 | ✅ 已完成 |
| 12 | **下载器须默认绕过代理**（或可配置）—— 代理限速 ~200× | ⏳ 待改代码 |
| 13 | GUI 栈：tkinter+pystray 已可用；**是否换回 PySide6 待定**（直连可装） | ⏳ 待用户定 |
| 14 | M0 剩余 3 项（env 三态 / user.yaml 存活 / bad-YAML 退出码） | ✅ 已完成（rev6，见 M0 表） |
| 15 | GUI 栈最终 = **PySide6**（已装 6.11.2，GUI 层已回迁并验证） | ✅ 已完成 |
| 16 | M2M100 不做（本地缺 license/notice）；新模型调研结论：**无更优候选** | ✅ 已定 |
| 17 | 下载器绕过代理 | ⏳ 待改代码 |
| 18 | M2 翻译页 / M4 记忆页 / M5 外观与按键页 / M6 打包 | ✅ 全部完成 |
| 19 | 三页功能化（外观 / 按键与开关 / 词库与记忆） | ✅ 完成并实测（测试改动均已回滚，`user.yaml` / `weasel.custom.yaml` 哈希与起始一致） |
| 20 | 外部 UI 审阅（**agy** / Google Antigravity，独立模型） | ✅ 完成。C1–C6 复核确认为真并已修复；**C7 部分不成立**（"API 卡片会被禁用"为误判，按钮位置问题为真） |
| 21 | 代码仓：`F:\documents\software\langInput`（`third-party/` 已 gitignore；`.gitattributes` 全源文件 **LF**，含 PowerShell） | ✅ 已建 |
| 22 | **Codex CLI 复审** | ⏳ **未完成**：撞 ChatGPT 用量上限（`try again at 3:40 AM`），退出码 1、无产出 → 需重试 |
| 23 | **GLM 第三方审阅**（独立模型，审阅当前 tree） | ✅ 完成。B1–B4 / S1–S8 / N1–N4 **全部 CONFIRMED 并已修复**；无 NOT-REPRODUCED 项（§0「GLM 审阅处置」） |
| 24 | ~~**D1（暂缓，不实施）**：实机事务阻塞 Qt 主线程~~ → **已实施**：`QThread` + signal/slot，worker 不碰 `QWidget`；各页共享 busy 进度呈现 | ✅ 已完成（R26） |
| 25 | 行尾统一 **LF**（不再保留 CRLF）；移除 `.gitattributes` 的 PowerShell CRLF 例外 | ✅ 已定并落地 |
