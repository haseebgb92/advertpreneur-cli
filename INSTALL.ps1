param(
    [switch]$InstallRecommended,
    [switch]$FromGitHub,
    [string]$Repository = "haseebgb92/advertpreneur-cli"
)

$ErrorActionPreference = "Stop"

if ($FromGitHub) {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) { throw "GitHub CLI is required for private installs. Install GitHub CLI, run 'gh auth login -h github.com', then retry." }
    $downloadRoot = Join-Path $env:LOCALAPPDATA "AdvertpreneurCLI\downloads"
    $stage = Join-Path $downloadRoot ("install-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    try {
        $release = (& gh api "repos/$Repository/releases/latest" | ConvertFrom-Json)
        $releaseTag = [string]$release.tag_name
        if (-not $releaseTag) { throw "GitHub did not return a latest release tag." }
        & gh release download $releaseTag --repo $Repository --pattern "update-manifest.json" --dir $stage --clobber
        $manifest = Get-Content (Join-Path $stage "update-manifest.json") -Raw | ConvertFrom-Json
        if (-not $manifest.asset -or -not $manifest.sha256) { throw "Release manifest is invalid." }
        & gh release download $releaseTag --repo $Repository --pattern $manifest.asset --dir $stage --clobber
        $archive = Join-Path $stage $manifest.asset
        $actual = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
        if ($actual -ne ([string]$manifest.sha256).ToLowerInvariant()) { throw "Release checksum verification failed." }
        $source = Join-Path $stage "source"
        Expand-Archive -Path $archive -DestinationPath $source -Force
        & (Join-Path $source "INSTALL.ps1") -InstallRecommended:$InstallRecommended
        exit $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Set-Location $PSScriptRoot

# Stop only the old Advertpreneur local bridge broker so protocol upgrades can bind
# the same localhost port cleanly. This never touches Codex, AGY, Ollama, or browser processes.
try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "advertpreneur_cli[\\./]bridge_server" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {}

Write-Host "Advertpreneur CLI installer" -ForegroundColor DarkYellow
Write-Host "Checking runtime..." -ForegroundColor DarkGray

function Find-Python {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python313\python.exe"
    )
    foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
    return $null
}

$python = Find-Python
if (-not $python) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.10+ is required and was not found. Install Python 3.12, then run INSTALL.ps1 again."
    }
    Write-Host "Python was not found. Installing Python 3.12 with winget..." -ForegroundColor Yellow
    & winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
    $python = Find-Python
    if (-not $python) {
        throw "Python installation completed but python.exe was not found in this PowerShell session. Open a new PowerShell window and run INSTALL.ps1 again."
    }
}

$version = & $python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-Host "  Python $version" -ForegroundColor Green

Write-Host "Installing required Python dependencies..." -ForegroundColor DarkGray
try {
    & $python -m pip --version | Out-Null
} catch {
    & $python -m ensurepip --upgrade
}
& $python -m pip install --disable-pip-version-check -r requirements.txt

$wheel = Get-ChildItem -Path ".\dist\advertpreneur_cli-*.whl" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($wheel) {
    & $python -m pip install --disable-pip-version-check --upgrade --force-reinstall --no-deps $wheel.FullName
} elseif (Test-Path ".\pyproject.toml") {
    # GitHub release archives intentionally contain source, not a machine-specific
    # wheel. pip installs it directly and keeps the console command current.
    & $python -m pip install --disable-pip-version-check --upgrade --force-reinstall --no-deps $PSScriptRoot
} else {
    throw "Advertpreneur CLI package files were not found."
}

$extensionPath = $null
try {
    $extensionPath = (& $python -c "from pathlib import Path; from advertpreneur_cli.bridge import BridgeClient; b=BridgeClient(Path.home()/'.advertpreneur-cli'); print(b.extension_path()); b.ensure_server()").Trim()
} catch {}

Write-Host ""
Write-Host "Required install complete." -ForegroundColor Green
Write-Host ""
Write-Host "Optional integrations:" -ForegroundColor DarkYellow
$optional = @(
    @{ Command = "git";    Name = "Git";              Why = "diff/review/branch awareness" },
    @{ Command = "ollama"; Name = "Ollama";           Why = "local models only" },
    @{ Command = "codex";  Name = "Codex CLI";        Why = "optional fallback; ADP can install OpenAI's official Codex SDK/runtime on first use" },
    @{ Command = "agy";    Name = "Antigravity";      Why = "optional now; ADP can install Google's official runtime on first AGY login" },
    @{ Command = "node";   Name = "Node.js";          Why = "some stdio MCP servers/tooling" },
    @{ Command = "npm";    Name = "npm";              Why = "Node-based tooling" }
)
foreach ($item in $optional) {
    $found = Get-Command $item.Command -ErrorAction SilentlyContinue
    if ($found) {
        Write-Host ("  [OK] {0,-12} {1}" -f $item.Name, $found.Source) -ForegroundColor Green
    } else {
        Write-Host ("  [--] {0,-12} optional - {1}" -f $item.Name, $item.Why) -ForegroundColor DarkGray
    }
}

$edgeCandidates = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
)
$edge = $edgeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
    Write-Host ("  [OK] {0,-12} {1}" -f "Edge", $edge) -ForegroundColor Green
} else {
    Write-Host ("  [--] {0,-12} optional - browser control can use Chrome/Playwright Chromium instead" -f "Edge") -ForegroundColor DarkGray
}

if ($InstallRecommended) {
    Write-Host ""
    Write-Host "Installing missing recommended developer tools where winget packages are known..." -ForegroundColor Yellow
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
    }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        winget install --id OpenJS.NodeJS.LTS -e --silent --accept-package-agreements --accept-source-agreements
    }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Write-Host "  Ollama was left optional. Install it only if you want local models." -ForegroundColor DarkGray
    }
    if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
        Write-Host "  Codex CLI not present. No action needed: /providers login codex can install OpenAI's official SDK/runtime on demand." -ForegroundColor DarkGray
    }
    if (-not (Get-Command agy -ErrorAction SilentlyContinue)) {
        Write-Host "  Antigravity runtime not present. No action needed: /providers login agy can install Google's official runtime on demand." -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "Advertpreneur CLI installed." -ForegroundColor Green
Write-Host "Run from any project folder:" -ForegroundColor DarkGray
Write-Host "  advertpreneur" -ForegroundColor Cyan
Write-Host "or:" -ForegroundColor DarkGray
Write-Host "  adp" -ForegroundColor Cyan
Write-Host ""
Write-Host "Inside the CLI, run /login once for Ollama Cloud. Use /model to browse Ollama + connected Codex/AGY models; external models can also be Primary Coders." -ForegroundColor DarkGray
if ($extensionPath) {
    Write-Host ("Advertpreneur Browser Bridge refreshed: {0}" -f $extensionPath) -ForegroundColor Cyan
    Write-Host "IMPORTANT: open edge://extensions and click Reload once on Advertpreneur Browser Bridge. The localhost broker is already running, so the extension should register immediately." -ForegroundColor Yellow
}
Write-Host "V0.15.1 adds the zero-daemon Resource Guard: cross-ADP RAM/CPU pressure awareness, adaptive warm-provider cleanup, heavy-build coordination, and improved /health estate reporting while preserving v0.15 project intelligence." -ForegroundColor DarkGray
Write-Host "Use /providers login codex or /providers login agy for official provider-owned sign-in. Missing provider runtimes can be installed on demand by ADP; OAuth/keyring credentials remain provider-owned." -ForegroundColor DarkGray
