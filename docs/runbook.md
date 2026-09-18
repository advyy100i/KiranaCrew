# Runbook

## Start / stop
- `.\scripts\start_demo.ps1` — Docker db+api+worker, migrate, seed (if empty), cloudflared tunnel, set_webhook, wait for `/readyz`.
  `-Local` runs api/worker as host processes (faster restarts, host Whisper cache). `-Tools` adds n8n. `-Reseed` wipes data.
- `.\scripts\stop_demo.ps1` — stops everything, keeps volumes.
- `.\scripts\demo_check.ps1` — readyz, stats, one simulated entry + undo, webhook info. Exit 0 = all green.

## Health
- `GET /healthz` process alive. `GET /readyz` → `db`, `worker` (heartbeat < 60 s), `stt.loaded`, `llm.reachable`.
- `GET /dev/stats` (admin key) → row counts, `queue_depth`, `failed_messages`, `dead_jobs`, parser split, worker last seen.
- `GET /dev/trace/{message_id}` → every stage plus `llm_calls`, rows written, pending actions, job attempts, audit.

## Failure → what to do
| Symptom | Action |
|---|---|
| bot silent, `set_webhook --info` shows `last_error_message` | tunnel died: `start_demo.ps1 -TunnelOnly`, or `python -m app.cli.poll` (deletes the webhook first; only one consumer at a time) |
| replies take > 20 s | `STT_PROVIDER=groq` or `fixture`; `PARSER_MODE=rules`; restart worker. Send one warm-up note first — model load is the slow part |
| Ollama: `unable to allocate CUDA_Host buffer` | `OLLAMA_NUM_GPU=0` (CPU inference) or free RAM; `PARSER_MODE=rules` still commits most sentences |
| `queue_depth` growing | worker down: `docker compose restart worker` / rerun `app.cli.worker`. Nothing is lost; the reaper re-queues stale RUNNING jobs after 5 min |
| `dead_jobs > 0` | `/dev/trace/<id>` → `jobs.last_error`; the message is FAILED and the user got a reply; fix and `/dev/simulate` the transcript |
| wrong entry booked | Undo button (15 min window) or `POST /dev/simulate/undo {"transaction_id": N}` — inserts a REVERSAL, deletes nothing |
| Postgres port 5432 busy | compose maps `127.0.0.1:5433`; `.env` already points there |
| `localhost` hangs on Windows | use `127.0.0.1` (Docker binds IPv4 only; `localhost` resolves to `::1` first) |

## Backups
`.\scripts\backup_db.ps1` → `demo/backup_<date>.sql`. Restore: `docker compose exec -T db psql -U kirana kirana < file.sql`.
The seed is deterministic (`--reset` reproduces rice = 100 kg), so a reseed is usually enough.

## Security reminders
Webhook secret + member allowlist protect the public tunnel URL — never disable them "for the demo". Close the tunnel
afterwards. Postgres, n8n and the API bind to 127.0.0.1. n8n uses the `kirana_ro` role (SELECT only).
