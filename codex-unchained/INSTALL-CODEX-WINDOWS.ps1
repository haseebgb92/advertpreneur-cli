param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\Advertpreneur\Unchained",
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
Copy-Item -Force $CodexSource (Join-Path $InstallRoot "codex-unchained.exe")
Copy-Item -Force $McpSource (Join-Path $InstallRoot "adp-mcp.exe")
Copy-Item -Recurse -Force $ExtensionSource (Join-Path $InstallRoot "extension")

$mcpPath = (Join-Path $InstallRoot "adp-mcp.exe").Replace("\", "\\")
$snippet = @"
[mcp_servers.adp]
command = "$mcpPath"
startup_timeout_sec = 20
"@
$snippetPath = Join-Path $InstallRoot "CODEX-MCP-CONFIG.toml"
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
Write-Host "Patched Codex installed as:"
Write-Host "  $(Join-Path $InstallRoot "codex-unchained.exe")"
Write-Host ""
Write-Host "ADP MCP bridge:"
Write-Host "  $(Join-Path $InstallRoot "adp-mcp.exe")"
Write-Host ""
Write-Host "Load this unpacked extension in Chrome/Edge:"
Write-Host "  $(Join-Path $InstallRoot "extension")"
Write-Host ""
Write-Host "Copy the block from this file into your Codex config.toml:"
Write-Host "  $snippetPath"
Write-Host ""
Write-Host "Then run:"
Write-Host "  codex-unchained --oss -m qwen3:1.7b"
