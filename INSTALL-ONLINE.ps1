param(
    [switch]$InstallRecommended,
    [string]$Repository = "haseebgb92/advertpreneur-cli"
)

$ErrorActionPreference = "Stop"
$base = "https://github.com/$Repository/releases/latest/download"

Write-Host "Downloading the verified Advertpreneur CLI installer..." -ForegroundColor DarkYellow
$script = (Invoke-WebRequest -UseBasicParsing "$base/INSTALL.ps1").Content
if (-not $script) { throw "The Advertpreneur installer could not be downloaded." }

# The downloaded installer verifies the release ZIP SHA-256 before it installs.
& ([scriptblock]::Create($script)) -FromGitHub -Repository $Repository -InstallRecommended:$InstallRecommended
