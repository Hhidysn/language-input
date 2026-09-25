[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
  [string]$Version,
  [string]$RepositoryRoot
)

$ErrorActionPreference = 'Stop'
if (-not $RepositoryRoot) {
  $RepositoryRoot = Split-Path -Parent $PSScriptRoot
}
$root = [IO.Path]::GetFullPath($RepositoryRoot)
$output = Join-Path $root 'output'
$archives = Join-Path $output 'archives'
$hostBundle = Join-Path $root 'tools\model-host\dist\LanguageInputModelHost'
$settingsBundle = Join-Path $root 'settings-app\dist\LanguageInputSettings'

# These files must come from the current native build. Do not silently create
# an installer around only the Python bundles or checked-in sample data.
foreach ($relative in @(
  'LICENSE.txt', 'README.txt', 'WeaselServer.exe', 'WeaselDeployer.exe',
  'WeaselSetup.exe', 'rime.dll', 'weaselx64.dll',
  'Win32\WeaselServer.exe', 'Win32\rime.dll',
  'data\default.yaml', 'data\opencc\TSCharacters.ocd2'
)) {
  if (-not (Test-Path -LiteralPath (Join-Path $output $relative) -PathType Leaf)) {
    throw "Native build output is missing $relative. Build the engine before packaging."
  }
}

& (Join-Path $PSScriptRoot 'stage_language_input.ps1') -RepositoryRoot $root
& (Join-Path $PSScriptRoot 'stage_model_host.ps1') -HostBundle $hostBundle -RepositoryRoot $root
& (Join-Path $PSScriptRoot 'stage_settings_app.ps1') -SettingsBundle $settingsBundle -RepositoryRoot $root

$nsis = @(
  (Join-Path ${env:ProgramFiles(x86)} 'NSIS\Bin\makensis.exe'),
  (Join-Path ${env:ProgramFiles(x86)} 'NSIS\makensis.exe')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $nsis) {
  $command = Get-Command makensis.exe -ErrorAction SilentlyContinue
  if (-not $command) { throw 'NSIS makensis.exe is required to package the installer.' }
  $nsis = $command.Source
}
New-Item -ItemType Directory -Path $archives -Force | Out-Null
$script = Join-Path $output 'install.nsi'
& $nsis /DWEASEL_VERSION=0.17.4 /DWEASEL_BUILD=0 "/DPRODUCT_VERSION=$Version" $script
if ($LASTEXITCODE -ne 0) {
  throw "NSIS failed with exit code $LASTEXITCODE."
}

$generated = Join-Path $archives "weasel-$Version-installer.exe"
$releaseName = "LanguageInput-$Version-Windows-x64.exe"
$release = Join-Path $archives $releaseName
if (-not (Test-Path -LiteralPath $generated -PathType Leaf)) {
  throw "NSIS returned success but did not create $generated"
}
if (Test-Path -LiteralPath $release) {
  Remove-Item -LiteralPath $release -Force
}
Move-Item -LiteralPath $generated -Destination $release

$hash = (Get-FileHash -LiteralPath $release -Algorithm SHA256).Hash.ToLowerInvariant()
$utf8NoBom = [Text.UTF8Encoding]::new($false)
$sums = Join-Path $archives 'SHA256SUMS.txt'
[IO.File]::WriteAllText($sums, "$hash  $releaseName`n", $utf8NoBom)
$notes = Join-Path $archives 'RELEASE-NOTES.md'
$body = @"
# Language Input $Version

This Windows installer contains the Weasel input method, the local translation
model host, and the Language Input settings application. It installs them in
one directory and removes them with one uninstaller.

The QuickMT model weights are not included. Open Language Input Settings after
installation to download or import the language packs you want to use.

SHA256: ``$hash``
"@
[IO.File]::WriteAllText($notes, $body.TrimEnd() + "`n", $utf8NoBom)

[pscustomobject]@{
  Installer = $release
  Bytes = (Get-Item -LiteralPath $release).Length
  SHA256 = $hash
  Checksums = $sums
  Notes = $notes
}
