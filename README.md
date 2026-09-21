# Language Input — 个人输入法设置工具

一个自用的 Windows 输入法增强项目：在你打字时，**候选栏后面显示另一个语言的翻译**，帮助语言学习。

本仓库只包含**新写的部分**（设置应用）。底层的输入法引擎是一个独立的 Weasel/小狼毫(Rime) 分支，
位于 `F:\documents\software\languageInput`，**已冻结、不在此仓库内、也不在这里修改**。

---

## 目录

```
langInput/
├─ settings-app/                  ← 本仓库的产品代码
│   ├─ src/language_input_settings/
│   │   ├─ paths.py               解析 RimeUserDir / WeaselRoot / 模型目录 / 可执行文件
│   │   ├─ yaml_io.py             原子写（UTF-8 无 BOM + LF）、备份、user.yaml 选项编辑
│   │   ├─ deploy.py              调用 WeaselDeployer /deploy（硬超时，退出码 1 = 忙）
│   │   ├─ appconfig.py           %APPDATA%\LanguageInput\config.json + DPAPI 密钥加解密
│   │   ├─ env_config.py          本地模型 / 外接 API / 关闭 三态环境变量语义
│   │   ├─ server.py              停 → 轮询 → 注入 env 启动 → 读回校验；maintenance_guard
│   │   ├─ schema_patch.py        用 <schema>.custom.yaml 中和 schema 的 reset（使选项可持久）
│   │   ├─ gloss_badge.py         简洁译注：用户目录 Lua 影子副本，去掉 〔en·词〕 标签
│   │   ├─ models_catalog.py      供应商目录（内置 quickmt 描述符为信任根）
│   │   ├─ models_download.py     stdlib 下载（断点续传 + SHA256 + 默认绕过系统代理）
│   │   ├─ models_install.py      合成 installed.json + 备份/换入/回滚
│   │   ├─ rime_settings.py       外观（weasel.custom.yaml）、开关、热键、学习/词典
│   │   ├─ cli.py                 所有命令行动作
│   │   └─ app.py                 PySide6 托盘应用（七个页面）
│   ├─ assets/                    图标、QSS 主题
│   ├─ config/download-sources.json
│   ├─ packaging/                 PyInstaller 打包 + 自启动 + 卸载脚本
│   └─ docs/                      设计文档（含两轮独立审阅记录与 M0 实测结论）
└─ third-party/                   （已 gitignore）上游参考克隆：MSIME-Windows / qingjian / weasel
```

## 运行

需要 Python 3.11（仓库不含虚拟环境）。本项目开发时用的是 uv 管理的 CPython 3.11.15。

```powershell
# 建虚拟环境并装依赖
uv venv --python 3.11 settings-app\.venv
uv pip install --python settings-app\.venv\Scripts\python.exe PySide6-Essentials ruamel.yaml

# 自检（只读，不改你的输入法）
$env:PYTHONPATH="settings-app\src"
settings-app\.venv\Scripts\python.exe -m language_input_settings --selftest
settings-app\.venv\Scripts\python.exe -m language_input_settings --gui-smoke

# 打开界面
settings-app\.venv\Scripts\python.exe -m language_input_settings
```

## 打包（目标机无需 Python）

```powershell
settings-app\packaging\build.ps1            # 产出 settings-app\dist\LanguageInputSettings\
settings-app\packaging\autostart.ps1 -Enable
settings-app\packaging\uninstall.ps1 -DryRun
```

## 设计原则（都是踩过坑换来的）

- **零 C++ 编译。** 只改 Rime 配置、Lua、Python 与独立前端；C++ 层完全冻结。
- **所有文本写入：UTF-8 无 BOM + LF + 原子替换 + 先备份。**
  一个 BOM 就会让宿主静默跳过 `installed.json`。
- **`/deploy` 的退出码不可信**（配置写错也返回 0）；成败要看 stderr 与编译产物。
- **环境变量只在 WeaselServer 启动时读一次**，且必须显式注入子进程；"关闭"是 `=0`，
  而"用本地"是**删掉**变量（不是设 0）。
- **用户目录的 Lua 会覆盖安装版**，所以改译注显示不需要管理员、也不动冻结源码。
- **下载默认绕过系统代理**（本机代理限速约 200 倍）。

完整的设计、风险表与验证计划见 `settings-app/docs/2026-09-20-language-input-settings-app-design.md`。
