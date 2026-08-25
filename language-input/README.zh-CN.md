# Language Input（Windows v0.1）

Language Input 是一个基于小狼毫 Weasel 0.17.4 和 librime 1.13.1 源码构建的 Windows 输入法实验版。它在中文候选后显示简短外语释义，并在用数字键 `1`–`9` 选词成功后异步朗读该释义。

## 已实现

- 默认方案：`译注小鹤双拼`；同时保留 `译注全拼`。
- 每页 9 个候选，本地英文释义默认开启。
- `1`–`9` 选中对应候选后朗读释义；朗读不阻塞按键线程。
- 本地释义来自 CC-CEDICT 生成的确定性 GlossPack，离线可用。
- 可选的 OpenAI Chat Completions 兼容远端缺词查询，默认关闭。
- 密码、PIN 和私密输入范围中关闭释义、朗读、远端访问、远端缓存读写，以及用户词典读取和学习。
- 同时包含 x64 与 Win32 组件，安装程序会按系统选择。

v0.1 不包含 Android 输入法，也不捆绑本地 LLM。当前内置本地词典只有英文；数据格式和远端目标语言已经为后续多语言包预留扩展点。

## 使用

安装后，在 Windows 输入法列表中选择小狼毫。使用小狼毫的方案选单（通常为 `F4`）选择：

- `译注小鹤双拼`：输入 `nihc` 可得到“你好”。
- `译注全拼`：输入 `nihao` 可得到“你好”。

方案中的三个开关为：

- `译注开/关`：控制候选后的本地或远端释义。
- `朗读开/关`：控制数字键选词成功后的释义朗读。
- `远译开/关`：允许显示已经配置并查询到的远端缺词释义；默认关闭。

朗读使用 Windows SAPI。请在 Windows“语言和语音”设置中安装目标语言语音；没有匹配语音时，系统可能使用默认语音。

## 可选远端缺词查询

远端功能需要同时满足两层开关：进程环境变量启用，并在方案中打开 `远译`。服务启动时读取以下变量：

| 变量 | 含义 | 默认值 |
| --- | --- | --- |
| `LANGUAGE_INPUT_REMOTE_ENABLED` | 设为 `1` 才创建远端服务 | 关闭 |
| `LANGUAGE_INPUT_REMOTE_URL` | OpenAI Chat Completions 兼容端点 | `https://api.openai.com/v1/chat/completions` |
| `LANGUAGE_INPUT_REMOTE_MODEL` | 兼容端点的模型名 | `gpt-4.1-mini` |
| `LANGUAGE_INPUT_REMOTE_API_KEY` | Bearer API key；只从环境变量读取 | 无 |
| `LANGUAGE_INPUT_REMOTE_LANGUAGE` | 目标语言 BCP-47 标签 | `en` |
| `LANGUAGE_INPUT_REMOTE_ALLOW_HTTP` | 设为 `1` 才允许明文 HTTP，仅限本机测试 | 关闭 |

示例（变量只传给这次启动的服务进程）：

```powershell
$root = (Get-ItemProperty 'HKLM:\SOFTWARE\Rime\Weasel').WeaselRoot
& "$root\WeaselServer.exe" /quit
$env:LANGUAGE_INPUT_REMOTE_ENABLED = '1'
$env:LANGUAGE_INPUT_REMOTE_URL = 'https://example.com/v1/chat/completions'
$env:LANGUAGE_INPUT_REMOTE_MODEL = 'your-model'
$env:LANGUAGE_INPUT_REMOTE_API_KEY = 'your-key'
$env:LANGUAGE_INPUT_REMOTE_LANGUAGE = 'en'
Start-Process -FilePath "$root\WeaselServer.exe"
```

不要把真实 key 写入源码、配置文件或截图。将 key 持久写入 Windows 用户环境变量会以可被当前用户读取的形式保存在注册表中；v0.1 尚未集成 Windows 凭据管理器。

每次请求最多发送当前页 9 个“本地释义缺失”的候选文字、目标语言和模型名。请求在后台执行，不阻塞输入；结果通常在下一次候选刷新时出现。成功结果缓存于：

```text
%APPDATA%\Rime\language_input\remote_cache_v1.json
```

远端请求禁止重定向，默认只允许 HTTPS。不要在真实输入中启用 `LANGUAGE_INPUT_REMOTE_ALLOW_HTTP`。

## 构建与许可

源代码中的构建、数据来源和验收记录见 `language-input/VERIFICATION.md`。本地英文 GlossPack 是 CC-CEDICT 的改编数据库，按 CC BY-SA 4.0 分发；安装目录 `data\licenses\language-input` 包含完整归属与依赖许可说明。

本地发布包使用 `CN=Language Input Local Build` 的本机自签名证书。签名可以证明安装包和内含的一方二进制在签名后未被修改，但它不是公共商业代码签名信誉。
