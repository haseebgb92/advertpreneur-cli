param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\Advertpreneur\Unchained",
    [string]$UnchainedHome = "$env:USERPROFILE\.codex-unchained",
    [switch]$AddToPath
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$CodexSource = Join-Path $PackageRoot "codex-unchained.exe"
$McpSource = Join-Path $PackageRoot "adp-mcp.exe"
$ExtensionSource = Join-Path $PackageRoot "extension"

foreach ($required in @($CodexSource, $McpSource, $ExtensionSource)) {
    if (-not (Test-Path $required)) {
        throw "Missing package component: $required"
    }
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
New-Item -ItemType Directory -Force -Path $UnchainedHome | Out-Null

Copy-Item -Force $CodexSource (Join-Path $InstallRoot "codex-unchained.exe")
Copy-Item -Force $McpSource (Join-Path $InstallRoot "adp-mcp.exe")

$InstalledExtension = Join-Path $InstallRoot "extension"
if (Test-Path $InstalledExtension) {
    Remove-Item $InstalledExtension -Recurse -Force
}
Copy-Item -Recurse -Force $ExtensionSource $InstalledExtension

$mcpPath = (Join-Path $InstallRoot "adp-mcp.exe").Replace("\", "\\")
$configPath = Join-Path $UnchainedHome "config.toml"

if (-not (Test-Path $configPath)) {
    @"
# Codex Unchained owns this config. Normal Codex continues to use ~/.codex/config.toml.
oss_provider = "ollama"

[mcp_servers.adp]
command = "$mcpPath"
startup_timeout_sec = 20
"@ | Set-Content -Path $configPath -Encoding UTF8
} else {
    $currentConfig = Get-Content $configPath -Raw
    if ($currentConfig -notmatch '(?m)^oss_provider\s*=') {
        Add-Content -Path $configPath -Value "`r`noss_provider = `"ollama`""
    }
    if ($currentConfig -notmatch '(?m)^\[mcp_servers\.adp\]\s*$') {
        @"

[mcp_servers.adp]
command = "$mcpPath"
startup_timeout_sec = 20
"@ | Add-Content -Path $configPath -Encoding UTF8
    }
}

$snippet = @"
[mcp_servers.adp]
command = "$mcpPath"
startup_timeout_sec = 20
"@
$snippetPath = Join-Path $InstallRoot "CODEX-UNCHAINED-MCP-CONFIG.toml"
Set-Content -Path $snippetPath -Value $snippet -Encoding UTF8

if ($AddToPath) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($current -split ";" | Where-Object { $_ })
    if ($parts -notcontains $InstallRoot) {
        [Environment]::SetEnvironmentVariable("Path", (($parts + $InstallRoot) -join ";"), "User")
        Write-Host "Added $InstallRoot to your user PATH."
    }
}

Write-Host ""
Write-Host "Codex Unchained installed as:"
Write-Host "  $(Join-Path $InstallRoot "codex-unchained.exe")"
Write-Host ""
Write-Host "Dedicated Unchained home:"
Write-Host "  $UnchainedHome"
Write-Host ""
Write-Host "Unchained config:"
Write-Host "  $configPath"
Write-Host ""
Write-Host "Normal Codex remains separate at:"
Write-Host "  $(Join-Path $env:USERPROFILE ".codex")"
Write-Host ""
Write-Host "ADP MCP bridge:"
Write-Host "  $(Join-Path $InstallRoot "adp-mcp.exe")"
Write-Host ""
Write-Host "Load this unpacked extension in Chrome/Edge:"
Write-Host "  $InstalledExtension"
Write-Host ""
Write-Host "Then run:"
Write-Host "  codex-unchained --oss"
Write-Host ""
Write-Host "Inside the TUI use /model to switch models."
