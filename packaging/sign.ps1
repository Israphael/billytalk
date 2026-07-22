# BillyTalk code signing - optional, needs a certificate we do not have yet.
#
# Run:  powershell -ExecutionPolicy Bypass -File sign.ps1
#
# Set these first (a .pfx from a CA - DigiCert, Sectigo, SSL.com, ...):
#   $env:BILLYTALK_PFX = "C:\path\to\billytalk.pfx"
#   $env:BILLYTALK_PFX_PASSWORD = "..."
#
# WHY THIS EXISTS UNSIGNED: without a certificate SmartScreen shows
# "Windows protected your PC / Unknown publisher" on the first run, and the
# user has to click "More info" -> "Run anyway". That is the honest state of
# the build, written down rather than discovered. A self-signed certificate
# does NOT help: SmartScreen trusts the CA chain plus reputation, and a
# self-signed one has neither. Even a real certificate only removes the
# warning after the binary earns reputation (or with an EV certificate,
# immediately).
#
# ASCII only (Windows PowerShell 5.1 reads a BOM-less .ps1 as system ANSI).

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $repoRoot "dist\BillyTalk\BillyTalk.exe"

if (-not (Test-Path $target)) {
    Write-Host "Nothing to sign: $target not found." -ForegroundColor Red
    Write-Host "Build first: .venv\Scripts\python.exe -m PyInstaller billytalk.spec --noconfirm"
    exit 1
}

if (-not $env:BILLYTALK_PFX) {
    Write-Host "No certificate configured - the build stays unsigned." -ForegroundColor Yellow
    Write-Host "SmartScreen will warn on first run: More info -> Run anyway."
    Write-Host "Set BILLYTALK_PFX and BILLYTALK_PFX_PASSWORD to sign."
    exit 0
}
if (-not (Test-Path $env:BILLYTALK_PFX)) {
    Write-Host "Certificate not found: $env:BILLYTALK_PFX" -ForegroundColor Red
    exit 1
}

# signtool ships with the Windows SDK; pick the newest one present.
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) {
    Write-Host "signtool.exe not found. Install the Windows SDK (Signing Tools)." -ForegroundColor Red
    exit 1
}

# SHA-256 everywhere, and an RFC3161 timestamp so the signature outlives the
# certificate: without it every binary goes untrusted the day the cert expires.
$args = @(
    "sign", "/fd", "sha256", "/td", "sha256",
    "/tr", "http://timestamp.digicert.com",
    "/f", $env:BILLYTALK_PFX
)
if ($env:BILLYTALK_PFX_PASSWORD) { $args += @("/p", $env:BILLYTALK_PFX_PASSWORD) }
$args += @("/d", "BillyTalk", $target)

Write-Host "Signing $target ..." -ForegroundColor Cyan
& $signtool.FullName @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $signtool.FullName verify /pa /v $target
Write-Host "Signed." -ForegroundColor Green
