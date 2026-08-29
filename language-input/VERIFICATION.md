# Language Input verification record

Baseline: Weasel `9cc96e2` (0.17.4), librime `1c23358` (1.13.1). The librime privacy delta is reproducible from `patches/librime-sensitive-mode.patch`. The Lua plugin and third-party Lua source are pinned by `scripts/prepare_librime_lua.ps1`.

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

## v0.2 local-AI release matrix (2026-08-29)

| Requirement | Automated evidence | Runtime evidence | Status |
| --- | --- | --- | --- |
| English, Japanese and Spanish `.limodel` packs | Whole-pack and per-file SHA256 audit; dependency and atomic-replace tests | Three real packs imported and all three routes produced glosses | Passed |
| AI output stays distinct from the fixed dictionary | v2 cache tests isolate model and language; Rime option matrix covers dictionary/AI selection | Final installed candidate markers and source switching | Pending |
| AI never falls back to the fixed dictionary | Source-path review and missing-model behavior tests | Missing-pack behavior in final installed build | Pending |
| Local Host is loopback-only and authenticated | Host test verifies unauthorized 401, authorized health and idle exit | Final installed loopback process returned 401 without a token, advertised all three languages with the token, and left no listener after exit | Passed |
| Password/PIN has zero AI request, cache, display and speech | Native sensitive cancellation, late-result and no-callback tests | Final installed password/PIN controls | Pending |
| AI completion refreshes the candidate UI | Native completion callback and main-thread state recheck tests | Final Notepad TSF automatic refresh without another key | Pending |
| Host and installer are reproducible and signed | Pinned Host build script, bundle manifest and Authenticode audit | Installed first-party PE audit and signed 0.17.4.3 installer extraction | Passed |

The three selected packs are individually below 1–2 GB: 409,710,191 bytes (zh→en), 403,642,726 bytes (en→ja), and 403,609,399 bytes (en→es). Japanese and Spanish use an English pivot. Known short-word ambiguity is accepted for vocabulary hints and must remain visible through the `〔<lang>·AI〕` marker.

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
