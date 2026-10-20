# Creates a Desktop shortcut to the built YTDLManager.exe, so it can be
# launched with a double-click without navigating into the dist folder.
# (The .exe itself is already directly double-clickable from Explorer --
# this script is purely a convenience so you don't have to go find it.)
#
# Usage (from PowerShell, after building with pyinstaller):
#   powershell -ExecutionPolicy Bypass -File packaging\install_windows.ps1

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$exePath = Join-Path $scriptDir "dist\YTDLManager\YTDLManager.exe"

if (-not (Test-Path $exePath)) {
    Write-Error "$exePath not found. Build it first: cd packaging; pyinstaller pyinstaller.spec --noconfirm"
    exit 1
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "YTDLManager.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = Split-Path $exePath
$shortcut.Description = "Social Media Download Manager"
$shortcut.Save()

Write-Host "Created desktop shortcut: $shortcutPath"
Write-Host "Tip: right-click it and choose 'Pin to Start' or 'Pin to taskbar' for one-click access."
