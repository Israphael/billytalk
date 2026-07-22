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

# A StartupApproved entry left by a previous install (or by the user turning
# the app off in Settings > Apps > Startup) would silently veto the value we
# just wrote - the app would be "installed with autostart" and never start.
# Installing is an explicit request, so the stale veto goes.
$approvedKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
if (Test-Path $approvedKey) {
    Remove-ItemProperty -Path $approvedKey -Name "BillyTalk" -ErrorAction SilentlyContinue
}

# --- Windows Error Reporting exclusion (spec section 13) ----------------------
# Without this a full crash dump (audio buffer, transcript, key) could reach
# %LOCALAPPDATA%\CrashDumps and possibly Microsoft.
$werKey = "HKCU:\Software\Microsoft\Windows\Windows Error Reporting\ExcludedApplications"
if (-not (Test-Path $werKey)) { New-Item -Path $werKey -Force | Out-Null }
New-ItemProperty -Path $werKey -Name $exeName -Value 1 -PropertyType DWord -Force | Out-Null
Write-Host "  WER exclusion: $exeName" -ForegroundColor DarkGray

# --- Start-menu shortcut ------------------------------------------------------
# Not decoration: spec section 11 notes that notifications need a Start-menu
# shortcut to exist at all. The icon comes from the exe itself (PyInstaller
# embedded packaging\billytalk.ico), so the shortcut inherits it.
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
# Present on every ordinary profile, absent on a freshly created or redirected
# one - and CreateShortcut().Save() fails with DirectoryNotFoundException
# rather than creating the path itself.
if (-not (Test-Path $startMenu)) { New-Item -ItemType Directory -Force -Path $startMenu | Out-Null }
$lnk = Join-Path $startMenu "BillyTalk.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnk)
$shortcut.TargetPath = $destExe
$shortcut.WorkingDirectory = $dest
$shortcut.IconLocation = "$destExe,0"
$shortcut.Description = "BillyTalk - voice dictation"
$shortcut.Save()
Write-Host "  shortcut: Start Menu \ BillyTalk" -ForegroundColor DarkGray

# --- uninstall entry (Settings > Apps, currentUser hive) ----------------------
# So the app can be removed the way every other program is, instead of by
# knowing that uninstall.ps1 exists.
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\BillyTalk"
$ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$uninstallCmd = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$dest\uninstall.ps1`""
New-Item -Path $uninstallKey -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name "DisplayName" -Value "BillyTalk" -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name "DisplayIcon" -Value $destExe -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name "InstallLocation" -Value $dest -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name "Publisher" -Value "BillyTalk" -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name "NoModify" -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name "NoRepair" -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name "UninstallString" -Value $uninstallCmd -PropertyType String -Force | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot "uninstall.ps1") $dest
Write-Host "  uninstall entry: Settings > Apps > Installed apps" -ForegroundColor DarkGray

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Run now: $destExe"
Write-Host "(or the BillyTalk shortcut in the Start menu; it also auto-starts on login)."
Write-Host "The core starts into the tray - look for the mic icon near the clock."
Write-Host "On the first run it opens the setup wizard: microphone, button, Groq key, live test."
Write-Host "Uninstall: Settings > Apps, or run uninstall.ps1 from $dest"
