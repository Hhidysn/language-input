[CmdletBinding()]
param(
  [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = [Text.UTF8Encoding]::new($false)
$root = [IO.Path]::GetFullPath($RepositoryRoot)
$sourceRoot = Join-Path $root 'language-input'
$rimeSource = Join-Path $sourceRoot 'rime'
$licenseSource = Join-Path $sourceRoot 'licenses'
$readmeSource = Join-Path $sourceRoot 'README.zh-CN.md'
$privacySource = Join-Path $sourceRoot 'PRIVACY.zh-CN.md'
$modelCatalogSource = Join-Path $sourceRoot 'models\packs-v2.json'
$outputData = Join-Path $root 'output\data'
$outputRoot = Join-Path $root 'output'

foreach ($required in @(
  $rimeSource,
  $licenseSource,
  $readmeSource,
  $privacySource,
  $modelCatalogSource,
  $outputData
)) {
  if (-not (Test-Path -LiteralPath $required)) {
    throw "Required staging path is missing: $required"
  }
}

$manifestPath = Join-Path $rimeSource 'language_input\gloss\en.manifest.json'
$packPath = Join-Path $rimeSource 'language_input\gloss\en.tsv'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$actualPackHash = (Get-FileHash -LiteralPath $packPath -Algorithm SHA256).Hash
if ($actualPackHash -ne $manifest.output.sha256) {
  throw "GlossPack hash mismatch: expected $($manifest.output.sha256), got $actualPackHash"
}

function Copy-TreeFiles {
  param([string]$Source, [string]$Destination)
  $destinationPrefix = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
  foreach ($file in Get-ChildItem -LiteralPath $Source -File -Recurse) {
    $relative = [IO.Path]::GetRelativePath($Source, $file.FullName)
    $target = [IO.Path]::GetFullPath((Join-Path $Destination $relative))
    if (-not $target.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Staging target escaped output data directory: $target"
    }
    $parent = Split-Path -Parent $target
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
  }
}

Copy-TreeFiles $rimeSource $outputData
Copy-TreeFiles $licenseSource (Join-Path $outputData 'licenses\language-input')
New-Item -ItemType Directory -Path (Join-Path $outputData 'language_input\models') -Force | Out-Null
Copy-Item -LiteralPath $modelCatalogSource `
  -Destination (Join-Path $outputData 'language_input\models\packs-v2.json') -Force
Copy-Item -LiteralPath $readmeSource `
  -Destination (Join-Path $outputRoot 'LANGUAGE-INPUT-README.txt') -Force
Copy-Item -LiteralPath $privacySource `
  -Destination (Join-Path $outputRoot 'LANGUAGE-INPUT-PRIVACY.txt') -Force

$defaultPath = Join-Path $outputData 'default.yaml'
$defaultText = [IO.File]::ReadAllText($defaultPath, $utf8NoBom)
$newline = if ($defaultText.Contains("`r`n")) { "`r`n" } else { "`n" }
$schemaBlock = @(
  'schema_list:'
  '  - schema: language_input_flypy'
  '  - schema: language_input_pinyin'
  ''
) -join $newline
$schemaPattern = [regex]::new('(?ms)^schema_list:\r?\n(?:  - schema: [^\r\n]+\r?\n)+')
if (-not $schemaPattern.IsMatch($defaultText)) {
  throw 'Could not locate schema_list in output/data/default.yaml'
}
$defaultText = $schemaPattern.Replace($defaultText, $schemaBlock, 1)

$pageSizePattern = [regex]::new('(?m)^(  page_size:)\s*\d+\s*$')
if (-not $pageSizePattern.IsMatch($defaultText)) {
  throw 'Could not locate menu/page_size in output/data/default.yaml'
}
$defaultText = $pageSizePattern.Replace($defaultText, '${1} 9', 1)

$desiredSavedOptions = @(
  'language_input_gloss'
  'language_input_ai'
  'language_input_en'
  'language_input_ja'
  'language_input_es'
  'language_input_speech'
)
$saveOptionsPattern = [regex]::new(
  '(?m)^(  save_options:\r?\n)((?:    - [^\r\n]+\r?\n)*)'
)
$saveOptionsMatch = $saveOptionsPattern.Match($defaultText)
if (-not $saveOptionsMatch.Success) {
  throw 'Could not locate switcher/save_options in output/data/default.yaml'
}
$existingSavedOptions = @(
  [regex]::Matches(
    $saveOptionsMatch.Groups[2].Value,
    '(?m)^    - ([^\r\n]+)\s*$'
  ) | ForEach-Object { $_.Groups[1].Value.Trim() }
)
$otherSavedOptions = @(
  $existingSavedOptions | Where-Object { $_ -notin $desiredSavedOptions }
)
$orderedSavedOptions = @($desiredSavedOptions + $otherSavedOptions)
$savedOptionsBlock = '  save_options:' + $newline +
  (($orderedSavedOptions | ForEach-Object { '    - ' + $_ }) -join $newline) +
  $newline
$defaultText = $defaultText.Substring(0, $saveOptionsMatch.Index) +
  $savedOptionsBlock +
  $defaultText.Substring($saveOptionsMatch.Index + $saveOptionsMatch.Length)

[IO.File]::WriteAllText($defaultPath, $defaultText, $utf8NoBom)

$userDatabases = @(Get-ChildItem -LiteralPath $outputData -Recurse -Force |
  Where-Object { $_.Name -like '*.userdb*' })
if ($userDatabases.Count -ne 0) {
  throw "Refusing to stage install data containing user databases: $($userDatabases.FullName -join ', ')"
}

[pscustomobject]@{
  OutputData = $outputData
  GlossEntries = $manifest.entries
  GlossPackSHA256 = $actualPackHash
  DefaultSchemas = 'language_input_flypy, language_input_pinyin'
  PageSize = 9
  UserDatabases = 0
}
