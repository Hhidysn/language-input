<#
.SYNOPSIS
    Uninstall helper for the Language Input settings application.

.DESCRIPTION
    Reverses everything the settings app owns for the **current user** only.
    It never needs administrator rights and it is designed to be safe:

    Removes
    -------
    1. The per-user autostart entry
       ``HKCU\Software\Microsoft\Windows\CurrentVersion\Run\LanguageInputSettings``.
    2. The app-owned config ``%APPDATA%\LanguageInput\config.json`` (and any
       ``*.bak-*`` backups in that directory; the directory is removed when it
       becomes empty).
    3. Every ``*.bak-*`` backup the app created under the Rime user directory
       (e.g. ``user.yaml.bak-<stamp>`` and
       ``lua\language_input\gloss_filter.lua.bak-<stamp>``).
    4. The **plain-gloss shadow** (``<rime_user_dir>\lua\language_input\
       gloss_filter.lua``) and, when it was present, runs
       ``WeaselDeployer.exe /deploy`` so the shipped badge is restored.

    Deliberately does NOT touch
    ---------------------------
    * Installed model packs (``<rime_user_dir>\language_input\models\``).
      The recursive ``*.bak-*`` sweep skips that subtree explicitly.
    * The downloaded model cache ``ai_cache_v3.json`` (not owned by this app).
    * The app's own program files.  Only ``-RemoveProgramFiles`` deletes the
      onedir bundle directory (default
      ``<settings-app>\dist\LanguageInputSettings``).

    Safety
    ------
    Pass ``-DryRun`` to print exactly what *would* be removed without changing
    anything.  Every destructive step reports ``[removed]`` / ``[dry-run]``
    lines.

.PARAMETER DryRun
    Preview only.  No file, registry value or deployment change is made.

.PARAMETER RemoveProgramFiles
    Also delete the onedir bundle directory (``-InstallDir``).

.PARAMETER InstallDir
    Directory of the onedir bundle.  Defaults to
    ``<settings-app>\dist\LanguageInputSettings``.

.PARAMETER RimeUserDir
    Override the resolved Rime user directory (otherwise the
    ``HKCU\Software\Rime\Weasel\RimeUserDir`` value or ``%APPDATA%\Rime``).

.EXAMPLE
    .\packaging\uninstall.ps1 -DryRun
.EXAMPLE
    .\packaging\uninstall.ps1
.EXAMPLE
    .\packaging\uninstall.ps1 -RemoveProgramFiles
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$RemoveProgramFiles,
    [string]$InstallDir,
    [string]$RimeUserDir
)

$ErrorActionPreference = 'Stop'

$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$ValueName = 'LanguageInputSettings'
$WeaselKey = 'HKLM:\Software\Rime\Weasel'
$WeaselKeyHkcu = 'HKCU:\Software\Rime\Weasel'
$DefaultWeaselRoot = 'C:\Program Files\Rime\weasel-0.1.0'

$AppDir = Split-Path -Parent $PSScriptRoot

if (-not $InstallDir) {
    $InstallDir = Join-Path $AppDir 'dist\LanguageInputSettings'
}
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)

$AppData = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $env:USERPROFILE 'AppData\Roaming' }
$ConfigDir = Join-Path $AppData 'LanguageInput'
$ConfigFile = Join-Path $ConfigDir 'config.json'

function Resolve-RimeUserDir {
    if ($RimeUserDir) { return [System.IO.Path]::GetFullPath($RimeUserDir) }
    $stored = (Get-ItemProperty -Path $WeaselKeyHkcu -Name 'RimeUserDir' -ErrorAction SilentlyContinue).'RimeUserDir'
    if ($stored) {
        # Stored verbatim by the frozen C++ side (no environment expansion).
        return $stored
    }
    return (Join-Path $AppData 'Rime')
}

function Resolve-DeployerExe {
    $root = (Get-ItemProperty -Path $WeaselKey -Name 'WeaselRoot' -ErrorAction SilentlyContinue).'WeaselRoot'
    if (-not $root) {
        $root = (Get-ItemProperty -Path $WeaselKey -Name 'InstallDir' -ErrorAction SilentlyContinue).'InstallDir'
    }
    if (-not $root) { $root = $DefaultWeaselRoot }
    return (Join-Path $root 'WeaselDeployer.exe')
}

$RimeDir = Resolve-RimeUserDir
$ShadowPath = Join-Path $RimeDir 'lua\language_input\gloss_filter.lua'
$ModelsDir = [System.IO.Path]::GetFullPath((Join-Path $RimeDir 'language_input\models'))
$DeployerExe = Resolve-DeployerExe

$Mode = if ($DryRun) { 'DRY-RUN (no changes)' } else { 'LIVE' }
Write-Host "Language Input settings uninstall [$Mode]" -ForegroundColor Cyan
Write-Host "  Rime user dir : $RimeDir"
Write-Host "  App config    : $ConfigFile"
Write-Host "  Gloss shadow  : $ShadowPath"
Write-Host "  Weasel deploy : $DeployerExe"
Write-Host "  Bundle dir    : $InstallDir (deleted only with -RemoveProgramFiles)"
Write-Host ""

function Invoke-RemoveFile {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        if ($DryRun) {
            Write-Host "[dry-run] would remove file          : $Path"
        }
        else {
            Remove-Item -LiteralPath $Path -Force
            Write-Host "[removed] file                       : $Path"
        }
    }
}

# --- 1. autostart entry -----------------------------------------------------
$existing = Get-ItemProperty -Path $RunKey -Name $ValueName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    if ($DryRun) {
        Write-Host ("[dry-run] would remove registry value : {0}\{1} = {2}" -f $RunKey, $ValueName, $existing.$ValueName)
    }
    else {
        Remove-ItemProperty -Path $RunKey -Name $ValueName -Force
        Write-Host ("[removed] registry value             : {0}\{1}" -f $RunKey, $ValueName)
    }
}
else {
    Write-Host "[skip]    autostart registry value not present"
}

# --- 2. app-owned config (+ its backups) ------------------------------------
Invoke-RemoveFile -Path $ConfigFile

# --- 3. app-created *.bak-* backups -----------------------------------------
$backups = @()
if (Test-Path -LiteralPath $ConfigDir -PathType Container) {
    $backups += Get-ChildItem -LiteralPath $ConfigDir -File -Filter '*.bak-*' -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $RimeDir -PathType Container) {
    $backups += Get-ChildItem -LiteralPath $RimeDir -Recurse -File -Filter '*.bak-*' -ErrorAction SilentlyContinue |
        Where-Object { -not $_.FullName.StartsWith($ModelsDir, [System.StringComparison]::OrdinalIgnoreCase) }
}
$backups = $backups | Where-Object { $_ } | Sort-Object FullName -Unique

if (@($backups).Count -eq 0) {
    Write-Host "[skip]    no *.bak-* backups found"
}
foreach ($backup in @($backups)) {
    Invoke-RemoveFile -Path $backup.FullName
}

# --- 4. plain-gloss shadow + redeploy ---------------------------------------
$shadowPresent = Test-Path -LiteralPath $ShadowPath -PathType Leaf
if ($shadowPresent) {
    if ($DryRun) {
        Write-Host "[dry-run] would remove file           : $ShadowPath"
        Write-Host "[dry-run] would run                   : $DeployerExe /deploy"
    }
    else {
        Remove-Item -LiteralPath $ShadowPath -Force
        Write-Host "[removed] file                        : $ShadowPath"
        if (Test-Path -LiteralPath $DeployerExe -PathType Leaf) {
            Write-Host "[deploy]  running $DeployerExe /deploy ..."
            & $DeployerExe /deploy
            $deployExit = $LASTEXITCODE
            Write-Host "[deploy]  exit code $deployExit (0 does not by itself prove the configuration is valid)"
        }
        else {
            Write-Warning "WeaselDeployer.exe not found; run it manually to restore the badge."
        }
    }
}
else {
    Write-Host "[skip]    plain-gloss shadow not present (badge already reverted)"
}

# --- 5. empty config directory ----------------------------------------------
if (-not $DryRun -and (Test-Path -LiteralPath $ConfigDir -PathType Container)) {
    if (-not (Get-ChildItem -LiteralPath $ConfigDir -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $ConfigDir -Force
        Write-Host "[removed] empty directory             : $ConfigDir"
    }
}

# --- 6. optional program files ----------------------------------------------
if ($RemoveProgramFiles) {
    $safeRoot = [System.IO.Path]::GetPathRoot($InstallDir)
    if ($InstallDir -eq $safeRoot -or $InstallDir.Length -le 3) {
        throw "Refusing to remove suspicious InstallDir: $InstallDir"
    }
    if (Test-Path -LiteralPath $InstallDir -PathType Container) {
        if ($DryRun) {
            Write-Host "[dry-run] would remove directory      : $InstallDir (recursive)"
        }
        else {
            Remove-Item -LiteralPath $InstallDir -Recurse -Force
            Write-Host "[removed] directory                   : $InstallDir (recursive)"
        }
    }
    else {
        Write-Host "[skip]    bundle directory not present: $InstallDir"
    }
}
else {
    Write-Host "[skip]    bundle directory kept (pass -RemoveProgramFiles to delete)"
}

Write-Host ""
if ($DryRun) {
    Write-Host "Dry run complete; nothing was changed." -ForegroundColor Yellow
}
else {
    Write-Host "Uninstall complete. Installed model packs were left untouched." -ForegroundColor Green
}
