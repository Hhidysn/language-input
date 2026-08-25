[CmdletBinding()]
param(
  [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
  [ValidateSet('x64', 'Win32')]
  [string]$Architecture = 'x64'
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($RepositoryRoot)
$testParent = [IO.Path]::GetFullPath((Join-Path $root ".cache\tests\privacy-$Architecture"))
$allowedParent = [IO.Path]::GetFullPath((Join-Path $root '.cache\tests')).TrimEnd('\') + '\'
if (-not $testParent.StartsWith($allowedParent, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Privacy test path escaped the owned test cache: $testParent"
}
if (Test-Path -LiteralPath $testParent) {
  Remove-Item -LiteralPath $testParent -Recurse -Force
}
New-Item -ItemType Directory -Path $testParent | Out-Null

$probe = Join-Path $root ".cache\tests\$Architecture\Release\LanguageInputPrivacyProbe.exe"
$shared = Join-Path $root 'output\data'
$rimeBin = Join-Path $root "librime\build_$Architecture\bin\Release"
$manager = Join-Path $rimeBin 'rime_dict_manager.exe'
$runtime = if ($Architecture -eq 'x64') {
  Join-Path $root 'output'
} else {
  Join-Path $root 'output\Win32'
}
foreach ($required in @($probe, $shared, $manager, (Join-Path $runtime 'rime.dll'))) {
  if (-not (Test-Path -LiteralPath $required)) {
    throw "Required privacy-test path is missing: $required"
  }
}
$sharedUserDatabases = @(Get-ChildItem -LiteralPath $shared -Recurse -Force |
  Where-Object { $_.Name -like '*.userdb*' })
if ($sharedUserDatabases.Count -ne 0) {
  throw "Privacy test requires clean shared data, but found: $($sharedUserDatabases.FullName -join ', ')"
}

$originalPath = $env:PATH
$env:PATH = "$runtime;$rimeBin;$originalPath"
try {
  $exports = @{}
  foreach ($mode in @('normal', 'sensitive')) {
    $user = Join-Path $testParent $mode
    New-Item -ItemType Directory -Path $user | Out-Null
    & $probe $shared $user $mode
    if ($LASTEXITCODE -ne 0) {
      throw "Privacy probe failed in $mode mode with exit code $LASTEXITCODE"
    }
    $export = Join-Path $testParent "$mode.userdb.txt"
    Push-Location -LiteralPath $user
    try {
      $managerOutput = & $manager -e luna_pinyin $export 2>&1 | Out-String
      $managerExit = $LASTEXITCODE
    } finally {
      Pop-Location
    }
    if ($managerExit -ne 0) {
      throw "Could not export the $mode user dictionary.`n$managerOutput"
    }
    $entries = if (Test-Path -LiteralPath $export) {
      @(Get-Content -LiteralPath $export -Encoding UTF8 | Where-Object {
        $_ -and -not $_.StartsWith('#')
      })
    } else {
      @()
    }
    $exports[$mode] = $entries
  }
} finally {
  $env:PATH = $originalPath
}

if ($exports.normal.Count -lt 1 -or
    -not ($exports.normal -match '^你好\tni hao\t')) {
  throw "The normal control did not learn 你好; the probe is not meaningful."
}
if ($exports.sensitive.Count -ne 0) {
  throw "Sensitive mode wrote user-dictionary entries:`n$($exports.sensitive -join "`n")"
}

[pscustomobject]@{
  Architecture = $Architecture
  NormalLearnedEntries = $exports.normal.Count
  SensitiveLearnedEntries = $exports.sensitive.Count
  Result = 'passed'
}
