[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$HostBundle,
  [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($RepositoryRoot)
$outputRoot = [IO.Path]::GetFullPath((Join-Path $root 'output'))
$source = [IO.Path]::GetFullPath($HostBundle)
$destination = [IO.Path]::GetFullPath((Join-Path $outputRoot 'model-host'))
$outputPrefix = $outputRoot.TrimEnd('\') + '\'

if (-not $destination.StartsWith($outputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Model-host destination escaped output: $destination"
}
$sourceItem = Get-Item -LiteralPath $source -Force
if (-not $sourceItem.PSIsContainer -or
    ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
  throw 'HostBundle must be a real directory, not a link or reparse point.'
}
$hostExe = Join-Path $source 'LanguageInputModelHost.exe'
$internal = Join-Path $source '_internal'
if (-not (Test-Path -LiteralPath $hostExe -PathType Leaf) -or
    -not (Test-Path -LiteralPath $internal -PathType Container)) {
  throw 'HostBundle is missing LanguageInputModelHost.exe or _internal.'
}
$linked = @(Get-ChildItem -LiteralPath $source -Recurse -Force | Where-Object {
  ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
})
if ($linked.Count -ne 0) {
  throw "HostBundle contains links or reparse points: $($linked.FullName -join ', ')"
}
$files = @(Get-ChildItem -LiteralPath $source -Recurse -File -Force)
if ($files.Count -eq 0 -or @($files | Where-Object Extension -eq '.pdb').Count -ne 0) {
  throw 'HostBundle is empty or contains debug symbols.'
}

if (Test-Path -LiteralPath $destination) {
  $resolvedDestination = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $destination))
  if (-not $resolvedDestination.StartsWith($outputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Existing model-host output escaped output: $resolvedDestination"
  }
  Remove-Item -LiteralPath $resolvedDestination -Recurse -Force
}
New-Item -ItemType Directory -Path $destination | Out-Null

$manifestFiles = foreach ($file in $files) {
  $relative = [IO.Path]::GetRelativePath($source, $file.FullName)
  $target = [IO.Path]::GetFullPath((Join-Path $destination $relative))
  if (-not $target.StartsWith($destination.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Host staging target escaped destination: $target"
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
  format = 'language-input-model-host-bundle-v1'
  architecture = 'x64'
  ctranslate2 = '4.8.1'
  sentencepiece = '0.2.1'
  files = @($manifestFiles)
}
$manifestPath = Join-Path $outputRoot 'model-host.manifest.json'
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM

[pscustomobject]@{
  Destination = $destination
  Files = $files.Count
  Bytes = ($files | Measure-Object Length -Sum).Sum
  HostSHA256 = (Get-FileHash -LiteralPath $hostExe -Algorithm SHA256).Hash.ToLowerInvariant()
  Manifest = $manifestPath
}
