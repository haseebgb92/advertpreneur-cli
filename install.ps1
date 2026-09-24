$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$CodexVersion = (Get-Content (Join-Path $Root 'UPSTREAM_CODEX_VERSION') -Raw).Trim()
$InstallRoot = Join-Path $env:LOCALAPPDATA 'CodexUnchained'
$BinDir = Join-Path $InstallRoot 'bin'
$AgentDir = Join-Path $HOME '.gemini\config\agents\codex-unchained-brain'

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw 'Rust/Cargo is required to build the two small Unchained adapters.'
}

New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir, $AgentDir | Out-Null

Write-Host "==> Installing official OpenAI Codex CLI $CodexVersion"
$previousRelease = $env:CODEX_RELEASE
$previousNonInteractive = $env:CODEX_NON_INTERACTIVE
try {
    $env:CODEX_RELEASE = $CodexVersion
    $env:CODEX_NON_INTERACTIVE = '1'
    Invoke-RestMethod https://chatgpt.com/codex/install.ps1 | Invoke-Expression
}
finally {
    if ($null -eq $previousRelease) { Remove-Item Env:CODEX_RELEASE -ErrorAction SilentlyContinue } else { $env:CODEX_RELEASE = $previousRelease }
    if ($null -eq $previousNonInteractive) { Remove-Item Env:CODEX_NON_INTERACTIVE -ErrorAction SilentlyContinue } else { $env:CODEX_NON_INTERACTIVE = $previousNonInteractive }
}

Write-Host '==> Building model-brain gateway'
& cargo build --release --manifest-path (Join-Path $Root 'agy-gateway\Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw 'Brain gateway build failed' }
Copy-Item (Join-Path $Root 'agy-gateway\target\release\codex-unchained-agy-gateway.exe') (Join-Path $InstallRoot 'codex-unchained-agy-gateway.exe') -Force

Write-Host '==> Building browser MCP bridge'
& cargo build --release --manifest-path (Join-Path $Root 'browser-mcp\Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw 'Browser MCP build failed' }
Copy-Item (Join-Path $Root 'browser-mcp\target\release\codex-unchained-browser-mcp.exe') (Join-Path $InstallRoot 'codex-unchained-browser-mcp.exe') -Force

Write-Host '==> Installing Chrome/Edge extension'
$ExtensionDest = Join-Path $InstallRoot 'extension'
if (Test-Path $ExtensionDest) { Remove-Item $ExtensionDest -Recurse -Force }
Copy-Item (Join-Path $Root 'extension') $ExtensionDest -Recurse -Force

Write-Host '==> Installing model-only Antigravity agent'
Copy-Item (Join-Path $Root 'agents\codex-unchained-brain\agent.md') (Join-Path $AgentDir 'agent.md') -Force

Write-Host '==> Installing launcher'
Copy-Item (Join-Path $Root 'bin\codex-unchained.ps1') (Join-Path $BinDir 'codex-unchained.ps1') -Force
Copy-Item (Join-Path $Root 'bin\codex-unchained.cmd') (Join-Path $BinDir 'codex-unchained.cmd') -Force
Copy-Item (Join-Path $Root 'bin\codex-unchained.cmd') (Join-Path $BinDir 'unchained.cmd') -Force

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($userPath -split ';') -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $BinDir).TrimStart(';')), 'User')
    Write-Host "Added $BinDir to your user PATH. Open a new terminal after installation."
}

Write-Host ''
Write-Host 'Installed.'
Write-Host 'Run: codex-unchained'
Write-Host 'Then use /model inside Codex to pick any discovered AGY or Ollama model.'
Write-Host ''
Write-Host "Load the browser extension from: $ExtensionDest"
