[CmdletBinding()]
param(
  [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
  [ValidateSet('x64', 'Win32')]
  [string]$Architecture = 'x64'
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($RepositoryRoot)
$testParent = [IO.Path]::GetFullPath(
  (Join-Path $root ".cache\tests\performance-$Architecture")
)
$allowedParent = [IO.Path]::GetFullPath((Join-Path $root '.cache\tests')).TrimEnd('\') + '\'
if (-not $testParent.StartsWith($allowedParent, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Performance test path escaped the owned test cache: $testParent"
}
if (Test-Path -LiteralPath $testParent) {
  Remove-Item -LiteralPath $testParent -Recurse -Force
}
New-Item -ItemType Directory -Path $testParent | Out-Null

$benchmark = Join-Path $root ".cache\tests\$Architecture\Release\LanguageInputPerformance.exe"
$shared = Join-Path $root 'output\data'
$rimeBin = Join-Path $root "librime\build_$Architecture\bin\Release"
$runtime = if ($Architecture -eq 'x64') {
  Join-Path $root 'output'
} else {
  Join-Path $root 'output\Win32'
}
foreach ($required in @($benchmark, $shared, (Join-Path $runtime 'rime.dll'))) {
  if (-not (Test-Path -LiteralPath $required)) {
    throw "Required performance-test path is missing: $required"
  }
}

$originalPath = $env:PATH
$env:PATH = "$runtime;$rimeBin;$originalPath"
try {
  $output = & $benchmark $shared (Join-Path $testParent 'user') 2>&1 | Out-String
  $exitCode = $LASTEXITCODE
} finally {
  $env:PATH = $originalPath
}
if ($exitCode -ne 0) {
  throw "LanguageInputPerformance failed with exit code $exitCode.`n$output"
}
$result = $output | ConvertFrom-Json
if (-not $result.passed) {
  throw "LanguageInputPerformance did not meet its limits.`n$output"
}

[pscustomobject]@{
  Architecture = $Architecture
  Iterations = $result.iterations
  ColdMilliseconds = [Math]::Round($result.cold_ms, 3)
  MedianMilliseconds = [Math]::Round($result.median_ms, 3)
  P95Milliseconds = [Math]::Round($result.p95_ms, 3)
  MaximumMilliseconds = [Math]::Round($result.max_ms, 3)
  WorkingSetMiB = [Math]::Round($result.working_set_bytes / 1MB, 2)
  WorkingSetDeltaMiB = [Math]::Round($result.working_set_delta_bytes / 1MB, 2)
  Result = 'passed'
}
