# Engine rebuild pins

Everything needed to reproduce the personal engine fork from **official upstream
only**. The personal fork may be deleted; nothing below depends on it surviving.

## 1. Upstream engine to submodule

| Field | Value |
| --- | --- |
| Repository | `https://github.com/rime/weasel.git` |
| Ref to check out | tag `0.17.4` |
| Commit SHA | `9cc96e20dc71b80876b12f689bb5863c76c2a7ed` |
| Submodule path in our repo | `engine/` |

> The annotated tag object is `0.17.4` → tag object
> `f53a92543f2ef890503d0391e595af8fa9abdb02`; dereferencing it yields the commit
> `9cc96e20dc71b80876b12f689bb5863c76c2a7ed`. Pin the **commit**, not the tag
> object, when scripting.

## 2. Personal fork provenance (source of this patch series)

| Field | Value |
| --- | --- |
| Repository | `https://github.com/Hhidysn/weasel.git` (personal fork — may be deleted) |
| Branch | `codex/language-input-v0.2-local-ai` |
| HEAD commit | `a57d7f06ce72be9db4bd7b774fb1c6353847ea3a` |
| Base tag | `0.17.4` |
| Base commit | `9cc96e20dc71b80876b12f689bb5863c76c2a7ed` (identical to upstream `0.17.4`) |
| Product version (last verified installer build) | `0.17.4.3` (2026-08-29, fork then 3 commits past the tag — see `language-input/VERIFICATION.md`) |
| Range captured | `0.17.4..a57d7f0` (10 commits) |
| Product version at HEAD | `0.17.4.10` (`build.bat` computes `WEASEL_BUILD` = `git rev-list 0.17.4..HEAD --count` = 10) |

## 3. Nested submodules pinned by the fork

Raw `git -C <fork> submodule status` (identical at the base tag and at HEAD —
the fork did **not** move these gitlinks):

```
 1c23358157934bd6e6d6981f0c0164f05393b497 librime (1c23358)
 cab9ed35289f2022b6888503750d2dfb1851dbe0 plum (cab9ed3)
```

| Submodule | URL (from `.gitmodules`) | Pinned commit |
| --- | --- | --- |
| `librime` | `https://github.com/rime/librime.git` | `1c23358157934bd6e6d6981f0c0164f05393b497` (librime 1.13.1) |
| `plum` | `https://github.com/rime/plum.git` | `cab9ed35289f2022b6888503750d2dfb1851dbe0` |

**Verification of the pins:** `git ls-tree 0.17.4 librime plum` and
`git ls-tree a57d7f0 librime plum` return the *same* two gitlink SHAs, and the
fresh upstream `rime/weasel@0.17.4` clone has byte-identical `.gitmodules` and
the same gitlinks. Therefore the official `rime/weasel@0.17.4` submodule
pointers are the ones to use — no fork URL is required anywhere.

## 4. Toolchain expected by the engine build (0.17.4)

The 0.17.4 engine is driven by `build.bat` / `xbuild.bat`, which require an
xmake + Visual Studio (MSVC, ATL/MFC) environment; `build.bat rime` additionally
builds the nested librime via CMake.

| Tool | Notes |
| --- | --- |
| Visual Studio 2022 (MSVC v143, ATL/MFC) | `xbuild.bat` requires a Developer Command Prompt; `VsDevCmd.bat` supplies it |
| xmake | `build.bat` invokes `xmake` for the Weasel binaries |
| CMake | required to build the nested `librime` (`build.bat rime`) |
| Boost **source** tree | `BOOST_ROOT`; upstream default `deps/boost_1_78_0`, `install_boost.bat` default `1.84.0` |
| NSIS | only for the installer step (`build.bat installer`) |
| 7z / aria2c | only used by `install_boost.bat` / `get-rime.ps1` |

The fork added `patches/boost-1.84-msvc-14.4.patch` (a Boost 1.84 + MSVC 14.4
fix) and used Boost 1.84. `tools/build-engine.ps1` checks for these tools and
fails loudly when they are missing.
