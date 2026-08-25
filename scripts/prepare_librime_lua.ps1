[CmdletBinding()]
param(
  [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$pluginRepository = 'https://github.com/hchunhui/librime-lua.git'
$pluginCommit = 'ec52e48ea18f11af37717a01c337f853215cf70b'
$thirdpartyCommit = 'fa40fadd8af1e5b1fbd55703ccbd54476956d74c'

function Invoke-Git {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
  & git @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "git exited with ${LASTEXITCODE}: git $($Arguments -join ' ')"
  }
}

function Confirm-OwnedPath {
  param([string]$Candidate, [string]$Parent)
  $candidatePath = [IO.Path]::GetFullPath($Candidate)
  $parentPath = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
  if (-not $candidatePath.StartsWith($parentPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Path escaped its build-owned parent: $candidatePath"
  }
  return $candidatePath
}

function Checkout-PinnedRepository {
  param(
    [string]$Target,
    [string]$Commit,
    [string]$Branch = ''
  )
  if (-not (Test-Path -LiteralPath $Target)) {
    $cloneArguments = @('clone', '--filter=blob:none', '--no-checkout')
    if ($Branch) {
      $cloneArguments += @('--branch', $Branch)
    }
    $cloneArguments += @($pluginRepository, $Target)
    Invoke-Git @cloneArguments
  }
  $inside = (& git -C $Target rev-parse --is-inside-work-tree 2>$null)
  if ($LASTEXITCODE -ne 0 -or $inside -ne 'true') {
    throw "Existing dependency directory is not a Git checkout: $Target"
  }
  $head = (& git -C $Target rev-parse HEAD 2>$null)
  if ($LASTEXITCODE -ne 0 -or $head -ne $Commit) {
    $changes = @(& git -C $Target status --short)
    if ($changes.Count -gt 0) {
      throw "Refusing to replace a modified dependency checkout: $Target"
    }
    Invoke-Git @('-C', $Target, 'fetch', '--depth', '1', 'origin', $Commit)
    Invoke-Git @('-C', $Target, 'checkout', '--detach', $Commit)
  }
  $actual = (& git -C $Target rev-parse HEAD).Trim()
  if ($actual -ne $Commit) {
    throw "Pinned checkout mismatch in ${Target}: expected $Commit, got $actual"
  }
}

function Ensure-GitPatch {
  param(
    [string]$Repository,
    [string]$Patch
  )
  & git -C $Repository apply --check --reverse -- $Patch 2>$null
  if ($LASTEXITCODE -eq 0) {
    return 'already applied'
  }
  $checkOutput = @(& git -C $Repository apply --check -- $Patch 2>&1)
  if ($LASTEXITCODE -ne 0) {
    throw "Patch does not apply cleanly: $Patch`n$($checkOutput -join "`n")"
  }
  Invoke-Git @('-C', $Repository, 'apply', '--whitespace=nowarn', '--', $Patch)
  return 'applied'
}

$root = [IO.Path]::GetFullPath($RepositoryRoot)
$sensitivePatch = Join-Path $root 'patches\librime-sensitive-mode.patch'
if (-not (Test-Path -LiteralPath $sensitivePatch)) {
  throw "Required librime patch is missing: $sensitivePatch"
}
$sensitivePatchStatus = Ensure-GitPatch (Join-Path $root 'librime') $sensitivePatch
$pluginsRoot = Join-Path $root 'librime\plugins'
if (-not (Test-Path -LiteralPath (Join-Path $pluginsRoot 'CMakeLists.txt'))) {
  throw "librime plugin directory not found: $pluginsRoot"
}

$pluginTarget = Confirm-OwnedPath (Join-Path $pluginsRoot 'lua') $pluginsRoot
Checkout-PinnedRepository $pluginTarget $pluginCommit

$thirdpartyTarget = Confirm-OwnedPath (Join-Path $pluginTarget 'thirdparty') $pluginTarget
Checkout-PinnedRepository $thirdpartyTarget $thirdpartyCommit 'thirdparty'

$luaHeader = Join-Path $thirdpartyTarget 'lua5.4\lua.h'
if (-not (Test-Path -LiteralPath $luaHeader)) {
  throw "Pinned Lua source is incomplete: $luaHeader"
}
$luaRelease = (Select-String -LiteralPath $luaHeader -Pattern '^#define LUA_RELEASE\s+').Line

[pscustomobject]@{
  PluginPath = $pluginTarget
  PluginCommit = $pluginCommit
  ThirdpartyCommit = $thirdpartyCommit
  LuaReleaseDefinition = $luaRelease
  SensitivePatch = $sensitivePatchStatus
}
