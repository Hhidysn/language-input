# 本地模型服务：生命周期与延迟实测

- 日期：2026-09-21
- 环境：本机（Weasel 安装于 `C:\Program Files\Rime\weasel-0.1.0`），已安装 3 个 QuickMT 组件
- 方法：直接以 WeaselServer 所用的同一组参数启动 `LanguageInputModelHost.exe`（额外加 `--idle-seconds 20` 缩短测试），
  轮询 `GET /health`（Bearer token），按 `BuildRemoteGlossRequest` 的形状发真实译注请求。**只测，不改配置。**

## 1. 启动模型：按需，无自启动

代码证据（冻结树，只读）：

| 事实 | 证据 |
|---|---|
| 由 WeaselServer **在有待处理的译注任务时**按需拉起 | `LanguageInputRemote.cpp:957` `Run()` → `:978` `local_host_.EnsureRunning(config_)` |
| 宿主可执行文件取自 **WeaselServer 自己所在目录**（不是服务） | `LanguageInputRemote.cpp:523-524` |
| 命令行含 `--idle-seconds 600` | `LanguageInputRemote.cpp:383-386`（`CreateProcessW` + `CREATE_NO_WINDOW`，`:392-395`） |
| 随 WeaselServer 一起被杀（kill-on-close job） | `LanguageInputRemote.cpp:402-412`（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`） |
| 空闲退出实现与默认值 | `language_input_model_host.py:1275`（`while monotonic() - last_request < idle_seconds`）、`:1424`（`default=600`） |

**自启动排查（全部为否）**：`HKCU/HKLM/WOW6432Node` 的 `Run`、`RunOnce`、服务列表、任务计划程序、启动文件夹
—— **没有任何条目启动 `LanguageInputModelHost.exe`**。只有 `WeaselServer.exe` 在 HKLM `Run` 里。

## 2. 实测延迟

| 指标 | 实测 |
|---|---|
| 冷启动（首次、文件系统冷） | **6 252 ms** |
| 冷启动（空闲退出后再启，文件已缓存） | **2 458 ms**（另两次 2 479 / 2 404） |
| 首次请求（含 INT8 模型加载） | **4 299 ms**（冷盘）/ **2 266 ms**（缓存） |
| **热请求**（宿主活着，5 次采样） | **985 / 1045 / 1049 / 1074 / 1095 ms** → 中位 **1 049 ms** |
| 热请求（zh→ja / zh→es） | 1 029 / 1 003 ms |
| 空闲退出（`--idle-seconds 20`） | **20.52 s**，退出码 0，无残留监听 |
| 峰值工作集（一次 zh→en 后） | **585.3 MB**（私有 806.3 MB） |
| 三个组件全部加载后 | 峰值 **1 028.9 MB** / 私有 1 269.8 MB |

**冷启动口径下的"第一次译注"总代价**：

| 场景 | 总计 |
|---|---|
| 宿主活着（10 分钟内用过） | **~0.94–1.10 s** |
| 空闲退出后重来（典型） | **~4.6–4.8 s** |
| 开机后首次（文件系统冷） | **~10.5 s** |

**单次 zh→en 只加载一个组件**（路由 `en = [quickmt-zh-en]`）；每多一种语言再加 ~231 MB（懒加载，按路由）。

## 3. ⚠️ 重大发现：每次请求都在重算全部模型文件的 SHA-256

`QuickMtRuntime.translate()` 在**每一次**请求里都调用 `installed_components(model_root)`
（`language_input_model_host.py:887`），而它会 `load_installed_component()` → 对**所有已安装组件的所有文件**
逐块 SHA-256（`sha256_file`，1 MiB 分块），总计约 **1.21 GB 读取/请求**。
`/health` 甚至哈希**两遍**（`:1187` 与 `:1189` 各调一次 `available_languages()`）——实测 health 延迟 **1.80–1.87 s**。

也就是说：**"热请求 ~1.0 s"里大约 0.9 s 是重复的完整性哈希，真正的 3 词 CTranslate2 推理只有几十毫秒。**

这与设计文档早期引用的"39–207 ms 基线"差距巨大；**该基线不成立**。

### 影响

- 打字时每次候选刷新都可能触发这 1 秒级开销 —— 这是实际体感的主要瓶颈，**远大于"前端框架快不快"**。
- 它也会让 `/health` 探测（我们设置应用的连通性测试）显得很慢。

### 可修性（重要）

模型宿主**不是 C++**，而是 **Python 脚本 + PyInstaller 冻结**（`scripts/language_input_model_host.py` + `build_model_host.ps1`）。
→ 修复它**不需要 Visual Studio**，只需 Python + PyInstaller + ctranslate2/sentencepiece wheel（本机已有）。
可行的修法：把"组件完整性校验"从**每次请求**降级为**安装时一次 + 显式校验命令**（或加缓存），
热请求可从 ~1.0 s 降到 ~0.05–0.1 s。

> 但这是**改动引擎产物**（替换 `LanguageInputModelHost.exe`）。是否做，需要单独决策；
> 我们的仓库可以以"补丁 + 重建脚本"的形式拥有它，而不是去改冻结的 `languageInput` 源码。

## 4. 直接回答

1. **要开机启动吗？** 不需要。没有任何自启动项。
2. **要后台挂机吗？** 不需要。空闲 **600 秒**自动退出；下次请求自动重新拉起。
3. **切到别的输入法再切回来要重启吗？** 不需要。宿主是常驻 `WeaselServer.exe` 的子进程（后者不会因切换输入法而退出）；
   若已空闲退出，下一次译注请求会自动重启它，用户无需操作。
4. **启动要多久？** 见上表：热 ~1 s；空闲后重来 ~4.6–4.8 s；开机后首次 ~10.5 s。
