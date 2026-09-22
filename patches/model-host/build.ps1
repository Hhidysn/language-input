# Build the patched LanguageInputModelHost.
#
# Produces an onedir/windowed PyInstaller bundle in
#   patches\model-host\dist\LanguageInputModelHost\
# using a dedicated virtual environment at patches\model-host\.venv
# (never the settings-app venv).
#
# The build mirrors scripts\build_model_host.ps1's pinned versions:
#   Python 3.11, ctranslate2 4.8.1, sentencepiece 0.2.1, numpy 2.4.6,
#   PyInstaller 6.15.0.
#
# This script only ever touches patches\model-host\.  It never writes to the
# frozen engine tree (F:\documents\software\languageInput) or to
# C:\Program Files.  Installing the result is deliberately left to apply.ps1
# (which needs administrator rights).

[CmdletBinding()]
param(
  [string]$EngineRoot = 'F:\documents\software\languageInput',
  [string]$RepositoryRoot,
  [switch]$SkipDependencyInstall,
  [switch]$ForceVenv
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = [Text.UTF8Encoding]::new($false)

# This machine's proxy throttles to ~43 KB/s; direct is ~8 MB/s.  Clear every
# proxy variable for this process before creating the venv or downloading
# packages.
foreach ($name in @(
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
    'http_proxy', 'https_proxy', 'all_proxy')) {
  Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
Write-Host 'proxy env cleared for this process (direct connection)'

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
  $RepositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}
$engineRoot = [IO.Path]::GetFullPath($EngineRoot)
$here = [IO.Path]::GetFullPath($PSScriptRoot)
$venv = Join-Path $here '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$buildRoot = Join-Path $here 'build'
$distRoot = Join-Path $here 'dist'
$workRoot = Join-Path $buildRoot 'work'
$specRoot = Join-Path $buildRoot 'spec'
$pyInstallerCache = Join-Path $buildRoot 'pyinstaller-cache'
$patchedSource = Join-Path $buildRoot 'src\language_input_model_host.py'
$frozenSource = Join-Path $engineRoot 'scripts\language_input_model_host.py'
$patcher = Join-Path $here 'patch_host.py'

$expectedPackages = @(
  'ctranslate2==4.8.1',
  'sentencepiece==0.2.1',
  'numpy==2.4.6',
  'pyinstaller==6.15.0'
)

if (-not (Test-Path -LiteralPath $frozenSource -PathType Leaf)) {
  throw "Frozen model-host source not found: $frozenSource"
}
if (-not (Test-Path -LiteralPath $patcher -PathType Leaf)) {
  throw "Patcher not found: $patcher"
}

function Get-UvPath {
  $command = Get-Command uv -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }
  return $null
}

# --- dedicated venv ---------------------------------------------------------
if ($ForceVenv -and (Test-Path -LiteralPath $venv)) {
  Write-Host "removing existing venv: $venv"
  Remove-Item -LiteralPath $venv -Recurse -Force
}
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
  $uv = Get-UvPath
  if ($uv) {
    Write-Host "creating venv with uv (python 3.11): $venv"
    & $uv venv --python 3.11 $venv
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed with exit code $LASTEXITCODE" }
  }
  else {
    Write-Host "creating venv with py -3.11: $venv"
    & py -3.11 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "py -3.11 -m venv failed with exit code $LASTEXITCODE" }
  }
}
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
  throw "venv python was not created: $venvPython"
}

# --- pinned dependencies ----------------------------------------------------
if (-not $SkipDependencyInstall) {
  $uv = Get-UvPath
  if ($uv) {
    Write-Host "installing pinned dependencies with uv: $($expectedPackages -join ' ')"
    & $uv pip install --python $venvPython @expectedPackages
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed with exit code $LASTEXITCODE" }
  }
  else {
    Write-Host "installing pinned dependencies with pip: $($expectedPackages -join ' ')"
    & $venvPython -m pip install --disable-pip-version-check @expectedPackages
    if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }
  }
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
$versions = $versionProbe | & $venvPython - | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the build environment.' }
Write-Host "build environment: $($versions | ConvertTo-Json -Compress)"

foreach ($pkg in $expectedPackages) {
  $parts = $pkg -split '==', 2
  $name = $parts[0]
  $expectedVersion = $parts[1]
  $key = $name.ToLowerInvariant()
  if ([string]$versions.$key -ne $expectedVersion) {
    throw "Unexpected $name version '$($versions.$key)'; expected '$expectedVersion'."
  }
}

# --- patch ------------------------------------------------------------------
Write-Host "patching frozen source -> $patchedSource"
& $venvPython $patcher --source $frozenSource --output $patchedSource
if ($LASTEXITCODE -ne 0) { throw "patch_host.py failed with exit code $LASTEXITCODE" }

# --- PyInstaller ------------------------------------------------------------
New-Item -ItemType Directory -Path $distRoot, $workRoot, $specRoot, $pyInstallerCache -Force | Out-Null
$previousPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = $pyInstallerCache
try {
  & $venvPython -m PyInstaller `
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
    $patchedSource
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
}
finally {
  $env:PYINSTALLER_CONFIG_DIR = $previousPyInstallerConfig
}

$bundle = Join-Path $distRoot 'LanguageInputModelHost'
$hostExe = Join-Path $bundle 'LanguageInputModelHost.exe'
$internal = Join-Path $bundle '_internal'
if (-not (Test-Path -LiteralPath $hostExe -PathType Leaf) -or
    -not (Test-Path -LiteralPath $internal -PathType Container)) {
  throw 'PyInstaller completed without the expected onedir bundle.'
}

function Get-RelativePathText {
  param([string]$BasePath, [string]$TargetPath)
  $baseUri = [Uri]::new(([IO.Path]::GetFullPath($BasePath).TrimEnd('\') + '\'))
  $targetUri = [Uri]::new([IO.Path]::GetFullPath($TargetPath))
  return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString()).Replace('/', '\')
}

$files = @(Get-ChildItem -LiteralPath $bundle -Recurse -File -Force)
$manifest = [ordered]@{
  format                 = 'language-input-model-host-patched-build-v1'
  frozen_source          = (Get-RelativePathText $RepositoryRoot $frozenSource).Replace('\', '/')
  frozen_source_sha256   = (Get-FileHash -LiteralPath $frozenSource -Algorithm SHA256).Hash.ToLowerInvariant()
  patched_source_sha256  = (Get-FileHash -LiteralPath $patchedSource -Algorithm SHA256).Hash.ToLowerInvariant()
  environment            = $versions
  bundle_bytes           = ($files | Measure-Object Length -Sum).Sum
  bundle_files           = $files.Count
  executable_sha256      = (Get-FileHash -LiteralPath $hostExe -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifestPath = Join-Path $buildRoot 'build-manifest.json'
[IO.File]::WriteAllText(
  $manifestPath,
  ($manifest | ConvertTo-Json -Depth 5) + "`n",
  $utf8NoBom)

[pscustomobject]@{
  Bundle         = $bundle
  Files          = $files.Count
  Bytes          = $manifest.bundle_bytes
  HostSHA256     = $manifest.executable_sha256
  Manifest       = $manifestPath
} | Format-List
