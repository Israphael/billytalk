# BillyTalk uninstaller - undoes install.ps1 (currentUser, no admin).
#
# Run:  powershell -ExecutionPolicy Bypass -File uninstall.ps1
#
# Removes autostart (both the Run value and the StartupApproved record, spec
# section 12), the WER exclusion, the shortcut, the uninstall entry and the
# program folder. Does NOT touch user data (%LOCALAPPDATA%\BillyTalk: history,
# audio, logs), the config (%APPDATA%\BillyTalk) or the Groq key in the
# Credential Manager - those are the user's to keep or clear deliberately.
#
# ASCII only (Windows PowerShell 5.1 reads a BOM-less .ps1 as system ANSI).

$ErrorActionPreference = "SilentlyContinue"

Write-Host "Removing BillyTalk ..." -ForegroundColor Cyan

Get-Process -Name "BillyTalk" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400

# autostart: the value AND the Startup-page record it is paired with
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "BillyTalk" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" -Name "BillyTalk" -ErrorAction SilentlyContinue

# WER exclusion
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\Windows Error Reporting\ExcludedApplications" -Name "BillyTalk.exe" -ErrorAction SilentlyContinue

# shortcut
Remove-Item (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\BillyTalk.lnk") -Force -ErrorAction SilentlyContinue

# uninstall entry
Remove-Item -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\BillyTalk" -Recurse -Force -ErrorAction SilentlyContinue

# program folder. The uninstaller may be running FROM this folder (started by
# Settings > Apps), so a plain Remove-Item would fail on the running script:
# copy the removal to a detached shell that waits for us to exit.
$dest = Join-Path $env:LOCALAPPDATA "Programs\BillyTalk"
if (Test-Path $dest) {
    $self = $MyInvocation.MyCommand.Path
    if ($self -and $self.StartsWith($dest, [System.StringComparison]::OrdinalIgnoreCase)) {
        $cmd = "Start-Sleep -Seconds 2; Remove-Item -Recurse -Force '$dest'"
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @("-ExecutionPolicy", "Bypass", "-Command", $cmd)
        Write-Host "  program folder will be removed in a moment" -ForegroundColor DarkGray
    } else {
        Remove-Item -Recurse -Force $dest
    }
}

Write-Host "Done. BillyTalk removed." -ForegroundColor Green
Write-Host "History, audio, logs (%LOCALAPPDATA%\BillyTalk), settings (%APPDATA%\BillyTalk)"
Write-Host "and the Groq key in the Credential Manager were NOT touched - clear them by hand"
Write-Host "if you want them gone. The key: Credential Manager > Windows Credentials >"
Write-Host "BillyTalk/groq-api-key."
