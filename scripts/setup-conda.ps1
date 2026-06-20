param(
    [string]$EnvName = "deepseek-agent",
    [switch]$InitEnvFile
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$env:CONDARC = Join-Path $RepoRoot "condarc.project.yml"

$EnvExists = $false
$EnvList = conda --no-plugins env list --json | ConvertFrom-Json
foreach ($Prefix in $EnvList.envs) {
    if ((Split-Path -Leaf $Prefix) -eq $EnvName) {
        $EnvExists = $true
        break
    }
}

if ($EnvExists) {
    Write-Host "Updating conda environment '$EnvName' from environment.yml..."
    conda --no-plugins env update --name $EnvName --file environment.yml --prune
} else {
    Write-Host "Creating conda environment '$EnvName' from environment.yml..."
    conda --no-plugins env create --name $EnvName --file environment.yml
}

if ($InitEnvFile -and -not (Test-Path "app/.env")) {
    Copy-Item ".env.example" "app/.env"
    Write-Host "Created app/.env from .env.example"
}

Write-Host ""
Write-Host "Next:"
Write-Host "  conda activate $EnvName"
Write-Host "  pytest"
