[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$PythonExecutable,
  [Parameter(Mandatory = $true)]
  [string]$BuildRoot,
  [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($RepositoryRoot)
$python = [IO.Path]::GetFullPath($PythonExecutable)
$buildRoot = [IO.Path]::GetFullPath($BuildRoot)
$source = [IO.Path]::GetFullPath((Join-Path $root 'scripts\language_input_model_host.py'))

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw "Python executable does not exist: $python"
}
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
  throw "Model-host source does not exist: $source"
}

$versionProbe = @'
import json, platform
import ctranslate2, numpy, sentencepiece, PyInstaller
print(json.dumps({
    "python": platform.python_version(),
    "architecture": platform.machine().lower(),
    "ctranslate2": ctranslate2.__version__,
    "sentencepiece": sentencepiece.__version__,
    "numpy": numpy.__version__,
    "pyinstaller": PyInstaller.__version__,
}, sort_keys=True))
'@
$versions = $versionProbe | & $python - | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
  throw 'Unable to inspect the model-host build environment.'
}
$expected = [ordered]@{
  python = '3.11.15'
  architecture = 'amd64'
  ctranslate2 = '4.8.1'
  sentencepiece = '0.2.1'
  numpy = '2.4.6'
  pyinstaller = '6.15.0'
}
foreach ($name in $expected.Keys) {
  if ([string]$versions.$name -ne [string]$expected.$name) {
    throw "Unexpected $name version '$($versions.$name)'; expected '$($expected.$name)'."
  }
}

$distRoot = Join-Path $buildRoot 'dist'
$workRoot = Join-Path $buildRoot 'work'
$specRoot = Join-Path $buildRoot 'spec'
$pyInstallerCache = Join-Path $buildRoot 'pyinstaller-cache'
New-Item -ItemType Directory -Path $distRoot,$workRoot,$specRoot,$pyInstallerCache -Force | Out-Null
$previousPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = $pyInstallerCache

try {
  & $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name LanguageInputModelHost `
    --distpath $distRoot `
    --workpath $workRoot `
    --specpath $specRoot `
    --collect-binaries ctranslate2 `
    --hidden-import ctranslate2 `
    --hidden-import sentencepiece `
    $source
  if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
  }
} finally {
  $env:PYINSTALLER_CONFIG_DIR = $previousPyInstallerConfig
}

$bundle = Join-Path $distRoot 'LanguageInputModelHost'
$hostExe = Join-Path $bundle 'LanguageInputModelHost.exe'
$internal = Join-Path $bundle '_internal'
if (-not (Test-Path -LiteralPath $hostExe -PathType Leaf) -or
    -not (Test-Path -LiteralPath $internal -PathType Container)) {
  throw 'PyInstaller completed without the expected onedir bundle.'
}
$files = @(Get-ChildItem -LiteralPath $bundle -Recurse -File -Force)
$manifest = [ordered]@{
  format = 'language-input-model-host-build-v1'
  source = [IO.Path]::GetRelativePath($root, $source).Replace('\', '/')
  source_sha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
  environment = $expected
  bundle_bytes = ($files | Measure-Object Length -Sum).Sum
  executable_sha256 = (Get-FileHash -LiteralPath $hostExe -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifestPath = Join-Path $buildRoot 'build-manifest.json'
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM

[pscustomobject]@{
  Bundle = $bundle
  Files = $files.Count
  Bytes = $manifest.bundle_bytes
  HostSHA256 = $manifest.executable_sha256
  Manifest = $manifestPath
}
