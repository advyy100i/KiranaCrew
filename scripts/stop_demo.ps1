# Stop everything (containers keep their volumes; data survives).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "app.cli.worker|uvicorn app.main:app|app.cli.poll" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
docker compose -f docker/docker-compose.yml --profile tools down
Write-Host "stopped. (volumes kept; use 'docker compose -f docker/docker-compose.yml down -v' to wipe)"
