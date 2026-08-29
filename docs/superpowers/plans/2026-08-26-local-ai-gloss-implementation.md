# Language Input v0.2 local-AI gloss implementation plan

Date: 2026-08-26  
Design: `docs/superpowers/specs/2026-08-26-local-ai-gloss-design.md`  
Branch: `codex/language-input-v0.2-local-ai`

## Delivery rule

Work in independently verifiable slices. Do not put candidate repositories,
model weights, download caches or benchmark runtime output in the project or a
Skill directory. Only durable source, compact test fixtures, provenance,
benchmark summaries, licenses and approved product artifacts belong in the
repository.

The existing dirty `librime` working tree is intentional: its three privacy
changes are reproduced by `patches/librime-sensitive-mode.patch`. Do not stage
the submodule working tree or change its gitlink unless an approved dependency
update requires it.

## Task 1: establish bounded research storage and provenance

1. Resolve `F:\documents\.i-wish-research` and verify it is a real directory,
   not a junction or symlink.
2. Resolve the proposed child
   `F:\documents\.i-wish-research\language-input-ai-models-20260826` and verify
   its parent containment before creation.
3. Record free space and enforce the approved cumulative limits:
   - network transfer: 30 GiB;
   - total owned child: 30 GiB.
4. Route model downloads, candidate source clones, build caches, command temp
   and benchmark output into that child.
5. Research current CTranslate2 translation components no larger than 1–2 GB
   each with Chinese-to-English/Japanese/Spanish capability.
6. For every candidate, record immutable source URL, commit/release, model
   revision, INT8 format, bytes, SHA256, license text, attribution,
   redistribution status and why it enters or leaves the shortlist.
7. Write durable findings to
   `language-input/model-research/CANDIDATES.md` and a machine-readable
   `language-input/models/catalog-v1.json`; do not copy candidate bytes into
   either location.

Gate: no candidate may be downloaded until the path, caps, estimated bytes,
license source and available space have all been recorded.

## Task 2: build the fair translation benchmark

Files:

- add `scripts/build_model_evaluation_set.py`;
- add `scripts/benchmark_local_model.py`;
- add `tests/test_model_evaluation_tools.py`;
- add compact source/reference fixtures below
  `language-input/model-research/evaluation-v1/`;
- add generated summary schemas, not raw model logs or weights.

Steps:

1. Extract the fixed, deterministic 120-source practical corpus plus a separate
   76-item safety fixture specified by the revised design.
2. Pin source hashes and provenance for every corpus segment.
3. Define the exact system prompt, JSON schema, sampling settings, context,
   thread count, warm-up and measurement repetitions.
4. Test the harness with a fake OpenAI-compatible local server before using a
   real model.
5. Download components within the approved cap and run all three language
   routes with the same pinned runtime.
6. Record JSON/key validity, valid-gloss coverage, cold latency, warm p50/p95,
   peak working set, output length, target-language detection and failures.
7. Keep semantic sampling advisory and disclose representative failure modes.
8. Select a route only if it satisfies license, safety, memory, structure and
   practical coverage gates. Do not disguise generated text as a dictionary.
9. Commit compact reports and selected-model provenance; retain only useful
   workflow-owned research bytes under the bounded research child.

Gate: the selected runtime/model pair must be evidence-backed before product
code or catalog defaults name it.

## Task 3: implement the option matrix and deterministic fallback

Files:

- update both `language-input/rime/language_input_*.schema.yaml` files;
- update `language-input/rime/lua/language_input/gloss_filter.lua`;
- update `scripts/stage_language_input.ps1`;
- update `tests/test_rime_gloss.ps1`;
- extend native speech/parser tests if marker parsing changes.

Steps:

1. Preserve `language_input_gloss` as the display master.
2. Add `language_input_ai` with states `词典 / AI`.
3. Add the exclusive target-language group for English, Japanese and Spanish.
4. Persist the display, provider, language and speech options through staged
   `default.yaml`.
5. Change fixed English markers to `〔en·词〕`.
6. Add OpenCC `t2s.json` normalized lookup after exact lookup, with exact-only
   behavior if OpenCC cannot load.
7. Ensure selecting Japanese or Spanish sets the effective and visible
   provider to AI; returning to English preserves AI.
8. Make display-off remove glosses and suppress all provider/cache/speech work.
9. Add switch-matrix tests for both full pinyin and Xiaohe.

Gate: deterministic tests must prove the four controls and the measured
normalization gain before the AI host is introduced.

## Task 4: refactor AI request, marker and cache primitives

Files:

- evolve `RimeWithWeasel/LanguageInputRemote.*` into local-AI-oriented types,
  retaining a transport seam for tests;
- add focused files such as `LanguageInputAiCache.*` and
  `LanguageInputOptions.*` if separation keeps units small;
- update `LanguageInputSpeech.*` marker parsing;
- extend `tests/native/LanguageInputRemoteTests.cpp` and
  `LanguageInputSpeechTests.cpp`.

Steps:

1. Represent model SHA256, prompt version and target language explicitly in
   every lookup and request.
2. Build exact-key JSON Schema/grammar requests for at most nine unique words.
3. Validate UTF-8, exact keys, single-line values, length and control
   characters before accepting results.
4. Render AI markers as `〔<language>·AI〕` while passing only the BCP-47 prefix
   and gloss text to SAPI.
5. Replace `remote_cache_v1.json` with bounded `ai-cache-v2.json` namespaces.
6. Implement 20,000-entry LRU and 16 MiB limits, wrong-version/corruption
   rejection and same-directory atomic replacement off the input thread.
7. Preserve unknown-as-sensitive defaults, queue purge, late-result rejection
   and zero sensitive cache access.

Gate: native fake-transport tests cover valid/invalid batches, cache isolation,
corruption, bounds, cancellation and markers without a real model.

## Task 5: add asynchronous current-page refresh

Files:

- update `RimeWithWeasel/RimeWithWeasel.*`;
- update Weasel server/IPC message handling only where needed;
- add a deterministic callback/message test seam.

Steps:

1. Give every queued batch a session generation and candidate-page identity.
2. Make AI completion enqueue a private Windows message rather than touching
   UI or Rime state from the worker thread.
3. On the server thread, recheck session existence, generation, page contents,
   provider, language and sensitive state.
4. Refresh only a still-current page; allow stale valid results to remain in
   cache without changing the screen.
5. Rate-limit/coalesce refresh messages so one batch causes at most one useful
   repaint.
6. Measure keyboard p95 and completion-to-refresh scheduling latency.

Gate: Chinese candidates appear before a fake delayed response, and the same
current page receives the gloss without an extra keystroke; stale and sensitive
responses never repaint.

## Task 6: integrate the signed x64 model host

Files and dependencies:

- pin CTranslate2 4.8.1 and SentencePiece 0.2.1 by immutable releases using a
  reproducible dependency mechanism;
- add build/staging scripts that keep dependency caches out of the project;
- add the x64 `LanguageInputModelHost` project or reproducible wrapper target;
- add host lifecycle/controller code and integration tests;
- add required MIT/runtime notices.

Steps:

1. Build or bundle the pinned x64 runtime reproducibly and verify every native
   dependency's license/provenance.
2. Bind only to `127.0.0.1` on an OS-selected port and require a fresh random
   bearer token.
3. Launch hidden, place the process in a kill-on-close Job Object and prevent a
   firewall rule or LAN binding.
4. Start only for eligible AI sessions with a validated active model.
5. Stop after ten idle minutes or before model switching/removal.
6. Restart one unexpected exit, then apply bounded backoff.
7. Expose lifecycle/error status without candidate text or generated glosses.
8. Run real-host structure, privacy, network, latency and peak-memory tests with
   the selected model.

Gate: the host meets the 8 GB CPU-only, 3.5 GiB peak and warm p95 requirements,
and an external-network monitor remains silent during inference.

## Task 7: add model management to WeaselDeployer

Files:

- add a `/language-model` route to `WeaselDeployer.exe`;
- add focused catalog, path-validation, download/import and dialog units;
- update resources for Simplified Chinese, Traditional Chinese and English;
- update installer shortcuts;
- add unit/integration tests around non-UI logic.

Steps:

1. Parse only the installed, versioned catalog with strict schema and duplicate
   rejection.
2. List installed components and active routes; show license, bytes, memory, languages,
   validation and benchmark summary.
3. Use BITS for explicit HTTPS downloads and resume; check free space first.
4. Stage outside the active directory, verify exact bytes/SHA256, then move
   atomically.
5. Implement `.limodel` stored-ZIP import with archive path, duplicate, size,
   catalog, hash and license validation.
6. Stop the model host before switching or removing a model.
7. Never remove an active or user-selected file without an explicit UI action
   and confirmation.
8. Surface actionable missing-model, corrupt-download, policy-disabled BITS and
   insufficient-memory states.

Gate: fake download and archive fixtures prove resume abstraction, hash failure,
path containment, dependency handling, activation, switching and removal before
using the real component packs.

## Task 8: package, document and verify the release artifacts

Files:

- update `output/install.nsi` and staging scripts;
- update `language-input/README.zh-CN.md` and `PRIVACY.zh-CN.md`;
- add model/runtime notices and model-manager help;
- update `language-input/VERIFICATION.md` only from fresh final evidence.

Steps:

1. Rebuild x64 and Win32 Weasel components serially with `/m:1` and
   `/nodeReuse:false`.
2. Build native tests for both architectures and the x64 model host.
3. Run GlossPack, option, speech, AI/cache, sensitive-userdb, async refresh,
   model-manager, performance and real-model benchmarks.
4. Stage runtime/catalog/notices while proving no model weight or user database
   enters the core installer.
5. Sign every first-party PE, then sign the NSIS installer and embedded
   uninstaller with the existing local certificate without exporting its key.
6. Create the selected English, Japanese and Spanish `.limodel` component packs
   only when redistribution is permitted; verify archive paths, dependencies,
   manifests, model hashes and notices.
7. Extract the final installer to a unique audit directory and verify all files,
   signatures, source markers, privacy notices and zero diagnostic artifacts.
8. Perform controlled runtime tests in the scope host and Notepad for immediate
   Chinese candidates, automatic AI refresh, all languages, provider switching,
   speech and password suppression.
9. Commit only durable source/reports/docs, push
   `codex/language-input-v0.2-local-ai` to `Hhidysn/weasel`, and verify local and
   remote commit IDs match.
10. Report local artifact paths and SHA256 values. Do not create a public GitHub
    Release without separate authorization.

## Completion audit

Before marking the goal complete, map every design acceptance criterion and
deliverable to fresh authoritative evidence. A passing narrow unit test cannot
stand in for real-model quality, runtime refresh, sensitive-input behavior,
package content, signatures, remote branch state or offline model-pack
integrity. Any missing or indirect evidence keeps the goal active.
