# BillyTalk installer - currentUser, no admin (spec section 12, 13).
#
# Run:  powershell -ExecutionPolicy Bypass -File install.ps1
#
# Copies the PyInstaller bundle to %LOCALAPPDATA%\Programs\BillyTalk (no version
# in the path), registers autostart of the CORE in HKCU\...\Run, creates a
# Start-menu shortcut, and excludes the exe from Windows Error Reporting so a
# crash dump can never carry audio, transcripts or the key (spec section 13).
#
# ASCII only on purpose: Windows PowerShell 5.1 reads a BOM-less .ps1 in the
# system ANSI codepage, so non-ASCII text would corrupt the script.

$ErrorActionPreference = "Stop"

# --- locate the built bundle (dist\BillyTalk next to the repo root) -----------
$repoRoot = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $repoRoot "dist\BillyTalk"
$exeName = "BillyTalk.exe"
if (-not (Test-Path (Join-Path $bundle $exeName))) {
    Write-Host "Build not found: $bundle\$exeName" -ForegroundColor Red
    Write-Host "Build first: .venv\Scripts\python.exe -m PyInstaller billytalk.spec --noconfirm"
    exit 1
}

# --- destination --------------------------------------------------------------
$dest = Join-Path $env:LOCALAPPDATA "Programs\BillyTalk"
$destExe = Join-Path $dest $exeName

Write-Host "Installing BillyTalk to $dest ..." -ForegroundColor Cyan

# Stop a running instance so files are not locked.
Get-Process -Name "BillyTalk" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Recurse -Force (Join-Path $bundle "*") $dest

# --- autostart of the core (spec section 12: core in HKCU\Run) ----------------
# The Run value is the exe path wrapped in quotes (it has spaces).
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$quotedExe = '"' + $destExe + '"'
New-ItemProperty -Path $runKey -Name "BillyTalk" -Value $quotedExe -PropertyType String -Force | Out-Null
Write-Host "  autostart: HKCU\...\Run\BillyTalk" -ForegroundColor DarkGray

# --- Windows Error Reporting exclusion (spec section 13) ----------------------
# Without this a full crash dump (audio buffer, transcript, key) could reach
# %LOCALAPPDATA%\CrashDumps and possibly Microsoft.
$werKey = "HKCU:\Software\Microsoft\Windows\Windows Error Reporting\ExcludedApplications"
if (-not (Test-Path $werKey)) { New-Item -Path $werKey -Force | Out-Null }
New-ItemProperty -Path $werKey -Name $exeName -Value 1 -PropertyType DWord -Force | Out-Null
Write-Host "  WER exclusion: $exeName" -ForegroundColor DarkGray

# --- Start-menu shortcut ------------------------------------------------------
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$lnk = Join-Path $startMenu "BillyTalk.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnk)
$shortcut.TargetPath = $destExe
$shortcut.WorkingDirectory = $dest
$shortcut.Description = "BillyTalk - voice dictation"
$shortcut.Save()
Write-Host "  shortcut: Start Menu \ BillyTalk" -ForegroundColor DarkGray

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Run now: $destExe"
Write-Host "(or the BillyTalk shortcut in the Start menu; it also auto-starts on login)."
Write-Host "The core starts into the tray - look for the mic icon near the clock."
Write-Host "The Groq key is read from the Credential Manager (BillyTalk/groq-api-key)."
Write-Host "Uninstall: powershell -ExecutionPolicy Bypass -File uninstall.ps1"
