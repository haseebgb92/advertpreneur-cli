param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\Advertpreneur\Unchained",
    [switch]$AddToPath
)

$ErrorActionPreference = "Stop"

$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExeSource = Join-Path $PackageRoot "adp-unchained.exe"
$ExtensionSource = Join-Path $PackageRoot "extension"

if (-not (Test-Path $ExeSource)) {
    throw "adp-unchained.exe was not found beside INSTALL-WINDOWS.ps1"
}
if (-not (Test-Path $ExtensionSource)) {
    throw "extension folder was not found beside INSTALL-WINDOWS.ps1"
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
Copy-Item -Force $ExeSource (Join-Path $InstallRoot "adp-unchained.exe")
Copy-Item -Recurse -Force $ExtensionSource (Join-Path $InstallRoot "extension")

if ($AddToPath) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($current -split ";" | Where-Object { $_ })
    if ($parts -notcontains $InstallRoot) {
        $updated = (($parts + $InstallRoot) -join ";")
        [Environment]::SetEnvironmentVariable("Path", $updated, "User")
        Write-Host "Added $InstallRoot to your user PATH."
    }
}

Write-Host ""
Write-Host "ADP Unchained installed to:"
Write-Host "  $InstallRoot"
Write-Host ""
Write-Host "Chrome/Edge extension folder:"
Write-Host "  $(Join-Path $InstallRoot "extension")"
Write-Host ""
Write-Host "Load it with Extensions -> Developer mode -> Load unpacked."
Write-Host "Then test:"
Write-Host "  & '$(Join-Path $InstallRoot "adp-unchained.exe")' doctor --provider local --model qwen3:1.7b"
