<#
.SYNOPSIS
    Enable, disable or report the per-user autostart entry for
    LanguageInputSettings.

.DESCRIPTION
    Manages the ``LanguageInputSettings`` value under
    ``HKCU\Software\Microsoft\Windows\CurrentVersion\Run`` (REG_SZ).  This is a
    per-user key, so **no administrator rights are required** and the entry is
    independent of the Weasel/Rime installation.

    The command points at the bundled executable, e.g.::

        "C:\...\settings-app\dist\LanguageInputSettings\LanguageInputSettings.exe" --start-minimized

    The application implements the ``--start-minimized`` switch (it starts
    hidden in the system tray instead of popping the settings window), so the
    autostart entry passes it.  The persisted ``start_minimized`` setting in
    ``%APPDATA%\LanguageInput\config.json`` is honoured as well.

    All three actions are idempotent and print the resulting registry state.

.PARAMETER Enable
    Create or overwrite the autostart entry.

.PARAMETER Disable
    Remove the autostart entry (no-op when already absent).

.PARAMETER Status
    Report whether the entry exists and what it points at.  This is the
    default when no action switch is given.

.PARAMETER ExePath
    Explicit path to ``LanguageInputSettings.exe``.  Defaults to the bundle
    produced by ``build.ps1``:
    ``<settings-app>\dist\LanguageInputSettings\LanguageInputSettings.exe``.

.EXAMPLE
    .\packaging\autostart.ps1 -Status
.EXAMPLE
    .\packaging\autostart.ps1 -Enable
.EXAMPLE
    .\packaging\autostart.ps1 -Disable
#>
[CmdletBinding()]
param(
    [switch]$Enable,
    [switch]$Disable,
    [switch]$Status,
    [string]$ExePath
)

$ErrorActionPreference = 'Stop'

$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$ValueName = 'LanguageInputSettings'

$selected = @($Enable, $Disable, $Status) | Where-Object { $_ }
if ($selected.Count -gt 1) {
    throw "Choose exactly one of -Enable, -Disable or -Status."
}
if ($selected.Count -eq 0) {
    $Status = $true
}

if (-not $ExePath) {
    $appDir = Split-Path -Parent $PSScriptRoot
    $ExePath = Join-Path $appDir 'dist\LanguageInputSettings\LanguageInputSettings.exe'
}
$ExePath = [System.IO.Path]::GetFullPath($ExePath)

function Get-AutostartValue {
    $properties = Get-ItemProperty -Path $RunKey -Name $ValueName -ErrorAction SilentlyContinue
    if ($null -eq $properties) { return $null }
    return $properties.$ValueName
}

function Write-AutostartState {
    $current = Get-AutostartValue
    if ($null -eq $current) {
        Write-Host "Autostart: DISABLED (no '$ValueName' value under $RunKey)"
    }
    else {
        Write-Host "Autostart: ENABLED"
        Write-Host "  Registry : $RunKey"
        Write-Host "  Name     : $ValueName"
        Write-Host "  Value    : $current"
    }
}

switch ($true) {
    $Enable {
        if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
            Write-Warning "Executable not found yet: $ExePath (building it first? run packaging\build.ps1)."
        }
        # REG_SZ command line: quoted exe path + --start-minimized (no window
        # on login; the app starts hidden in the tray).
        $command = '"{0}" --start-minimized' -f $ExePath
        if (-not (Test-Path -LiteralPath $RunKey)) {
            New-Item -Path $RunKey -Force | Out-Null
        }
        Set-ItemProperty -Path $RunKey -Name $ValueName -Value $command -Type String
        Write-Host "Autostart entry written for '$ValueName'."
        Write-AutostartState
    }
    $Disable {
        if ($null -ne (Get-AutostartValue)) {
            Remove-ItemProperty -Path $RunKey -Name $ValueName -Force
            Write-Host "Autostart entry removed for '$ValueName'."
        }
        else {
            Write-Host "Autostart entry for '$ValueName' was already absent (nothing to do)."
        }
        Write-AutostartState
    }
    $Status {
        Write-AutostartState
    }
}
