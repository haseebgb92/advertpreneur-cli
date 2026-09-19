param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\Advertpreneur\Unchained",
    [string]$UnchainedHome = "$env:USERPROFILE\.codex-unchained",
    [switch]$AddToPath
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$CodexSource = Join-Path $PackageRoot "codex-unchained.exe"
$McpSource = Join-Path $PackageRoot "adp-mcp.exe"
$SandboxSetupSource = Join-Path $PackageRoot "codex-windows-sandbox-setup.exe"
$CommandRunnerSource = Join-Path $PackageRoot "codex-command-runner.exe"
$ExtensionSource = Join-Path $PackageRoot "extension"
$AntigravityAgentSource = Join-Path $PackageRoot "antigravity-agent\adp-unchained-brain"

foreach ($required in @($CodexSource, $McpSource, $SandboxSetupSource, $CommandRunnerSource, $ExtensionSource, $AntigravityAgentSource)) {
    if (-not (Test-Path $required)) {
        throw "Missing package component: $required"
    }
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
New-Item -ItemType Directory -Force -Path $UnchainedHome | Out-Null

Copy-Item -Force $CodexSource (Join-Path $InstallRoot "codex-unchained.exe")
Copy-Item -Force $McpSource (Join-Path $InstallRoot "adp-mcp.exe")
Copy-Item -Force $SandboxSetupSource (Join-Path $InstallRoot "codex-windows-sandbox-setup.exe")
Copy-Item -Force $CommandRunnerSource (Join-Path $InstallRoot "codex-command-runner.exe")

$InstalledExtension = Join-Path $InstallRoot "extension"
if (Test-Path $InstalledExtension) {
    Remove-Item $InstalledExtension -Recurse -Force
}
Copy-Item -Recurse -Force $ExtensionSource $InstalledExtension

$AntigravityAgentRoot = Join-Path $env:USERPROFILE ".gemini\config\agents"
$InstalledAntigravityAgent = Join-Path $AntigravityAgentRoot "adp-unchained-brain"
New-Item -ItemType Directory -Force -Path $AntigravityAgentRoot | Out-Null
if (Test-Path $InstalledAntigravityAgent) {
    Remove-Item $InstalledAntigravityAgent -Recurse -Force
}
Copy-Item -Recurse -Force $AntigravityAgentSource $InstalledAntigravityAgent

$mcpPath = (Join-Path $InstallRoot "adp-mcp.exe").Replace("\", "\\")
$configPath = Join-Path $UnchainedHome "config.toml"

if (-not (Test-Path $configPath)) {
@"
# Codex Unchained owns this config. Normal Codex continues to use ~/.codex/config.toml.
model = "ollama-local/qwen3:1.7b"
model_provider = "unchained"
oss_provider = "ollama"

[model_providers.unchained]
name = "ADP Unchained Model Router"
base_url = "http://127.0.0.1:8766/v1"
model_catalog_url = "http://127.0.0.1:8766/v1/models"
wire_api = "responses"
requires_openai_auth = false

[mcp_servers.adp]
command = "$mcpPath"
startup_timeout_sec = 20
"@ | Set-Content -Path $configPath -Encoding UTF8
} else {
    $currentConfig = Get-Content $configPath -Raw

    if ($currentConfig -notmatch '(?m)^model\s*=') {
        Add-Content -Path $configPath -Value 'model = "ollama-local/qwen3:1.7b"'
    }
    if ($currentConfig -notmatch '(?m)^model_provider\s*=') {
        Add-Content -Path $configPath -Value 'model_provider = "unchained"'
    }
    if ($currentConfig -notmatch '(?m)^oss_provider\s*=') {
        Add-Content -Path $configPath -Value 'oss_provider = "ollama"'
    }
    if ($currentConfig -notmatch '(?m)^\[model_providers\.unchained\]\s*
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
Write-Host "Windows sandbox helpers:"
Write-Host "  $(Join-Path $InstallRoot "codex-windows-sandbox-setup.exe")"
Write-Host "  $(Join-Path $InstallRoot "codex-command-runner.exe")"
Write-Host ""
Write-Host "Antigravity brain-only adapter:"
Write-Host "  $InstalledAntigravityAgent"
Write-Host ""
Write-Host "Load this unpacked extension in Chrome/Edge:"
Write-Host "  $InstalledExtension"
Write-Host ""
Write-Host "Then run:"
Write-Host "  codex-unchained"
Write-Host ""
Write-Host "Inside the TUI use /model to switch models."
) {
@"

[model_providers.unchained]
name = "ADP Unchained Model Router"
base_url = "http://127.0.0.1:8766/v1"
model_catalog_url = "http://127.0.0.1:8766/v1/models"
wire_api = "responses"
requires_openai_auth = false
"@ | Add-Content -Path $configPath -Encoding UTF8
    }
    elseif ($currentConfig -notmatch '(?m)^model_catalog_url\s*=') {
        $baseUrlLine = 'base_url = "http://127.0.0.1:8766/v1"'
        $catalogLines = $baseUrlLine + "`r`n" + 'model_catalog_url = "http://127.0.0.1:8766/v1/models"'
        $currentConfig = $currentConfig.Replace($baseUrlLine, $catalogLines)
        Set-Content -Path $configPath -Value $currentConfig -Encoding UTF8
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
Write-Host "Windows sandbox helpers:"
Write-Host "  $(Join-Path $InstallRoot "codex-windows-sandbox-setup.exe")"
Write-Host "  $(Join-Path $InstallRoot "codex-command-runner.exe")"
Write-Host ""
Write-Host "Antigravity brain-only adapter:"
Write-Host "  $InstalledAntigravityAgent"
Write-Host ""
Write-Host "Load this unpacked extension in Chrome/Edge:"
Write-Host "  $InstalledExtension"
Write-Host ""
Write-Host "Then run:"
Write-Host "  codex-unchained"
Write-Host ""
Write-Host "Inside the TUI use /model to switch models."
