# Bring the whole stack up from cold. Usage:
#   .\scripts\start_demo.ps1                 # db + api + worker (Docker), migrate, seed if empty, tunnel + webhook
#   .\scripts\start_demo.ps1 -TunnelOnly     # new cloudflared tunnel + set_webhook (when the old tunnel died)
#   .\scripts\start_demo.ps1 -NoTunnel       # everything except the tunnel (use app.cli.poll instead)
#   .\scripts\start_demo.ps1 -Tools          # also n8n
#   .\scripts\start_demo.ps1 -Reseed         # wipe and reseed the demo data
param([switch]$TunnelOnly, [switch]$NoTunnel, [switch]$Tools, [switch]$Reseed, [switch]$Local)
# native tools (docker, python) write progress to stderr; under "Stop" PowerShell 5.1 would treat that as fatal
$ErrorActionPreference = "Continue"
function Step($label, [scriptblock]$cmd) {
    Write-Host "== $label"
    & $cmd 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "$label failed (exit $LASTEXITCODE)" }
}
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "created .env from .env.example - fill in TELEGRAM_BOT_TOKEN" }

function Start-Tunnel {
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
    $log = Join-Path $env:TEMP "cloudflared.log"
    if (Test-Path $log) { Remove-Item $log }
    Start-Process cloudflared -ArgumentList "tunnel --url http://localhost:8000" -RedirectStandardError $log -WindowStyle Hidden
    $url = $null
    for ($i = 0; $i -lt 30 -and -not $url; $i++) {
        Start-Sleep 1
        if (Test-Path $log) { $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
            if ($m) { $url = $m.Matches[0].Value } }
    }
    if (-not $url) { throw "cloudflared did not print a URL (see $log)" }
    $env:TUNNEL_URL = $url
    Write-Host "tunnel: $url"
    Push-Location backend
    Step "set_webhook" { python -m app.cli.set_webhook --url "$url/telegram/webhook" }
    python -m app.cli.set_webhook --info 2>&1 | ForEach-Object { "$_" }
    Pop-Location
}

if ($TunnelOnly) { Start-Tunnel; exit 0 }

if ($Local) {
    # DB in Docker, API + worker as host processes (fast reloads, host Whisper cache)
    Step "db" { docker compose -f docker/docker-compose.yml up -d db }
} else {
    Step "db + api + worker" { docker compose -f docker/docker-compose.yml up -d --build db api worker }
    if ($Tools) { Step "n8n" { docker compose -f docker/docker-compose.yml --profile tools up -d n8n } }
}
Push-Location backend
$env:PYTHONIOENCODING = "utf-8"
Step "migrate" { python -m app.cli.migrate }
if ($Reseed) { Step "seed --reset" { python -m app.cli.seed_demo --reset } } else { Step "seed" { python -m app.cli.seed_demo } }
if ($Local) {
    Start-Process python -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" -WindowStyle Minimized -RedirectStandardError "$env:TEMP\kirana-api.log"
    Start-Process python -ArgumentList "-m app.cli.worker" -WindowStyle Minimized -RedirectStandardError "$env:TEMP\kirana-worker.log"
}
Pop-Location

# wait for /readyz
$ready = $false
for ($i = 0; $i -lt 60 -and -not $ready; $i++) {
    Start-Sleep 2
    try { $r = Invoke-RestMethod http://127.0.0.1:8000/readyz; $ready = $r.db -and $r.worker } catch {}
}
Invoke-RestMethod http://127.0.0.1:8000/readyz | ConvertTo-Json -Compress
if (-not $NoTunnel) {
    if (Get-Command cloudflared -ErrorAction SilentlyContinue) { Start-Tunnel }
    else { Write-Host "cloudflared not installed: winget install -e --id Cloudflare.cloudflared  (or run: python -m app.cli.poll)" }
}
Write-Host "up. dev stats: curl http://127.0.0.1:8000/dev/stats -H 'X-Admin-Key: <ADMIN_API_KEY>'"
