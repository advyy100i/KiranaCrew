# Demo failover: drop the tunnel/webhook and long-poll instead (no inbound connectivity needed).
$root = Split-Path -Parent $PSScriptRoot
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Set-Location (Join-Path $root "backend")
python -m app.cli.poll
