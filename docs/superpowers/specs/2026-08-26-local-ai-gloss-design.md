# Language Input v0.2: local-AI gloss design

Date: 2026-08-26  
Status: user-confirmed design

## 1. Product decision

Language Input v0.2 keeps the deterministic English GlossPack as one selectable
provider and adds a local small-model provider as the other. The latest
confirmed control model supersedes the earlier AI-only idea:

- whether glosses are shown is one switch;
- whether translation comes from the English dictionary or local AI is a
  separate two-state switch;
- the target language is independently selectable among English, Japanese and
  Spanish;
- speech remains independently selectable, but speaks only a gloss that is
  currently enabled and available.

The local-AI path never calls a remote large model during typing. The inference
runtime ships in the signed core installer, while the 1–2 GB model is installed
later through the settings tool or an offline model pack.

## 2. Goals

1. Improve practical translation coverage beyond exact CC-CEDICT matches.
2. Preserve immediate, non-blocking Chinese candidate generation.
3. Support English, Japanese and Spanish local-AI glosses on an 8 GB,
   CPU-only x64 Windows computer with no discrete GPU.
4. Make display, provider, language and speech choices explicit in the Rime
   option menu.
5. Download, verify, import, activate, switch and remove model packs from the
   existing Weasel settings program.
6. Keep password, PIN and unknown input scopes free of model requests, cache
   access, gloss display and gloss speech.
7. Produce a reproducible source branch, signed core installer and verified
   offline model pack.

## 3. Non-goals

- Android support is not part of this slice.
- The core installer does not embed a model file.
- Typing never triggers a model download or an external Internet request.
- Japanese and Spanish fixed dictionaries are not built in v0.2.
- A separately installed Ollama, LM Studio or another registered model runtime
  is not required.
- A 32-bit model host is not provided. Win32 applications continue to use the
  normal Weasel client path on x64 Windows.

## 4. Current baseline

The v0.1 GlossPack contains 197,860 CC-CEDICT-derived entries. A fresh local
measurement found:

| Corpus | Direct coverage |
| --- | ---: |
| Unique `luna_pinyin.dict.yaml` terms | 32.09% |
| `essay.txt` terms | 25.38% |
| `essay.txt`, weighted by corpus frequency | 64.24% |

Applying the packaged OpenCC `t2s.json` normalization before lookup raises the
weighted `essay.txt` coverage to 65.53%, a 1.29-point gain. The remaining gap
is dominated by multiword expressions, new terms, proper names and long-tail
phrases. AI is therefore a selectable long-tail provider, not a reason to
block the input path.

## 5. User-visible controls

Both `language_input_flypy` and `language_input_pinyin` expose:

1. `译注显示：关 / 开`
   - backed by `language_input_gloss`;
   - when off, no dictionary lookup, model start, model request, translation
     cache access or gloss speech occurs;
   - switching it off immediately removes displayed glosses, cancels queued AI
     work for the session and stops current gloss speech.
2. `翻译来源：词典 / AI`
   - backed by the Boolean option `language_input_ai`;
   - false selects the fixed dictionary and true selects local AI.
3. `目标语言：英语 / 日语 / 西班牙语`
   - backed by an exclusive Rime option group
     `language_input_en`, `language_input_ja`, `language_input_es`;
   - selecting Japanese or Spanish automatically selects AI so that the shown
     provider state always matches the effective provider;
   - returning to English preserves AI until the user explicitly switches back
     to the dictionary.
4. `朗读：关 / 开`
   - backed by `language_input_speech`;
   - after an unmodified numeric key successfully commits a candidate, the
     currently shown gloss is spoken asynchronously.

Source markers are visible and machine-parseable:

- `〔en·词〕 hello; hi`
- `〔en·AI〕 hello; hi`
- `〔ja·AI〕 こんにちは`
- `〔es·AI〕 hola`

The speech parser extracts the BCP-47 language prefix before the source marker
and never speaks `词` or `AI`.

## 6. Architecture

### 6.1 WeaselServer orchestration

`WeaselServer` remains the only component connected to Rime sessions. It:

- resolves the display, provider, target-language and speech options;
- renders cached or dictionary glosses;
- batches at most the current page's nine unique AI misses;
- assigns a session generation to every request;
- posts a private Windows message when a result batch finishes;
- refreshes the candidate UI only after rechecking session, generation,
  candidate page, target language and sensitive state.

No model operation runs on the keyboard event thread. Chinese candidates are
returned before any AI work begins.

### 6.2 Local inference process

The installer contains a source-built, pinned and signed x64
`LanguageInputModelHost.exe` based on `llama.cpp`'s GGUF inference and
OpenAI-compatible server implementation. It:

- binds only to `127.0.0.1` on an operating-system-selected port;
- requires a new random bearer token for every launch;
- receives only the current batch of Chinese candidate strings, the selected
  target language and a versioned system prompt;
- uses a dynamically generated JSON schema or grammar that permits exactly the
  requested keys and single-line string values;
- is placed in a Windows Job Object with kill-on-close semantics;
- is started only when AI gloss display is active and a valid model is
  selected;
- is stopped after ten minutes without an eligible AI request.

`WeaselServer` retries one unexpected host exit. Further failures use bounded
exponential backoff until a user action or model change resets the failure
state.

### 6.3 Model management in WeaselDeployer

The existing `WeaselDeployer.exe` gains a `/language-model` entry point and a
Start Menu shortcut. The dialog shows installed models, active model, license,
file size, minimum memory, supported languages, validation state and benchmark
summary.

The catalog is a versioned JSON file shipped in the signed installer. Updating
the supported catalog therefore requires a product update; the typing process
does not trust a network-fetched catalog. Each entry fixes:

- model ID and display name;
- upstream repository and immutable download URL;
- model and quantization version;
- exact byte size and SHA256;
- license identifier, attribution and redistribution decision;
- supported languages and minimum RAM;
- prompt-template version and context settings.

Downloads start only after an explicit click. The manager uses Windows BITS
for background transfer and resume, requires HTTPS, checks free space first,
writes outside the active model directory, verifies size and SHA256, then
atomically moves the file into place. If BITS is disabled, offline import
remains available.

Model files live below:

```text
%LOCALAPPDATA%\Rime\LanguageInput\models\<model-id>\
```

Resolved targets must stay inside that root and may not traverse a junction,
symlink or reparse point. Switching or removing the active model first stops
the model host.

### 6.4 Offline model-pack format

The redistributable model uses a `.limodel` ZIP container with stored rather
than recompressed GGUF bytes. It contains one root-level manifest, the GGUF
file and required license/attribution files. Import rejects absolute paths,
parent traversal, unexpected file names, duplicate entries, reparse points,
size mismatches and catalog/hash mismatches before activation.

Only a model whose license permits redistribution can become the official
offline pack. A model that is otherwise technically strong but fails this
license gate is not eligible for release.

## 7. Translation data flow

1. Rime produces the normal Chinese candidate page immediately.
2. If gloss display is off or the session is sensitive/unknown, translation
   processing stops before any provider or cache access.
3. English dictionary mode performs an exact GlossPack lookup, then an OpenCC
   simplified/variant-normalized lookup. A hit is rendered with `〔en·词〕`.
4. AI mode derives a cache namespace from model SHA256, prompt version and
   target language.
5. Cache hits are rendered immediately with the appropriate AI marker.
6. Up to nine unique misses are queued for the local host as one batch.
7. The response parser requires valid UTF-8, an exact requested-key set,
   single-line values, a per-gloss length limit and no control characters.
8. Valid values are committed to the AI cache off the input thread.
9. A completion callback posts to the Weasel server window. Current results
   refresh the current candidate page; stale results may remain cached but may
   not alter a new page or session.

Prompt text treats every candidate as untrusted data rather than an
instruction. Candidate strings never become system-message text.

## 8. Cache design

AI results are stored under:

```text
%LOCALAPPDATA%\Rime\LanguageInput\cache\ai-cache-v2.json
```

The file contains isolated namespaces keyed by model SHA256, prompt version
and target language. It is bounded by 20,000 least-recently-used entries and
16 MiB, written only by the background worker through a same-directory
temporary file followed by atomic replacement. Corrupt, oversized or
wrong-version cache files are ignored rather than partially trusted.

Entering a sensitive scope blocks cache reads and writes before returning from
the scope transition. Late model results from a pre-transition request are
discarded without persistence.

## 9. Failure handling

- Missing model: keep Chinese candidates and show one non-blocking action that
  opens `/language-model`.
- Download interruption: retain the resumable BITS job.
- Insufficient disk space: refuse before transfer and show required/free size.
- Hash or size failure: never activate the file; mark the transfer damaged.
- Host cold start: keep Chinese candidates visible while loading.
- Host crash: restart once, then back off without repeated UI disruption.
- Timeout, malformed JSON or invalid gloss: discard the batch and preserve the
  existing candidate UI.
- Out of memory: stop the host, mark the active model unavailable for the
  session and recommend a smaller catalog model.
- Enterprise download restriction: allow verified `.limodel` import from a
  file copied from another computer.
- Password, PIN, private or unknown scope: cancel queued work, reject late
  results, stop speech, hide glosses and prohibit cache access.

Product logging records lifecycle/error codes, model ID and timings, but never
candidate text, generated glosses, bearer tokens or password-scope content.

## 10. Model research and selection

Research uses the dedicated child directory:

```text
F:\documents\.i-wish-research\language-input-ai-models-20260826
```

Before downloading, the workflow verifies the resolved child path, reparse
points, estimated bytes and free space. The user-approved hard limits are:

- cumulative network downloads: 20 GiB;
- total research directory size: 30 GiB;
- candidate count: not predetermined;
- stop early once evidence identifies a stable winner.

Candidates must be current, provenance-verifiable, compatible with pinned
`llama.cpp`, between 1.0 and 2.0 GiB in the tested GGUF quantization, usable on
an 8 GB CPU-only machine, multilingual for
Chinese-to-English/Japanese/Spanish, and license-compatible with the intended
distribution.

All candidates use the same runtime build, prompt, thread policy, context,
batch and sampling settings. The 320-item evaluation corpus contains:

- 120 Rime high-frequency candidates;
- 80 fixed-dictionary misses including compounds, new terms and proper names;
- 40 simplified/traditional/variant cases;
- 40 ambiguous or polysemous words;
- 40 mixed-character and prompt-injection-shaped adversarial inputs.

Every candidate produces English, Japanese and Spanish output, exactly 960
translations per model. Evaluation covers semantic correctness, concision,
target-language correctness, JSON/key stability, hallucination,
prompt-injection resistance, cold and warm latency, peak working set and
license/provenance. License and redistribution eligibility are hard gates, not
quality-score bonuses.

Full-corpus checks measure structure, coverage, latency and memory. Semantic
acceptance uses a fixed rubric on a stratified 120-source subset: 24 sources
from each of the five corpus groups, producing 360 translations per model.
Model identities and output order are hidden from two independent multilingual
reviewers. Disagreements are adjudicated against cited, versioned bilingual
references before acceptance rates are calculated; an uncorroborated score
from a single automatic judge cannot select the release model.

## 11. Acceptance criteria

1. Keyboard-processing p95 remains below 1 ms in the existing 250-iteration
   benchmark; inference never executes on that thread.
2. On the reference CPU-only configuration, model-host peak working set is no
   more than 3.5 GiB.
3. A warm nine-candidate AI batch has a p95 of no more than 3 seconds.
4. The candidate UI is scheduled for refresh within 250 ms after a valid model
   result becomes available.
5. Exact JSON/key validation succeeds for at least 99% of evaluation batches.
6. At least 95% of eligible ordinary evaluation terms receive a valid gloss.
7. Blind semantic acceptance is at least 90% for English and 85% for Japanese
   and Spanish.
8. Sensitive-scope model calls, cache reads/writes, displayed glosses and
   spoken glosses are all zero.
9. Packet/process monitoring finds no external network request during typing;
   only explicit model downloads may reach the network.
10. x64 and Win32 input-method components build successfully; the model host is
    x64 and serves both client paths through the existing Weasel architecture.
11. The installer, embedded uninstaller and every first-party executable are
    signed and verified.
12. The installer contains the model runtime, catalog and notices but no GGUF
    model; the separate `.limodel` pack passes manifest, path, size, hash and
    license audits.

If no candidate satisfies all hard gates, the release does not silently lower
them. The research report records the failed gates and the model-selection
decision returns for user review.

## 12. Verification plan

### Automated tests

- all display/provider/language/speech switch combinations;
- automatic AI selection for Japanese and Spanish;
- exact and OpenCC-normalized English dictionary lookup;
- fake-host batches, JSON-schema validation and source markers;
- asynchronous refresh, session generations and stale-result rejection;
- numeric selection and marker-aware speech parsing;
- model/language/prompt cache isolation, LRU bounds, atomic persistence and
  corrupt-cache rejection;
- sensitive and unknown scope cancellation with zero cache/model access;
- host startup, random token, loopback binding, idle stop, crash retry,
  timeout and out-of-memory reporting;
- model catalog validation, disk checks, BITS resume abstraction, hash failure,
  path traversal, duplicate archive entry and offline import;
- x64/Win32 full-pinyin and Xiaohe sessions;
- local typing performance and model-host memory/latency benchmarks;
- package contents, zero user databases, notices, signatures and absence of a
  bundled GGUF model.

### Runtime tests

- Notepad and the controlled scope host show Chinese candidates immediately;
- AI glosses appear on the same current page without an extra keystroke;
- English/Japanese/Spanish switching refreshes with the correct marker;
- dictionary/AI switching changes the effective provider as displayed;
- numeric selection commits the Chinese candidate and speaks the shown target
  gloss when speech is enabled;
- password controls show neither candidate gloss nor model/cache activity;
- model download, interruption/resume, activation, switching, removal and
  offline import work through `WeaselDeployer.exe /language-model`.

## 13. Deliverables

- a committed and pushed v0.2 source branch in `Hhidysn/weasel`;
- model provenance, benchmark and selection reports;
- an updated Chinese README and privacy notice;
- a signed core installer without model weights;
- one verified `.limodel` offline model package when redistribution is allowed;
- SHA256 values and a final verification record for both artifacts.

No public GitHub Release is created without a separate user instruction.
