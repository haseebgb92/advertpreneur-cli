$ErrorActionPreference = 'Stop'
$DefaultModel = if ($env:UNCHAINED_MODEL) { $env:UNCHAINED_MODEL } else { 'agy/gemini-3.8-flash-medium' }
$GatewayPort = if ($env:UNCHAINED_AGY_PORT) { $env:UNCHAINED_AGY_PORT } else { '41415' }
$GatewayUrl = "http://127.0.0.1:$GatewayPort"
$UnchainedHome = if ($env:UNCHAINED_HOME) { $env:UNCHAINED_HOME } else { Join-Path $env:LOCALAPPDATA 'CodexUnchained' }
$GatewayBin = if ($env:UNCHAINED_AGY_GATEWAY_BIN) { $env:UNCHAINED_AGY_GATEWAY_BIN } else { Join-Path $UnchainedHome 'codex-unchained-agy-gateway.exe' }

function Has-Command([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Require-Command([string]$Name) { if (-not (Has-Command $Name)) { throw "Missing required command: $Name" } }

if ($args.Count -gt 0 -and $args[0] -eq 'models') {
    Write-Host 'AGY models'
    Write-Host '----------'
    if (Has-Command 'agy') { & agy models } else { Write-Host 'agy is not installed' }
    Write-Host ''
    Write-Host 'Ollama models'
    Write-Host '-------------'
    if (Has-Command 'ollama') { & ollama list } else { Write-Host 'ollama is not installed' }
    exit 0
}

if ($args.Count -gt 0 -and $args[0] -eq 'doctor') {
    Write-Host 'Codex Unchained doctor'
    Write-Host '----------------------'
    if (Has-Command 'codex') { & codex --version } else { Write-Host 'codex: MISSING' }
    if (Has-Command 'agy') { try { & agy --version } catch { Write-Host 'agy: installed' } } else { Write-Host 'agy: MISSING (only needed for AGY models)' }
    if (Has-Command 'ollama') { try { & ollama --version } catch { Write-Host 'ollama: installed' } } else { Write-Host 'ollama: MISSING (only needed for Ollama models)' }
    if (Test-Path $GatewayBin) { Write-Host "AGY gateway: $GatewayBin" } else { Write-Host "AGY gateway: MISSING at $GatewayBin" }
    exit 0
}

$modelSpec = $DefaultModel
$forward = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $args.Count; $i++) {
    $arg = [string]$args[$i]
    if ($arg -eq '-m' -or $arg -eq '--model') {
        if ($i + 1 -ge $args.Count) { throw "$arg requires a model" }
        $i++
        $modelSpec = [string]$args[$i]
    } elseif ($arg.StartsWith('--model=')) {
        $modelSpec = $arg.Substring(8)
    } else {
        $forward.Add($arg)
    }
}

if (-not $modelSpec.Contains('/')) {
    $providerDefault = if ($env:UNCHAINED_PROVIDER) { $env:UNCHAINED_PROVIDER } else { 'agy' }
    $modelSpec = "$providerDefault/$modelSpec"
}
$slash = $modelSpec.IndexOf('/')
$provider = $modelSpec.Substring(0, $slash)
$model = $modelSpec.Substring($slash + 1)

Require-Command 'codex'

if ($provider -eq 'ollama') {
    Require-Command 'ollama'
    & codex --oss -m $model @forward
    exit $LASTEXITCODE
}

if ($provider -ne 'agy' -and $provider -ne 'antigravity') {
    throw "Unknown brain provider '$provider'. Use agy/<model> or ollama/<model>."
}

Require-Command 'agy'
if (-not (Test-Path $GatewayBin)) { throw "AGY gateway not installed: $GatewayBin" }

$startedGateway = $false
$gatewayProcess = $null
try {
    $healthy = $false
    try { $null = Invoke-RestMethod -Uri "$GatewayUrl/health" -TimeoutSec 1; $healthy = $true } catch {}
    if (-not $healthy) {
        New-Item -ItemType Directory -Force -Path $UnchainedHome | Out-Null
        $stdout = Join-Path $UnchainedHome 'agy-gateway.log'
        $stderr = Join-Path $UnchainedHome 'agy-gateway.err.log'
        $gatewayProcess = Start-Process -FilePath $GatewayBin -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $startedGateway = $true
        for ($n = 0; $n -lt 50; $n++) {
            Start-Sleep -Milliseconds 100
            if ($gatewayProcess.HasExited) { throw "AGY gateway failed to start. See $stderr" }
            try { $null = Invoke-RestMethod -Uri "$GatewayUrl/health" -TimeoutSec 1; $healthy = $true; break } catch {}
        }
        if (-not $healthy) { throw 'AGY gateway did not become ready' }
    }

    $providerConfig = "model_providers.agy-unchained={name=`"Antigravity`",base_url=`"$GatewayUrl/v1`",wire_api=`"responses`",requires_openai_auth=false,supports_websockets=false,supports_standalone_web_search=false}"
    $codexArgs = @('-c', 'model_provider="agy-unchained"', '-c', $providerConfig, '-m', $model) + $forward.ToArray()
    & codex @codexArgs
    exit $LASTEXITCODE
}
finally {
    if ($startedGateway -and $null -ne $gatewayProcess -and -not $gatewayProcess.HasExited) {
        Stop-Process -Id $gatewayProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
