$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $project

if (-not $env:TERI_ADMIN_TOKEN) {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $env:TERI_ADMIN_TOKEN = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    Write-Host "Temporary admin token for this run: $env:TERI_ADMIN_TOKEN" -ForegroundColor Yellow
}

$python = if (Test-Path -LiteralPath (Join-Path $project '.venv\Scripts\python.exe')) { Join-Path $project '.venv\Scripts\python.exe' } else { 'python' }
& $python (Join-Path $project 'server.py')
