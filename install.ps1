$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$CodexVersion = (Get-Content (Join-Path $Root 'UPSTREAM_CODEX_VERSION') -Raw).Trim()
$InstallRoot = Join-Path $env:LOCALAPPDATA 'CodexUnchained'
$BinDir = Join-Path $InstallRoot 'bin'
$AgentDir = Join-Path $HOME '.gemini\config\agents\codex-unchained-brain'

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw 'Rust/Cargo is required to build the small AGY gateway. Install rustup, then rerun this installer.'
}

New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir, $AgentDir | Out-Null

Write-Host "==> Installing official OpenAI Codex CLI $CodexVersion"
$previousRelease = $env:CODEX_RELEASE
try {
    $env:CODEX_RELEASE = $CodexVersion
    Invoke-RestMethod https://chatgpt.com/codex/install.ps1 | Invoke-Expression
}
finally {
    if ($null -eq $previousRelease) { Remove-Item Env:CODEX_RELEASE -ErrorAction SilentlyContinue } else { $env:CODEX_RELEASE = $previousRelease }
}

Write-Host '==> Building AGY compatibility gateway'
& cargo build --release --manifest-path (Join-Path $Root 'agy-gateway\Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw 'Gateway build failed' }
Copy-Item (Join-Path $Root 'agy-gateway\target\release\codex-unchained-agy-gateway.exe') (Join-Path $InstallRoot 'codex-unchained-agy-gateway.exe') -Force

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
Write-Host 'Installed. Run:'
Write-Host '  codex-unchained doctor'
Write-Host '  codex-unchained models'
Write-Host '  codex-unchained -m agy/gemini-3.8-flash-medium'
Write-Host '  codex-unchained -m ollama/gpt-oss:120b-cloud'
