# OPUS-MT-tiny 中译英本机筛选（2026-09-26）

## 结论

**不接入输入法候选译注。** 25.4M 参数的模型经 CTranslate2 INT8 转换后，预热的九候选页足够快，模型文件也很小；但短词首选译义经常变成不相干的对话句子。对于帮助学习词义的输入法，这个问题比节省十几毫秒更严重。当前仍使用 QuickMT。

## 来源与复现

- 上游：[Helsinki-NLP/opus-mt_tiny_zho-eng](https://huggingface.co/Helsinki-NLP/opus-mt_tiny_zho-eng)，Apache-2.0；固定 revision `4ee3a9342aae4ca6fbae82c930b5dccff5dd68dd`。
- 上游 `model.safetensors`：50,743,442 字节，SHA256 `e285c15744265535ce9cbcbb59e4144e2e7c0e6f113f05ca092f8c41f1f6b28a`。
- 用 CTranslate2 `4.8.1` 的 `ct2-transformers-converter --quantization int8` 转换；`model.bin` 为 17,603,135 字节，SHA256 `2b1d17c423fc20ff5d46f87825e7cf1230d9b517361df28b99be2565f8b4cae3`。
- 测试机：AMD Ryzen 7 7745HX，8 核 / 16 线程；Windows x64；分词器为 Transformers `4.57.1` 的 MarianTokenizer。
- 语料：`evaluation-v2/evaluation-set-v2.json`，120 条，当前工作树文件 SHA256 `43372fe1503cab3dcfac9cc94e10524ff5453bfe006d6cd98ae6d3d010c1fed7`。QuickMT 和 Tiny 在同一台机器、同一环境、同一脚本上分别运行。
- 两组测试共同参数：`scripts/benchmark_translation_route.py --batch-size 9 --threads 8 --beam-size 4 --num-hypotheses 4 --max-glosses 2 --suppress-unsafe-sources`，分别指定上述语料和各自的中译英模型路线；两次预热后统计 13 个完整九候选批次。每项使用首选输出作为主要词义判断依据。

| 指标 | OPUS-MT-tiny INT8 | QuickMT 中→英 INT8 |
| --- | ---: | ---: |
| 模型文件 | 17.6 MB | 约 400 MB |
| 初始化耗时（包括分词器与 Translator 加载） | 5,207 ms | 3,738 ms |
| 预热九候选批次 p95（13 批） | 33 ms | 51 ms |
| 峰值进程工作集 | 373 MB | 755 MB |
| 结构上有效的输出 | 120/120 | 120/120 |

上述「结构上有效」只检查非空、长度等格式，**不代表译义正确**。初始化耗时是测试脚本内 `TranslationPipeline` 构造时间，不等同于已预加载的输入法候选请求。

## 短词样例

| 原词 | Tiny 首选 | QuickMT 首选 | 观察 |
| --- | --- | --- | --- |
| 打字 | Typing | Typing | 两者均可用 |
| 输入法 | Can not open message | Input method | Tiny 词义错误 |
| 应该 | That's right | It should | Tiny 偏离原词 |
| 觉得 | I don't think so | I feel | Tiny 加入否定 |
| 不会 | No, no | It won't | Tiny 偏离原词 |
| 熟 | I'm familiar with you | Mature | Tiny 变成完整对话 |
| 快 | Come on, come on | Quick | Tiny 变成完整对话 |
| 充电宝 | It's a charger | Charging treasure | 两者都需要改进 |

直接将模型附带的 `source.spm` / `target.spm` 交给通用 SentencePiece 路线以缩短启动，结果只有 75/120 个有效输出，九候选 p95 增至 322 ms；这条路线与 MarianTokenizer 的处理并不等价，不能作为可用的启动优化。

另用未量化的 CTranslate2 float32 转换结果复测表中 12 个短词，`输入法`、`觉得`、`熟`、`快` 等仍给出相同的错误首选义。因此主要问题不是 INT8 量化造成的。

上游模型卡的 Flores+ 分数为 BLEU 21.3、chrF 50.9、COMET 0.8116；这是句子翻译指标，不能替代本产品的短词词义检查。若将来有词典式微调版本，应重新测首选译义、九候选 p95 和焦点预热后的首次请求。
