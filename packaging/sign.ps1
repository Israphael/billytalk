# BillyTalk code signing - optional, needs a certificate we do not have yet.
#
# Run:  powershell -ExecutionPolicy Bypass -File sign.ps1
#
# Point it at a .pfx from a CA (DigiCert, Sectigo, SSL.com, ...):
#   $env:BILLYTALK_PFX = "C:\path\to\billytalk.pfx"
# The password is asked for interactively and never leaves this process.
#
# WHY NOT signtool /p <password>: a command line is public. Any process
# running as this user - and WMI's Win32_Process for anyone else on the box -
# can read the arguments of a running program, so the password of the code
# signing key would be readable for the whole duration of signing and
# timestamping. Together with the .pfx sitting on disk that is the private
# signing key, i.e. the ability to sign malware as BillyTalk. Set-Authenticode-
# Signature keeps the certificate in this process, as a SecureString, and
# starts no child process at all.
#
# WHY THIS EXISTS UNSIGNED: without a certificate SmartScreen shows
# "Windows protected your PC / Unknown publisher" on the first run, and the
# user has to click "More info" -> "Run anyway". That is the honest state of
# the build, written down rather than discovered. A self-signed certificate
# does NOT help: SmartScreen trusts the CA chain plus reputation, and a
# self-signed one has neither. Even a real certificate only removes the
# warning after the binary earns reputation (or immediately, with EV).
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
    Write-Host "Set BILLYTALK_PFX to the .pfx path to sign."
    exit 0
}
if (-not (Test-Path $env:BILLYTALK_PFX)) {
    Write-Host "Certificate not found: $env:BILLYTALK_PFX" -ForegroundColor Red
    exit 1
}

# Read-Host -AsSecureString: the password exists as a SecureString and as the
# certificate object, never as a process argument and never in the console
# history.
$password = Read-Host -Prompt "Password for $($env:BILLYTALK_PFX)" -AsSecureString
try {
    $certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(
        $env:BILLYTALK_PFX, $password,
        [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
    )
} catch {
    Write-Host "Could not open the certificate (wrong password, or not a .pfx)." -ForegroundColor Red
    exit 1
}

Write-Host "Signing $target ..." -ForegroundColor Cyan
# SHA-256 everywhere, and an RFC3161 timestamp so the signature outlives the
# certificate: without one every binary goes untrusted the day the cert
# expires. http:// for the TSA is correct - the response is signed and
# verified cryptographically, the transport does not matter.
$result = Set-AuthenticodeSignature -FilePath $target -Certificate $certificate `
    -HashAlgorithm SHA256 `
    -TimestampServer "http://timestamp.digicert.com"

if ($result.Status -ne "Valid") {
    Write-Host "Signing failed: $($result.Status) - $($result.StatusMessage)" -ForegroundColor Red
    exit 1
}
Write-Host "Signed: $($result.SignerCertificate.Subject)" -ForegroundColor Green
Write-Host "Verify: Get-AuthenticodeSignature '$target' | Format-List"
