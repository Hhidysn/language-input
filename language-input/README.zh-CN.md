# Language Input（Windows v0.2）

Language Input 是一个基于小狼毫 Weasel 0.17.4 和 librime 1.13.1 源码构建的 Windows 输入法实验版。它在中文候选后显示简短的英语、日语或西班牙语词汇提示，并在用数字键 `1`–`9` 选词成功后异步朗读对应释义。

## 已实现

- 默认方案为 `译注小鹤双拼`，同时保留 `译注全拼`。
- 每页 9 个候选；候选译注显示、翻译来源、目标语言和朗读分别控制。
- 词典模式使用内置 CC-CEDICT 英文 GlossPack，完全离线。
- AI 模式使用本机 QuickMT 小模型，支持英语、日语和西班牙语；不会回退到固定词典。
- 三个模型组件都是约 404–410 MB 的按需 `.limodel` 包，不随核心安装包捆绑。
- AI Host 只监听随机的 `127.0.0.1` 端口，使用每次启动随机生成的 Bearer 令牌，并在 10 分钟没有合格请求后退出。
- 密码、PIN 和尚未识别输入范围的控件中，禁止译注、模型请求、缓存读写、朗读和用户词典学习。
- 同时包含 x64 与 Win32 输入法组件；AI Host 为 x64，32 位应用仍可通过小狼毫服务使用它。

v0.2 仍不包含 Android 输入法。AI 输出只是帮助扩展词汇的简短提示，不是权威词典或考试级翻译；孤立的短词和多义词可能选错词义。

## 使用

安装后，在 Windows 输入法列表中选择小狼毫。使用小狼毫方案选单（通常为 `F4`）选择：

- `译注小鹤双拼`：输入 `nihc` 可得到“你好”。
- `译注全拼`：输入 `nihao` 可得到“你好”。

方案中有四组控制项：

- `译注显示：关/开`：总开关。关闭后不查询词典、不启动模型、不读写 AI 缓存，也不显示或朗读译注。
- `翻译来源：词典/AI`：英语固定词典与本地 AI 二选一。AI 模式没有词典回退。
- `目标语言：英语/日语/西班牙语`：日语或西班牙语会自动选择 AI；切回英语时仍保持 AI，除非手动切回词典。
- `朗读：关/开`：用数字键 `1`–`9` 选中对应候选后，朗读当前译注文字。

AI 译注带有明确来源标记，例如 `〔en·AI〕 hello; hi`、`〔ja·AI〕 こんにちは`、`〔es·AI〕 hola`。词典译注沿用 `[en]` 标记。

朗读使用 Windows SAPI。请在 Windows“语言和语音”设置中安装目标语言语音；没有匹配语音时，系统可能使用默认语音。

## 导入本地 AI 语言包

安装程序只包含约 118 MB 的本地推理 Host，不包含模型。安装后从开始菜单打开 `Language Input AI 语言包管理`，逐个选择 `.limodel` 文件导入。导入时会校验整包和包内每个文件的 SHA256；不匹配或依赖缺失的包会被拒绝。

| 用途 | 文件 | 大小 | SHA256 |
| --- | --- | ---: | --- |
| 英语基础包 | `quickmt-zh-en-c27cc802.limodel` | 409,710,191 B | `fc1df39f7620febd18256a5ce13082e83543dc240d140a8281926044087065a7` |
| 日语增量包 | `quickmt-en-ja-c09e98b8.limodel` | 403,642,726 B | `e08181424a18a1f85b35f7e51a1e84dc78ece081bf2df31c9958d4a4036bba67` |
| 西班牙语增量包 | `quickmt-en-es-430b7889.limodel` | 403,609,399 B | `561c1434de457aad9e750ec79b99a1ef6991fcf6a8ab96aa6181afad18a9a0b2` |

英语基础包必须先导入。日语和西班牙语是“中文→英语→目标语言”的增量路线，因此都依赖英语基础包。模型安装到当前小狼毫用户目录下：

```text
<RimeUserDir>\language_input\models\
```

默认 `<RimeUserDir>` 是 `%APPDATA%\Rime`；如果在小狼毫安装选项中自定义过用户目录，则自动使用该目录。导入后重启小狼毫服务再启用 AI。

AI 成功结果按“模型 + 语言”隔离缓存于：

```text
<RimeUserDir>\language_input\ai_cache_v2.json
```

退出小狼毫服务后删除该文件即可清空 AI 译注缓存。删除模型组件不会自动删除缓存。

## 可选兼容 API

源码仍保留 OpenAI Chat Completions 兼容端点，供开发与对比测试使用；正式安装在存在本地 Host 时默认使用本地模型，不需要网络或 API key。只有显式设置 `LANGUAGE_INPUT_REMOTE_ENABLED=1` 时才改用兼容端点。可配置变量见源码中的 `LoadRemoteGlossConfig`；真实 key 不应写入源码、Rime 配置或截图。

兼容 API 每次最多发送当前页 9 个候选文字和目标语言。请求在后台执行，默认拒绝重定向和明文 HTTP。服务运营方可能按其政策记录请求、IP、账户和计费信息，启用前应阅读所选服务的隐私条款。

## 构建与许可

构建、数据来源和验收记录见 `language-input/VERIFICATION.md`。`scripts/build_model_host.ps1` 固定并校验 Python 3.11.15、CTranslate2 4.8.1、SentencePiece 0.2.1、NumPy 2.4.6 和 PyInstaller 6.15.0，再生成 windowed x64 Host；`scripts/stage_model_host.ps1` 对暂存 Bundle 逐文件记录大小和 SHA256。

固定英文 GlossPack 是 CC-CEDICT 的改编数据库，按 CC BY-SA 4.0 分发。QuickMT 模型组件按 CC BY 4.0 分发。安装目录 `data\licenses\language-input` 包含完整归属与运行时依赖许可说明。

本地发布包使用 `CN=Language Input Local Build` 的本机自签名证书。签名可以证明安装包和内含的一方二进制在签名后未被修改，但它不是公共商业代码签名信誉。
