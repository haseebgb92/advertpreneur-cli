$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -c "import prompt_toolkit, rich" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing terminal dependency..." -ForegroundColor DarkGray
    python -m pip install -r requirements.txt
}

$project = if ($args.Count -gt 0) { $args[0] } else { "." }
python advertpreneur.py --project $project
