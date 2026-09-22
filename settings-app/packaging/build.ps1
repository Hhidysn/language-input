<#
.SYNOPSIS
    Build the LanguageInputSettings onedir bundle with PyInstaller.

.DESCRIPTION
    Builds ``packaging\language_input_settings.spec`` with the project's own
    virtual environment into ``settings-app\dist\LanguageInputSettings\``.

    * PyInstaller is installed on demand via pip.  The machine's HTTP proxy
      (``HTTP(S)_PROXY`` / ``ALL_PROXY`` = http://127.0.0.1:7898) throttles
      PyPI to ~43 KB/s, so those variables are cleared **for this process
      only** before pip runs, which makes pip go direct (~8 MB/s).
    * The spec is a ``--onedir --windowed`` build, so no console window appears
      on the target machine and no Python installation is required there.
    * UPX is disabled (Qt DLLs must never be UPX-packed).

    The application resolves ``assets\`` and ``config\`` relative to the
    bundle *root* (see the spec header); PyInstaller's COLLECT cannot write
    DATA files outside ``_internal``, so those two directories are mirrored to
    the bundle root after the build.

.PARAMETER Clean
    Delete ``build\pyinstaller`` and ``dist\LanguageInputSettings`` before
    building (PyInstaller is also invoked with ``--clean``).

.EXAMPLE
    .\packaging\build.ps1
#>
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Split-Path -Parent $ScriptDir
$Python = Join-Path $AppDir '.venv\Scripts\python.exe'
$Spec = Join-Path $ScriptDir 'language_input_settings.spec'
$DistPath = Join-Path $AppDir 'dist'
$BundleDir = Join-Path $DistPath 'LanguageInputSettings'
$ExePath = Join-Path $BundleDir 'LanguageInputSettings.exe'
$WorkPath = Join-Path $AppDir 'build\pyinstaller'

function Clear-ProxyEnv {
    foreach ($name in @(
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy'
        )) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}

# Keep pip/PyInstaller off the throttling proxy for this process.
Clear-ProxyEnv

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Virtual-environment Python not found: $Python"
}
if (-not (Test-Path -LiteralPath $Spec -PathType Leaf)) {
    throw "PyInstaller spec not found: $Spec"
}

# --- ensure PyInstaller -----------------------------------------------------
$pyinstallerOk = $false
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -eq 0) {
    $pyinstallerOk = $true
}

if (-not $pyinstallerOk) {
    Write-Host "PyInstaller not found in the venv; installing it (proxies cleared)..." -ForegroundColor Yellow
    # PowerShell 5.1 turns native stderr into ErrorRecords, and with
    # $ErrorActionPreference='Stop' that would abort the script; merge the
    # streams so pip's progress/warnings cannot do that.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Python -m pip install --upgrade PyInstaller 2>&1 | ForEach-Object { Write-Host $_ }
    $pipExit = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($pipExit -ne 0) { throw "pip install PyInstaller failed (exit $pipExit)." }
}

$piVersion = (& $Python -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
Write-Host "Using PyInstaller $piVersion" -ForegroundColor Cyan

# --- optional clean ---------------------------------------------------------
if ($Clean) {
    Write-Host "Cleaning work path $WorkPath and bundle $BundleDir ..." -ForegroundColor DarkGray
    foreach ($target in @($WorkPath, $BundleDir)) {
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }
}

# --- build ------------------------------------------------------------------
Push-Location $AppDir
try {
    Write-Host "Building onedir bundle (windowed, no UPX)..." -ForegroundColor Cyan
    # See the pip note above: PyInstaller logs to stderr, so merge the streams.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $DistPath `
        --workpath $WorkPath `
        $Spec 2>&1 | ForEach-Object { Write-Host $_ }
    $buildExit = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($buildExit -ne 0) { throw "PyInstaller failed (exit $buildExit)." }
}
finally {
    Pop-Location
}

# --- mirror runtime assets to the bundle root -------------------------------
# app.py: assets_dir()/config_dir() == Path(bundle_root) in the frozen layout.
foreach ($item in @(
        @{ Source = (Join-Path $AppDir 'assets'); Dest = (Join-Path $BundleDir 'assets') },
        @{ Source = (Join-Path $AppDir 'config'); Dest = (Join-Path $BundleDir 'config') }
    )) {
    if (Test-Path -LiteralPath $item.Source) {
        New-Item -ItemType Directory -Path $item.Dest -Force | Out-Null
        Copy-Item -Path (Join-Path $item.Source '*') -Destination $item.Dest -Recurse -Force
    }
}

# --- report -----------------------------------------------------------------
if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "Build reported success but the executable is missing: $ExePath"
}

$exeInfo = Get-Item -LiteralPath $ExePath
$allFiles = Get-ChildItem -LiteralPath $BundleDir -Recurse -File
$totalBytes = ($allFiles | Measure-Object -Property Length -Sum).Sum

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host ("  Output : {0}" -f $BundleDir)
Write-Host ("  Exe    : {0}" -f $ExePath)
Write-Host ("  Exe size       : {0:N0} bytes ({1:N2} MB)" -f $exeInfo.Length, ($exeInfo.Length / 1MB))
Write-Host ("  File count     : {0}" -f $allFiles.Count)
Write-Host ("  Bundle total   : {0:N0} bytes ({1:N2} MB)" -f $totalBytes, ($totalBytes / 1MB))
