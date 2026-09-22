# Language Input Settings (设置应用)

A standalone **PySide6 (Qt for Python)** tray application for the
**Language Input** project (the Weasel/Rime fork described in
`docs/2026-09-20-language-input-settings-app-design.md`).

This is the **M1 skeleton**: the tray icon, the settings window with seven
placeholder pages, single-instance guarding, app-owned config, the deploy
helper, and path resolution.  Feature pages arrive in later milestones.

> **GUI stack note (design doc §6 / decision #13).**  The original plan used
> PySide6; a temporary pivot to **tkinter + pystray + Pillow** was made because
> PySide6 appeared un-installable on this machine.  That conclusion was an
> **environment / network artifact**: a proxy throttle (`~42 KB/s` through the
> proxy vs `~8.8 MB/s` direct) made PyPI look unreachable.  With the proxy
> bypassed PySide6 installs in seconds, so the GUI layer has been **ported back
> to PySide6** and the interim tkinter/pystray stack is no longer used or
> depended on.  The rest of the application is GUI-agnostic.

## Hard rules this app follows

* **Zero compilation.** It is pure Python and never builds C++ (no
  `msbuild`/Visual Studio).
* Every text file it writes is **UTF-8 without BOM**, **LF newlines** and
  **atomic** (temp file in the same directory, then `os.replace`).
* Existing files are backed up before being overwritten
  (`<name>.bak-<YYYYmmddTHHMMSSZ>`).
* It **never** round-trip-parses/rewrites Rime's existing YAML.  It only
  writes app-owned files or performs one surgical, byte-preserving edit of
  `var/option/<name>` in `user.yaml`.
* `--selftest` is strictly read-only with respect to the user's Rime data: it
  only creates, reads and deletes files inside a private temp directory.

## Layout

```
src/language_input_settings/
  __init__.py     __version__
  paths.py        registry / path resolution (read-only)
  yaml_io.py      atomic UTF-8/LF writes, backups, surgical user.yaml edit
  deploy.py       bounded `WeaselDeployer.exe /deploy` runner
  appconfig.py    %APPDATA%\LanguageInput\config.json + DPAPI secrets
  app.py          PySide6 GUI, --selftest, --gui-smoke, --version
  __main__.py     `python -m language_input_settings`
assets/
  icon.ico        committed asset (tray + installer icon); never regenerated
  theme.qss       light, neutral QSS theme loaded at startup
```

Threading model: Qt owns the main thread; the window, the `QSystemTrayIcon`
and all callbacks live on it, so no cross-thread marshalling is needed.  The
application calls `setQuitOnLastWindowClosed(False)`, so closing the window
hides it to the tray (with a one-time tray notification) instead of quitting.

## Running

From this directory (`settings-app/`), with the bundled virtual environment:

```powershell
# 1. Headless diagnostic report (no GUI, no QApplication). Read-only.
$env:PYTHONPATH = "src"
& .\.venv\Scripts\python.exe -m language_input_settings --selftest

# 2. Headless GUI construction smoke test (builds QApplication + window +
#    all seven pages + tray icon, then tears down without an event loop)
& .\.venv\Scripts\python.exe -m language_input_settings --gui-smoke

# 3. Version
& .\.venv\Scripts\python.exe -m language_input_settings --version

# 4. GUI (system tray icon + settings window)
& .\.venv\Scripts\python.exe -m language_input_settings
```

Alternatively install it in editable mode (`pip install -e .`) and use the
`language-input-settings` console script.

## Model management (M3)

Model commands print JSON on stdout and download progress on stderr.  They
accept `--model-root DIR` to override the resolved model root (the real one
lives under the live Rime user directory).

```powershell
# List components, installed state and per-language route coverage
& .\.venv\Scripts\python.exe -m language_input_settings --models-list

# Re-hash an installed component against its manifest (ok/fail per file)
& .\.venv\Scripts\python.exe -m language_input_settings --models-verify quickmt-zh-en --model-root <dir>

# Download a component's files into a staging directory (network)
& .\.venv\Scripts\python.exe -m language_input_settings --models-download quickmt-zh-en --dest <dir>

# Synthesize + install from already-downloaded, verified source files
& .\.venv\Scripts\python.exe -m language_input_settings --models-install-from-dir quickmt-zh-en --src <dir> [--replace]

# Import an exact catalog .limodel via the frozen host
& .\.venv\Scripts\python.exe -m language_input_settings --models-install-limodel <path.limodel> [--replace] [--m2m100]
```

Trust model:

* `packs-v2.json` only has a whole-`.limodel` hash, so per-file trust data for
  QuickMT is **vendored** in `src/language_input_settings/data/components/`
  (design doc §7.3 / R10).
* The shipped catalogs are **never** modified (the host requires an exact key
  set).  Download base URL / per-component overrides / mirrors live in the
  app-owned `config/download-sources.json`.
* `installed.json` is written with exactly the 5 keys the host expects, UTF-8
  **without BOM** + LF (a BOM makes the host silently skip the component).
* Replacing a component mirrors the host's backup/swap/rollback and recovers
  from a swap interrupted between the two renames.
* Installation refuses to proceed while `WeaselServer.exe` or
  `LanguageInputModelHost.exe` is running (the server's `Stop()` does not wait
  for its child host process).

## Plain gloss / 简洁译注 (hide the badge)

The candidate window prepends a badge (``〔en·词〕 ``) to every gloss.  There is
no Rime config key to hide just the badge, so the app shadows the shipped Lua
filter with a copy whose marker assignment is empty:

```powershell
# Report the plain-gloss state (JSON)
& .\.venv\Scripts\python.exe -m language_input_settings --gloss-badge status

# Apply: hide the badge (writes a user-dir shadow copy + redeploys)
& .\.venv\Scripts\python.exe -m language_input_settings --gloss-badge plain

# Revert: restore the shipped badge
& .\.venv\Scripts\python.exe -m language_input_settings --gloss-badge fancy
```

Mechanism: ``librime-lua`` resolves ``<user>\\lua\\?.lua`` **before**
``<install>\\data\\lua\\?.lua``, so a copy at
``<user_dir>\\lua\\language_input\\gloss_filter.lua`` shadows the installed
filter with no admin rights and survives a reinstall.  Nothing under the
installation directory is ever modified.  Lua modules are ``require``-cached
per process, so ``/deploy`` (or a ``WeaselServer`` restart) is required before
a new copy takes effect.

> **Caveat — stale shadow copy.**  The shadow file is a **full copy** of the
> shipped filter.  If a future product version changes
> ``gloss_filter.lua``, this stale copy would **shadow** the new version, so
> the change would not take effect until the user reverts (``--gloss-badge
> fancy`` / uncheck the box on the 翻译 page).  The settings UI exposes the
> revert for exactly this reason; keep it reachable.

## Notes

* Runtime dependencies are **PySide6-Essentials** and **ruamel.yaml** only.
  `pystray`, `Pillow` and `tkinter` are **not** dependencies of this app.
* `assets/icon.ico` is a **committed asset** (used by the tray icon via
  `QIcon` and by the installer).  It is never regenerated at runtime; no
  imaging library is required.
* `--selftest` requires only the standard library plus the optional
  `ruamel.yaml`; it never creates a `QApplication` and reports whether PySide6
  / ruamel.yaml are importable (with `pyside6_version` and `qt_version`)
  without failing when they are not.
* `--gui-smoke` creates a `QApplication`, builds the window, all seven pages
  and the tray icon, processes pending events, then tears everything down
  without entering the event loop, and prints `gui_smoke: passed`.
* The deploy helper never blocks forever and documents explicitly that
  **exit code 0 does not mean the configuration was valid**
  (`Configurator.cpp` ignores `rime->deploy()`'s return value); callers must
  verify the deployed output separately.

## 打包 / Packaging

构建一个**双击即可运行**的 Windows onedir 应用；目标机器**无需安装
Python**。打包脚本位于 `packaging/`。

| 文件 | 作用 |
|---|---|
| `packaging/entry.py` | 极简启动器：`from language_input_settings.app import main`；**不含任何逻辑**。 |
| `packaging/language_input_settings.spec` | PyInstaller **onedir + `--windowed`**（无控制台）规格；名称 `LanguageInputSettings`，图标 `assets/icon.ico`；**禁用 UPX**。 |
| `packaging/build.ps1` | 构建到 `settings-app\dist\`，按需用 pip 安装 PyInstaller（**清除代理**），最后打印输出路径与体积。 |
| `packaging/autostart.ps1` | `-Enable` / `-Disable` / `-Status`：写 `HKCU\...\Run` 的 `LanguageInputSettings` 值（无需管理员）。 |
| `packaging/uninstall.ps1` | 安全卸载/回滚（默认 `-DryRun` 可预览）。 |

### 构建 / Build

```powershell
# 在 settings-app\ 下运行；使用仓库自带 venv 的 Python。
& .\packaging\build.ps1
# 可选：先清理 build\pyinstaller 与 dist\LanguageInputSettings
& .\packaging\build.ps1 -Clean
```

* 本机代理会严重限速 PyPI，脚本只在**当前进程**内清空
  `HTTP(S)_PROXY` / `ALL_PROXY`，让 pip 直连。
* **不使用 UPX**：Qt 的 DLL 不能被 UPX 压缩。
* 被排除的 Qt 模块（未使用）：`QtQml`/`QtQuick*`、`QtSql`、`QtTest`、
  `QtDesigner`、`QtHelp`、`QtUiTools`、`QtMultimedia`、`QtCharts`、`Qt3D*`、
  `QtWebEngine*`、`QtDBus`、`QtOpenGL*`、`QtConcurrent` 等；同时排除
  `pystray`、`PIL`、`tkinter`。实际只打包
  `QtCore`/`QtGui`/`QtWidgets`/`QtNetwork`。

### 产物布局 / dist layout

```
settings-app\dist\LanguageInputSettings\
  LanguageInputSettings.exe        # 双击启动（GUI 子系统，无控制台）
  assets\  icon.ico  theme.qss     # 运行时资源（bundle 根）
  config\  download-sources.json   # 应用自带下载元数据
  _internal\                       # Python 运行时、Qt DLL/插件、PYZ 等
    language_input_settings\data\components\quickmt-*.json  # 逐文件信任清单
```

> **打包注意（PyInstaller 6 / Qt）。** `app.py` 用
> `Path(__file__).resolve().parents[2] / "assets"` 定位资源；在 onedir 中冻结
> 包位于 `<bundle>\_internal\language_input_settings\`，因此
> `parents[2]` == **bundle 根目录**。PyInstaller 的 COLLECT **不能**把 DATA
> 写到 `_internal` 之外，所以 `build.ps1` 在构建后把 `assets\` 与 `config\`
> 复制到 bundle 根目录（spec 里同时也声明了这些 `datas`）。

### 自启动 / Autostart

```powershell
& .\packaging\autostart.ps1 -Status    # 查看
& .\packaging\autostart.ps1 -Enable    # 启用（幂等）
& .\packaging\autostart.ps1 -Disable   # 关闭（幂等）
```

只写当前用户 hive（`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`），
**无需管理员**。当前应用**没有** `--start-minimized` 命令行开关（"最小化启动"
是 `%APPDATA%\LanguageInput\config.json` 里的持久设置），因此启动项写的是
不带参数的 exe 路径。

### 卸载 / Uninstall

```powershell
& .\packaging\uninstall.ps1 -DryRun              # 只预览，不改动任何东西
& .\packaging\uninstall.ps1                      # 执行
& .\packaging\uninstall.ps1 -RemoveProgramFiles  # 顺带删除 onedir 程序目录
```

删除：自启动项、`%APPDATA%\LanguageInput\config.json`（含其 `*.bak-*`）、
Rime 用户目录下应用产生的 `*.bak-*` 备份、以及**纯译注 shadow**
（`%APPDATA%\Rime\lua\language_input\gloss_filter.lua`，若存在则运行
`WeaselDeployer.exe /deploy` 恢复内置徽标）。**不会**删除已安装的模型包
（`<userdir>\language_input\models\`）；**不会**删除程序目录，除非传入
`-RemoveProgramFiles`。

### 验证 / Verify

```powershell
# 冻结 exe 的自检（只读，创建 QApplication 之前即退出）
& .\dist\LanguageInputSettings\LanguageInputSettings.exe --selftest
& .\dist\LanguageInputSettings\LanguageInputSettings.exe --version
& .\dist\LanguageInputSettings\LanguageInputSettings.exe --gui-smoke
```

> **脚本注意。** exe 是 `--windowed`（GUI 子系统）：直接 `& exe --selftest`
> 会打印 JSON，但 PowerShell **不会等待**它、也拿不到 `$LASTEXITCODE`。
> 要可靠取得退出码，请重定向：
> `Start-Process -FilePath .\dist\...\LanguageInputSettings.exe -ArgumentList '--selftest' -Wait -PassThru -RedirectStandardOutput out.json`。
