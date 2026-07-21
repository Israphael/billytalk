# BillyTalk uninstaller - undoes install.ps1 (currentUser, no admin).
#
# Run:  powershell -ExecutionPolicy Bypass -File uninstall.ps1
#
# Removes autostart, the WER exclusion, the shortcut and the program folder.
# Does NOT touch user data (%LOCALAPPDATA%\BillyTalk: history, audio, logs) or
# the Groq key in the Credential Manager - those are the user's to keep or
# clear deliberately.
#
# ASCII only (Windows PowerShell 5.1 reads a BOM-less .ps1 as system ANSI).

$ErrorActionPreference = "SilentlyContinue"

Write-Host "Removing BillyTalk ..." -ForegroundColor Cyan

Get-Process -Name "BillyTalk" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400

# autostart
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "BillyTalk" -ErrorAction SilentlyContinue

# WER exclusion
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\Windows Error Reporting\ExcludedApplications" -Name "BillyTalk.exe" -ErrorAction SilentlyContinue

# shortcut
Remove-Item (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\BillyTalk.lnk") -Force -ErrorAction SilentlyContinue

# program folder
$dest = Join-Path $env:LOCALAPPDATA "Programs\BillyTalk"
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }

Write-Host "Done. BillyTalk removed." -ForegroundColor Green
Write-Host "History, audio, logs (%LOCALAPPDATA%\BillyTalk) and the Groq key were NOT touched -"
Write-Host "clear them by hand if you want them gone."
