# Apply the freshly built WeaselServer.exe over the installed one.
#
#   * backs up the installed exe (once, to tools\engine\backup\original)
#   * swaps the binary by RENAME + COPY, so it does NOT require the running
#     server to exit first
#   * performs the Program Files write from an elevated child
#     (Start-Process -Verb RunAs) -- the ONLY step that needs admin
#   * stops + restarts WeaselServer.exe from the non-elevated parent afterwards
#     so the new binary is the one actually loaded
#
# Why rename instead of "stop then replace":
#   `WeaselServer.exe /q` shuts the current instance down but TSF immediately
#   respawns it, so a "wait until the process is gone" loop can never succeed
#   (observed: pid 27132 -> 7532 within 2s).  Windows allows renaming a running
#   .exe, so we rename the live image out of the way and put the new file in its
#   place; the next start picks up the new binary.
#
# Why: the repo's RimeWithWeasel/RimeWithWeasel.cpp now appends the AI gloss as
# `remote->text` instead of `remote->MarkedComment()`, which removes the
# hardcoded "〔<lang>·AI〕 " marker from the candidate window without breaking
# the speech feature (the speech path builds its own marked comment from the
# lookup result).  Only WeaselServer.exe contains that code path, so only that
# binary needs replacing.
#
# NOTE: restarting WeaselServer.exe with no explicit environment resets the
# LANGUAGE_INPUT_REMOTE_* backend env that the settings app injects.  Re-apply
# the desired backend from the settings app afterwards if you had one set.

[CmdletBinding()]
param(
  [string]$InstallRoot,
  [string]$SourceExe,
  [string]$BackupRoot,
  [switch]$DryRun,
  [switch]$Force,
  [switch]$SkipServerRestart,
  [string]$LogPath,  # internal: transcript path written by the elevated child
  [switch]$Elevated  # internal: set on the elevated child
)

$ErrorActionPreference = 'Stop'

$here = [IO.Path]::GetFullPath($PSScriptRoot)
$repo = [IO.Path]::GetFullPath((Join-Path $here '..\..'))

if ([string]::IsNullOrWhiteSpace($SourceExe)) {
  $SourceExe = Join-Path $repo 'output\WeaselServer.exe'
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

# ---------------------------------------------------------------------------
$InstallRoot = Get-InstallRoot -Override $InstallRoot
$targetExe = Join-Path $InstallRoot 'WeaselServer.exe'
$sourceExe = $SourceExe

if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
  throw "built WeaselServer.exe not found: $sourceExe (build it first: msbuild weasel.sln /p:Configuration=Release /p:Platform=x64)"
}

$sourceHash = Get-FileSha256 $sourceExe
$installedHash = Get-FileSha256 $targetExe
$backupExe = Join-Path $BackupRoot 'WeaselServer.exe'

$banner = if ($Elevated) { '[elevated]' } else { '[user]' }
Write-Host "$banner install root : $InstallRoot"
Write-Host "$banner built exe    : $sourceExe  sha256=$sourceHash"
Write-Host "$banner installed exe: $targetExe  sha256=$installedHash"
Write-Host "$banner backup root  : $BackupRoot  (existing=$(Test-Path -LiteralPath $BackupRoot))"

if ($installedHash -eq $sourceHash -and -not $Force) {
  Write-Host "$banner already applied: installed exe already matches the built one."
  if ($DryRun) { Write-Host "$banner dry-run: no changes made" }
  return
}

if (-not (Test-Path -LiteralPath $targetExe -PathType Leaf)) {
  throw "installed WeaselServer.exe not found: $targetExe"
}
Write-Host "$banner will copy: $sourceExe -> $targetExe"
Write-Host "$banner the live image is renamed aside first, so the server does not have to exit"

if ($DryRun -and -not $Elevated) {
  $running = @(Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)
  Write-Host "$banner dry-run: WeaselServer.exe currently $(if ($running.Count) { "running: pids $($running.Id -join ', ')" } else { 'not running' })"
  Write-Host "$banner dry-run: would elevate (Start-Process -Verb RunAs) to perform the Program Files write"
  Write-Host "$banner dry-run: would then stop (force, if needed) and restart WeaselServer.exe"
  Write-Host "$banner dry-run: no changes made"
  return
}

if ($Elevated) {
  # We are the elevated child: only touch Program Files, never the server.
  if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
    try { Start-Transcript -Path $LogPath -Force | Out-Null } catch { }
  }
  try {
    if (-not (Test-Path -LiteralPath $backupExe -PathType Leaf)) {
      Write-Host "[elevated] creating backup directory $BackupRoot"
      New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
      Write-Host "[elevated] copying $targetExe -> $backupExe"
      Copy-Item -LiteralPath $targetExe -Destination $backupExe -Force
      $manifest = [ordered]@{
        format            = 'weasel-server-backup-v1'
        created_utc       = (Get-Date).ToUniversalTime().ToString('o')
        install_root      = $InstallRoot
        executable_sha256 = (Get-FileSha256 $backupExe)
        executable_bytes  = (Get-Item -LiteralPath $backupExe).Length
        replaced_with     = $sourceHash
      }
      [IO.File]::WriteAllText(
        (Join-Path $BackupRoot 'backup-manifest.json'),
        ($manifest | ConvertTo-Json -Depth 5) + "`n",
        [Text.UTF8Encoding]::new($false))
    }
    else {
      Write-Host "[elevated] backup already present; keeping $BackupRoot"
    }

    $asides = Join-Path $InstallRoot ('WeaselServer.exe.aside-' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    Write-Host "[elevated] renaming the live image: $targetExe -> $asides"
    Move-Item -LiteralPath $targetExe -Destination $asides -Force

    Write-Host "[elevated] writing $targetExe"
    Copy-Item -LiteralPath $sourceExe -Destination $targetExe -Force

    $newHash = Get-FileSha256 $targetExe
    Write-Host "[elevated] installed exe sha256 now $newHash"
    if ($newHash -ne $sourceHash) {
      throw "[elevated] verification failed: installed exe sha256 $newHash != $sourceHash"
    }
    Write-Host "[elevated] old image kept at $asides (delete it after the next successful restart)"
  }
  finally {
    if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
      try { Stop-Transcript | Out-Null } catch { }
    }
  }
  return
}

# Non-elevated parent: elevate for the swap, then restart the server.
try {
  # Start-Process joins an -ArgumentList ARRAY with spaces and does NOT quote
  # the elements, which splits any path containing a space.  Build one
  # explicitly quoted argument string instead.
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
    '-SourceExe', (& $quote $SourceExe),
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
    $weaselExe = Join-Path $InstallRoot 'WeaselServer.exe'
    # The server self-respawns on /q, so a "wait until gone" loop cannot work;
    # force-stop whatever is running, then start the new binary explicitly.
    $running = @(Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
      Write-Host "[user] stopping WeaselServer.exe (force): pids $($running.Id -join ', ')"
      $running | Stop-Process -Force -ErrorAction SilentlyContinue
      $deadline = (Get-Date).AddSeconds(15)
      while ((Get-Date) -lt $deadline -and (Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)) {
        Start-Sleep -Milliseconds 200
      }
    }
    Write-Host '[user] starting WeaselServer.exe (loads the new binary)'
    Start-Process -FilePath $weaselExe -WorkingDirectory $InstallRoot -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 2
    $now = @(Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)
    Write-Host "[user] WeaselServer.exe now $(if ($now.Count) { "running: pids $($now.Id -join ', ')" } else { 'NOT running' })"
  }
}

Write-Host '[user] apply complete'
