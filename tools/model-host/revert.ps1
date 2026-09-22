# Revert the installed LanguageInputModelHost from the backup created by
# apply.ps1 (default tools\model-host\backup\original).
#
#   * stops WeaselServer.exe (/q)
#   * restores the backed-up exe + _internal from an elevated child
#     (Start-Process -Verb RunAs)
#   * restarts WeaselServer.exe from the non-elevated parent
#
# Idempotent: if the installed exe already matches the backup, it is a no-op.
# -DryRun prints the plan and changes nothing; it never elevates.

[CmdletBinding()]
param(
  [string]$InstallRoot,
  [string]$BackupRoot,
  [switch]$DryRun,
  [switch]$Force,
  [switch]$SkipServerRestart,
  [switch]$Elevated  # internal: set on the elevated child
)

$ErrorActionPreference = 'Stop'

$here = [IO.Path]::GetFullPath($PSScriptRoot)
if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
  $BackupRoot = Join-Path $here 'backup\original'
}

function Get-InstallRoot {
  param([string]$Override)
  if (-not [string]::IsNullOrWhiteSpace($Override)) { return $Override.TrimEnd('\') }
  foreach ($hive in @('HKLM:\SOFTWARE\Rime\Weasel', 'HKLM:\SOFTWARE\WOW6432Node\Rime\Weasel')) {
    try {
      $key = Get-ItemProperty -Path $hive -ErrorAction Stop
      foreach ($name in @('WeaselRoot', 'InstallDir')) {
        if ($key.$name) { return ([string]$key.$name).TrimEnd('\') }
      }
    }
    catch { }
  }
  return 'C:\Program Files\Rime\weasel-0.1.0'
}

function Get-FileSha256 {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

# ---------------------------------------------------------------------------
$InstallRoot = Get-InstallRoot -Override $InstallRoot
$targetExe = Join-Path $InstallRoot 'LanguageInputModelHost.exe'
$targetInternal = Join-Path $InstallRoot '_internal'
$backupExe = Join-Path $BackupRoot 'LanguageInputModelHost.exe'
$backupInternal = Join-Path $BackupRoot '_internal'

if (-not (Test-Path -LiteralPath $backupExe -PathType Leaf)) {
  throw "no backup found at $BackupRoot; nothing to revert to (expected $backupExe)"
}
if (-not (Test-Path -LiteralPath $backupInternal -PathType Container)) {
  throw "backup _internal not found: $backupInternal"
}

$backupHash = Get-FileSha256 $backupExe
$installedHash = Get-FileSha256 $targetExe

$banner = if ($Elevated) { '[elevated]' } else { '[user]' }
Write-Host "$banner install root : $InstallRoot"
Write-Host "$banner backup exe   : $backupExe  sha256=$backupHash"
Write-Host "$banner installed exe: $targetExe  sha256=$installedHash"

if ($installedHash -eq $backupHash -and -not $Force) {
  Write-Host "$banner already reverted: installed exe already matches the backup; nothing to do."
  if ($DryRun) { Write-Host "$banner dry-run: no changes made" }
  return
}

Write-Host "$banner will restore: $backupExe -> $targetExe"
Write-Host "$banner will restore directory: $backupInternal -> $targetInternal"

if ($DryRun -and -not $Elevated) {
  $running = @(Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)
  Write-Host "$banner dry-run: would stop WeaselServer.exe (currently $(if ($running.Count) { "running: pids $($running.Id -join ', ')" } else { 'not running' }))"
  Write-Host "$banner dry-run: would elevate (Start-Process -Verb RunAs) to restore into Program Files"
  Write-Host "$banner dry-run: would restart WeaselServer.exe afterwards"
  Write-Host "$banner dry-run: no changes made"
  return
}

if ($Elevated) {
  Write-Host "[elevated] restoring $targetExe"
  Copy-Item -LiteralPath $backupExe -Destination $targetExe -Force

  Write-Host "[elevated] restoring $targetInternal"
  if (Test-Path -LiteralPath $targetInternal -PathType Container) {
    Remove-Item -LiteralPath $targetInternal -Recurse -Force
  }
  Copy-Item -LiteralPath $backupInternal -Destination $targetInternal -Recurse -Force

  $newHash = Get-FileSha256 $targetExe
  Write-Host "[elevated] installed exe sha256 now $newHash"
  if ($newHash -ne $backupHash) {
    throw "[elevated] verification failed: installed exe sha256 $newHash != $backupHash"
  }
  return
}

$weaselExe = Join-Path $InstallRoot 'WeaselServer.exe'
$serverWasRunning = @(Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue).Count -gt 0

if (-not $SkipServerRestart -and $serverWasRunning) {
  Write-Host '[user] stopping WeaselServer.exe (/q)'
  Start-Process -FilePath $weaselExe -ArgumentList '/q' -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
  $deadline = (Get-Date).AddSeconds(20)
  while ((Get-Date) -lt $deadline -and (Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)) {
    Start-Sleep -Milliseconds 200
  }
  if (Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue) {
    throw 'WeaselServer.exe did not exit within 20s; refusing to replace its host.'
  }
}

try {
  $arguments = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
    '-Elevated', '-InstallRoot', $InstallRoot, '-BackupRoot', $BackupRoot
  )
  if ($Force) { $arguments += '-Force' }
  Write-Host '[user] elevating to restore Program Files (Start-Process -Verb RunAs)'
  $child = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList $arguments
  if ($child.ExitCode -ne 0) {
    throw "elevated restore failed with exit code $($child.ExitCode)"
  }
  Write-Host '[user] elevated restore completed'
}
finally {
  if (-not $SkipServerRestart) {
    if ($serverWasRunning) {
      Write-Host '[user] restarting WeaselServer.exe'
      Start-Process -FilePath $weaselExe -WorkingDirectory $InstallRoot | Out-Null
    }
    else {
      Write-Host '[user] WeaselServer.exe was not running before; leaving it stopped'
    }
  }
}

Write-Host '[user] revert complete'
