# Language Input verification record

Baseline: Weasel `9cc96e2` (0.17.4), librime `1c23358` (1.13.1). The librime privacy delta is reproducible from `patches/librime-sensitive-mode.patch`. The Lua plugin and third-party Lua source are pinned by `scripts/prepare_librime_lua.ps1`.

## QuickMT focus preloading check (2026-09-25)

On a Ryzen 7 7745HX, the old first-use path spent about 921 ms verifying all
three imported components, 909 ms loading the English CTranslate2 stage, and
65 ms translating nine candidates. The revised path verifies only the selected
language route. A fresh Python-process English request without preloading took
1,340 ms; this is still above the one-second typing target.

The authenticated, candidate-free `/warmup` request prepared the English,
Japanese and Spanish routes in 1,222/946/1,003 ms. Their following nine-word
requests took 63/90/102 ms in the source Host. A newly built windowed Host and
the x64 native WinHTTP bridge returned an English nine-candidate page in
1,796 ms without preloading and 73 ms after preloading. A Win32 bridge smoke
returned a prepared nine-candidate page in 87 ms. These are request and bridge
timings, not a claim about full keypress-to-visible-UI p95/p99 on an installed build.
The first page can still exceed one second when typing starts before preloading
finishes, after the ten-minute idle shutdown, or after a Host restart.

Checks: `python -m unittest tests.test_language_input_model_host -v` (9/9);
the x64 and Win32 native `LanguageInputRemoteTests.exe` suites and
`RimeWithWeasel.vcxproj` builds; and the installed-style
`--installed-quickmt-cold`, `--installed-quickmt-warmup` and M2M100 smoke tests
against the newly built Host. The previous installed binaries were not replaced
for this check.

## Acceptance matrix

| Requirement | Automated evidence | Runtime evidence | Status |
| --- | --- | --- | --- |
| x64 and Win32 source builds | Serial full `Rebuild` logs, 0 errors; both links include speech and remote objects | Installer chooses native architecture | Passed |
| Full pinyin `nihao` | `tests/test_rime_gloss.ps1` in both architectures | Installed schema and GlossPack hashes matched the tested staged data | Passed |
| Xiaohe `nihc` | `tests/test_rime_gloss.ps1` in both architectures | Formal installed TSF showed `你好 [en] hello; hi` | Passed |
| Nine candidates and English gloss | Real librime sessions in both architectures | Formal installed TSF showed nine translated candidates for `n` | Passed |
| `1`–`9` selection and asynchronous speech | Native key/parser/SAPI tests; selection session test | Formal installed TSF committed `你好` with `1` and invoked the speech path | Passed |
| Sensitive controls hide gloss and stop speech | Rime sensitive-session and native control tests | Formal installed TSF password control showed only `*`, with no candidate UI | Passed |
| Sensitive mode has no user-dictionary read/write | Patched librime reverse-check; isolated userdb behavior test | Installed shared data contained zero user databases | Passed |
| Remote is opt-in and non-blocking | Native fake-transport, parser, HTTPS, cancellation and late-result tests | Optional live endpoint not required | Passed |
| Sensitive mode has no remote/cache access | Unknown-session, lazy-cache and late-result tests | Formal password-control runtime check | Passed |
| Local typing performance | 250 warm iterations: x64 p95 0.476 ms, 47.45 MiB delta; Win32 p95 0.424 ms, 34.16 MiB delta | Formal candidate UI remained responsive | Passed |
| Installer contains custom data and notices | Package-content audit | Installed GlossPack hash, privacy notice and zero-userdb audit | Passed |
| Every first-party executable is signed | Ten packaged first-party PE signatures | Installer, embedded uninstaller and installed-file Authenticode audit | Passed |

Do not change a `Pending` row to `Passed` without recording fresh evidence from the final staged and signed artifacts.

## v0.3 local-AI release matrix (2026-08-31)

The authoritative description of the models actually used by the installer is
[`MODEL.zh-CN.md`](MODEL.zh-CN.md). QuickMT
`quickmt-gloss-route-v2` remains the default route through CTranslate2; the
optional M2M100 route is a separate explicitly selected CTranslate2 backend,
not a hidden QuickMT dependency. Hy-MT2 is historical research only and is not
part of this release.

| Requirement | Automated evidence | Runtime evidence | Status |
| --- | --- | --- | --- |
| English, Japanese and Spanish `.limodel` packs | Whole-pack and per-file SHA256 audit; dependency and atomic-replace tests | Three real packs imported and all three routes produced glosses | Passed |
| AI output stays distinct from the fixed dictionary | v2 cache tests isolate model and language; Rime option matrix covers dictionary/AI selection | Rime x64/Win32 matrix and installed native M2M100 smoke confirmed separate source paths; the candidate AI marker was later hidden intentionally | Passed |
| AI never falls back to the fixed dictionary or QuickMT | Source-path review and missing-model behavior tests | Installed Host returned explicit `409 missing-model-component` for a missing M2M100 component, with no QuickMT response | Passed |
| Local Host is loopback-only and authenticated | Host test verifies unauthorized 401, authorized health and idle exit | Installed loopback process returned 401 without a token, advertised `en,es,ja` with the token, and left no listener after exit | Passed |
| Password/PIN has zero AI request, cache, display and speech | Native sensitive cancellation, late-result and no-callback tests | Final installed password/PIN controls | Pending |
| AI completion refreshes the candidate UI | Native completion callback and main-thread state recheck tests | Native installed smoke refreshed the candidate result after M2M100 completed, without another key | Passed |
| Host and installer are reproducible and signed | Pinned Host build script, bundle manifest and Authenticode audit | Installed first-party PE audit and signed 0.17.4.3 installer extraction | Passed |

The three selected QuickMT packs are individually below 1–2 GB: 409,710,191 bytes (zh→en), 403,642,726 bytes (en→ja), and 403,609,399 bytes (en→es). Japanese and Spanish use an English pivot. Known short-word ambiguity is documented as a limitation; the AI source marker is intentionally hidden in the candidate window.

## M2M100 418M optional backend evidence (2026-08-31, Asia/Shanghai)

M2M100 is an optional experimental backend selected by the user after the
Hy-MT2 trial was rejected for input-method latency. QuickMT remains the
default. M2M100 uses its own MIT/CTranslate2 pack format and does not share
QuickMT components, cache entries or a fallback path.

| Requirement | Fresh evidence | Status |
| --- | --- | --- |
| Fixed provenance and independent package | Official `facebook/m2m100_418M` revision `55c2e61bbf05dfb8d7abccdc3fae6fc8512fd636`; source `pytorch_model.bin` 1,935,796,948 bytes, SHA256 `d907ea45e4e4b9db163382a6674f6218b3c59566fe06d77f4055c208b4e87ed1`; converted INT8 runtime 499,724,092 bytes; `.limodel` 499,727,982 bytes, SHA256 `238b1ed0859704b9d220bd40b7a1cabb234454b408f538930b2b6669f1e5a525`; MIT license and NOTICE | Passed |
| QuickMT default and independent model switch | Rime x64/Win32 matrix: 48 cases per architecture, including saved options, dictionary/AI separation, target-language AI enforcement, sensitive suppression and M2M100 model selection | Passed |
| Host transport and lifecycle | Installed `LanguageInputModelHost.exe` size 4,892,238 bytes, SHA256 `84148537a96c5ae0444217aaa53205d513df1b21ddf90bb464c774a98d3001d8`; random loopback port, bearer token, Host-owned lifecycle, no external endpoint; installed native x64/Win32 smoke both passed | Passed |
| Existing 120-entry blind set | Installed Host returned 120/120 legal outputs for English, Japanese and Spanish; ordinary eligible coverage was 96/96 for each language | Measured |
| Input-method latency and memory | Warm nine-candidate p95: English 749.44 ms, Japanese 902.50 ms, Spanish 794.17 ms; cold requests 1,723.30/1,831.78/1,739.73 ms; peak working set 663,203,840/681,754,624/671,547,392 bytes | Measured |
| Password/PIN safety fixture | 76 cases: 40/40 unsafe sources suppressed, 36/36 safe sources legally translated; suppression recall and allowed-output rate both 1.0. Sensitive controls do not call Host, cache, display or speech paths | Passed |
| Missing model behavior | Installed Host returned HTTP 409 `missing-model-component` for an empty model root; no QuickMT or dictionary fallback | Passed |
| Hy removal from package and installation | Final NSIS archive `weasel-0.1.0-m2m100-20260831-installer.exe`: 41,104,255 bytes, SHA256 `5fe374fe9670d46a97b0177d1c3810c0bb3896d15bf552134fcc5f215a7fa109`; archive and installed directory contain M2M catalog/notice but no GGUF catalog, Hy NOTICE, llama runtime or GGUF/LiModel weight | Passed |

The machine-readable installed-Host benchmark is
`F:\documents.i-wish-research\language-input-m2m100-418m-20260830\benchmarks\m2m100-installed-host-20260831.json`.
The research artifacts, source conversion manifest and pack input remain under
the same research child; model weights are not stored in the repository.

## Retired Hy-MT2 GGUF evidence (2026-08-30, Asia/Shanghai)

This section is retained as an audit trail only. Hy-MT2 was removed from the
active product, catalog, installer, Host runtime and user model directory on
2026-08-31 after real input-method testing showed multi-minute cold and long
candidate-refresh latency. The research copy, package provenance and retired
installed files remain under
`F:\documents.i-wish-research\language-input-hy-mt2-20260830`; no active
release claim below authorizes reinstalling it.

The optional Tencent backend is distributed as an independent Apache-2.0
component. The GGUF itself is not bundled into the installer; users import the
audited `.limodel` component, and a selected-but-missing component is an
explicit error rather than a QuickMT fallback.

| Requirement | Fresh evidence | Status |
| --- | --- | --- |
| Fixed model provenance and independent package | HF revision `9df5c824a00a744fb0512a29c640466f4d97dfb0`, model size `461,860,800` bytes, model SHA256 `cc497fe8f033b52b3b8b00a7669e9661435432f9d4cd43f7ed24400c01507a93`, pack size `461,874,574` bytes, pack SHA256 `3d2c9146ecb7c9d9f3570de4464660b6016d363724ea63b3d61d86155e2f531d`, Apache-2.0 license and NOTICE; pack audit/install unit tests | Passed |
| STQ1_0 runtime compatibility | Patched `llama.cpp` revision `1e411d8f5a1e23525fa3265dfb4bd76265465397`; metadata-gated legacy serialized type-42 mapping for the official Hunyuan 2bit-stride16 file; exact model load and real translation smoke | Passed |
| QuickMT default and model switch | `tests/test_rime_gloss.ps1`: x64 48/48 and Win32 48/48, including `ModelSelection`, saved options and independent display/speech controls | Passed |
| Loopback/auth/lifecycle/no external network | Packaged Host benchmark used a random loopback port and bearer token; runtime child is Host-owned, proxy variables/API key are stripped, and benchmark network mode is loopback-only | Passed |
| Missing model behavior | Packaged Host returned HTTP 409 `missing-model-component` with no fallback when the Hy component directory was absent | Passed |
| Sensitive controls | x64/Win32 Rime matrices passed `SensitiveSuppression`; sensitive sources are filtered before cache, Host and llama-server request paths, candidate display and speech | Passed |
| Existing 120-entry blind set | 360 submitted translations (120 each for en/ja/es), 333 legal outputs (92.5% overall): en 111/120 (92.5%), ja 120/120 (100%), es 102/120 (85%). Three batches returned empty numbered lines and were correctly rejected as HTTP 502; this is recorded model-format/coverage behavior, not counted as legal output or silently substituted | Measured |
| Latency and memory | Overall batch latency p50 69,868 ms, p95 105,096 ms, max 118,759 ms; peak Host 33,816,576 bytes, llama-server 1,132,978,176 bytes, total 1,174,675,456 bytes | Measured |
| Safety fixture | 76/76: 40/40 unsafe suppressed (recall 1.0), 36/36 safe allowed (specificity 1.0) | Passed |

The machine-readable benchmark evidence is
`F:\documents.i-wish-research\language-input-hy-mt2-20260830\hy-mt2-benchmark-v1.json`.
That full run used the immediately prior source-equivalent Host build with SHA256
`c1f449797138fe51e83833c8b83165d6eb10db97626445f31704f1213f488537`; the
final staged PyInstaller rebuild has the same recorded source/runtime inputs,
passed a fresh real-translation/missing-model smoke, and has SHA256
`dac1e9d8148c5211bd7b51417a499da772ab4dbd3928d4bf87b050cd4879ee79`.
The invalid batches contained replacement-character/ambiguous corpus entries;
the direct raw-response reproduction returned numbered blank lines. The Host
therefore rejected the result, preserving the exact-output contract.

Final staging and packaging evidence: `stage_language_input.ps1` completed with
197,860 GlossPack entries, page size 9 and zero user databases;
`stage_model_host.ps1` produced 121 files and 136,805,919 bytes with final Host
SHA256 `dac1e9d8148c5211bd7b51417a499da772ab4dbd3928d4bf87b050cd4879ee79`.
NSIS exited 0 and produced
`output/archives/weasel-0.1.0-test-installer.exe` (44,569,812 bytes,
SHA256 `6DBEEB98DD3FAB77C7245D304384E8F4F9E6C3BC71159A2E40EA776E15DE41BF`).
A fresh 7-Zip extraction contained 260 regular files, the GGUF catalog, Hy
NOTICE, Host and llama-server, zero `.gguf`/`.limodel` files and zero user
databases. The installed x64 `WeaselServer.exe` is 2,833,920 bytes with SHA256
`0201692F62AB72CCD5BCB1F5DA0D3064EAED4980F0595AED293E686C29415C9A`;
installed Host, catalog and NOTICE hashes were also checked against the staged
artifacts.

After installing that exact archive, a fresh Notepad window selected AI,
Hy-MT2-1.8B and English, then entered `q`. The candidate list refreshed to
`[en·AI]` rows such as `器 / instrument` and `去 / go`; the model-cache v3
contained both the QuickMT and Hy-MT2 keys. The installed direct Host smoke
returned HTTP 200, advertised `en,es,ja` and translated `量子纠缠` to
`Quantum entanglement` in 30.8 seconds. Its random loopback port, bearer
authentication and graceful idle cleanup were observed; no Host or
llama-server child remained after cleanup.

## Local transport retry hotfix evidence (2026-08-30, Asia/Shanghai)

The interactive local path no longer retries a failed Host/llama request 100
times. A local transport error now completes the worker once, refreshes the UI
with `本地 AI 翻译请求失败`, and uses the existing per-word cooldown before a
later input may retry. This prevents a slow or failed Hy-MT2 batch from keeping
the worker apparently hung for hours; QuickMT routing and the separate model
cache remain unchanged.

- The new regression fixture forced a local `kTransport` failure through the
  Host-owned process path and verified one request, one completion callback and
  `WaitUntilIdleForTesting(2s)` on both x64 and Win32. Native output was
  `LanguageInputRemoteTests: all checks passed` for both architectures.
- Fresh Python tests ran `46/46`, the Rime option matrix ran `48/48` for each
  x64 and Win32, and `weasel.sln` Release builds completed with zero errors for
  both architectures.
- The retry-fix installer is
  `output/archives/weasel-0.1.0-retryfix-20260830-installer.exe`,
  `44,564,357` bytes, SHA256
  `2665163703e60a942ae4ad0948f8d2c4b725d8e24b392d1ee9f914427a5b1c86`.
  Its archive listing contains the GGUF catalog, Hy NOTICE, Host and
  `llama-server.exe`; the imported user GGUF remains outside the installer.
- The installed registry version is `0.1.0.1`; installed x64
  `WeaselServer.exe` matches the rebuilt binary byte-for-byte
  (`10b749e4e7c153757d564ff184249ce9510b0d59fcab6aba4d853bd94b5a9736`).
  The installed C++ WinHTTP smoke used the installed Host and user-imported
  Hy-MT2 model, returned the exact UTF-8 result `你好` → `Hello`, and completed
  in about 32.3 seconds. Peak total working set was `834,101,248` bytes; the
  Host and llama-server children were absent after cleanup.

## Final 0.17.4.3 installation evidence (2026-08-29, Asia/Shanghai)

- `output/archives/weasel-0.17.4.3-installer.exe` is 41,119,520 bytes, has SHA256 `7B97F33BEA9D5FB6CA3EFEF410ABFC07E034F0A180F54685A7AE57E9F9B399FB`, and has a valid Authenticode signature from `CN=Language Input Local Build`. Its embedded product version is `0.17.4.3`.
- The installed uninstaller registry entry reports DisplayVersion `0.17.4.3`, with install directory `C:\Program Files\Rime\weasel-0.17.4`; `WeaselDeployer.exe /deploy` returned exit code 0 and `WeaselServer.exe` is running from that directory.
- A fresh 7-Zip extraction of the installer contained 250 files, the three model-pack catalog entries, both Language Input notices, and zero `*.userdb*` files. The ten first-party PE files at the package root (Host, Weasel binaries, Rime DLL, IME files and uninstaller) all have valid Authenticode signatures; bundled third-party runtime files are tracked separately and are not first-party signing claims.
- The installed Host smoke test used a loopback port: an unauthenticated `/health` request returned 401; the authenticated response advertised `en,es,ja`; requests for `你好`, `内` and `你` returned JSON glosses for all three languages. The Host process was then stopped and left no listener.
- Fresh regression evidence: `test_language_input_model_host.py` 6/6, `test_limodel_pack.py` 3/3, `test_model_evaluation_tools.py` 32/32; `test_rime_gloss.ps1` 48 switch-matrix cases for each of x64 and Win32; sensitive dictionary probes passed with 0 entries; 250-iteration performance checks passed (x64 p95 0.536 ms, 47.19 MiB delta; Win32 p95 0.599 ms, 34.86 MiB delta); native remote and speech tests passed for both architectures.
- The unsaved `n.txt` Notepad test tab was closed with “不保存”; the remaining user tabs were left open. The separate Notepad++ configuration tabs were not modified.

## Final signed release evidence (2026-08-26, Asia/Shanghai)

- Full serial builds completed with zero error matches in
  `.cache/build/formal-release-x64-20260826.log` and
  `.cache/build/formal-release-Win32-20260826.log`. The earlier `LNK1104`
  was caused by parallel `WeaselTSF` and `WeaselIME` links sharing the same
  architecture-specific import-library and `.exp` names; `/m:1` removes that
  output race without changing product code.
- The signed installer is
  `output/archives/weasel-0.17.4.0-installer.exe`, SHA256
  `B3133F834BD40E7387839661A7089C357AB25E4B8888C32D76E3DEB4FC4D56C1`.
  Its Authenticode signature is valid under certificate thumbprint
  `D1C39F1D440F8AAEEF4B1589FF6E0FF586D15CDE`.
- A duplicate-preserving NSIS extraction at
  `.cache/package-audit-formal-unique-20260826-0234` found all nine signed
  first-party PE files, all required notices and Lua/Rime data, a 197,860-entry
  GlossPack whose SHA256 matches its manifest, and zero `*.userdb*` files.
- The installed directory is `C:\Program Files\Rime\weasel-0.17.4`. Every
  installed first-party file matched its signed build artifact byte-for-byte;
  the installed uninstaller was also signed and valid.
- Both fresh Notepad PID 34616 and the Win32 scope host PID 44884 loaded
  `C:\Windows\System32\weasel.dll` with SHA256
  `8E0AEED101106127EE2B310ED09C20C920206FA2B5DE30AFFB49E2DC5A273C8F`
  and a valid signature. The formal scope-host run then showed nine English
  glosses for `n`, `nihc -> 你好 [en] hello; hi`, numeric `1` committing
  `你好`, and `n` in `ES_PASSWORD + IS_PASSWORD` producing only `*` with no
  candidate UI.
- The current Computer Use backend could activate and capture the fresh
  Windows 11 Notepad window but could not inject unmodified character keys
  into its WinUI RichEdit surface; window-level shortcuts still worked. This
  is recorded as an automation limitation, not counted as fresh formal
  Notepad keystroke evidence. The immediately preceding signed diagnostic DLL
  run had already reproduced the fixed Notepad path (`n`, `nihc`, numeric
  selection) and the formal binary differs by removal of diagnostic code.
- Formal binaries contain no `language-input-tsf-scope.log` marker. A late
  log write came from pre-install Notepad++ PID 6504, which still had the old
  diagnostic DLL mapped. The complete stale-process log is archived at
  `.cache/diagnostics/language-input-tsf-scope-20260826-0243-stale-process-complete.log`
  with SHA256
  `051372208813B3EA9B91F84DE4BF85CEA3DBB3E6E37EF29E8D5B0459963E2EAC`;
  the `%TEMP%` copy was removed. Other applications started before installation
  can retain the old in-memory diagnostic DLL until those applications exit.
- Glosses are exact-entry lookups from the local GlossPack. A candidate that
  has no matching entry can legitimately appear without a translation; this
  is a vocabulary-coverage limitation rather than an application-specific
  rendering failure.
