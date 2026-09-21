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
    3. The app-created ``<schema>.custom.yaml`` artifacts
       (``language_input_flypy.custom.yaml`` / ``language_input_pinyin.custom.yaml``):
       only the keys this app wrote are removed (``switches/@N/reset`` from
       ``schema_patch.neutralize_resets`` and ``translator/enable_user_dict``
       from ``rime_settings.set_learning``).  A file that is left without any
       other content is deleted, so learning is not left disabled after
       uninstall.
    4. **Only** the ``*.bak-*`` backups the app actually created under the Rime
       user directory -- i.e. backups of ``user.yaml``, ``weasel.custom.yaml``,
       the two ``<schema>.custom.yaml`` files and the plain-gloss shadow.
       User-owned ``*.bak-*`` files are deliberately left alone.
    5. The **plain-gloss shadow** (``<rime_user_dir>\lua\language_input\
       gloss_filter.lua``) and, when it was present, runs
       ``WeaselDeployer.exe /deploy`` so the shipped badge is restored.

    Deliberately does NOT touch
    ---------------------------
    * Installed model packs (``<rime_user_dir>\language_input\models\``).
    * The downloaded model cache ``ai_cache_v3.json`` (not owned by this app).
    * User-owned ``*.bak-*`` backups (only the app's own backup names match).
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
# The app-owned <schema>.custom.yaml patch files (schema_patch / set_learning).
$AppCustomYamls = @(
    (Join-Path $RimeDir 'language_input_flypy.custom.yaml'),
    (Join-Path $RimeDir 'language_input_pinyin.custom.yaml')
)
# Files the app backs up (only their ``.bak-*`` backups are swept).
$AppBackupTargets = @(
    (Join-Path $RimeDir 'user.yaml'),
    (Join-Path $RimeDir 'weasel.custom.yaml')
) + $AppCustomYamls + @($ShadowPath)
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

function Invoke-RevertAppCustomYaml {
    # Remove ONLY the keys this app wrote into a <schema>.custom.yaml; delete
    # the file when nothing else remains.  Never touches user-added keys.
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-Host "[skip]    app custom yaml not present : $Path"
        return
    }
    $lines = [System.IO.File]::ReadAllLines($Path)
    $kept = New-Object System.Collections.Generic.List[string]
    $removed = 0
    foreach ($line in $lines) {
        if ($line -match '^\s*"?switches/@\d+/reset"?\s*:' -or
            $line -match '^\s*"?translator/enable_user_dict"?\s*:') {
            $removed++
            continue
        }
        $kept.Add($line)
    }
    if ($removed -eq 0) {
        Write-Host "[skip]    no app keys in              : $Path"
        return
    }
    $meaningful = $false
    foreach ($line in $kept) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed -eq 'patch:' -or $trimmed -match '^patch:\s*\{\s*\}\s*$') { continue }
        $meaningful = $true
        break
    }
    if ($DryRun) {
        Write-Host "[dry-run] would remove $removed app key(s) from : $Path"
        if (-not $meaningful) {
            Write-Host "[dry-run] would remove file           : $Path"
        }
        return
    }
    if ($meaningful) {
        $text = ($kept -join "`n")
        if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $text += "`n" }
        [System.IO.File]::WriteAllText($Path, $text, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "[reverted] app keys from              : $Path"
    }
    else {
        Remove-Item -LiteralPath $Path -Force
        Write-Host "[removed] file                       : $Path"
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

# --- 2. app-owned config ----------------------------------------------------
Invoke-RemoveFile -Path $ConfigFile

# --- 3. app-created <schema>.custom.yaml artifacts --------------------------
foreach ($customYaml in $AppCustomYamls) {
    Invoke-RevertAppCustomYaml -Path $customYaml
}

# --- 4. app-created *.bak-* backups (scoped to the app's own files) ----------
$backups = @()
if (Test-Path -LiteralPath $ConfigDir -PathType Container) {
    $backups += Get-ChildItem -LiteralPath $ConfigDir -File -Filter 'config.json.bak-*' -ErrorAction SilentlyContinue
}
foreach ($target in $AppBackupTargets) {
    $dir = Split-Path -Parent $target
    $leaf = Split-Path -Leaf $target
    if (Test-Path -LiteralPath $dir -PathType Container) {
        $backups += Get-ChildItem -LiteralPath $dir -File -Filter "$leaf.bak-*" -ErrorAction SilentlyContinue
    }
}
$backups = $backups | Where-Object { $_ } | Sort-Object FullName -Unique

if (@($backups).Count -eq 0) {
    Write-Host "[skip]    no app *.bak-* backups found"
}
foreach ($backup in @($backups)) {
    Invoke-RemoveFile -Path $backup.FullName
}

# --- 5. plain-gloss shadow + redeploy ---------------------------------------
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

# --- 6. empty config directory ----------------------------------------------
if (-not $DryRun -and (Test-Path -LiteralPath $ConfigDir -PathType Container)) {
    if (-not (Get-ChildItem -LiteralPath $ConfigDir -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $ConfigDir -Force
        Write-Host "[removed] empty directory             : $ConfigDir"
    }
}

# --- 7. optional program files ----------------------------------------------
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
