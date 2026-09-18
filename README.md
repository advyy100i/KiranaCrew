# KiranaCrew

Telegram voice bookkeeping for a kirana shop. A Hindi/Hinglish voice note goes in
("Ramesh Kumar ne paanch kilo chawal udhaar pe liya"); a validated stock movement and credit-ledger entry come out.
Runs entirely on a laptop (Docker + faster-whisper + Ollama), ₹0, offline-capable — the only thing that needs the
internet is Telegram itself.

**The rule that shapes everything:** the LLM is never allowed near the arithmetic or the IDs. Rules parse most
sentences; an LLM extracts *mentions* for the tail, every number it returns must already be in the transcript, and
`money.py` prices in integer paise. Ambiguity becomes a Telegram button, never a guess.

```
Telegram ──► /telegram/webhook ──► messages (idempotency_key UNIQUE) + jobs ──► 200 OK
                                        │
              worker (FOR UPDATE SKIP LOCKED) ──► STT ──► normalize ──► rules ─┬─► resolve ──► decide ──► post (1 txn)
                                                                     LLM (tail) ┘                    └──► pending_actions + buttons
```

## Quick start (Windows, fresh clone → working bot in ~10 minutes)

```powershell
winget install -e --id Python.Python.3.12; winget install -e --id Docker.DockerDesktop
winget install -e --id Ollama.Ollama; winget install -e --id Cloudflare.cloudflared   # cloudflared optional
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt -r backend\requirements-tools.txt      # tools file is optional (whisper, forecast)
copy .env.example .env      # fill TELEGRAM_BOT_TOKEN (BotFather), set ADMIN_API_KEY / TELEGRAM_WEBHOOK_SECRET
ollama pull qwen2.5:3b

docker compose -f docker\docker-compose.yml up -d db            # Postgres on 127.0.0.1:5433
cd backend
python -m app.cli.migrate
python -m app.cli.seed_demo --reset                              # shop, 8 customers, 12 products, 12 weeks of history
python -m uvicorn app.main:app --port 8000                       # terminal 1
python -m app.cli.worker                                         # terminal 2 (loads Whisper once)
```

Register yourself: send `/start` to the bot, copy the user id, then
`python -m app.cli.register_shop --telegram-user-id <id>`. Receive updates either way:

```powershell
cloudflared tunnel --url http://localhost:8000                   # A) webhook: prints https://xxx.trycloudflare.com
python -m app.cli.set_webhook --url https://xxx.trycloudflare.com/telegram/webhook
python -m app.cli.poll                                           # B) long polling, no tunnel, no inbound connectivity
```

Or everything at once: `.\scripts\start_demo.ps1` (Docker stack + migrate + seed + tunnel + webhook), then
`.\scripts\demo_check.ps1` (the T-30-minute checklist, automated).

No Telegram? The simulator runs the identical pipeline:

```powershell
curl -X POST http://127.0.0.1:8000/dev/simulate -H "X-Admin-Key: <ADMIN_API_KEY>" -H "Content-Type: application/json" `
     -d '{"text":"Ramesh ne 2 kilo cheeni udhaar liya"}'        # -> buttons as JSON; answer with /dev/simulate/choose
curl http://127.0.0.1:8000/dev/trace/<message_id> -H "X-Admin-Key: ..."   # transcript -> normalized -> parsed -> decision -> rows
```

## What is where

| Path | Role |
|---|---|
| `backend/app/domain/` | pure functions, no DB/network: `normalize`, `rules_parser`, `llm_parser` (+ grounding), `resolve`, `decide`, `money` |
| `backend/app/services/` | `pipeline` (three short transactions per message), `bookkeeping` (the only writer to ledgers), `confirmations`, `jobs`, `replies`, `queries` |
| `backend/app/stt/`, `app/llm/` | provider interfaces: faster-whisper / Groq / fixture; Ollama / Groq / Gemini |
| `backend/app/api/` | `telegram` webhook, `dev` simulator + trace, `internal` (n8n), `dashboard` (JWT, read-only) |
| `backend/app/cli/` | `migrate seed_demo register_shop set_webhook poll worker replay eval forecast insights` |
| `backend/migrations/` | SQL schema (001) and the read-only role for n8n (002) |
| `eval/` | `dataset.jsonl` (130), `heldout.jsonl` (55, written without looking at the parser), `results/`, `make_report.py` |
| `workflows/` | four n8n schedules, exported JSON |
| `frontend/` | Flutter web dashboard (226 lines), served at `/dashboard` after `flutter build web` |
| `docs/` | `eval_report.md`, `runbook.md`, `decisions/ADR-00x.md` |
| `scripts/` | `start_demo.ps1 demo_check.ps1 stop_demo.ps1 backup_db.ps1 switch_to_local.ps1` |

## Tests and evaluation

```powershell
cd backend
python -m pytest -q                                              # unit + integration (needs the Docker Postgres; creates kirana_test)
python -m app.cli.eval --dataset ..\eval\heldout.jsonl --catalog ..\eval\seed_catalog.json --parser rules --errors
python -m app.cli.eval --dataset ..\eval\heldout.jsonl --catalog ..\eval\seed_catalog.json --parser hybrid --provider ollama
python -m app.cli.replay --update ..\demo\updates\credit_sale.json --times 10 --concurrency 5 --fresh   # idempotency demo
```

Numbers, per parser mode and dataset, are in [`docs/eval_report.md`](docs/eval_report.md). The metric that matters
is the **false-update rate** (committed when the gold says ask/reject, or committed different values): 0 on both sets.

## The catalog grows from the chat

- Say an unknown item → **➕ Naya item** → the bot asks only what it still needs (unit is skipped when you said
  "packet"/"kilo"; rate can be skipped) → the entry is booked and the word you used becomes the alias.
- Near-miss spellings get **"Refined Oil?"**-style buttons; picking one teaches that spelling.
- Unknown customer → **➕ Naya customer**.
- `/add_product` and `/add_customer` are wizards, not syntax: `/add_product Maggi packet 14`, `/add_product 14 rs maggi pkt`,
  `/add_product Maggi` (asks unit, then rate), or bare `/add_product` (asks name). Pack sizes are understood
  (`Bournvita 500 g packet 250` → item "Bournvita 500g").
- `/alias Rice chaval` · `/price Rice 52` · `/remove_product Maggi` · `/products` · `/customers`. Owner only; all audited.

## Demo-day switches (env only, zero code changes)

| Problem | Switch |
|---|---|
| tunnel died | `.\scripts\start_demo.ps1 -TunnelOnly` or `python -m app.cli.poll` |
| Whisper slow | `STT_PROVIDER=groq` (needs `GROQ_API_KEY`), or `STT_PROVIDER=fixture` with a pre-recorded clip |
| Ollama slow / GPU cannot pin memory | `PARSER_MODE=rules`, or `LLM_PROVIDERS=groq`, or `OLLAMA_NUM_GPU=0` |
| Telegram down | `/dev/simulate` + `/dev/trace` |

## Decisions

Why Telegram, why rules first, why derived balances and five-layer idempotency, why n8n is read-only, why agents are
kept out of bookkeeping: [`docs/decisions/`](docs/decisions/).
