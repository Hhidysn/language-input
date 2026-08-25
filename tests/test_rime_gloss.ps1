[CmdletBinding()]
param(
  [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
  [ValidateSet('x64', 'Win32')]
  [string]$Architecture = 'x64'
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($RepositoryRoot)
$dataDirectory = Join-Path $root 'output\data'
$sessionDirectory = [IO.Path]::GetFullPath(
  (Join-Path $root ".cache\tests\rime-session-$Architecture")
)
$allowedTestParent = [IO.Path]::GetFullPath((Join-Path $root '.cache\tests')).TrimEnd('\') + '\'
$console = Join-Path $root "librime\build_$Architecture\bin\Release\rime_api_console.exe"

foreach ($required in @($dataDirectory, $console)) {
  if (-not (Test-Path -LiteralPath $required)) {
    throw "Required Rime test path is missing: $required"
  }
}

function Assert-NoSharedUserDatabase {
  $userDatabases = @(Get-ChildItem -LiteralPath $dataDirectory -Recurse -Force |
    Where-Object { $_.Name -like '*.userdb*' })
  if ($userDatabases.Count -ne 0) {
    throw "Shared install data was contaminated by a user database: $($userDatabases.FullName -join ', ')"
  }
}

Assert-NoSharedUserDatabase
if (-not $sessionDirectory.StartsWith($allowedTestParent, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Rime session path escaped the owned test cache: $sessionDirectory"
}
if (Test-Path -LiteralPath $sessionDirectory) {
  Remove-Item -LiteralPath $sessionDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $sessionDirectory | Out-Null
Copy-Item -Path (Join-Path $dataDirectory '*') `
  -Destination $sessionDirectory -Recurse -Force

function Invoke-RimeSession {
  param([string[]]$Commands)
  Push-Location -LiteralPath $sessionDirectory
  try {
    $result = @($Commands + 'exit') | & $console 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
      throw "rime_api_console exited with $LASTEXITCODE`n$result"
    }
    return $result
  } finally {
    Pop-Location
  }
}

function Assert-Match {
  param([string]$Text, [string]$Pattern, [string]$Message)
  if ($Text -notmatch $Pattern) {
    throw "$Message`nPattern: $Pattern`nOutput:`n$Text"
  }
}

$pinyin = Invoke-RimeSession @(
  'select schema language_input_pinyin'
  'set option language_input_gloss'
  'nihao'
)
Assert-Match $pinyin 'schema: language_input_pinyin /' 'Full-pinyin schema did not activate.'
Assert-Match $pinyin 'page: 1\s+\(of size 9\)' 'Full-pinyin page size is not 9.'
Assert-Match $pinyin '1\. \[你好\]〔en〕 hello; hi' 'Full-pinyin first candidate lacks its English gloss.'

$flypy = Invoke-RimeSession @(
  'select schema language_input_flypy'
  'set option language_input_gloss'
  'nihc'
)
Assert-Match $flypy 'schema: language_input_flypy /' 'Xiaohe double-pinyin schema did not activate.'
Assert-Match $flypy 'page: 1\s+\(of size 9\)' 'Xiaohe double-pinyin page size is not 9.'
Assert-Match $flypy '1\. \[你好\]〔en〕 hello; hi' 'Xiaohe double-pinyin first candidate lacks its English gloss.'

$sensitive = Invoke-RimeSession @(
  'select schema language_input_pinyin'
  'set option language_input_gloss'
  'set option language_input_sensitive'
  'nihao'
)
Assert-Match $sensitive '1\. \[你好\](\r?\n|\s*$)' 'Sensitive mode did not retain the Chinese candidate.'
if ($sensitive.Contains('〔en〕')) {
  throw "Sensitive mode leaked a gloss marker.`n$sensitive"
}

$selection = Invoke-RimeSession @(
  'select schema language_input_pinyin'
  'set option language_input_gloss'
  'nihao'
  # This test needs only the numeric commit. Sensitive mode deliberately
  # suppresses user-dictionary learning so output/data remains installable.
  'set option language_input_sensitive'
  '1'
)
Assert-Match $selection 'commit: 你好' 'Number-key candidate selection did not commit the first candidate.'
Assert-NoSharedUserDatabase

[pscustomobject]@{
  Architecture = $Architecture
  FullPinyinGloss = 'passed'
  XiaoheGloss = 'passed'
  SensitiveSuppression = 'passed'
  NumericSelection = 'passed'
}
