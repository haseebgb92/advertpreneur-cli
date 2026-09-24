$ErrorActionPreference = 'Stop'
$GatewayPort = if ($env:UNCHAINED_AGY_PORT) { $env:UNCHAINED_AGY_PORT } else { '41415' }
$GatewayUrl = "http://127.0.0.1:$GatewayPort"
$UnchainedHome = if ($env:UNCHAINED_HOME) { $env:UNCHAINED_HOME } else { Join-Path $env:LOCALAPPDATA 'CodexUnchained' }
$GatewayBin = if ($env:UNCHAINED_AGY_GATEWAY_BIN) { $env:UNCHAINED_AGY_GATEWAY_BIN } else { Join-Path $UnchainedHome 'codex-unchained-agy-gateway.exe' }
$BrowserMcpBin = if ($env:UNCHAINED_BROWSER_MCP_BIN) { $env:UNCHAINED_BROWSER_MCP_BIN } else { Join-Path $UnchainedHome 'codex-unchained-browser-mcp.exe' }
$CatalogPath = if ($env:UNCHAINED_MODEL_CATALOG) { $env:UNCHAINED_MODEL_CATALOG } else { Join-Path $UnchainedHome 'models.json' }

function Has-Command([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Require-Command([string]$Name) { if (-not (Has-Command $Name)) { throw "Missing required command: $Name" } }

if ($args.Count -gt 0 -and $args[0] -eq 'doctor') {
    Write-Host 'Codex Unchained doctor'
    Write-Host '----------------------'
    if (Has-Command 'codex') { & codex --version } else { Write-Host 'codex: MISSING' }
    if (Has-Command 'agy') { try { & agy --version } catch { Write-Host 'agy: installed' } } else { Write-Host 'agy: not installed' }
    if (Has-Command 'ollama') { try { & ollama --version } catch { Write-Host 'ollama: installed' } } else { Write-Host 'ollama: not installed' }
    if (Test-Path $GatewayBin) { Write-Host "brain gateway: $GatewayBin" } else { Write-Host "brain gateway: MISSING at $GatewayBin" }
    if (Test-Path $BrowserMcpBin) { Write-Host "browser MCP: $BrowserMcpBin" } else { Write-Host "browser MCP: MISSING at $BrowserMcpBin" }
    $extensionPath = Join-Path $UnchainedHome 'extension'
    if (Test-Path $extensionPath) { Write-Host "browser extension: $extensionPath" } else { Write-Host "browser extension: MISSING" }
    if (Test-Path $CatalogPath) { Write-Host "last imported model catalog: $CatalogPath" }
    exit 0
}

Require-Command 'codex'
if (-not (Test-Path $GatewayBin)) { throw "Brain gateway not installed: $GatewayBin" }
New-Item -ItemType Directory -Force -Path $UnchainedHome | Out-Null

$startedGateway = $false
$gatewayProcess = $null
try {
    $healthy = $false
    try { $null = Invoke-RestMethod -Uri "$GatewayUrl/health" -TimeoutSec 1; $healthy = $true } catch {}

    if (-not $healthy) {
        $stdout = Join-Path $UnchainedHome 'brain-gateway.log'
        $stderr = Join-Path $UnchainedHome 'brain-gateway.err.log'
        $gatewayProcess = Start-Process -FilePath $GatewayBin -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $startedGateway = $true
        for ($n = 0; $n -lt 50; $n++) {
            Start-Sleep -Milliseconds 100
            if ($gatewayProcess.HasExited) { throw "Brain gateway failed to start. See $stderr" }
            try { $null = Invoke-RestMethod -Uri "$GatewayUrl/health" -TimeoutSec 1; $healthy = $true; break } catch {}
        }
        if (-not $healthy) { throw 'Brain gateway did not become ready' }
    }

    try {
        $catalog = Invoke-RestMethod -Uri "$GatewayUrl/v1/models" -TimeoutSec 15
        $catalog | ConvertTo-Json -Depth 100 | Set-Content -Path $CatalogPath -Encoding UTF8
    }
    catch {
        throw "Could not import AGY/Ollama models: $($_.Exception.Message)"
    }

    $providerConfig = "model_providers.unchained={name=`"Codex Unchained`",base_url=`"$GatewayUrl/v1`",wire_api=`"responses`",requires_openai_auth=false,supports_websockets=false,supports_standalone_web_search=false}"
    $catalogConfig = "model_catalog_json=`"$CatalogPath`""

    $codexArgs = @(
        '-c', 'model_provider="unchained"',
        '-c', $providerConfig,
        '-c', $catalogConfig
    )

    if (Test-Path $BrowserMcpBin) {
        $browserConfig = "mcp_servers.unchained_browser.command=`"$BrowserMcpBin`""
        $codexArgs += @('-c', $browserConfig)
    }

    if ($env:UNCHAINED_MODEL) {
        $hasModel = $false
        foreach ($arg in $args) {
            if ($arg -eq '-m' -or $arg -eq '--model' -or $arg.StartsWith('--model=')) {
                $hasModel = $true
                break
            }
        }
        if (-not $hasModel) {
            $codexArgs += @('-m', $env:UNCHAINED_MODEL)
        }
    }

    & codex @codexArgs @args
    exit $LASTEXITCODE
}
finally {
    if ($startedGateway -and $null -ne $gatewayProcess -and -not $gatewayProcess.HasExited) {
        Stop-Process -Id $gatewayProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
