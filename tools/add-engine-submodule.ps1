<#
.SYNOPSIS
    Add official rime/weasel as a submodule at engine\ pinned to the 0.17.4 tag
    commit, then initialize the nested submodules.

.DESCRIPTION
    Idempotent. The submodule points at OFFICIAL upstream
    (https://github.com/rime/weasel.git), never at the personal fork, so a
    deleted fork cannot break the rebuild.

    What it does (printed in full before it runs):
      1. git -C <repo> submodule add <url> engine          (only if not present)
      2. git -C engine fetch --tags origin <tag>
      3. git -C engine checkout --detach <pin-commit>      (pin to the tag commit)
      4. git -C <repo> add engine                          (stage the gitlink)
      5. git -C <repo> submodule update --init --recursive (librime + plum)

    It never commits and never pushes.

.PARAMETER DryRun
    Print exactly what would be done and exit without touching anything.

.PARAMETER Proxy
    Optional proxy URL (e.g. http://127.0.0.1:7898) exported to child git
    processes. On this machine direct GitHub access is reset; the proxy works
    but throttles. Leave empty to use the ambient environment.

.EXAMPLE
    .\tools\add-engine-submodule.ps1 -DryRun
    .\tools\add-engine-submodule.ps1 -Proxy http://127.0.0.1:7898
#>
[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$EnginePath = 'engine',
    [string]$UpstreamUrl = 'https://github.com/rime/weasel.git',
    [string]$PinTag = '0.17.4',
    [string]$PinCommit = '9cc96e20dc71b80876b12f689bb5863c76c2a7ed',
    [string]$Proxy = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}
$repo = [IO.Path]::GetFullPath($RepoRoot)
$engine = Join-Path $repo $EnginePath

if (-not (Test-Path -LiteralPath (Join-Path $repo '.git'))) {
    throw "Not a git repository: $repo"
}

if ($Proxy) {
    foreach ($n in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy')) {
        [Environment]::SetEnvironmentVariable($n, $Proxy, 'Process')
    }
    Write-Host "proxy exported to child git processes: $Proxy"
}

function Invoke-Git {
    param([string[]]$GitArgs, [switch]$AllowFail)
    $display = 'git ' + ($GitArgs -join ' ')
    Write-Host "  $ $display"
    if ($DryRun) { return 0 }
    $output = & git @GitArgs 2>&1
    $code = $LASTEXITCODE
    if ($output) { $output | ForEach-Object { Write-Host "      $_" } }
    if ($code -ne 0 -and -not $AllowFail) {
        throw "command failed (exit $code): $display"
    }
    return $code
}

# ---------------------------------------------------------------------------
# Inspect current state
# ---------------------------------------------------------------------------
$gitmodules = Join-Path $repo '.gitmodules'
$registered = $false
if (Test-Path -LiteralPath $gitmodules) {
    $patterns = @(
        ('(?m)^\s*path\s*=\s*' + [Regex]::Escape($EnginePath) + '\s*$')
    )
    $registered = [bool](Select-String -LiteralPath $gitmodules -Pattern $patterns -Quiet)
}
$engineDirExists = Test-Path -LiteralPath $engine
$engineIsGit = Test-Path -LiteralPath (Join-Path $engine '.git')

Write-Host "repo           : $repo"
Write-Host "engine path    : $engine"
Write-Host "registered     : $registered"
Write-Host "engine exists  : $engineDirExists"
Write-Host ""

if ($DryRun) { Write-Host '*** DRY RUN: no changes will be made ***' -ForegroundColor Yellow }

if ($engineDirExists -and -not $engineIsGit) {
    throw "Path '$engine' exists but is not a git worktree. Move it aside, then re-run."
}

# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
if ($registered) {
    Write-Host '[1/5] submodule already registered in .gitmodules -- skipping add'
    Write-Host '[2/5] ensure checked out and pinned'
    Invoke-Git @('-C', $repo, 'submodule', 'update', '--init', '--recursive', $EnginePath) | Out-Null
    Invoke-Git @('-C', $engine, 'fetch', '--tags', 'origin', $PinTag) | Out-Null
    Invoke-Git @('-C', $engine, 'checkout', '--detach', $PinCommit) | Out-Null
    Invoke-Git @('-C', $repo, 'add', $EnginePath) | Out-Null
} else {
    Write-Host '[1/5] add official upstream as a submodule at engine\'
    Invoke-Git @('-C', $repo, 'submodule', 'add', $UpstreamUrl, $EnginePath) | Out-Null
    Write-Host '[2/5] fetch the tag and pin to the tag commit'
    Invoke-Git @('-C', $engine, 'fetch', '--tags', 'origin', $PinTag) | Out-Null
    Invoke-Git @('-C', $engine, 'checkout', '--detach', $PinCommit) | Out-Null
    Invoke-Git @('-C', $repo, 'add', $EnginePath) | Out-Null
}

Write-Host '[3/5] verify the pinned URL is official upstream'
if (-not $DryRun) {
    $url = (& git -C $repo config -f .gitmodules --get "submodule.$EnginePath.url" 2>&1)
    Write-Host "      .gitmodules submodule.$EnginePath.url = $url"
    if ($url -notmatch 'github\.com/rime/weasel') {
        Write-Warning "submodule URL is not rime/weasel -- the rebuild would depend on a fork!"
    }
}

Write-Host '[4/5] initialize nested submodules (librime, plum), recursively'
Invoke-Git @('-C', $repo, 'submodule', 'update', '--init', '--recursive') | Out-Null

Write-Host '[5/5] report status (nothing is committed or pushed)'
Invoke-Git @('-C', $repo, 'submodule', 'status', '--recursive') | Out-Null
if (-not $DryRun) {
    Write-Host ''
    Write-Host 'Done. Review with: git status ; git diff -- .gitmodules' -ForegroundColor Green
    Write-Host 'Then the orchestrator commits. Next: .\tools\build-engine.ps1' -ForegroundColor Green
}
