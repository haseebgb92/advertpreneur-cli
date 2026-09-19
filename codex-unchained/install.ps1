$ErrorActionPreference = "Stop"
$repo = if ($env:CODEX_UNCHAINED_REPOSITORY) { $env:CODEX_UNCHAINED_REPOSITORY } else { "haseebgb92/advertpreneur-cli" }
$version = if ($env:CODEX_UNCHAINED_VERSION) { $env:CODEX_UNCHAINED_VERSION } else { "latest" }
$installDir = if ($env:CODEX_UNCHAINED_INSTALL_DIR) { $env:CODEX_UNCHAINED_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "CodexUnchained\bin" }
$asset = "patched-codex-windows-x64"
$base = if ($version -eq "latest") { "https://github.com/$repo/releases/latest/download" } else { "https://github.com/$repo/releases/download/$version" }
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("codex-unchained-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force $tmp | Out-Null
try {
  $archive = Join-Path $tmp "package.tar.gz"
  Invoke-WebRequest "$base/$asset.tar.gz" -OutFile $archive
  tar -xzf $archive -C $tmp
  New-Item -ItemType Directory -Force $installDir | Out-Null
  Copy-Item (Join-Path $tmp "$asset\*") $installDir -Recurse -Force
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if (($userPath -split ';') -notcontains $installDir) {
    [Environment]::SetEnvironmentVariable("Path", (($userPath.TrimEnd(';') + ';' + $installDir).Trim(';')), "User")
  }
  Write-Host "Codex Unchained installed. Open a new terminal and run: codex-unchained"
} finally { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
