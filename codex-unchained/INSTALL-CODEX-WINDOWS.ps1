param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\Advertpreneur\Unchained",
    [string]$UnchainedHome = "$env:USERPROFILE\.codex-unchained",
    [switch]$AddToPath
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$CodexSource = Join-Path $PackageRoot "codex-unchained.exe"
$McpSource = Join-Path $PackageRoot "adp-mcp.exe"
$HelperSource = Join-Path $PackageRoot "adp-unchained.exe"
$SandboxSetupSource = Join-Path $PackageRoot "codex-windows-sandbox-setup.exe"
$SandboxServiceSource = Join-Path $PackageRoot "codex-windows-sandbox-service.exe"
$CommandRunnerSource = Join-Path $PackageRoot "codex-command-runner.exe"
$ExtensionSource = Join-Path $PackageRoot "extension"
$AntigravityAgentSource = Join-Path $PackageRoot "antigravity-agent\adp-unchained-brain"

foreach ($required in @(
    $CodexSource,
    $McpSource,
    $HelperSource,
    $SandboxSetupSource,
    $SandboxServiceSource,
    $CommandRunnerSource,
    $ExtensionSource,
    $AntigravityAgentSource
)) {
    if (-not (Test-Path $required)) {
        throw "Missing package component: $required"
    }
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
New-Item -ItemType Directory -Force -Path $UnchainedHome | Out-Null

Copy-Item -Force $CodexSource (Join-Path $InstallRoot "codex-unchained.exe")
Copy-Item -Force $McpSource (Join-Path $InstallRoot "adp-mcp.exe")
Copy-Item -Force $HelperSource (Join-Path $InstallRoot "adp-unchained.exe")
Copy-Item -Force $SandboxSetupSource (Join-Path $InstallRoot "codex-windows-sandbox-setup.exe")
Copy-Item -Force $SandboxServiceSource (Join-Path $InstallRoot "codex-windows-sandbox-service.exe")
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
    @(
        '# Codex Unchained owns this config. Normal Codex continues to use ~/.codex/config.toml.'
        'model = "adp/auto"'
        'model_provider = "unchained"'
        'oss_provider = "ollama"'
        ''
        '[model_providers.unchained]'
        'name = "ADP Unchained Model Router"'
        'base_url = "http://127.0.0.1:8766/v1"'
        'model_catalog_url = "http://127.0.0.1:8766/v1/models"'
        'wire_api = "responses"'
        'requires_openai_auth = false'
        ''
        '[mcp_servers.adp]'
        ('command = "' + $mcpPath + '"')
        'startup_timeout_sec = 20'
    ) | Set-Content -Path $configPath -Encoding UTF8
} else {
    $lines = @(Get-Content $configPath)

    if (-not ($lines -match '^model\s*=')) {
        Add-Content -Path $configPath -Value 'model = "adp/auto"'
    }
    if (-not ($lines -match '^model_provider\s*=')) {
        Add-Content -Path $configPath -Value 'model_provider = "unchained"'
    }
    if (-not ($lines -match '^oss_provider\s*=')) {
        Add-Content -Path $configPath -Value 'oss_provider = "ollama"'
    }

    $lines = @(Get-Content $configPath)
    $providerHeader = '[model_providers.unchained]'
    $providerIndex = [Array]::IndexOf($lines, $providerHeader)

    if ($providerIndex -lt 0) {
        @(
            ''
            $providerHeader
            'name = "ADP Unchained Model Router"'
            'base_url = "http://127.0.0.1:8766/v1"'
            'model_catalog_url = "http://127.0.0.1:8766/v1/models"'
            'wire_api = "responses"'
            'requires_openai_auth = false'
        ) | Add-Content -Path $configPath -Encoding UTF8
    } else {
        $providerEnd = $lines.Count
        for ($i = $providerIndex + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match '^\s*\[') {
                $providerEnd = $i
                break
            }
        }

        $hasCatalog = $false
        $baseUrlIndex = -1
        for ($i = $providerIndex + 1; $i -lt $providerEnd; $i++) {
            if ($lines[$i] -match '^\s*model_catalog_url\s*=') {
                $hasCatalog = $true
            }
            if ($lines[$i] -match '^\s*base_url\s*=\s*"http://127\.0\.0\.1:8766/v1"\s*$') {
                $baseUrlIndex = $i
            }
        }

        if (-not $hasCatalog) {
            $editable = [System.Collections.Generic.List[string]]::new()
            foreach ($line in $lines) {
                [void]$editable.Add($line)
            }

            $insertAt = if ($baseUrlIndex -ge 0) { $baseUrlIndex + 1 } else { $providerEnd }
            $editable.Insert($insertAt, 'model_catalog_url = "http://127.0.0.1:8766/v1/models"')
            $editable | Set-Content -Path $configPath -Encoding UTF8
        }
    }

    $lines = @(Get-Content $configPath)
    if (-not ($lines -contains '[mcp_servers.adp]')) {
        @(
            ''
            '[mcp_servers.adp]'
            ('command = "' + $mcpPath + '"')
            'startup_timeout_sec = 20'
        ) | Add-Content -Path $configPath -Encoding UTF8
    }
}

# Migrate the original v0.1 local-model default to ADP Auto without touching
# any other explicitly pinned model.
$configText = Get-Content $configPath -Raw
$configText = $configText -replace 'model\s*=\s*"ollama-local/qwen3:1\.7b"', 'model = "adp/auto"'
Set-Content -Path $configPath -Value $configText -Encoding UTF8

$snippetPath = Join-Path $InstallRoot "CODEX-UNCHAINED-MCP-CONFIG.toml"
@(
    '[mcp_servers.adp]'
    ('command = "' + $mcpPath + '"')
    'startup_timeout_sec = 20'
) | Set-Content -Path $snippetPath -Encoding UTF8

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
Write-Host "  $(Join-Path $InstallRoot "codex-windows-sandbox-service.exe")"
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

Write-Host ""
Write-Host "Provider setup:"
Write-Host "  Ollama browser login: adp-unchained auth ollama"
Write-Host "  Ollama API key:       set OLLAMA_API_KEY, then run adp-unchained auth ollama --method api"
Write-Host "  Antigravity login:    adp-unchained auth agy"
Write-Host ""
Write-Host "Default routing mode: /adp auto"
Write-Host "Other modes: /adp economy | /adp balanced | /adp max"
