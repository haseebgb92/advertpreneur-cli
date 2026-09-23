$ErrorActionPreference = "Stop"

$repo = if ($env:CODEX_UNCHAINED_REPOSITORY) { $env:CODEX_UNCHAINED_REPOSITORY } else { "haseebgb92/advertpreneur-cli" }
$version = if ($env:CODEX_UNCHAINED_VERSION) { $env:CODEX_UNCHAINED_VERSION } else { "latest" }
$installDir = if ($env:CODEX_UNCHAINED_INSTALL_DIR) { $env:CODEX_UNCHAINED_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Advertpreneur\Unchained" }
$unchainedHome = if ($env:CODEX_UNCHAINED_HOME) { $env:CODEX_UNCHAINED_HOME } else { Join-Path $env:USERPROFILE ".codex-unchained" }
$asset = "patched-codex-windows-x64"

if ($version -eq "latest") {
    $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases?per_page=50" -Headers @{ Accept = "application/vnd.github+json" }
    $release = $releases | Where-Object { -not $_.draft -and $_.tag_name -like "codex-unchained-v*" } | Select-Object -First 1
    if (-not $release) {
        throw "No Codex Unchained release was found. Expected a release tag matching codex-unchained-v*."
    }
    $version = $release.tag_name
}

$base = "https://github.com/$repo/releases/download/$version"
Write-Host "Installing Codex Unchained $version ($asset)"

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("codex-unchained-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force $tmp | Out-Null

try {
    $archive = Join-Path $tmp "package.tar.gz"
    Invoke-WebRequest "$base/$asset.tar.gz" -OutFile $archive
    tar -xzf $archive -C $tmp

    $pkg = Join-Path $tmp $asset
    New-Item -ItemType Directory -Force $installDir | Out-Null
    New-Item -ItemType Directory -Force $unchainedHome | Out-Null

    foreach ($name in @(
        "codex-unchained.exe",
        "adp-mcp.exe",
        "adp-unchained.exe",
        "codex-windows-sandbox-setup.exe",
        "codex-windows-sandbox-service.exe",
        "codex-command-runner.exe"
    )) {
        $src = Join-Path $pkg $name
        if (-not (Test-Path $src)) {
            throw "Release package is incomplete. Missing: $name"
        }
        Copy-Item $src (Join-Path $installDir $name) -Force
    }

    $extension = Join-Path $pkg "extension"
    if (Test-Path $extension) {
        $destExtension = Join-Path $unchainedHome "extension"
        if (Test-Path $destExtension) { Remove-Item $destExtension -Recurse -Force }
        Copy-Item $extension $destExtension -Recurse -Force
    }

    $agySource = Join-Path $pkg "antigravity-agent\adp-unchained-brain"
    if (Test-Path $agySource) {
        $agyRoot = Join-Path $env:USERPROFILE ".gemini\config\agents"
        $agyDest = Join-Path $agyRoot "adp-unchained-brain"
        New-Item -ItemType Directory -Force $agyRoot | Out-Null
        if (Test-Path $agyDest) { Remove-Item $agyDest -Recurse -Force }
        Copy-Item $agySource $agyDest -Recurse -Force
    }

    $config = Join-Path $unchainedHome "config.toml"
    $mcpPath = (Join-Path $installDir "adp-mcp.exe").Replace("\", "\\")

    if (-not (Test-Path $config)) {
        @(
            '# Codex Unchained owns this config. Normal Codex continues to use ~/.codex/config.toml.'
            'model = "adp/auto"'
            'model_provider = "unchained"'
            'oss_provider = "ollama"'
            ''
            '[model_providers.unchained]'
            'name = "ADP Unchained Model Router"'
            'base_url = "http://127.0.0.1:8766/v1"'
            'wire_api = "responses"'
            'requires_openai_auth = false'
            ''
            '[mcp_servers.adp]'
            ('command = "' + $mcpPath + '"')
            'startup_timeout_sec = 20'
        ) | Set-Content -Path $config -Encoding UTF8
    } else {
        $text = Get-Content $config -Raw
        $text = $text -replace 'model\s*=\s*"ollama-local/qwen3:1\.7b"', 'model = "adp/auto"'

        if ($text -notmatch '(?m)^model\s*=') {
            $text = "model = `"adp/auto`"`r`n" + $text
        }
        if ($text -notmatch '(?m)^model_provider\s*=') {
            $text = "model_provider = `"unchained`"`r`n" + $text
        }
        if ($text -notmatch '(?m)^oss_provider\s*=') {
            $text = "oss_provider = `"ollama`"`r`n" + $text
        }
        if ($text -notmatch '(?m)^\[model_providers\.unchained\]$') {
            $text += @"

[model_providers.unchained]
name = "ADP Unchained Model Router"
base_url = "http://127.0.0.1:8766/v1"
model_catalog_url = "http://127.0.0.1:8766/v1/models"
wire_api = "responses"
requires_openai_auth = false
"@
        }

        $text = $text -replace '(?m)^model_catalog_url\s*=.*(?:\r?\n)?', ''

        if ($text -notmatch '(?m)^\[mcp_servers\.adp\]
            $text += @"

[mcp_servers.adp]
command = "$mcpPath"
startup_timeout_sec = 20
"@
        }

        Set-Content -Path $config -Value $text -Encoding UTF8
    }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($userPath -split ';' | Where-Object { $_ })
    if ($parts -notcontains $installDir) {
        [Environment]::SetEnvironmentVariable("Path", (($parts + $installDir) -join ";"), "User")
    }

    Write-Host "Codex Unchained installed."
    Write-Host "Default routing: /adp auto"
    Write-Host ""
    Write-Host "Provider setup:"
    Write-Host "  Ollama login:  adp-unchained auth ollama"
    Write-Host "  Ollama API:    set OLLAMA_API_KEY, then run adp-unchained auth ollama --method api"
    Write-Host "  Antigravity:   adp-unchained auth agy"
    Write-Host ""
    Write-Host "Open a new terminal and run: codex-unchained"
}
finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
) {
            $text += @"

[mcp_servers.adp]
command = "$mcpPath"
startup_timeout_sec = 20
"@
        }

        Set-Content -Path $config -Value $text -Encoding UTF8
    }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($userPath -split ';' | Where-Object { $_ })
    if ($parts -notcontains $installDir) {
        [Environment]::SetEnvironmentVariable("Path", (($parts + $installDir) -join ";"), "User")
    }

    Write-Host "Codex Unchained installed."
    Write-Host "Default routing: /adp auto"
    Write-Host ""
    Write-Host "Provider setup:"
    Write-Host "  Ollama login:  adp-unchained auth ollama"
    Write-Host "  Ollama API:    set OLLAMA_API_KEY, then run adp-unchained auth ollama --method api"
    Write-Host "  Antigravity:   adp-unchained auth agy"
    Write-Host ""
    Write-Host "Open a new terminal and run: codex-unchained"
}
finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
