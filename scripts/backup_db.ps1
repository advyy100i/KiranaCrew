# pg_dump the demo database to demo/backup_<date>.sql
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root ("demo/backup_" + (Get-Date -Format "yyyyMMdd_HHmm") + ".sql")
docker compose -f (Join-Path $root "docker/docker-compose.yml") exec -T db pg_dump -U kirana kirana | Out-File -Encoding utf8 $out
Write-Host "wrote $out"
