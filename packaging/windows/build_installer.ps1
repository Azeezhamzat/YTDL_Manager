# Makes the Windows downloads from the PyInstaller output:
#   packaging\release\YTDLManager-<version>-windows-x64-setup.exe   (installer)
#   packaging\release\YTDLManager-<version>-windows-x64-portable.zip (no install)
#
# Usage (after `cd packaging; pyinstaller pyinstaller.spec --noconfirm`):
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1
#
# Needs Inno Setup 6 (https://jrsoftware.org/isinfo.php); installs it with
# Chocolatey if it isn't already there (as on GitHub's Windows runners).

$ErrorActionPreference = "Stop"

$packaging = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $packaging
$build = Join-Path $packaging "dist\YTDLManager"
$release = Join-Path $packaging "release"
$init = Join-Path $root "src\ytdlmanager\__init__.py"
$version = (Select-String -Path $init -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value

if (-not (Test-Path (Join-Path $build "YTDLManager.exe"))) {
    throw "$build\YTDLManager.exe not found -- run pyinstaller first."
}
New-Item -ItemType Directory -Force -Path $release | Out-Null

$zip = Join-Path $release "YTDLManager-$version-windows-x64-portable.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $build -DestinationPath $zip
Write-Host "Created $zip"

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
$default = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not $iscc -and (Test-Path $default)) { $iscc = $default }
if (-not $iscc) {
    choco install innosetup -y --no-progress | Out-Null
    $iscc = $default
}
& $iscc "/DAppVersion=$version" "/DSourceDir=$build" "/DOutputDir=$release" `
    (Join-Path $PSScriptRoot "ytdlmanager.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }
Write-Host "Created installer in $release"
