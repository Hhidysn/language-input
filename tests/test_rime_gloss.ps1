[CmdletBinding()]
param(
  [string]$RepositoryRoot = '',
  [ValidateSet('x64', 'Win32')]
  [string]$Architecture = 'x64'
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
  $RepositoryRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
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
  $stderrPath = Join-Path $sessionDirectory 'rime_api_console.stderr.log'
  try {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
      $result = @($Commands + 'exit') | & $console 2> $stderrPath | Out-String
    } finally {
      $ErrorActionPreference = $previousErrorActionPreference
    }
    $stderr = if (Test-Path -LiteralPath $stderrPath) {
      [IO.File]::ReadAllText($stderrPath)
    } else {
      ''
    }
    if ($LASTEXITCODE -ne 0) {
      throw "rime_api_console exited with $LASTEXITCODE`n$result`n$stderr"
    }
    return $result
  } finally {
    if (Test-Path -LiteralPath $stderrPath) {
      Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
    Pop-Location
  }
}

function Assert-Match {
  param([string]$Text, [string]$Pattern, [string]$Message)
  if ($Text -notmatch $Pattern) {
    throw "$Message`nPattern: $Pattern`nOutput:`n$Text"
  }
}

function Assert-NotMatch {
  param([string]$Text, [string]$Pattern, [string]$Message)
  if ($Text -match $Pattern) {
    throw "$Message`nPattern: $Pattern`nOutput:`n$Text"
  }
}

$defaultText = [IO.File]::ReadAllText((Join-Path $dataDirectory 'default.yaml'))
$savedOptions = @(
  'language_input_gloss',
  'language_input_ai',
  'language_input_model_m2m100',
  'language_input_en',
  'language_input_ja',
  'language_input_es',
  'language_input_speech'
)
foreach ($option in $savedOptions) {
  $count = [regex]::Matches(
    $defaultText,
    '(?m)^    - ' + [regex]::Escape($option) + '\s*$'
  ).Count
  if ($count -ne 1) {
    throw "Saved option $option appears $count times instead of exactly once."
  }
}

$schemaCases = @(
  [pscustomobject]@{
    Id = 'language_input_pinyin'
    Name = 'Full-pinyin'
    Input = 'nihao'
    NormalizedInput = 'shangtai'
  },
  [pscustomobject]@{
    Id = 'language_input_flypy'
    Name = 'Xiaohe double-pinyin'
    Input = 'nihc'
    NormalizedInput = 'uhtd'
  }
)
$languages = @('en', 'ja', 'es')
$matrixCases = 0

foreach ($schema in $schemaCases) {
  $activation = Invoke-RimeSession @(
    "select schema $($schema.Id)",
    $schema.Input
  )
  Assert-Match $activation "schema: $([regex]::Escape($schema.Id)) /" `
    "$($schema.Name) schema did not activate."
  Assert-Match $activation 'page: 1\s+\(of size 9\)' `
    "$($schema.Name) page size is not 9."

  foreach ($display in @($false, $true)) {
    foreach ($requestedAi in @($false, $true)) {
      foreach ($language in $languages) {
        foreach ($speech in @($false, $true)) {
          $commands = [Collections.Generic.List[string]]::new()
          [void]$commands.Add("select schema $($schema.Id)")
          [void]$commands.Add('set option !language_input_sensitive')
          [void]$commands.Add('set option !language_input_en')
          [void]$commands.Add('set option !language_input_ja')
          [void]$commands.Add('set option !language_input_es')
          [void]$commands.Add("set option language_input_$language")
          $aiCommand = if ($requestedAi) {
            'set option language_input_ai'
          } else {
            'set option !language_input_ai'
          }
          [void]$commands.Add($aiCommand)
          $displayCommand = if ($display) {
            'set option language_input_gloss'
          } else {
            'set option !language_input_gloss'
          }
          [void]$commands.Add($displayCommand)
          $speechCommand = if ($speech) {
            'set option language_input_speech'
          } else {
            'set option !language_input_speech'
          }
          [void]$commands.Add($speechCommand)
          [void]$commands.Add($schema.Input)

          $result = Invoke-RimeSession $commands.ToArray()
          $cell = "$($schema.Name): display=$display requestedAi=$requestedAi language=$language speech=$speech"
          Assert-Match $result '1\. \[你好\]' "$cell lost the Chinese candidate."

          $effectiveAi = $requestedAi -or $language -ne 'en'
          $expectsDictionary = $display -and -not $effectiveAi
          if ($expectsDictionary) {
            Assert-Match $result '1\. \[你好\]〔en·词〕 hello; hi' `
              "$cell did not render the fixed English dictionary gloss."
          } else {
            Assert-NotMatch $result '1\. \[你好\].*〔en·词〕' `
              "$cell rendered a dictionary gloss outside English dictionary mode."
          }
          if (-not $requestedAi -and $language -ne 'en') {
            Assert-Match $result 'updated option: language_input_ai = 1' `
              "$cell did not force the visible provider back to AI."
          }
          ++$matrixCases
        }
      }
    }
  }

  $preservedAi = Invoke-RimeSession @(
    "select schema $($schema.Id)",
    'set option !language_input_sensitive',
    'set option language_input_gloss',
    'set option !language_input_en',
    'set option !language_input_es',
    'set option language_input_ja',
    'set option !language_input_ja',
    'set option language_input_en',
    $schema.Input
  )
  Assert-NotMatch $preservedAi '1\. \[你好\].*〔en·词〕' `
    "$($schema.Name) did not preserve AI when returning from Japanese to English."

  $normalized = Invoke-RimeSession @(
    "select schema $($schema.Id)",
    'set option !language_input_sensitive',
    'set option !language_input_ja',
    'set option !language_input_es',
    'set option language_input_en',
    'set option !language_input_ai',
    'set option language_input_gloss',
    'set option !zh_hans',
    'set option zh_hant',
    $schema.NormalizedInput
  )
  Assert-Match $normalized '\d+\.\s+(?:\[上臺\]|上臺\s+)〔en·词〕 to rise to power' `
    "$($schema.Name) did not perform exact-then-OpenCC-normalized lookup."
}

$sensitive = Invoke-RimeSession @(
  'select schema language_input_pinyin',
  'set option !language_input_ja',
  'set option !language_input_es',
  'set option language_input_en',
  'set option !language_input_ai',
  'set option language_input_gloss',
  'set option language_input_sensitive',
  'nihao'
)
Assert-Match $sensitive '1\. \[你好\](\r?\n|\s*$)' 'Sensitive mode did not retain the Chinese candidate.'
if ($sensitive.Contains('〔en·词〕')) {
  throw "Sensitive mode leaked a gloss marker.`n$sensitive"
}

$selection = Invoke-RimeSession @(
  'select schema language_input_pinyin',
  'set option !language_input_ja',
  'set option !language_input_es',
  'set option language_input_en',
  'set option !language_input_ai',
  'set option language_input_gloss',
  'nihao',
  # This test needs only the numeric commit. Sensitive mode deliberately
  # suppresses user-dictionary learning so output/data remains installable.
  'set option language_input_sensitive',
  '1'
)
Assert-Match $selection 'commit: 你好' 'Number-key candidate selection did not commit the first candidate.'
Assert-NoSharedUserDatabase

$modelSwitch = Invoke-RimeSession @(
  'select schema language_input_pinyin',
  'set option !language_input_sensitive',
  'set option language_input_gloss',
  'set option language_input_ai',
  'set option language_input_model_m2m100',
  'nihao'
)
Assert-Match $modelSwitch 'updated option: language_input_model_m2m100 = 1' `
  'M2M100 model switch did not update independently.'
Assert-Match $modelSwitch '1\. \[你好\]' `
  'M2M100 model switch changed the candidate text.'
Assert-NotMatch $modelSwitch '〔en·词〕' `
  'M2M100 model switch did not remain in AI mode.'
Assert-NoSharedUserDatabase

[pscustomobject]@{
  Architecture = $Architecture
  SwitchMatrixCases = $matrixCases
  FullPinyin = 'passed'
  Xiaohe = 'passed'
  OpenCCNormalization = 'passed'
  ProviderLanguageRules = 'passed'
  SavedOptions = 'passed'
  SensitiveSuppression = 'passed'
  NumericSelection = 'passed'
  ModelSelection = 'passed'
}
