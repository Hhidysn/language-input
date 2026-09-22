<#
.SYNOPSIS
    Apply patches\engine\*.patch onto engine\, init nested submodules, and run
    the engine's own build (build.bat / xbuild.bat).

.DESCRIPTION
    Steps:
      1. initialize the nested submodules (librime, plum) recursively
      2. apply the numbered engine patch series to engine\ (idempotent)
      3. apply patches\engine\librime-sensitive-mode.patch to engine\librime
      4. verify the toolchain (Visual Studio + xmake + CMake + Boost)
      5. invoke engine\build.bat (or xbuild.bat) inside a VS developer prompt

    The toolchain check fails LOUDLY with a clear, actionable message when a
    required tool is missing, instead of letting build.bat emit a confusing
    error. Use -SkipToolchainCheck to bypass it.

    This script never commits and never pushes.

.PARAMETER BuildArgs
    Extra arguments forwarded to the engine build. Examples:
      -BuildArgs rime,data,weasel   # full chain from source
      -BuildArgs all                # upstream "all" (adds arm64 + installer)
    Empty (default) means "let the engine decide" (it builds Weasel only and
    expects rime.lib/rime.dll to already exist).

.PARAMETER DryRun
    Print every command and the toolchain findings; change nothing.

.EXAMPLE
    .\tools\build-engine.ps1 -DryRun
    .\tools\build-engine.ps1 -BuildArgs rime,data,weasel
#>
[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$EnginePath = 'engine',
    [ValidateSet('release', 'debug')]
    [string]$Config = 'release',
    [string[]]$BuildArgs = @(),
    [string]$BuildScript = '',
    [switch]$SkipLibrimePatch,
    [switch]$SkipToolchainCheck,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}
$repo = [IO.Path]::GetFullPath($RepoRoot)
$engine = Join-Path $repo $EnginePath
$patchesDir = Join-Path $repo 'patches\engine'
$librimePatch = Join-Path $patchesDir 'librime-sensitive-mode.patch'

if ($DryRun) { Write-Host '*** DRY RUN: no changes will be made ***' -ForegroundColor Yellow }

$engineReady = Test-Path -LiteralPath (Join-Path $engine '.git')
if (-not $engineReady) {
    if (-not $DryRun) {
        throw @"
engine\ is not a git worktree ($engine).
Run .\tools\add-engine-submodule.ps1 first (add it with -DryRun to preview).
"@
    }
    Write-Warning "engine\ is not a git worktree yet ($engine); previewing the plan as if it were."
}

function Invoke-Git {
    param([string[]]$GitArgs, [switch]$AllowFail)
    $display = 'git ' + ($GitArgs -join ' ')
    Write-Host "  $ $display"
    if ($DryRun) { return 0 }
    $output = & git @GitArgs 2>&1
    $code = $LASTEXITCODE
    if ($output) { $output | ForEach-Object { Write-Host "      $_" } }
    if ($code -ne 0) {
        if ($AllowFail) { return $code }
        throw "command failed (exit $code): $display"
    }
    return 0
}

function Test-GitApply {
    param([string]$Dir, [string]$Patch, [switch]$Reverse)
    $extra = @('-C', $Dir, 'apply')
    if ($Reverse) { $extra += '--reverse' }
    $extra += @('--check', '--whitespace=nowarn', $Patch)
    & git @extra *> $null
    return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------------------
# 1. nested submodules
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '[1/5] initialize nested submodules (librime, plum) recursively'
Invoke-Git @('-C', $repo, 'submodule', 'update', '--init', '--recursive') | Out-Null
if (-not $DryRun) {
    if (-not (Test-Path -LiteralPath (Join-Path $engine 'librime\src\rime\gear\memory.cc'))) {
        throw "engine\librime is not initialized (expected librime\src\rime\gear\memory.cc). 'git submodule update --init --recursive' did not complete."
    }
}

# ---------------------------------------------------------------------------
# 2. engine patch series (idempotent)
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '[2/5] apply the numbered engine patch series to engine\'
$patchFiles = @()
if (Test-Path -LiteralPath $patchesDir) {
    $patchFiles = Get-ChildItem -LiteralPath $patchesDir -Filter '*.patch' |
        Where-Object { $_.Name -match '^\d{4}-' } | Sort-Object Name
}
if ($patchFiles.Count -eq 0) {
    throw "no numbered patches found in $patchesDir"
}

if ($DryRun) {
    foreach ($p in $patchFiles) {
        Write-Host "  $ git -C `"$EnginePath`" apply --check --whitespace=nowarn `"$($p.FullName)`""
        Write-Host "  $ git -C `"$EnginePath`" apply --whitespace=nowarn `"$($p.FullName)`""
    }
} else {
    $first = $patchFiles[0]
    $last = $patchFiles[$patchFiles.Count - 1]
    if (Test-GitApply -Dir $engine -Patch $first.FullName) {
        # clean tree: apply the whole series in order
        foreach ($p in $patchFiles) {
            if (-not (Test-GitApply -Dir $engine -Patch $p.FullName)) {
                throw "patch does not apply cleanly: $($p.Name). Reset engine\ (git -C engine checkout -- . ; git -C engine clean -fd) and retry."
            }
            Invoke-Git @('-C', $engine, 'apply', '--whitespace=nowarn', $p.FullName) | Out-Null
        }
        Write-Host "  applied $($patchFiles.Count) patches"
    }
    elseif (Test-GitApply -Dir $engine -Patch $last.FullName -Reverse) {
        Write-Host '  series already applied -- skipping (idempotent)'
    }
    else {
        throw @"
engine\ is neither pristine nor fully patched.
  * first patch forward-check : FAIL
  * last  patch reverse-check : FAIL
Reset engine\ to a clean checkout and retry:
    git -C engine checkout -- .
    git -C engine clean -fd
Then re-run this script.
"@
    }
}

# ---------------------------------------------------------------------------
# 3. librime patch (nested submodule)
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '[3/5] apply the librime sensitive-mode patch to engine\librime'
if ($SkipLibrimePatch) {
    Write-Host '  skipped (-SkipLibrimePatch)'
}
elseif (-not (Test-Path -LiteralPath $librimePatch)) {
    Write-Host "  no librime patch at $librimePatch -- skipping"
}
else {
    $lr = Join-Path $engine 'librime'
    if ($DryRun) {
        Write-Host "  $ git -C `"$EnginePath\librime`" apply --whitespace=nowarn `"$librimePatch`""
    }
    elseif (Test-GitApply -Dir $lr -Patch $librimePatch -Reverse) {
        Write-Host '  already applied -- skipping (idempotent)'
    }
    else {
        if (-not (Test-GitApply -Dir $lr -Patch $librimePatch)) {
            throw "librime patch does not apply cleanly: $librimePatch. Check that engine\librime is at the pinned commit (see PINS.md)."
        }
        Invoke-Git @('-C', $lr, 'apply', '--whitespace=nowarn', $librimePatch) | Out-Null
        Write-Host '  applied'
    }
}

# ---------------------------------------------------------------------------
# 4. toolchain
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '[4/5] verify toolchain (Visual Studio, xmake, CMake, Boost)'

function Find-VsDevCmd {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        $ip = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
        if ($ip) {
            $candidate = Join-Path ($ip | Select-Object -First 1) 'Common7\Tools\VsDevCmd.bat'
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
    }
    return $null
}

$vsDevCmd = Find-VsDevCmd
$haveCl = [bool](Get-Command cl.exe -ErrorAction SilentlyContinue)
$haveXmake = [bool](Get-Command xmake -ErrorAction SilentlyContinue)
$haveCmake = [bool](Get-Command cmake -ErrorAction SilentlyContinue)

$boostCandidates = @()
if ($env:BOOST_ROOT) { $boostCandidates += $env:BOOST_ROOT }
$boostCandidates += (Join-Path $engine 'deps\boost_1_84_0')
$boostCandidates += (Join-Path $engine 'deps\boost_1_78_0')
$boostRoot = $null
foreach ($c in $boostCandidates) {
    if ($c -and (Test-Path -LiteralPath (Join-Path $c 'boost'))) { $boostRoot = $c; break }
}

$nsis = Join-Path ${env:ProgramFiles(x86)} 'NSIS\Bin\makensis.exe'
$wantInstaller = ($BuildArgs -contains 'installer') -or ($BuildArgs -contains 'all')
$haveNsis = (Test-Path -LiteralPath $nsis) -or [bool](Get-Command makensis.exe -ErrorAction SilentlyContinue)

$missing = @()
if (-not ($vsDevCmd -or $haveCl)) { $missing += 'Visual Studio 2022 (MSVC v143 + ATL/MFC)' }
if (-not $haveXmake) { $missing += 'xmake' }
if (-not $haveCmake) { $missing += 'CMake' }
if (-not $boostRoot) { $missing += 'Boost source tree (BOOST_ROOT or engine\deps\boost_1_84_0)' }
if ($wantInstaller -and -not $haveNsis) { $missing += 'NSIS (makensis)' }

Write-Host ("  Visual Studio dev prompt : {0}" -f $(if ($vsDevCmd) { $vsDevCmd } elseif ($haveCl) { 'cl.exe already on PATH' } else { 'MISSING' }))
Write-Host ("  xmake                    : {0}" -f $(if ($haveXmake) { (Get-Command xmake).Source } else { 'MISSING' }))
Write-Host ("  CMake                    : {0}" -f $(if ($haveCmake) { (Get-Command cmake).Source } else { 'MISSING' }))
Write-Host ("  Boost                    : {0}" -f $(if ($boostRoot) { $boostRoot } else { 'MISSING' }))
Write-Host ("  NSIS                     : {0}" -f $(if ($haveNsis) { 'found' } else { 'not found (only needed for -BuildArgs installer/all)' }))

if ($missing.Count -gt 0) {
    $msg = @"
Missing required build tool(s):
  - $($missing -join "`n  - ")

How to fix:
  * Visual Studio 2022 with the "Desktop development with C++" workload
    (includes MSVC v143 and ATL/MFC). After installing, re-run this script.
  * xmake   : https://xmake.io  (winget install Xmake.Xmake)
  * CMake   : https://cmake.org (winget install Kitware.CMake)
  * Boost   : run engine\install_boost.bat, or set BOOST_ROOT to a Boost
              source tree root (the directory that contains boost\).
              The fork used Boost 1.84 (see patches\boost-1.84-msvc-14.4.patch);
              install_boost.bat defaults to 1.84.0.

Re-run with -SkipToolchainCheck to bypass this check (not recommended).
"@
    if ($DryRun) {
        Write-Warning $msg
    }
    else {
        throw $msg
    }
}
elseif (-not $SkipToolchainCheck) {
    Write-Host '  toolchain OK'
}

# ---------------------------------------------------------------------------
# 5. build
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '[5/5] invoke the engine build'

$script = $BuildScript
if (-not $script) {
    if (Test-Path -LiteralPath (Join-Path $engine 'build.bat')) { $script = 'build.bat' }
    elseif (Test-Path -LiteralPath (Join-Path $engine 'xbuild.bat')) { $script = 'xbuild.bat' }
    elseif ($DryRun) { $script = 'build.bat'; Write-Warning 'engine not present; previewing with build.bat' }
    else { throw "neither build.bat nor xbuild.bat found in $engine" }
}
if ($engineReady -and -not (Test-Path -LiteralPath (Join-Path $engine $script))) {
    throw "build script not found: $script in $engine"
}

$argString = ''
if ($Config) { $argString += " $Config" }
if ($BuildArgs.Count -gt 0) { $argString += ' ' + ($BuildArgs -join ' ') }

$cmdText = ''
if ($vsDevCmd) {
    $cmdText = 'call "' + $vsDevCmd + '" -arch=amd64 -host_arch=amd64 && cd /d "' + $engine + '" && ' + $script + $argString
}
else {
    $cmdText = 'cd /d "' + $engine + '" && ' + $script + $argString
}

Write-Host "  $ cmd.exe /c `"$cmdText`""

if ($DryRun) {
    Write-Host ''
    Write-Host '[DryRun] nothing was executed.' -ForegroundColor Yellow
    return
}

& cmd.exe /c $cmdText
$code = $LASTEXITCODE
if ($code -ne 0) {
    throw "engine build failed (exit $code). See output above. Script: $script$argString"
}
Write-Host 'engine build completed.' -ForegroundColor Green
Write-Host 'Binaries are under engine\output\.' -ForegroundColor Green
