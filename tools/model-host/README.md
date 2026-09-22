# In-tree `LanguageInputModelHost` — per-process integrity cache

The host source now lives **in this repository** and is edited directly:

```
scripts/language_input_model_host.py
```

This directory owns the **build + apply + harness tooling** for it. There is no
patch/patcher step: `build.ps1` freezes the in-tree source with PyInstaller.

## TL;DR

| Metric (5 samples, same flags as WeaselServer) | Current host | Rebuilt host |
|---|---|---|
| `/health` median | **1 944.9 ms** | **5.6 ms** (~347× faster) |
| warm zh→en median | **994.5 ms** | **27.1 ms** (~37× faster) |
| cold startup | 2.590 s | 1.517 s |
| zh→en / zh→ja / zh→es responses | — | **byte-identical** |
| corrupted / oversized / missing component | rejected | **rejected** |
| `--list` / `--audit-pack` / `--import-pack` JSON | — | **identical** |

## 1. The defect (measured)

The original host re-hashed **every installed model file on every request**:

```
QuickMtRuntime.translate()  (:887)  -> installed_components(model_root) (:520)
  -> load_installed_component (:495) -> sha256_file (:41)
```

The three installed QuickMT components total ~1.21 GB, so each warm request read
all of that data just to re-confirm integrity. `/health` did it **twice**
(both `available_languages()` calls), measured at 1.80–1.87 s. Real CTranslate2
inference for three words is only tens of ms, so ~0.9 s of every ~1.0 s request
was redundant hashing. `M2m100Runtime` had the same shape.

Baselines recorded in `settings-app/docs/MODEL-HOST-LIFECYCLE.md §3`:
warm request median **1 049 ms**, `/health` **~1.83 s**.

## 2. What the change does

`scripts/language_input_model_host.py` now carries the fix **directly in the
tree** (the same anchored transformation used previously):

1. **Per-process verification cache.** `load_installed_component` and
   `load_installed_m2m100_component` are renamed to `_…_uncached` cores, and
   cached wrappers (`_cached_load`) are installed under the original public
   names. The **first** load of each component in a process performs the full
   existing **size + SHA-256** checks; later loads are served from an in-memory
   dict. Warm requests therefore hash **nothing**.
2. **Cheap invalidation path.** The cache value is keyed to
   `_component_stat_token()`, a content-free fingerprint of the component:
   root directory `mtime_ns`, an entry count, and a sorted recursive listing of
   `(relative path, size, mtime_ns)`. Building the token only `stat`s the tree
   (microseconds), never reads file contents. If the token changes, the
   component is re-verified from scratch. `_invalidate_component_cache()` is
   exposed for explicit invalidation.
3. **`/health` double-hash fixed.** `quickmt.available_languages()` is called
   once and reused for both `languages` and
   `models["quickmt-gloss-route-v2"]["languages"]`.

No route, JSON shape, error message, exit code, CLI flag, token check or
idle-exit loop is changed. `--list` in a fresh process still verifies every
component (cache is empty), so `--models-verify` semantics in the settings app
(which hashes independently, in its own Python) are unaffected.

## 3. Security trade-off (read this)

The cache intentionally trades **continuous** integrity checking for
**first-use-per-process** checking:

* A tampered / oversized / missing file is still detected on the **first** use
  of that component in the process, and the component is still kept out of
  `installed_components` / `available_languages` (`--list`, `/health`,
  `/v1/chat/completions` all reject it). Verified below.
* BUT a long-running host will **not** notice a file that is modified *after*
  its first verification until the process restarts (or the stat token changes).
  Previously it would notice on the very next request.
* The stat-token invalidation catches in-place edits, size changes, renames and
  additions/removals via mtime/size. An attacker who rewrites a file and then
  forges its size **and** mtime to exactly match the cached listing would evade
  the token — this is a deliberate, documented reduction in guarantee, not a
  silent one. The process is short-lived by design (idle-exit, default 600 s),
  which bounds the window.

If you need the old guarantee, do not build this host.

## 4. Layout

```
tools/model-host/
├─ build.ps1            dedicated venv + pinned deps + PyInstaller (onedir/windowed)
├─ apply.ps1            backup + elevated copy over the installed host + restart WeaselServer
├─ revert.ps1           restore from backup + restart WeaselServer
├─ harness/             A/B latency, integrity and CLI-parity harnesses (stdlib only)
│   ├─ ab.py
│   ├─ integrity.py
│   ├─ cli_parity.py
│   └─ verify_cache.py
├─ evidence/            raw JSON results captured for this change
├─ README.md
└─ .gitignore           ignores .venv/ dist/ build/ backup/ harness/results/

The host source itself lives in the repository at
../scripts/language_input_model_host.py (repo root).
```

Generated artifacts (`.venv/`, `dist/` ~110 MB, `build/`, `backup/`) are
deliberately **not committed** — they are reproducible from the scripts and
would bloat the repository. `evidence/` holds the small raw results.

## 5. Build

Requires Windows, Python 3.11 and (preferred) `uv` on PATH. Creates a dedicated
venv at `tools\model-host\.venv` — **never** the settings-app venv.

```powershell
# Clears HTTP(S)_PROXY / ALL_PROXY for the process first (this machine's proxy
# throttles to ~43 KB/s; direct is ~8 MB/s), then:
#   uv venv --python 3.11 .venv
#   install ctranslate2==4.8.1 sentencepiece==0.2.1 numpy==2.4.6 pyinstaller==6.15.0
#   PyInstaller --onedir --windowed over scripts\language_input_model_host.py
tools\model-host\build.ps1
# faster re-builds once the venv exists:
tools\model-host\build.ps1 -SkipDependencyInstall
# rebuild the venv from scratch:
tools\model-host\build.ps1 -ForceVenv
```

Output: `tools\model-host\dist\LanguageInputModelHost\` (onedir bundle) and
`tools\model-host\build\build-manifest.json`.

Measured build for this change:

| | |
|---|---|
| Bundle size | **115 368 983 bytes** across **69 files** |
| `LanguageInputModelHost.exe` | 4 893 797 bytes, sha256 `05ae6b43e4d94f36fd6b3ae47acb6967d27eb4bc00360f791f58f7c8a0a15a80` |
| Source sha256 (in-tree, LF-normalised) | `7b521ed905fad967be30c9f8a981fd89d9a59ab7fe8b4d351a7512dcc8ba028a` |

## 6. Apply / rollback

> ⚠️ **The install step needs administrator rights.** Writing to
> `C:\Program Files\Rime\weasel-0.1.0` requires elevation, so `apply.ps1` and
> `revert.ps1` re-invoke themselves via `Start-Process -Verb RunAs` (a UAC
> prompt). **Nothing in this repository automates the install** — you run the
> script yourself, deliberately. The build and the harnesses stay entirely
> inside the repo.

```powershell
# Show the plan; changes nothing and never elevates:
tools\model-host\apply.ps1  -DryRun
tools\model-host\revert.ps1 -DryRun

# Apply (backs up the current exe + _internal to backup\original once, then
# stops WeaselServer.exe, prompts for UAC, copies, restarts WeaselServer.exe):
tools\model-host\apply.ps1

# Roll back to the backup:
tools\model-host\revert.ps1
```

Both are idempotent: a second `apply` is a no-op once the installed exe matches
the rebuilt bundle, and a second `revert` is a no-op once it matches the backup.
Use `-Force` to re-copy anyway.

Notes:

* Restarting `WeaselServer.exe` with no explicit environment **resets** the
  `LANGUAGE_INPUT_REMOTE_*` backend environment that the settings app injects;
  re-apply the backend from the settings app afterwards if you had one set.
* `-SkipServerRestart` skips the stop/start step.
* The elevated child only touches `Program Files`; the server lifecycle stays in
  the non-elevated parent so WeaselServer is not launched elevated.

## 7. Verification performed

All harnesses use the same launch flags as WeaselServer (`--serve --catalog …
--models … --port … --token <64 hex> --idle-seconds … --m2m100-catalog …`) and
read the live `%APPDATA%\Rime` model root read-only. Raw JSON is in `evidence/`.

### 7.1 Latency A/B (current installed host vs rebuilt), 5 samples

| Metric | Current | Rebuilt |
|---|---|---|
| `/health` min | 1 875.0 ms | 5.1 ms |
| `/health` median | 1 944.9 ms | 5.6 ms |
| `/health` max | 1 976.3 ms | 18.4 ms |
| warm zh→en min | 975.5 ms | 25.3 ms |
| warm zh→en median | 994.5 ms | 27.1 ms |
| warm zh→en max | 1 008.0 ms | 29.3 ms |
| cold startup | 2.590 s | 1.517 s |

Targets were "warm request well under 200 ms" and "`/health` likewise" — both
beat by roughly an order of magnitude. `evidence/ab-current.json`,
`evidence/ab-patched.json`.

### 7.2 Correctness parity

`打字 / 搭子 / 大` for zh→en, zh→ja and zh→es returned **byte-identical** JSON
(parsed-object equality, including the translation content) from both hosts.
`evidence/ab-parity.json`.

### 7.3 Integrity still enforced (TEMP model root, live root untouched)

A real `quickmt-zh-en` component was copied into a TEMP root and mutated:

| Case | Current host | Rebuilt host |
|---|---|---|
| valid | listed | listed |
| corrupted (same size, changed byte) | rejected | rejected |
| oversized (extra byte) | rejected | rejected |
| missing file | rejected | rejected |

The rebuilt host's `/health` on the corrupted root returned `languages=[]` and
`quickmt=[]`. `evidence/integrity-results.json`.

In-process cache semantics (`harness/verify_cache.py`): first load hashes once;
second load and `installed_components` hash zero times; a same-size tamper
re-verifies and is rejected; `_invalidate_component_cache()` forces
re-verification — all PASS.

### 7.4 CLI parity

`--list` (live root), `--audit-pack` (the real
`quickmt-zh-en-c27cc802.limodel`) and `--import-pack` (into TEMP roots; the
resulting `installed.json` compared byte-for-byte) produced identical results on
both hosts. `evidence/cli-parity-results.json`.

### 7.5 Immutability

* The host source is edited **in-tree**; the pre-fix revision is recorded by git
  history.
* Nothing was written to `C:\Program Files`; the installed exe retains its
  original mtime/size and no backup was created there.
* No `LanguageInputModelHost.exe` / `WeaselServer.exe` process is left running,
  and the live `%APPDATA%\Rime\language_input\models` root is untouched
  (original mtimes, no staging/backup directories).

## 8. Rationale / decision record

The host is Python frozen with PyInstaller, not C++, so this fix needs no Visual
Studio and does not touch the engine build. The source is now part of the tree,
so the fix ships as an ordinary source edit with the build tooling beside it.
The change is behaviour-preserving except for the documented integrity-cache
trade-off in §3. Recorded in the design doc decision record (§13) as row 26.

## 9. Not verified here

* The actual elevated install into `C:\Program Files` was **not** performed — it
  needs administrator rights and is deliberately manual. Only `-DryRun` and the
  temp-directory `-Elevated` code path (backup/copy/restore/idempotency) were
  executed.
* `WeaselServer.exe` was never running during the tests, so the stop/restart
  branch was exercised only via `-DryRun` output, not against a live server.
