# 当前使用的翻译模型

## 一句话结论

当前 `0.17.4.3` 版本的 AI 翻译采用 **QuickMT 三组件翻译路线**
`quickmt-gloss-route-v2`，不是腾讯混元 `Hy-MT2-1.8B`。核心安装包不携带权重；导入相应的 `.limodel` 包后才会在本机运行。

它的定位是输入法候选栏的**短释义和词汇提示**，不是语言考试、长文翻译或权威词典。AI 输出会保留 `〔语言·AI〕` 来源标记，且 AI 模式不会回退到固定词典。

## 实际组件和语言路线

这不是一个包覆盖所有语言，而是按需导入的三个 QuickMT 组件。每个 `.limodel` 包都小于 1–2 GB。

| 目标语言 | 实际路线 | 需要导入的包 | `.limodel` 大小 |
| --- | --- | --- | ---: |
| 英语 | 中文 → 英语 | `quickmt-zh-en` | 409,710,191 B |
| 日语 | 中文 → 英语 → 日语 | `quickmt-zh-en` + `quickmt-en-ja` | 403,642,726 B（增量包） |
| 西班牙语 | 中文 → 英语 → 西班牙语 | `quickmt-zh-en` + `quickmt-en-es` | 403,609,399 B（增量包） |

日语和西班牙语包依赖英语基础包，因此必须先导入 `quickmt-zh-en`。包名、版本、SHA256 和依赖关系以 [`models/packs-v2.json`](models/packs-v2.json) 为准。

### 固定版本

| 组件 | 上游仓库 | 固定 revision |
| --- | --- | --- |
| 中文→英语 | [`quickmt/quickmt-zh-en`](https://huggingface.co/quickmt/quickmt-zh-en) | `c27cc8024e01a047733a1e34796e2ab19d74b237` |
| 英语→日语 | [`quickmt/quickmt-en-ja`](https://huggingface.co/quickmt/quickmt-en-ja) | `c09e98b8438a239a8210060114cea19c426c0559` |
| 英语→西班牙语 | [`quickmt/quickmt-en-es`](https://huggingface.co/quickmt/quickmt-en-es) | `430b78899a30bf5a867dffd407c64b028bbaebf4` |

三个组件均按 CC BY 4.0 分发，归属说明见 [`licenses/QUICKMT-CC-BY-4.0-NOTICE.txt`](licenses/QUICKMT-CC-BY-4.0-NOTICE.txt)。

## 本地运行方式

- 推理运行时：CTranslate2 `4.8.1`，INT8；分词器为 SentencePiece `0.2.1`。
- 解码参数：beam size `4`，最多生成 `4` 个候选释义，候选栏最多显示 `2` 个不同释义。
- 进程：安装目录中的 `LanguageInputModelHost.exe`（x64），仅通过随机端口的 `127.0.0.1` 回环地址与输入法通信。
- 默认 AI 模式不访问互联网，也不需要 API key；模型包由用户显式导入，不随核心安装包捆绑。

## 为什么选它

在面向输入法的实用测试中，QuickMT 路线满足了当前的结构、安全、覆盖率、内存和延迟要求：

| 目标语言 | 普通词覆盖率 | 热启动 9 候选 p95 | 峰值工作集 |
| --- | ---: | ---: | ---: |
| 英语 | 100% | 39 ms | 0.70 GiB |
| 日语 | 98.96% | 207 ms | 0.89 GiB |
| 西班牙语 | 100% | 121 ms | 0.89 GiB |

这些数据来自新的实用盲测集；这里的“覆盖率”表示能生成合法短释义，不代表每个词义都完美。完整记录见 [`model-research/quickmt-practical-v2.json`](model-research/quickmt-practical-v2.json)。

## 与输入法开关的关系

- `翻译来源：AI`：使用上面的 QuickMT 路线。
- `翻译来源：词典`：使用内置 CC-CEDICT 英语 GlossPack，不调用模型。
- `译注显示：关`：不查询词典、不请求模型、不读写 AI 缓存，也不显示或朗读译注。
- 日语和西班牙语使用英语中间结果，所以英语词义判断错误可能传递到第二段翻译。

## 已知限制

- 孤立短词、多义词、地域词和专业词可能得到不合适的释义。
- AI 结果是辅助记忆的词汇提示，不应当当作权威定义、法律/医学翻译或考试评分依据。
- 没有匹配结果时可以不显示译注，这是覆盖率限制，不是输入法崩溃。
- 密码、PIN 等敏感控件不会请求模型、读写缓存、显示译注或朗读。

## 没有集成的模型

腾讯混元 `Hy-MT2-1.8B`、Qwen、M2M100 等名称出现在候选评估或讨论资料中，但**不是当前安装包使用的模型**。它们没有被偷偷替换到正式路径；历史筛选证据保留在 [`model-research/CANDIDATES.md`](model-research/CANDIDATES.md) 和 [`models/catalog-v1.json`](models/catalog-v1.json) 中。

用户操作和导入步骤见 [`README.zh-CN.md`](README.zh-CN.md)。
