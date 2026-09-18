# T-30 minutes checklist, automated. Exits non-zero if anything is red.
param([string]$Api = "http://127.0.0.1:8000")
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$envs = Get-Content (Join-Path $root ".env") | Where-Object { $_ -match "^ADMIN_API_KEY=" }
$key = ($envs -split "=", 2)[1]
$ok = $true
$r = Invoke-RestMethod "$Api/readyz"
Write-Host ("readyz: db={0} worker={1} stt={2}/{3} llm={4} reachable={5}" -f $r.db, $r.worker, $r.stt.provider, $r.stt.loaded, $r.llm.provider, $r.llm.reachable)
if (-not ($r.db -and $r.worker)) { $ok = $false }
$s = Invoke-RestMethod "$Api/dev/stats" -Headers @{ "X-Admin-Key" = $key }
Write-Host ("stats: queue_depth={0} failed_messages={1} dead_jobs={2} parser_split={3}" -f $s.queue_depth, $s.failed_messages, $s.dead_jobs, ($s.parser_split | ConvertTo-Json -Compress))
if ($s.queue_depth -gt 0 -or $s.dead_jobs -gt 0) { $ok = $false }
$sim = Invoke-RestMethod -Method Post "$Api/dev/simulate" -Headers @{ "X-Admin-Key" = $key } -ContentType "application/json" `
    -Body '{"text":"Ramesh Kumar ne 5 kilo chawal udhaar pe liya"}'
Write-Host ("simulate: {0} -> {1}" -f $sim.status, ($sim.reply -split "`n")[0])
if ($sim.status -ne "COMMITTED") { $ok = $false }
if ($sim.transaction_id) { Invoke-RestMethod -Method Post "$Api/dev/simulate/undo" -Headers @{ "X-Admin-Key" = $key } -ContentType "application/json" -Body ("{""transaction_id"":" + $sim.transaction_id + "}") | Out-Null; Write-Host "undo: ok (warm-up entry reversed)" }
Push-Location (Join-Path $root "backend")
try { python -m app.cli.set_webhook --info | Select-String -Pattern '"url"|pending_update_count|last_error_message' } catch { Write-Host "webhook info unavailable (no token?)" }
Pop-Location
if ($ok) { Write-Host "ALL GREEN" -ForegroundColor Green; exit 0 } else { Write-Host "SOMETHING IS RED" -ForegroundColor Red; exit 1 }
