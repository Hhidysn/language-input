[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$SettingsBundle,
  [string]$RepositoryRoot
)

$ErrorActionPreference = 'Stop'
if (-not $RepositoryRoot) {
  $RepositoryRoot = Split-Path -Parent $PSScriptRoot
}
$root = [IO.Path]::GetFullPath($RepositoryRoot)
$outputRoot = [IO.Path]::GetFullPath((Join-Path $root 'output'))
$destination = [IO.Path]::GetFullPath((Join-Path $outputRoot 'settings'))
$outputPrefix = $outputRoot.TrimEnd('\') + '\'
$source = [IO.Path]::GetFullPath($SettingsBundle)

if (-not $destination.StartsWith($outputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Settings destination escaped output: $destination"
}
$sourceItem = Get-Item -LiteralPath $source -Force
if (-not $sourceItem.PSIsContainer -or
    ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
  throw 'SettingsBundle must be a real directory.'
}
foreach ($required in @(
  'LanguageInputSettings.exe', '_internal', 'assets', 'config'
)) {
  if (-not (Test-Path -LiteralPath (Join-Path $source $required))) {
    throw "SettingsBundle is missing $required."
  }
}
$linked = @(Get-ChildItem -LiteralPath $source -Recurse -Force | Where-Object {
  ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
})
if ($linked.Count -ne 0) {
  throw "SettingsBundle contains links or reparse points: $($linked.FullName -join ', ')"
}
$files = @(Get-ChildItem -LiteralPath $source -Recurse -File -Force)
if ($files.Count -eq 0 -or @($files | Where-Object Extension -eq '.pdb').Count -ne 0) {
  throw 'SettingsBundle is empty or contains debug symbols.'
}

if (Test-Path -LiteralPath $destination) {
  $existing = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $destination).Path)
  if (-not $existing.StartsWith($outputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Existing settings output escaped output: $existing"
  }
  Remove-Item -LiteralPath $existing -Recurse -Force
}
New-Item -ItemType Directory -Path $destination | Out-Null

$manifestFiles = foreach ($file in $files) {
  $relative = [IO.Path]::GetRelativePath($source, $file.FullName)
  $target = [IO.Path]::GetFullPath((Join-Path $destination $relative))
  if (-not $target.StartsWith($destination.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Settings file escaped destination: $target"
  }
  New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
  Copy-Item -LiteralPath $file.FullName -Destination $target
  [ordered]@{
    path = $relative.Replace('\', '/')
    bytes = $file.Length
    sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  }
}
$manifest = [ordered]@{
  format = 'language-input-settings-bundle-v1'
  architecture = 'x64'
  files = @($manifestFiles)
}
$manifestPath = Join-Path $outputRoot 'settings.manifest.json'
$utf8NoBom = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText(
  $manifestPath,
  ($manifest | ConvertTo-Json -Depth 5) + [Environment]::NewLine,
  $utf8NoBom
)

[pscustomobject]@{
  Destination = $destination
  Files = $files.Count
  Bytes = ($files | Measure-Object Length -Sum).Sum
  ExeSHA256 = (Get-FileHash -LiteralPath (Join-Path $source 'LanguageInputSettings.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
  Manifest = $manifestPath
}
