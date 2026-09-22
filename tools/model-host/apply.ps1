# Apply the patched LanguageInputModelHost over the installed one.
#
#   * backs up the installed exe + _internal (once, to tools\model-host\backup\original)
#   * stops WeaselServer.exe (/q) before touching Program Files
#   * performs the Program Files write from an elevated child
#     (Start-Process -Verb RunAs) -- this is the ONLY step that needs admin
#   * restarts WeaselServer.exe from the non-elevated parent afterwards
#
# Idempotent: running it twice is a no-op once the installed exe already
# matches the patched bundle (use -Force to re-copy anyway).
#
# -DryRun prints the plan and changes nothing; it never elevates.
#
# NOTE: restarting WeaselServer.exe with no explicit environment resets the
# LANGUAGE_INPUT_REMOTE_* backend env that the settings app injects.  Re-apply
# the desired backend from the settings app afterwards if you had one set.

[CmdletBinding()]
param(
  [string]$InstallRoot,
  [string]$SourceBundle,
  [string]$BackupRoot,
  [switch]$DryRun,
  [switch]$Force,
  [switch]$SkipServerRestart,
  [string]$LogPath,  # internal: transcript path written by the elevated child
  [switch]$Elevated  # internal: set on the elevated child
)

$ErrorActionPreference = 'Stop'

$here = [IO.Path]::GetFullPath($PSScriptRoot)
if ([string]::IsNullOrWhiteSpace($SourceBundle)) {
  $SourceBundle = Join-Path $here 'dist\LanguageInputModelHost'
}
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

function Write-Manifest {
  param([string]$Path, [string]$InstallRoot, [string]$SourceBundle)
  $exe = Join-Path $InstallRoot 'LanguageInputModelHost.exe'
  $manifest = [ordered]@{
    format                     = 'language-input-model-host-backup-v1'
    created_utc                = (Get-Date).ToUniversalTime().ToString('o')
    install_root               = $InstallRoot
    executable_sha256          = (Get-FileSha256 $exe)
    executable_bytes           = (Get-Item -LiteralPath $exe).Length
    internal_present           = (Test-Path -LiteralPath (Join-Path $InstallRoot '_internal') -PathType Container)
    backup_of_source_bundle    = $SourceBundle
  }
  [IO.File]::WriteAllText(
    $Path,
    ($manifest | ConvertTo-Json -Depth 5) + "`n",
    [Text.UTF8Encoding]::new($false))
}

# ---------------------------------------------------------------------------
$InstallRoot = Get-InstallRoot -Override $InstallRoot
$targetExe = Join-Path $InstallRoot 'LanguageInputModelHost.exe'
$targetInternal = Join-Path $InstallRoot '_internal'
$sourceExe = Join-Path $SourceBundle 'LanguageInputModelHost.exe'
$sourceInternal = Join-Path $SourceBundle '_internal'

if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
  throw "patched host not found: $sourceExe (build it first with build.ps1)"
}
if (-not (Test-Path -LiteralPath $sourceInternal -PathType Container)) {
  throw "patched bundle _internal not found: $sourceInternal"
}

$sourceHash = Get-FileSha256 $sourceExe
$installedHash = Get-FileSha256 $targetExe
$backupExe = Join-Path $BackupRoot 'LanguageInputModelHost.exe'
$backupHash = Get-FileSha256 $backupExe

$banner = if ($Elevated) { '[elevated]' } else { '[user]' }
Write-Host "$banner install root : $InstallRoot"
Write-Host "$banner patched exe  : $sourceExe  sha256=$sourceHash"
Write-Host "$banner installed exe: $targetExe  sha256=$installedHash"
$backupExists = Test-Path -LiteralPath $BackupRoot
Write-Host "$banner backup root  : $BackupRoot  (existing=$backupExists)"

if ($installedHash -eq $sourceHash -and -not $Force) {
  Write-Host "$banner already applied: installed exe already matches the patched bundle; nothing to do."
  if (-not $SkipServerRestart -and -not $DryRun -and -not $Elevated) {
    Write-Host "$banner restarting WeaselServer.exe so it picks up the host (no file change)"
    $exe = Join-Path $InstallRoot 'WeaselServer.exe'
    $running = @(Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
      Write-Host "$banner   stopping WeaselServer.exe (/q): pids $($running.Id -join ', ')"
      if (-not $DryRun) {
        Start-Process -FilePath $exe -ArgumentList '/q' -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $deadline -and (Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)) {
          Start-Sleep -Milliseconds 200
        }
      }
      Write-Host "$banner   starting WeaselServer.exe"
      if (-not $DryRun) { Start-Process -FilePath $exe -WorkingDirectory $InstallRoot | Out-Null }
    }
    else {
      Write-Host "$banner   WeaselServer.exe was not running; nothing to restart"
    }
  }
  if ($DryRun) { Write-Host "$banner dry-run: no changes made" }
  return
}

if (-not (Test-Path -LiteralPath $targetExe -PathType Leaf)) {
  throw "installed host not found: $targetExe"
}
if (-not (Test-Path -LiteralPath $backupExe -PathType Leaf)) {
  Write-Host "$banner will create backup of the CURRENT host at $BackupRoot"
}
else {
  Write-Host "$banner backup already exists at $BackupRoot (keeping it as the pre-patch original)"
}

Write-Host "$banner will copy: $sourceExe -> $targetExe"
Write-Host "$banner will replace directory: $sourceInternal -> $targetInternal"

if ($DryRun -and -not $Elevated) {
  $running = @(Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)
  Write-Host "$banner dry-run: would stop WeaselServer.exe (currently $(if ($running.Count) { "running: pids $($running.Id -join ', ')" } else { 'not running' }))"
  Write-Host "$banner dry-run: would elevate (Start-Process -Verb RunAs) to perform the Program Files write"
  Write-Host "$banner dry-run: would restart WeaselServer.exe afterwards"
  Write-Host "$banner dry-run: no changes made"
  return
}

if ($Elevated) {
  # We are the elevated child: only touch Program Files, never the server.
  # Start-Process -Verb RunAs cannot redirect stdout, so record a transcript;
  # the parent prints it when this child fails, otherwise a failure is just
  # "exit code 1" with no explanation.
  if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
    try { Start-Transcript -Path $LogPath -Force | Out-Null } catch { }
  }
  try {
  if (-not (Test-Path -LiteralPath $backupExe -PathType Leaf)) {
    Write-Host "[elevated] creating backup directory $BackupRoot"
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    Write-Host "[elevated] copying $targetExe -> $backupExe"
    Copy-Item -LiteralPath $targetExe -Destination $backupExe -Force
    if (Test-Path -LiteralPath $targetInternal -PathType Container) {
      Write-Host "[elevated] copying $targetInternal -> $(Join-Path $BackupRoot '_internal')"
      Copy-Item -LiteralPath $targetInternal -Destination (Join-Path $BackupRoot '_internal') -Recurse -Force
    }
    Write-Manifest -Path (Join-Path $BackupRoot 'backup-manifest.json') -InstallRoot $InstallRoot -SourceBundle $SourceBundle
  }
  else {
    Write-Host "[elevated] backup already present; not overwriting $BackupRoot"
  }

  Write-Host "[elevated] writing $targetExe"
  Copy-Item -LiteralPath $sourceExe -Destination $targetExe -Force

  Write-Host "[elevated] replacing $targetInternal"
  if (Test-Path -LiteralPath $targetInternal -PathType Container) {
    Remove-Item -LiteralPath $targetInternal -Recurse -Force
  }
  Copy-Item -LiteralPath $sourceInternal -Destination $targetInternal -Recurse -Force

  $newHash = Get-FileSha256 $targetExe
  Write-Host "[elevated] installed exe sha256 now $newHash"
  if ($newHash -ne $sourceHash) {
    throw "[elevated] verification failed: installed exe sha256 $newHash != $sourceHash"
  }
  }
  finally {
    if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
      try { Stop-Transcript | Out-Null } catch { }
    }
  }
  return
}

# Non-elevated parent: stop server -> elevate -> start server.
$weaselExe = Join-Path $InstallRoot 'WeaselServer.exe'
$serverWasRunning = @(Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue).Count -gt 0

if (-not $SkipServerRestart -and $serverWasRunning) {
  Write-Host "[user] stopping WeaselServer.exe (/q)"
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
  # BUGFIX: Start-Process joins an -ArgumentList ARRAY with spaces and does NOT
  # quote the elements, so any value containing a space (e.g.
  # "C:\Program Files\Rime\weasel-0.1.0") got split into two argv entries and
  # the elevated child aborted with exit code 1 before writing anything.
  # Build one explicitly quoted argument string instead.
  $quote = { param([string]$v) '"' + $v + '"' }
  $elevatedLog = Join-Path $here 'apply-elevated.log'
  if (Test-Path -LiteralPath $elevatedLog) {
    Remove-Item -LiteralPath $elevatedLog -Force -ErrorAction SilentlyContinue
  }
  $parts = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', (& $quote $PSCommandPath),
    '-Elevated',
    '-InstallRoot', (& $quote $InstallRoot),
    '-SourceBundle', (& $quote $SourceBundle),
    '-BackupRoot', (& $quote $BackupRoot),
    '-LogPath', (& $quote $elevatedLog)
  )
  if ($Force) { $parts += '-Force' }
  $argumentLine = $parts -join ' '
  Write-Host '[user] elevating to write Program Files (Start-Process -Verb RunAs)'
  Write-Host "[user] elevated child log: $elevatedLog"
  $child = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList $argumentLine
  if ($child.ExitCode -ne 0) {
    $childOutput = if (Test-Path -LiteralPath $elevatedLog) {
      (Get-Content -LiteralPath $elevatedLog -Raw)
    }
    else { '(the elevated child produced no log)' }
    throw "elevated copy failed with exit code $($child.ExitCode)`n----- elevated child output -----`n$childOutput`n--------------------------------"
  }
  Write-Host '[user] elevated copy completed'
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

Write-Host '[user] apply complete'
