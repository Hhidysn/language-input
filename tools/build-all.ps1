<#
.SYNOPSIS
    Orchestration skeleton for the full product build:
    engine -> model host -> settings app -> (single merged installer = TODO).

.DESCRIPTION
    This is a *skeleton*: steps 1-3 invoke the real build scripts that already
    exist in the repository. Step 4 (the single merged installer) is NOT
    implemented here -- it is left as an explicit TODO because it cannot be
    completed or validated without a real engine build and installer toolchain.

    Order matters:
      1. engine        (tools\build-engine.ps1)        -> engine\output\*
      2. model host    (tools\model-host\build.ps1)    -> tools\model-host\dist\LanguageInputModelHost\
      3. settings app  (settings-app\packaging\build.ps1) -> settings-app\dist\LanguageInputSettings\
      4. merged installer                               -> NOT IMPLEMENTED (TODO below)

    Nothing here commits or pushes.

.PARAMETER EngineBuildArgs
    Forwarded to tools\build-engine.ps1 -BuildArgs, e.g. `rime,data,weasel` or
    `rime,data,weasel,installer` (the latter also produces the engine's own
    NSIS installer, which step 4 would consume).

.EXAMPLE
    .\tools\build-all.ps1 -DryRun
    .\tools\build-all.ps1 -EngineBuildArgs rime,data,weasel,installer
#>
[CmdletBinding()]
param(
    [string]$RepoRoot,
    [ValidateSet('release', 'debug')]
    [string]$Config = 'release',
    [string[]]$EngineBuildArgs = @(),
    [switch]$SkipEngine,
    [switch]$SkipModelHost,
    [switch]$SkipSettingsApp,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}
$repo = [IO.Path]::GetFullPath($RepoRoot)

function Invoke-Script {
    param([string]$Name, [string]$Path, [string[]]$Arguments)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Name script not found: $Path"
    }
    Write-Host ''
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    Write-Host "  $ powershell -File `"$Path`" $($Arguments -join ' ')"
    if ($DryRun) { return }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Path @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed (exit $LASTEXITCODE): $Path"
    }
}

if ($DryRun) { Write-Host '*** DRY RUN: no changes will be made ***' -ForegroundColor Yellow }

$engine = Join-Path $repo 'engine'

# ---------------------------------------------------------------------------
# 1. engine
# ---------------------------------------------------------------------------
if ($SkipEngine) {
    Write-Host '[1/4] engine: skipped (-SkipEngine)'
}
else {
    $a = @('-Config', $Config)
    if ($EngineBuildArgs.Count -gt 0) { $a += @('-BuildArgs', ($EngineBuildArgs -join ',')) }
    if ($DryRun) { $a += '-DryRun' }
    Invoke-Script -Name '[1/4] engine' -Path (Join-Path $repo 'tools\build-engine.ps1') -Arguments $a
}

# ---------------------------------------------------------------------------
# 2. model host
# ---------------------------------------------------------------------------
if ($SkipModelHost) {
    Write-Host '[2/4] model host: skipped (-SkipModelHost)'
}
else {
    # tools\model-host\build.ps1 builds the in-tree
    # scripts\language_input_model_host.py into a PyInstaller onedir bundle.
    $a = @('-RepositoryRoot', $repo)
    Invoke-Script -Name '[2/4] model host' -Path (Join-Path $repo 'tools\model-host\build.ps1') -Arguments $a
}

# ---------------------------------------------------------------------------
# 3. settings app
# ---------------------------------------------------------------------------
if ($SkipSettingsApp) {
    Write-Host '[3/4] settings app: skipped (-SkipSettingsApp)'
}
else {
    Invoke-Script -Name '[3/4] settings app' -Path (Join-Path $repo 'settings-app\packaging\build.ps1') -Arguments @()
}

# ---------------------------------------------------------------------------
# 4. single merged installer  -- TODO, deliberately not implemented
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '=== [4/4] single merged installer ===' -ForegroundColor Cyan
Write-Host @'
TODO (not implemented, on purpose): build ONE installer that installs
  (a) the engine (Weasel binaries + Rime data),
  (b) the model host bundle, and
  (c) the settings app bundle,

and registers/unregisters all of them from a single add/remove-programs entry.

Inputs that would be combined (all produced by steps 1-3):
  * engine NSIS script : engine\output\install.nsi  (built when build-engine is
                         run with -BuildArgs ...installer; makensis required)
  * model host bundle  : tools\model-host\dist\LanguageInputModelHost\
  * settings app bundle: settings-app\dist\LanguageInputSettings\

Unknowns that must be resolved before this can be written honestly:
  1. Where in the Weasel install layout the two extra bundles should live
     (e.g. <WeaselRoot>\model-host\, <WeaselRoot>\settings\), and whether the
     settings app must be auto-started.
  2. Whether to extend engine\output\install.nsi (add File/Section entries) or
     wrap all three in a top-level NSIS/bootstrapper script. Extending is less
     invasive but couples us to the generated .nsi.
  3. Uninstall/upgrade semantics: single uninstaller, version pinning, and how
     a pre-existing Weasel install is detected/migrated.
  4. Code-signing (if any) of the merged payload.

Until (1)-(4) are decided, DO NOT fake a merged installer. Ship the three
artifacts separately and document the manual combination steps.
'@

Write-Host ''
if ($DryRun) {
    Write-Host '[DryRun] nothing was executed.' -ForegroundColor Yellow
}
else {
    Write-Host 'steps 1-3 completed; step 4 is a documented TODO (see above).' -ForegroundColor Yellow
}
