KiranaCrew — implementation plan (local-first, ₹0)
Telegram voice bookkeeping for a kirana shop. Hindi/Hinglish voice note in, validated ledger entry out. ₹0 budget, Windows laptop, must survive an interview demo.

Plan change from v1: the system no longer has to live on a public free host. It runs as a Docker stack on your laptop, and is optionally exposed to the internet through a temporary tunnel when you want a live phone demo. That removes the 512 MB / 0.1 CPU ceiling, which in turn buys back local Whisper, a local LLM, a real job queue, n8n automations and a CrewAI insights crew — all still ₹0.

Target: locally runnable MVP with production-style data integrity, demonstrable from a phone.

1. Reality check
What ₹0 buys you locally (this is the new baseline)

Your own laptop: 8–16 GB RAM, real CPU, no sleep, no request cap. Whisper and a small LLM both fit.
Docker Desktop: Postgres, API, worker, n8n, all on one docker compose up.
Telegram Bot API: free, and a temporary cloudflared tunnel gives it an HTTPS URL for as long as you need one.
Hosted free tiers become fallbacks, not the foundation: Groq STT/LLM when the laptop is slow or offline from itself.
What is still not free / not solved

Want	Reality	What you do
24×7 public bot	Your laptop is not a server; free hosts sleep (Render: 15 min idle, ~1 min wake, 512 MB)	Run the tunnel only during a demo; mark the project "runs locally" in the README
Real-time STT on a weak laptop	large-v3-turbo int8 on CPU is roughly real-time; a 10 s note can take 8–15 s on 4 cores	Measure on your machine in Phase 9; drop to small/medium, or use Groq, if it is too slow
A 7B+ local LLM	4-bit 7B needs ~5–6 GB RAM and is slow on CPU	Use a 2–4B class model; the LLM only sees sentences the rules failed on
Backups, uptime, SLA	Not applicable to a laptop	pg_dump before demo day; the seed script makes data reproducible
Real shop data	Free hosted tiers may train on your prompts	With Ollama + faster-whisper, nothing leaves the laptop — say this, it is a genuine privacy property
The real risk is unchanged: Hindi/Hinglish accuracy on names and quantities. Spend your time on the evaluation set (§11), not on infrastructure.

Scope for v1: one shop, one transaction per voice note, Hindi/Hinglish only, no returns.

Effort: ~6–7 weeks at 2 h/day. Core (Phases 0–11) ≈ 4 weeks; the tooling tail (queue, n8n, crew, forecasting, tracing) is another 2–3 weeks and is what makes the stack look senior.

2. KEEP / REPLACE / ADD from your stack
KEEP

Telegram Bot API over raw HTTPS (you control webhook, secret token, retries).
FastAPI + Pydantic v2 + PostgreSQL.
The core rule: LLM → structured command → Pydantic → deterministic service → DB transaction.
Idempotency and human confirmation as first-class features — still the most interesting part of the project.
Flutter, as a small read-only dashboard, built late.
REPLACE

Your item	Replace with	Why
"STT"	STTProvider: faster-whisper local (primary) / Groq hosted (fallback) / fixture (tests)	Local compute is free now; no rate limit, no quota risk on demo day, nothing leaves the laptop
"Parser"	Rule parser first → local LLM via Ollama → Groq/Gemini fallback, plus a number-grounding check	Rules handle the formulaic majority at zero cost; the LLM earns only the tail
LLM resolves customer/product	LLM returns mentions; code resolves IDs via alias + fuzzy match	An LLM picking a customer ID is an unauditable money decision
Background tasks	jobs table + worker process using FOR UPDATE SKIP LOCKED	Real at-least-once semantics, retries with backoff, dead-letter, visible queue depth — and it survives a restart
Supabase as a platform	Plain Postgres in Docker (Supabase optional, as a remote mirror)	One less lock-in; identical SQL either way
stock / balance columns	Append-only inventory_movements + credit_ledger, balances derived by SUM	No drift, free audit trail, trivial reversal
ADD — tools that now fit, all strictly outside the money path

Tool	Job	Boundary rule
n8n (self-hosted Docker)	daily 9 pm summary, low-stock watcher, weekly digest, alert when a message lands in FAILED, nightly forecast trigger	Scheduled reads and notifications only. If n8n is down, not one transaction is lost or delayed
CrewAI (Flow + 3 agents)	weekly shop-insights note: credit-risk flags, slow-moving stock, plain-Hindi advice	Reads pre-computed aggregates as tool output, writes to insights, never to transactions
StatsForecast	7-day demand forecast per product	Offline nightly job; shown only if it beats a seasonal-naive baseline
Ollama	local parser LLM + a /ask natural-language query command	Same ParsedCommand contract and grounding check as any hosted model
LLM call log (llm_calls table + /dev/trace)	latency, tokens, model, prompt version, cost-if-hosted per call	Langfuse Cloud's free tier is an optional upgrade; self-hosted Langfuse v3 needs ClickHouse + Redis + MinIO, too heavy for a laptop
STILL REMOVED: LLM-generated SQL, WhatsApp, Redis/Celery, Kubernetes, microservices, agents anywhere inside bookkeeping.

Six gaps in your proposed pipeline

Idempotency comes first, not last. Claim the message in the DB before STT, or a Telegram retry costs you a second transcription and can double-post.
A missing step between Pydantic and the transaction service: entity resolution. Pydantic validates shape, not that "Ramesh" exists or is unique.
Confirmation needs persisted state (pending_actions), otherwise a button press hours later has nothing to resume.
The normalizer must also run on replies ("dhai sau" as an answer to "how much?").
Do not do STT inside the webhook request. Claim → enqueue a job → return 200 → a worker processes it → a sweeper re-queues anything stuck.
Never hold a DB transaction open while calling Telegram. Commit, then send.
3. Final architecture
Everything below runs on your laptop under one docker compose up.

secret token + member check

duplicate

new

incomplete

complete

ASK

REJECT

COMMIT

callback

read-only

read-only

read-only

Telegram voice or text

cloudflared tunnel
only during a demo

FastAPI /telegram/webhook

Claim message
UNIQUE idempotency_key

200 OK, do nothing

jobs table

200 OK immediately

Worker: FOR UPDATE SKIP LOCKED

STTProvider
faster-whisper local / Groq / fixture

Normalizer

Rule parser

LLM parser: Ollama, then Groq/Gemini
+ grounding check

ParsedCommand

Entity resolution
alias + fuzzy, no guessing

Decision

pending_actions + Telegram buttons

Help reply

Transaction service
one DB transaction

PostgreSQL

Telegram confirmation + Undo

Flutter dashboard

n8n schedules
summary, low stock, alerts

CrewAI insights Flow
weekly note

StatsForecast nightly

The dotted edges are the boundary that matters: n8n, CrewAI and forecasting only read, and only write to their own tables. Kill all three and bookkeeping is unaffected.

Credit sale, end to end:

sequenceDiagram
    participant U as Shopkeeper
    participant T as Telegram
    participant API as FastAPI
    participant DB as Postgres
    participant S as STT/LLM
    U->>T: voice "Ramesh ne 5 kilo chawal udhaar pe liya"
    T->>API: POST update (may be retried)
    API->>DB: INSERT message ON CONFLICT DO NOTHING; INSERT job
    API-->>T: 200 OK
    Note over API: worker picks the job up (SKIP LOCKED)
    API->>S: transcribe (faster-whisper local, Groq fallback)
    API->>API: normalize -> rules parse -> resolve
    Note over API: "Ramesh" matches 2 customers
    API->>DB: pending_action PENDING, status AWAITING_CONFIRMATION
    API->>T: "Kaunse Ramesh?" [Ramesh Kumar][Ramesh Sharma]
    U->>T: taps Ramesh Kumar
    T->>API: callback_query
    API->>DB: BEGIN; resolve pending (status=PENDING guard);<br/>INSERT transaction (UNIQUE message_id);<br/>INSERT movement; INSERT ledger; message=COMMITTED; COMMIT
    API->>T: "Udhaar: Ramesh Kumar - Rice 5 kg x Rs 50 = Rs 250" [Undo]
4. ₹0 cost breakdown
Everything in the core column runs on your laptop and costs nothing, ever. Hosted limits verified Sept 2026; recheck before relying on a fallback.

Component	Core (local, always used)	Fallback / optional (hosted free tier)
API + worker	FastAPI + Uvicorn in Docker	Render free web service: 512 MB, 0.1 CPU, sleeps after 15 min idle, ~1 min cold start
Database	Postgres 16 in Docker, volume-backed	Supabase free: 500 MB, paused after ~7 days idle, no backups
STT	faster-whisper int8 on CPU — unlimited, offline	Groq whisper-large-v3-turbo: 20 req/min, 2,000 req/day, 28,800 audio-sec/day, 25 MB file cap, ogg accepted
Parser LLM	Ollama, 2–4B class model, offline	Groq chat free tier, then Gemini Flash-Lite free tier
Automations	n8n self-hosted in Docker	GitHub Actions cron (if you ever deploy)
Insights	CrewAI Flow pointed at your local Ollama	any free API key
Forecasting	StatsForecast, nightly, local	—
Tracing	llm_calls table + /dev/trace	Langfuse Cloud free tier
Bot transport	Telegram Bot API (free) — webhook via cloudflared quick tunnel, or long-polling with no tunnel at all	—
Dashboard	Flutter web served locally	GitHub Pages
Total	₹0	₹0
Two facts to know about the tunnel: a cloudflared quick tunnel needs no account and gives a random *.trycloudflare.com HTTPS URL that dies when you close it, so re-run set_webhook every session; and if you skip the tunnel entirely, app.cli.poll (long polling) works with no inbound connectivity at all.

5. Database design
One schema, public, on Postgres 16. Money is paise as BIGINT — no floats anywhere. Quantity is NUMERIC(12,3) in the product's base unit.

-- migrations/001_init.sql
CREATE TABLE shops (
  id               BIGSERIAL PRIMARY KEY,
  name             TEXT NOT NULL,
  language         TEXT NOT NULL DEFAULT 'hi',
  confirm_above_paise BIGINT NOT NULL DEFAULT 500000,   -- ask before booking > Rs 5,000
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE shop_members (                             -- who is allowed to write to this shop
  telegram_user_id BIGINT PRIMARY KEY,
  shop_id          BIGINT NOT NULL REFERENCES shops(id),
  role             TEXT NOT NULL DEFAULT 'OWNER' CHECK (role IN ('OWNER','STAFF')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE customers (
  id               BIGSERIAL PRIMARY KEY,
  shop_id          BIGINT NOT NULL REFERENCES shops(id),
  name             TEXT NOT NULL,
  aliases          TEXT[] NOT NULL DEFAULT '{}',        -- Devanagari spelling, nicknames
  phone            TEXT,
  is_active        BOOLEAN NOT NULL DEFAULT true,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (shop_id, name),
  UNIQUE (shop_id, id)                                  -- target for composite FKs below
);

CREATE TABLE products (
  id                  BIGSERIAL PRIMARY KEY,
  shop_id             BIGINT NOT NULL REFERENCES shops(id),
  name                TEXT NOT NULL,
  aliases             TEXT[] NOT NULL DEFAULT '{}',
  base_unit           TEXT NOT NULL CHECK (base_unit IN ('kg','litre','piece','packet')),
  selling_price_paise BIGINT CHECK (selling_price_paise > 0),   -- NULL = must ask the user
  low_stock_threshold NUMERIC(12,3),
  is_active           BOOLEAN NOT NULL DEFAULT true,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (shop_id, name),
  UNIQUE (shop_id, id)
);

CREATE TABLE messages (
  id                  BIGSERIAL PRIMARY KEY,
  shop_id             BIGINT NOT NULL REFERENCES shops(id),
  idempotency_key     TEXT NOT NULL UNIQUE,             -- 'tg:<chat_id>:<message_id>' or 'sim:<uuid>'
  source              TEXT NOT NULL CHECK (source IN ('TELEGRAM','SIMULATOR')),
  telegram_chat_id    BIGINT,
  telegram_message_id BIGINT,
  kind                TEXT NOT NULL CHECK (kind IN ('VOICE','TEXT')),
  telegram_file_id    TEXT,
  audio_duration_s    INT,
  transcript          TEXT,
  normalized_text     TEXT,
  parsed              JSONB,
  parser_used         TEXT,                             -- 'rules' | 'llm' | 'llm_fallback_failed'
  stt_provider        TEXT,
  stt_confidence      REAL,
  status              TEXT NOT NULL DEFAULT 'RECEIVED'
                        CHECK (status IN ('RECEIVED','PROCESSING','AWAITING_CONFIRMATION',
                                          'COMMITTED','REJECTED','FAILED')),
  error               TEXT,
  attempts            INT NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_messages_open ON messages (status, updated_at)
  WHERE status IN ('RECEIVED','PROCESSING');
CREATE INDEX ix_messages_shop_time ON messages (shop_id, created_at DESC);

CREATE TABLE transactions (
  id                BIGSERIAL PRIMARY KEY,
  shop_id           BIGINT NOT NULL REFERENCES shops(id),
  message_id        BIGINT REFERENCES messages(id),
  type              TEXT NOT NULL CHECK (type IN ('CREDIT_SALE','CASH_SALE','CREDIT_REPAYMENT',
                                                  'INVENTORY_PURCHASE','STOCK_ADJUSTMENT','REVERSAL')),
  customer_id       BIGINT,
  product_id        BIGINT,
  quantity          NUMERIC(12,3) CHECK (quantity > 0),
  unit              TEXT,
  unit_price_paise  BIGINT,
  amount_paise      BIGINT CHECK (amount_paise >= 0),
  price_source      TEXT CHECK (price_source IN ('CATALOG','EXPLICIT_TOTAL','EXPLICIT_UNIT','USER_REPLY')),
  reverses_transaction_id BIGINT UNIQUE REFERENCES transactions(id),   -- can reverse only once
  created_by        TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (message_id),                                  -- one message can never post twice
  UNIQUE (shop_id, id),
  FOREIGN KEY (shop_id, customer_id) REFERENCES customers(shop_id, id),
  FOREIGN KEY (shop_id, product_id)  REFERENCES products(shop_id, id)
);
CREATE INDEX ix_txn_shop_time ON transactions (shop_id, created_at DESC);

CREATE TABLE inventory_movements (
  id             BIGSERIAL PRIMARY KEY,
  shop_id        BIGINT NOT NULL,
  transaction_id BIGINT NOT NULL,
  product_id     BIGINT NOT NULL,
  qty_delta      NUMERIC(12,3) NOT NULL CHECK (qty_delta <> 0),   -- signed
  reason         TEXT NOT NULL CHECK (reason IN ('SALE','PURCHASE','DAMAGED','EXPIRED','LOST',
                                                 'FOUND','CORRECTION','REVERSAL')),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (shop_id, transaction_id) REFERENCES transactions(shop_id, id),
  FOREIGN KEY (shop_id, product_id)     REFERENCES products(shop_id, id)
);
CREATE INDEX ix_inv_shop_product ON inventory_movements (shop_id, product_id);

CREATE TABLE credit_ledger (
  id             BIGSERIAL PRIMARY KEY,
  shop_id        BIGINT NOT NULL,
  transaction_id BIGINT NOT NULL,
  customer_id    BIGINT NOT NULL,
  amount_paise   BIGINT NOT NULL CHECK (amount_paise <> 0),   -- + owes more, - paid back
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (shop_id, transaction_id) REFERENCES transactions(shop_id, id),
  FOREIGN KEY (shop_id, customer_id)    REFERENCES customers(shop_id, id)
);
CREATE INDEX ix_ledger_shop_customer ON credit_ledger (shop_id, customer_id);

CREATE TABLE pending_actions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  shop_id          BIGINT NOT NULL REFERENCES shops(id),
  message_id       BIGINT NOT NULL REFERENCES messages(id),
  kind             TEXT NOT NULL CHECK (kind IN ('CHOOSE_CUSTOMER','CHOOSE_PRODUCT','CHOOSE_PAYMENT',
                                                 'CONFIRM_NEW_CUSTOMER','CONFIRM_TRANSCRIPT',
                                                 'CONFIRM_LARGE_AMOUNT','ENTER_TEXT')),
  field            TEXT,                                  -- for ENTER_TEXT: price|quantity|customer
  draft            JSONB NOT NULL,                        -- the partially resolved command
  options          JSONB NOT NULL DEFAULT '[]',
  status           TEXT NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING','RESOLVED','CANCELLED','EXPIRED')),
  telegram_chat_id BIGINT,
  expires_at       TIMESTAMPTZ NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX ux_pending_one_per_message ON pending_actions (message_id) WHERE status = 'PENDING';
CREATE INDEX ix_pending_open_chat ON pending_actions (telegram_chat_id) WHERE status = 'PENDING';

CREATE TABLE jobs (                                     -- durable work queue, replaces BackgroundTasks
  id           BIGSERIAL PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('PROCESS_MESSAGE','RESOLVE_CALLBACK','DAILY_SUMMARY',
                                             'FORECAST','INSIGHTS')),
  payload      JSONB NOT NULL,
  dedupe_key   TEXT UNIQUE,                            -- 'msg:<message_id>' => enqueued at most once
  status       TEXT NOT NULL DEFAULT 'QUEUED'
                 CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED','DEAD')),
  attempts     INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 5,
  run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),      -- exponential backoff target
  last_error   TEXT,
  locked_at    TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_jobs_ready ON jobs (run_after) WHERE status = 'QUEUED';

CREATE TABLE llm_calls (                                -- one row per STT/LLM call, for the trace view
  id            BIGSERIAL PRIMARY KEY,
  message_id    BIGINT REFERENCES messages(id),
  stage         TEXT NOT NULL CHECK (stage IN ('STT','PARSE','INSIGHTS','ASK')),
  provider      TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT,
  latency_ms    INT NOT NULL, input_tokens INT, output_tokens INT,
  ok            BOOLEAN NOT NULL, error TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_llm_calls_msg ON llm_calls (message_id);

CREATE TABLE forecasts (                                -- written by the nightly StatsForecast job only
  id           BIGSERIAL PRIMARY KEY,
  shop_id      BIGINT NOT NULL REFERENCES shops(id),
  product_id   BIGINT NOT NULL,
  horizon_date DATE NOT NULL,
  qty_forecast NUMERIC(12,3) NOT NULL,
  model        TEXT NOT NULL, baseline_mase REAL, model_mase REAL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (shop_id, product_id, horizon_date, model),
  FOREIGN KEY (shop_id, product_id) REFERENCES products(shop_id, id)
);

CREATE TABLE insights (                                 -- written by the CrewAI Flow only
  id         BIGSERIAL PRIMARY KEY,
  shop_id    BIGINT NOT NULL REFERENCES shops(id),
  week_start DATE NOT NULL,
  body       TEXT NOT NULL,
  facts      JSONB NOT NULL DEFAULT '{}',               -- the aggregates the agents were given
  approved   BOOLEAN NOT NULL DEFAULT false,            -- human feedback step before sending
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (shop_id, week_start)
);

CREATE TABLE audit_logs (
  id         BIGSERIAL PRIMARY KEY,
  shop_id    BIGINT,
  actor      TEXT NOT NULL,                              -- 'tg:<user_id>' | 'system' | 'admin'
  action     TEXT NOT NULL,                              -- MESSAGE_RECEIVED, COMMITTED, ASKED, REVERSED...
  entity     TEXT, entity_id TEXT,
  details    JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_shop_time ON audit_logs (shop_id, created_at DESC);

CREATE VIEW v_product_stock AS
  SELECT p.shop_id, p.id AS product_id, p.name, p.base_unit,
         COALESCE(SUM(m.qty_delta), 0) AS stock, p.low_stock_threshold
  FROM products p
  LEFT JOIN inventory_movements m ON m.shop_id = p.shop_id AND m.product_id = p.id
  GROUP BY p.shop_id, p.id;

CREATE VIEW v_customer_balance AS
  SELECT c.shop_id, c.id AS customer_id, c.name,
         COALESCE(SUM(l.amount_paise), 0) AS balance_paise
  FROM customers c
  LEFT JOIN credit_ledger l ON l.shop_id = c.shop_id AND l.customer_id = c.id
  GROUP BY c.shop_id, c.id;
Why each design choice

idempotency_key UNIQUE — the single guarantee that Telegram retries are harmless.
transactions.message_id UNIQUE — the second, independent guarantee: even a bug in application logic cannot post a message twice.
Composite FKs (shop_id, customer_id) — the database itself refuses to attach a customer from another shop. Multi-tenancy enforced below the application.
No stock or balance column — both are SUM over append-only rows, so they cannot drift from their history. At 100k rows this is milliseconds; §17 covers when to snapshot.
reverses_transaction_id UNIQUE — Undo cannot be double-applied.
Partial unique index on pending_actions — at most one open question per message.
jobs.dedupe_key UNIQUE — a message is enqueued at most once even if the webhook is retried mid-insert.
forecasts and insights are write-only sinks for the optional tools; nothing in bookkeeping reads them, so deleting either table cannot break a ledger.
On Supabase, run ALTER TABLE <t> ENABLE ROW LEVEL SECURITY; on every table so nothing is readable through its auto-generated Data API. Your backend connects as the table owner and is unaffected.
Transaction boundaries — three short transactions per message, never one long one:

Claim: insert the message row (fails silently on duplicate).
Persist artefacts: transcript, normalized text, parsed JSON (external calls happen between transactions, never inside).
Decide and post: lock the message row, re-check status, then insert transaction + movement + ledger + status update, and commit. Telegram is called only after commit.
# app/services/bookkeeping.py (shape)
def post_credit_sale(conn, shop_id: int, message_id: int, cmd) -> int:
    conn.execute(text("SELECT id FROM products WHERE shop_id=:s AND id=:p FOR UPDATE"),
                 {"s": shop_id, "p": cmd.product_id})          # serialise stock per product
    txn_id = conn.execute(text("""
        INSERT INTO transactions (shop_id, message_id, type, customer_id, product_id, quantity, unit,
                                  unit_price_paise, amount_paise, price_source, created_by)
        VALUES (:s, :m, 'CREDIT_SALE', :c, :p, :q, :u, :up, :amt, :src, :by) RETURNING id"""), {...}).scalar_one()
    conn.execute(text("""INSERT INTO inventory_movements (shop_id, transaction_id, product_id, qty_delta, reason)
                         VALUES (:s, :t, :p, :q, 'SALE')"""), {"q": -cmd.quantity_base, ...})
    conn.execute(text("""INSERT INTO credit_ledger (shop_id, transaction_id, customer_id, amount_paise)
                         VALUES (:s, :t, :c, :amt)"""), {...})
    conn.execute(text("UPDATE messages SET status='COMMITTED', updated_at=now() WHERE id=:m"), {"m": message_id})
    return txn_id

# caller:  with engine.begin() as conn:  post_credit_sale(conn, ...)      # rollback on any exception
Any failure (bad FK, constraint, crash) rolls back all four writes together: no half-updated stock. Stock is allowed to go negative — a shopkeeper who forgot to log a purchase must not be blocked from recording a real sale. The reply carries a warning instead.

6. Idempotency: the guarantee and the proof
Telegram delivers at least once. Your bookkeeping must be exactly once.

Five layers, in order:

INSERT INTO messages ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING id — no row returned means duplicate: return 200 and stop, before the job, before STT, before any LLM call.
jobs.dedupe_key UNIQUE (msg:<id>) — one job per message no matter how many deliveries arrive.
SELECT ... FOR UPDATE SKIP LOCKED when a worker claims the job and again on the message row — two workers never process the same message, even with WORKER_CONCURRENCY=4.
Status guard: commit only if the message row is still PROCESSING.
transactions.message_id UNIQUE — the database backstop if 1–4 all fail.
def claim(conn, shop_id, key, **kw) -> int | None:
    return conn.execute(text("""
        INSERT INTO messages (shop_id, idempotency_key, source, telegram_chat_id, telegram_message_id,
                              kind, telegram_file_id, audio_duration_s, transcript)
        VALUES (:shop_id, :key, :source, :chat_id, :msg_id, :kind, :file_id, :dur, :text)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id"""), {"shop_id": shop_id, "key": key, **kw}).scalar()
Callback buttons get the same treatment, with a conditional update instead of an insert:

UPDATE pending_actions SET status='RESOLVED', resolved_at=now()
 WHERE id = :id AND status='PENDING' AND expires_at > now()
 RETURNING draft;         -- no row => already handled; answer the callback and stop
The demo: python -m app.cli.replay --update demo/updates/credit_sale.json --times 10 --concurrency 5 posts the same stored update ten times, five at once, then prints transactions, inventory_movements and credit_ledger counts before and after. Expected: 1 / 1 / 1, and rice stock moved 100 → 95 exactly once.

7. Implementation phases
Every phase ends with something you can run. Do not start the next phase until the DoD passes.

Phase 0 — Environment (Windows)
Objective: repo, Python, Docker Postgres, a /healthz that answers.

winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
winget install -e --id Docker.DockerDesktop
winget install -e --id Microsoft.VisualStudioCode
winget install -e --id Gyan.FFmpeg
winget install -e --id Cloudflare.cloudflared      # optional, for local webhook tunnelling

git init kirana-crew; cd kirana-crew
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
pip install fastapi "uvicorn[standard]" "psycopg[binary]" sqlalchemy pydantic pydantic-settings httpx `
            rapidfuzz python-dotenv pyjwt
pip install pytest pytest-cov ruff jiwer
pip freeze > backend\requirements-dev.txt     # then hand-split into requirements.txt (runtime only)
docker compose -f docker\docker-compose.yml up -d db
uvicorn app.main:app --reload --app-dir backend
requirements.txt now includes the heavy extras, because the laptop can carry them: faster-whisper, statsforecast, crewai (the last two only imported by their own CLI entrypoints, so the API and worker start fast even if you never install them — keep them in requirements-tools.txt if startup time annoys you).

Files: backend/app/main.py, config.py, db.py, docker/docker-compose.yml, .env.example, .gitignore, README.md.

# docker/docker-compose.yml   —  docker compose up -d      (add --profile tools for n8n)
services:
  db:
    image: postgres:16
    environment: { POSTGRES_USER: kirana, POSTGRES_PASSWORD: kirana, POSTGRES_DB: kirana }
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U kirana"], interval: 5s, retries: 10 }
  api:
    build: { context: ../backend }
    env_file: ../.env
    environment:
      DATABASE_URL: postgresql+psycopg://kirana:kirana@db:5432/kirana
      OLLAMA_BASE_URL: http://host.docker.internal:11434
    ports: ["8000:8000"]
    depends_on: { db: { condition: service_healthy } }
    extra_hosts: ["host.docker.internal:host-gateway"]
  worker:                                   # same image, different entrypoint
    build: { context: ../backend }
    command: ["python", "-m", "app.cli.worker", "--concurrency", "2"]
    env_file: ../.env
    environment:
      DATABASE_URL: postgresql+psycopg://kirana:kirana@db:5432/kirana
      OLLAMA_BASE_URL: http://host.docker.internal:11434
      STT_PROVIDER: faster_whisper
    volumes: ["whisper_models:/models"]     # keep the model cache across rebuilds
    depends_on: { db: { condition: service_healthy } }
    extra_hosts: ["host.docker.internal:host-gateway"]
  n8n:
    image: docker.n8n.io/n8nio/n8n
    profiles: ["tools"]
    ports: ["5678:5678"]
    environment:
      N8N_SECURE_COOKIE: "false"
      N8N_BASIC_AUTH_ACTIVE: "true"
      N8N_BASIC_AUTH_USER: admin
      N8N_BASIC_AUTH_PASSWORD: ${N8N_PASSWORD}
      GENERIC_TIMEZONE: Asia/Kolkata
    volumes: ["n8n_data:/home/node/.n8n", "../workflows:/workflows"]
volumes: { pgdata: {}, n8n_data: {}, whisper_models: {} }
Ollama runs on the Windows host, not in Compose (it gets your CPU/GPU directly): winget install -e --id Ollama.Ollama, then ollama pull <model> in Phase 6.

# backend/app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "local"
    database_url: str
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    admin_api_key: str = ""
    jwt_secret: str = "dev"
    groq_api_key: str = ""
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_llm_model: str = "llama-3.3-70b-versatile"
    gemini_api_key: str = ""
    stt_provider: str = "groq"          # groq | faster_whisper | fixture
    parser_mode: str = "hybrid"         # rules | llm | hybrid
    max_voice_seconds: int = 60
    dashboard_origin: str = "*"
settings = Settings()
Tests: GET /healthz → 200; docker compose exec db psql -U kirana -c "select 1". DoD: pytest green (1 test), API and DB both up. Common errors: PowerShell blocks Activate.ps1 → the Set-ExecutionPolicy line. Port 5432 busy → a local Postgres install is running; stop it or map 5433:5432.

Phase 1 — Database and seed
Objective: schema applied by a script you control, plus a reproducible demo dataset. Files: backend/migrations/001_init.sql (§5), app/cli/migrate.py, app/cli/seed_demo.py, eval/seed_catalog.json. migrate.py creates schema_migrations(filename PK, applied_at) and applies unapplied .sql files in order inside one transaction each.

python -m app.cli.migrate
python -m app.cli.seed_demo --catalog ..\eval\seed_catalog.json --reset
seed_demo.py inserts the shop, 8 customers, 12 products, opening stock as INVENTORY_PURCHASE transactions (so even the seed goes through real bookkeeping), and ~20 back-dated transactions so the dashboard is not empty. Tests: test_migrations_idempotent (run twice, no error), test_composite_fk_blocks_cross_shop (inserting a transaction with another shop's customer raises). DoD: SELECT * FROM v_product_stock shows rice 100 kg; running the seed twice does not duplicate. Common errors: gen_random_uuid() missing on Postgres < 13 — use the postgres:16 image. Arrays in psycopg need a Python list, not a string.

Phase 2 — Money and transaction engine
Objective: all five transaction types posting atomically, driven by a typed command — no parsing yet. Files: app/domain/money.py, app/domain/models.py, app/services/bookkeeping.py, app/services/queries.py. Rules: paise integers only; Decimal with ROUND_HALF_UP; unit conversion into the product's base unit (g→kg, ml→litre, dozen→piece); a mismatch (kg for soap) is an error, not a guess. Tests: one per type; rounding (0.333 kg × ₹140/kg); 2.5 kg atta = ₹100; rollback test (force a failure after the movement insert and assert nothing was written); repayment larger than the balance is allowed and leaves a negative balance. DoD: pytest tests/test_bookkeeping.py green, and v_customer_balance / v_product_stock agree with hand-computed values. Common errors: mixing float into Decimal math; forgetting the sign on qty_delta; computing the amount before unit conversion.

Phase 3 — Hindi/Hinglish normalizer
Objective: one function, text → canonical token string, no LLM. File: app/domain/normalize.py (shipped in the reference zip). Covers: Devanagari digits ०-९; ₹/rs → <n> rupaye; splitting 5kg; unit and keyword canonicalisation across both scripts; number words 1–90 plus sau/hazaar composition (do hazaar paanch sau → 2500); fractions aadha/paav/dedh/dhai; modifiers sawa/saade/paune (saade teen sau → 350). Two traps worth mentioning in interviews: saath is both "sixty" and "with", and do is both "two" and "give" — both convert only when the next token is a unit, a multiplier or rupaye. Tests: table-driven, ~40 pairs. DoD: all pass; normalize("5 5 5 kilo") does not invent a number. Common errors: Unicode without NFC normalisation (nukta forms mismatch); greedy regex eating the decimal point in 2.5.

Phase 4 — Rule parser, entity resolution, decision
Objective: normalized text → ParsedCommand → Decision(COMMIT|ASK|REJECT). Files: app/domain/rules_parser.py, resolve.py, decide.py (all shipped). Resolution policy, in order: exact name/alias → token-subset match (ramesh ⊆ ramesh kumar) → fuzzy (WRatio ≥ 92 and 8-point margin over the runner-up). Anything weaker is AMBIGUOUS or UNKNOWN, which becomes a question, never a guess. Decision precedence: NOT_A_TRANSACTION → MULTIPLE_ITEMS → customer issue → product issue → MISSING_QUANTITY → MISSING_AMOUNT → CHOOSE_PAYMENT → MISSING_PRICE → CONFIRM_LARGE_AMOUNT → commit. Price logic: explicit total wins; else explicit unit price (converted to base unit); else catalog price; else ask. Nothing is ever invented. Tests: unit tests per branch + the eval harness in Phase 5. DoD: every decision branch has a test; no code path can commit with amount_paise = None on a sale. Common errors: treating a product word as a customer name (guard with the product vocabulary); fuzzy thresholds so low that unknown names resolve to real customers — that is a false update, the worst failure in this project.

Phase 5 — Evaluation harness (do this before touching the LLM)
Objective: a number you can improve, and a safety metric you refuse to regress. Files: eval/dataset.jsonl (130 labelled examples, shipped), eval/seed_catalog.json, app/cli/eval.py (shipped).

python -m app.cli.eval --dataset ..\eval\dataset.jsonl --catalog ..\eval\seed_catalog.json --parser rules --errors
Important honesty note: the shipped dataset and the shipped rules were written together, so the reference implementation scores 100% on it. That proves consistency, not accuracy. Before you trust any number, write 40 more examples without looking at the parser (ideally transcripts of real voice notes from family) and keep them as a held-out set you only run at the end of each phase. DoD: harness prints the §11 metric table; held-out set exists; false-update rate is 0. Common errors: fixing the dataset to match the code. If gold and code disagree, decide which is right before editing either.

Phase 6 — LLM fallback parser (local first)
Objective: handle the sentences rules miss, offline, without letting the LLM near money or IDs. Files: app/llm/base.py, ollama.py, groq_chat.py, gemini.py, app/domain/llm_parser.py, app/domain/parser.py.

winget install -e --id Ollama.Ollama
ollama list                      # pick a small instruct model from ollama.com/library
ollama pull <model>              # 2-4B class: fits in RAM, fast enough on CPU, good enough for extraction
ollama run <model> "reply with only JSON: {\"ok\":true}"
Model choice is an eval question, not a taste question: try two small models plus one hosted model and put the three numbers in the report. Extraction from a 10-word sentence is an easy task — do not assume you need a big model.

SYSTEM = """You extract ONE shop transaction from a Hindi/Hinglish sentence.
Return ONLY JSON with keys: transaction_type (CREDIT_SALE|CASH_SALE|SALE|CREDIT_REPAYMENT|
INVENTORY_PURCHASE|STOCK_ADJUSTMENT|null), customer_mention, product_mention, quantity, unit
(kg|g|litre|ml|piece|packet|dozen|null), total_amount_rupees, unit_price_rupees, unit_price_unit,
adjustment_direction (OUT|IN|null).
Rules: copy numbers exactly as they appear in the text. NEVER calculate a total, price or sum.
NEVER invent a customer or product name; copy the words used. Use null when unsure."""
Provider chain, each behind the same LLMProvider interface: Ollama (/api/chat, format: "json", options: {temperature: 0}) → Groq (response_format={"type":"json_object"}) → Gemini → give up and use the rule output. Validate with ParsedCommand.model_validate_json, one retry on invalid JSON. Log every call to llm_calls. Grounding check (the interview-worthy bit): every number returned must appear among the numbers in the normalized text, and every mention must fuzzy-match (≥ 80) a span of it. A failing field becomes None, so a hallucination turns into a question instead of a wrong ledger entry. Hybrid policy: run rules; if the result is complete for its type, use it (free, instant, deterministic); otherwise call the LLM. Store parser_used so you can report the split. Tests: grounding rejects quantity=50 when the text says 5; Ollama unreachable falls through to the next provider; PARSER_MODE=rules never opens a socket. DoD: eval run for rules, llm-local, llm-hosted, hybrid; table in docs/eval_report.md; hybrid's false-update rate still 0; the whole pipeline works with the network cable unplugged. Common errors: a small model ignoring "no arithmetic" — delete the totals field rather than trusting the prompt; reasoning models wrapping JSON in prose (strip fences before validating); Ollama unreachable from a container (use host.docker.internal, not localhost).

Phase 7 — Job queue, worker, dev tooling
Objective: the whole path runs through a durable queue, and works without Telegram. Files: app/services/jobs.py, app/cli/worker.py, app/services/pipeline.py, app/api/dev.py, app/cli/replay.py. The webhook does two inserts and returns; the worker does everything slow.

# app/services/jobs.py
def enqueue(conn, kind: str, payload: dict, dedupe_key: str | None = None) -> int | None:
    return conn.execute(text("""
        INSERT INTO jobs (kind, payload, dedupe_key) VALUES (:k, :p, :d)
        ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"""),
        {"k": kind, "p": Json(payload), "d": dedupe_key}).scalar()

def claim(conn):                                   # one worker slot, one job
    return conn.execute(text("""
        UPDATE jobs SET status='RUNNING', attempts=attempts+1, locked_at=now(), updated_at=now()
        WHERE id = (SELECT id FROM jobs
                    WHERE status='QUEUED' AND run_after <= now()
                    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
        RETURNING id, kind, payload, attempts, max_attempts""")).mappings().first()

def fail(conn, job, err: str):                     # exponential backoff, then dead-letter
    dead = job["attempts"] >= job["max_attempts"]
    conn.execute(text("""UPDATE jobs SET status=:st, last_error=:e, updated_at=now(),
                         run_after = now() + (interval '20 seconds' * power(2, :a))
                       WHERE id=:id"""),
                 {"st": "DEAD" if dead else "QUEUED", "e": err[:2000], "a": job["attempts"], "id": job["id"]})
worker.py is a loop with N threads: claim → run handler → mark DONE/FAILED → sleep 1 s when idle. It also runs the reaper every 60 s: jobs RUNNING with locked_at older than 5 minutes go back to QUEUED (a worker was killed), and messages stuck in RECEIVED/PROCESSING get re-enqueued. pipeline.process_message keeps the three short transactions from §5: claim the message row with FOR UPDATE SKIP LOCKED, do STT/parse outside any transaction, then decide-and-post atomically, then call Telegram after the commit. Dev endpoints (admin key, always enabled — they are your demo failover): POST /dev/simulate {text} runs the pipeline synchronously with a synthetic key and returns reply + buttons; POST /dev/simulate/choose; GET /dev/trace/{message_id} returns transcript → normalized → parsed → decision → rows written → every llm_calls row; GET /dev/stats returns row counts and queue depth by status. Tests: same update ×10 sequential → 1 transaction; ×5 concurrent → 1 transaction; kill the worker mid-job → the reaper recovers it and still only one transaction exists; a handler that always raises ends as DEAD after max_attempts and never as a partial write. DoD: replay.py --times 10 --concurrency 5 shows identical counts except one new transaction; queue depth returns to 0; docker compose restart worker mid-demo loses nothing. Common errors: claiming with ORDER BY but no SKIP LOCKED (workers serialise); forgetting run_after in the claim query (a failing job spins hot); holding the job row lock while calling Whisper.

Phase 8 — Human confirmation and undo
Objective: ambiguity becomes a button, and a wrong entry is reversible. Files: app/services/confirmations.py, app/services/replies.py. callback_data layout: pa:<uuid-hex>:<index> (38 bytes, under Telegram's 64-byte limit); undo is un:<txn_id>. Flow: conditional UPDATE ... WHERE status='PENDING' → merge the choice into the draft → re-run decide → either a new question or a commit, all in one transaction → answerCallbackQuery (always, or the client spins) → edit the original message so stale buttons cannot be re-tapped. ENTER_TEXT pendings are answered by a plain message: if the chat has an open ENTER_TEXT, the next text message is normalized and parsed only for that field ("dhai sau" → 250). Undo: within 15 minutes, insert a REVERSAL transaction plus negated movement/ledger rows, guarded by reverses_transaction_id UNIQUE. Nothing is ever deleted. Tests: double-tapping a button commits once; expired pending answers "yeh sawaal purana ho gaya"; undo twice reverses once; choosing a customer after resolving a product ambiguity works in either order. DoD: the six §11 ambiguity examples all resolve correctly through buttons. Common errors: editing the message before the DB commit; keyboards built from stale drafts; forgetting answerCallbackQuery.

Phase 9 — Speech to text
Objective: swappable STT, with a provider that works offline. Files: app/stt/base.py, groq.py, faster_whisper.py, fixture.py.

class STTResult(BaseModel):
    text: str; provider: str; language: str | None = None
    avg_logprob: float | None = None; no_speech_prob: float | None = None; latency_ms: int = 0

class STTProvider(Protocol):
    name: str
    def transcribe(self, audio: bytes, filename: str, language: str = "hi") -> STTResult: ...
Option	Use it for	Notes
faster-whisper large-v3-turbo int8	primary, everywhere	CPU-only, no quota, no network, nothing leaves the laptop. ~1.5–2 GB RAM. Benchmark on your machine: if a 10 s note takes > 15 s, step down to medium or small int8 and report the CER cost. vad_filter=True trims silence; initial_prompt with a sample Hinglish sentence biases vocabulary; language="hi" stops Whisper writing Hindi in Urdu script. Models cache in the whisper_models volume — pre-pull them before demo day
Groq whisper-large-v3-turbo	fallback, and the speed comparison in your report	Free tier, ~1–2 s per clip, accepts Telegram ogg directly, verbose_json gives avg_logprob/no_speech_prob for the confidence gate
Fixture provider	tests, and the "STT is down" demo	maps audio SHA-256 → stored transcript
OpenAI Whisper API	—	paid, removed
Sarvam / AI4Bharat IndicConformer	later, for Kannada	worth benchmarking then; trial credits only
Guards before spending an STT call: reject voice > MAX_VOICE_SECONDS (Telegram gives duration), reject < 1 s, reject files over 20 MB, and on low confidence ask CONFIRM_TRANSCRIPT instead of committing. Tests: provider selection by env var; Groq client mocked with a recorded JSON response; fixture provider used by all integration tests. DoD: the same 10 clips transcribe through both real providers; docs/eval_report.md has CER and median latency per provider on your hardware — that table is the reason to have two providers at all. Common errors: loading the Whisper model per request instead of once per worker process (adds 5–10 s every time); language unset; retrying a Groq 429 without backoff; forgetting that the model download happens on first use — never on demo day.

Phase 10 — Telegram bot
Objective: a public bot on a webhook, with local polling as a fallback. Setup: BotFather → /newbot → name → username ending in bot → copy the token into .env. /setcommands: start, help, balance, stock, last, dashboard. Send /start to your bot; it replies with your telegram_user_id; then python -m app.cli.register_shop --telegram-user-id <id> --shop "Sharma Kirana".

@router.post("/telegram/webhook")
def webhook(background: BackgroundTasks, update: dict = Body(...),
            x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
            x_telegram_bot_api_secret_token, settings.telegram_webhook_secret):
        raise HTTPException(status_code=401)
    if cb := update.get("callback_query"):
        background.add_task(handle_callback, cb); return {"ok": True}
    msg = update.get("message")
    if not msg: return {"ok": True}
    member = get_member(msg["from"]["id"])
    if not member:                                        # unknown sender: never touch the DB
        background.add_task(tg.send, msg["chat"]["id"], "Yeh bot registered nahi hai."); return {"ok": True}
    mid = claim_from_telegram(member, msg)                # None => duplicate delivery
    if mid: background.add_task(process_message, mid)
    return {"ok": True}
Two ways to receive updates on a laptop — build both, they are each other's fallback:

# A) tunnel + webhook (what you use in a live demo)
cloudflared tunnel --url http://localhost:8000          # prints https://<random>.trycloudflare.com
python -m app.cli.set_webhook --url https://<random>.trycloudflare.com/telegram/webhook
python -m app.cli.set_webhook --info                    # pending_update_count, last_error_message

# B) long polling, no tunnel, no inbound connectivity at all
python -m app.cli.poll
The quick-tunnel URL changes every run, so set_webhook is part of your start-up ritual, and scripts/start_demo.ps1 should do compose-up → tunnel → set_webhook → health check in one command. Local fallback without any public URL — app/cli/poll.py: deleteWebhook, then long-poll getUpdates (timeout 30) and POST each update to your local webhook with the secret header, advancing the offset only on a 2xx so failures retry safely. Only one consumer can exist at a time: polling requires deleteWebhook, going back to cloud requires setWebhook. Tests: wrong/missing secret → 401; unregistered user → no DB row; voice update → claimed once; TestClient runs background tasks, so asserts can read the committed rows. DoD: you send a voice note from your phone and get the right reply from the deployed bot. Common errors: webhook on a non-allowed port; secret token containing characters outside A-Za-z0-9_-; running the poller while the cloud webhook is still set (updates vanish into the cloud instance).

Phase 11 — One-command local run (and optional public exposure)
Objective: scripts/start_demo.ps1 brings the whole system up from cold, reproducibly.

# scripts/start_demo.ps1  (sketch)
docker compose -f docker/docker-compose.yml up -d --build          # db, api, worker
docker compose -f docker/docker-compose.yml --profile tools up -d  # n8n, when you want it
ollama serve   # usually already running as a service
python -m app.cli.migrate; python -m app.cli.seed_demo --reset
Start-Process cloudflared -ArgumentList "tunnel --url http://localhost:8000"
python -m app.cli.set_webhook --url "$env:TUNNEL_URL/telegram/webhook"
curl http://localhost:8000/readyz
/healthz = process alive. /readyz = DB reachable, worker heartbeat < 60 s old, STT provider loaded, LLM provider reachable — it is what you check 30 minutes before the interview. Optional public mirror (only if you want a URL on your resume): same image on Render free + Supabase free, STT_PROVIDER=groq, PARSER_MODE=hybrid with Groq as the LLM, worker and API in the same process (RUN_WORKER_INLINE=true) because the free plan gives you one service. Expect the 15-minute sleep and ~1-minute wake; idempotency makes Telegram's retries harmless. Use the session pooler connection string (...pooler.supabase.com:5432) — Supabase's direct host is IPv6-only — and pool_size=3. DoD: a fresh git clone on your laptop reaches a working bot in under 10 minutes using only the README. Common errors: psycopg2 instead of psycopg[binary]; Ollama reachable from the host but not the container; deploying the mirror with faster_whisper still selected and running out of memory.

Phase 12 — Failure testing (§10) · Phase 13 — Flutter dashboard (§12)
Phase 14 — n8n automations
Objective: scheduled work and alerts leave your application code, without touching the money path. Setup: docker compose --profile tools up -d n8n → http://localhost:5678 → basic auth from .env. Four workflows, each exported to workflows/*.json and committed (this is how you version a low-code tool):

Workflow	Trigger	Steps
Daily summary	Schedule 21:00 IST	HTTP POST /internal/daily-summary (admin key) → Telegram node sends today's sales, credit given, credit collected, low-stock list
Low-stock watch	Schedule every 2 h	Postgres node SELECT * FROM v_product_stock WHERE stock < low_stock_threshold → IF rows → Telegram alert
Failed-message alert	Schedule every 15 min	HTTP GET /dev/stats → IF failed > 0 or queue_depth > 20 → Telegram alert to you, not the shopkeeper
Nightly jobs	Schedule 02:00	HTTP POST /internal/enqueue {kind: FORECAST} then {kind: INSIGHTS} on Sundays
Boundary rule, enforced by design: n8n gets read-only SQL credentials and admin-key HTTP endpoints. It		
cannot create a transaction. Write that down in docs/decisions/ADR-004-n8n-scope.md.		
Tests: stop n8n, send a voice note, confirm bookkeeping is unaffected; run each workflow manually with		
"Execute workflow" and check the Telegram output.		
DoD: all four run on schedule for 48 h; JSON exports committed.		
Common errors: n8n's Postgres node pointed at localhost instead of the db service; storing the admin		
key in the workflow instead of n8n credentials; letting a workflow write to transactions "just this once".		
Phase 15 — CrewAI weekly insights (the only agentic part)
Objective: an open-ended writing task where agents genuinely help, kept far away from the ledger. Files: app/crew/insights.py, app/cli/insights.py. The Flow (deterministic steps, agents only inside them):

Code, not an agent, computes the facts: weekly sales per product, customers whose balance rose for 3+ weeks without a payment, slow-moving stock, top-5 outstanding balances. These go in as tool output.
Credit-risk agent → ranks the risk list and explains each flag in one line.
Stock agent → names over- and under-stocked items against the forecast table.
Advisor agent → writes 5 lines of plain Hindi the shopkeeper can act on.
@human_feedback step → you approve → row in insights (approved=true) → Telegram. Point CrewAI's LLM at your local Ollama (OPENAI_API_BASE-style env or a LiteLLM ollama/<model> string); a hosted free key is the fallback. Cap it: 1 run/week, max 6 LLM calls, log all of them to llm_calls. Tests: the fact-builder is unit-tested against a seeded DB (this is where correctness lives); the crew is smoke-tested with a stub LLM; an unapproved insight is never sent. DoD: one real weekly note generated, approved and delivered; insights.facts shows exactly what the agents were given. Common errors: letting an agent compute a number (it will get it wrong and you will not notice); no run cap (a loop burns an hour of CPU); treating the note as ground truth — it is advice built on your own aggregates.
Phase 16 — Forecasting (StatsForecast, nightly)
Objective: 7-day demand per product, with an honest baseline. Requires ~8–12 weeks of daily per-product sales; the seed script generates a synthetic 12-week history with weekly seasonality so the pipeline is demonstrable. Models: AutoETS + SeasonalNaive as the baseline, freq="D", rolling-origin backtest, report MASE and WAPE per product. Write to forecasts including both model_mase and baseline_mase; the dashboard and the crew display a forecast only when the model beats the baseline. Runs as a FORECAST job triggered by n8n at 02:00, never inside a request. DoD: backtest table in docs/eval_report.md; at least one product where you honestly say "the baseline wins, so we show the baseline". Common errors: forecasting on seeded noise and presenting it as a result; leaking future rows into the backtest; importing statsforecast in the API process (slow start, no benefit).

Phase 17 — Tracing, then final eval and demo prep
llm_calls + /dev/trace/{message_id} give you a per-message waterfall: STT model and latency, parser used, tokens, decision, rows written. Add GET /dev/stats counters for queue depth, FAILED/DEAD jobs, and parser split (rules vs llm). Optional upgrade: Langfuse Cloud's free tier for a nicer UI — self-hosted Langfuse v3 wants ClickHouse, Redis and MinIO, which is more machine than this project deserves. Then: final eval on the held-out set, audio CER for both STT providers, the runbook rehearsal in §9.

8. Project structure
kirana-crew/
├── backend/
│   ├── app/
│   │   ├── main.py  config.py  db.py
│   │   ├── api/        telegram.py  dev.py  dashboard.py  internal.py
│   │   ├── domain/     models.py  money.py  normalize.py  rules_parser.py
│   │   │               llm_parser.py  parser.py  resolve.py  decide.py
│   │   ├── services/   pipeline.py  jobs.py  bookkeeping.py  confirmations.py  replies.py  queries.py
│   │   ├── stt/        base.py  faster_whisper.py  groq.py  fixture.py
│   │   ├── llm/        base.py  ollama.py  groq_chat.py  gemini.py
│   │   ├── crew/       insights.py            # CrewAI Flow, imported only by its CLI
│   │   ├── forecast/   demand.py              # StatsForecast job
│   │   ├── telegram/   client.py
│   │   └── cli/        migrate  seed_demo  register_shop  set_webhook  poll  worker  replay
│   │                   eval  insights  forecast
│   ├── migrations/001_init.sql
│   ├── tests/          unit/  integration/  conftest.py
│   ├── requirements.txt  requirements-dev.txt  requirements-local.txt  Dockerfile
├── frontend/                  # Flutter web dashboard
├── eval/                      # dataset.jsonl, seed_catalog.json, audio/ (gitignored), results/
├── demo/                      # recorded Telegram updates, .ogg clips, transcripts.md
├── docker/docker-compose.yml  # db, api, worker, n8n (profile: tools)
├── workflows/                 # n8n workflow JSON exports, version-controlled
├── scripts/                   # start_demo.ps1, demo_check.ps1, stop_demo.ps1, backup_db.ps1
├── .github/workflows/         # ci.yml, daily_summary.yml
├── docs/                      # architecture.md, decisions/ADR-*.md, eval_report.md, runbook.md
└── .env.example  README.md
domain/ is pure functions — no DB, no network, so it is fast to test and easy to reason about. services/ owns transactions and side effects. cli/ lives inside the package so the Docker image can run every operational script. docs/decisions/ holds five ADRs (why Telegram, why rules-first, why derived balances, why n8n is read-only, why agents live outside bookkeeping) — interviewers read them, and the last two are the ones that show judgement.

9. Demo Day Runbook
Primary: laptop Docker stack + cloudflared quick tunnel + Telegram webhook. Backup 1: same stack, app.cli.poll long polling — no tunnel, no inbound connectivity needed. Backup 2: the optional Render + Supabase mirror, if you built it in Phase 11. Backup data: seeded DB + 10 pre-recorded .ogg clips saved in Telegram Saved Messages (forwarding one to the bot behaves exactly like a fresh voice note, and works in a noisy room) + demo/updates/*.json for the replay demo + demo/transcripts.md for the STT-down path.

T-3 days

 Merge to main, tag demo-v1, freeze (no further merges).
 docker compose build --no-cache once, so demo day has no build step.
 Pre-pull the Whisper model into the whisper_models volume and ollama pull the parser model.
 pytest all green locally and in CI.
 python -m app.cli.eval on the held-out set; paste the table into docs/eval_report.md.
 Send one voice note per transaction type through the deployed bot; check /dev/trace.
 Confirmation flows: ambiguous customer, ambiguous product, missing price, payment unclear.
 replay.py --times 10 --concurrency 5 → counts unchanged.
 Verify .env complete; set_webhook --info clean after a tunnel restart.
 n8n: all four workflows active; run each once manually.
 Generate one insights note and one forecast run so the dashboard is not empty.
 Reseed the demo DB; note the opening numbers (rice 100 kg, Ramesh Kumar ₹X).
 Check the Groq fallback key still works (quota, not expired).
 Full cold rehearsal: stop_demo.ps1, then start_demo.ps1, then a real voice note — timed.
 backup_db.ps1 → demo/backup_<date>.sql.
T-30 minutes

 scripts/start_demo.ps1; wait for /readyz = all green (DB, worker heartbeat, STT loaded, LLM reachable).
 Send one warm-up voice note so the Whisper and Ollama models are already in RAM — this is the single biggest latency win, and skipping it is what makes a local demo look slow.
 GET /dev/stats → queue depth 0, no FAILED/DEAD jobs.
 Send one real voice note; confirm the reply and the dashboard both update.
 Open the Flutter dashboard via /dashboard link from the bot.
 Laptop on power, sleep disabled, Wi-Fi hotspot ready as a backup network.
 Poller command typed but not run (only one consumer at a time).
 Phone: brightness up, notifications off, bot chat open, Saved Messages ready.
 Laptop: terminal with switch_to_local.ps1 typed but not run; /dev/trace tab open.
If the tunnel dies (quick tunnels drop, and the URL changes on restart)

.\scripts\start_demo.ps1 -TunnelOnly     # new tunnel + set_webhook, ~15 seconds
python -m app.cli.poll                   # or skip tunnels entirely and long-poll
Say it out loud: "the transport is an adapter; webhook and polling are two implementations of it."

If STT is slow or fails: STT_PROVIDER=groq (env change + docker compose restart worker), or STT_PROVIDER=fixture with a pre-recorded clip whose hash is in the fixture map, or send the transcript as a text message — the pipeline after STT is identical and /dev/trace shows it.

If Ollama is slow: PARSER_MODE=rules still commits most sentences, or LLM_PROVIDER=groq. Neither changes a single line of bookkeeping — a good thing to demonstrate deliberately.

If Telegram fails: demo through the simulator.

curl -X POST $env:API/dev/simulate -H "X-Admin-Key: $env:KEY" -H "Content-Type: application/json" `
  -d '{"text":"Ramesh Kumar ne 5 kilo chawal udhaar pe liya"}'
Buttons come back as JSON options; /dev/simulate/choose resolves them; /dev/trace shows every stage.

If the network is gone entirely: this is the one scenario the local stack wins outright — Docker + faster-whisper + Ollama need no internet. Only Telegram itself does, so switch to the simulator and keep going with the dashboard, the trace view and the eval report. Never a blank screen.

10. Failure and recovery matrix
Failure	How to simulate	Expected safe behaviour	DB effect
Local STT slow / model missing	delete the model cache	job retries with backoff, falls back to Groq, then FAILED + "audio samajh nahi aaya"	message row only
Hosted STT 429	mock a 429	backoff, then the local provider	message row only
Ollama down	ollama stop	provider chain moves to Groq → Gemini → rules-only; latency logged	none
Worker killed mid-job	docker compose kill worker	reaper re-queues after 5 min; restart processes it once	exactly one transaction
Queue backlog	enqueue 200 jobs	depth visible in /dev/stats; n8n alerts above 20; nothing is lost	processed in order
Job poisoned	handler always raises	5 attempts with backoff, then DEAD, alert fires, no partial write	none
n8n down	stop the container	no summaries or alerts; bookkeeping completely unaffected	none
CrewAI run fails	stub LLM raises	insights row not created; nothing sent	none
LLM hallucination	mock a wrong quantity	grounding check nulls the field → question	none
Database unavailable	docker compose stop db	webhook returns 500 so Telegram retries later; nothing lost	none
Telegram unavailable	block the host	transaction stays committed, reply logged as failed; /last re-sends	committed once
Duplicate update ×10	replay.py	9 no-ops	exactly one transaction
Concurrent duplicates	--concurrency 5	FOR UPDATE SKIP LOCKED + unique constraint	exactly one
Malformed audio	send a renamed text file	STT error → FAILED + friendly reply	none
Empty / 0.5 s audio	tap-and-release voice note	rejected before STT	none
5-minute audio	long note	rejected: "ek message mein ek entry, 60 second se kam"	none
Unknown customer	"Kavita ne..."	offer "naya customer banayein?" button	none until confirmed
Unknown product	"1 packet maggi"	reply with closest matches or "admin se add karwayein"	none
Low-confidence transcript	quiet mumbled clip	CONFIRM_TRANSCRIPT before booking	none until confirmed
Network timeout mid-process	kill the process after STT	sweeper re-runs it in ≤ 3 min	one transaction
Partial DB failure	raise after the movement insert	full rollback	nothing written
Two shops, one customer name	insert a cross-shop reference	composite FK rejects it	nothing written
11. Testing and evaluation
Two datasets. eval/dataset.jsonl — 130 labelled Hinglish/Devanagari examples across 21 categories (credit sale, cash sale, repayment, purchase, adjustment, Devanagari, Hindi number words, mixed English, ambiguous customer, ambiguous product, unknown customer/product, missing quantity/customer/product/price, explicit price, large amount, payment unclear, malformed, STT-style errors). Plus a held-out set of ≥ 40 examples you write from real transcripts after the parser exists, run only at phase boundaries.

Audio track: record 40 clips (8 per type) from 3–4 different speakers, with a real room and some noise, write human transcripts, and measure CER with jiwer.

Metrics — exactly how each is computed (app/cli/eval.py):

Metric	Definition
Transaction classification accuracy	correct predicted type ÷ rows where gold specifies a type
Customer / product / quantity / unit / amount accuracy	per field, over gold-COMMIT rows; both-null counts as correct; quantity compared in base units with tolerance 0.001
Field-level accuracy	total correct fields ÷ total gold fields (COMMIT rows)
End-to-end transaction accuracy	rows where the action matches and every gold field matches (for ASK: the action and ask_reason and candidate set match) ÷ all rows
Confirmation rate	rows where the system asked ÷ all rows
Ask precision / recall	(asked ∧ gold-ask) ÷ asked, and ÷ gold-ask — this separates "cautious" from "annoying"
False update rate	rows where the system committed but the gold says ASK/REJECT, or committed different values ÷ all rows. Target: 0. This is the metric you defend.
STT CER / WER	jiwer on the audio track, per provider
Latency	p50/p95 per stage from structured logs (STT, parser, DB)
Report every number with the dataset name, size and date. Compare three parser modes (rules, llm, hybrid) in one table — that comparison is the technical story of the project.

Test layout: tests/unit/ (normalizer table, money rounding, resolution thresholds, decision branches, grounding check) and tests/integration/ (Postgres service container, fixture STT, fake Telegram client recording sends; idempotency, concurrency, sweeper, confirmation, undo, atomic rollback). CI runs both on every push.

12. Flutter dashboard (small, last)
Flutter web, built to frontend/build/web and served by the API in local mode (GitHub Pages only if you build the public mirror). Five screens: Today (sales, credit given, credit collected, low-stock count) · Customers (list, balance, history) · Inventory (stock, low-stock flags, and the 7-day forecast when it beats the baseline) · Transactions (type, amount, source transcript, parsed JSON, parser used, STT latency, confirmation status) — that last screen is what makes the pipeline visible in an interview, and the weekly insights note sits on Today.

Read-only API, six endpoints under /api/v1/dashboard/*, Bearer JWT. The bot command /dashboard returns a link with a 24-hour HS256 token carrying shop_id, so there is no login to build and no secret in the web bundle. CORS allows only your Pages origin. flutter build web --dart-define=API_BASE=.... Keep it under ~600 lines of Dart; do not spend a week here.

13. Forecasting — in, with a baseline
No longer optional, because local compute is free (build it in Phase 16). The honesty constraint stands: a 7-day demand forecast needs ~8–12 weeks of daily per-product sales, so the seed script generates a synthetic 12-week history with weekly seasonality, and every forecast is scored against a seasonal-naive baseline on a rolling-origin backtest (MASE, WAPE). Results go to the forecasts table with both scores, and the dashboard shows the model only where it wins.

Say this in the interview: "the interesting number is not the forecast, it is whether it beats seasonal-naive — on this data it wins for fast-movers like rice and loses for soap, so soap shows the baseline." That sentence separates people who have used a forecasting library from people who have evaluated one.

14. CrewAI — where it goes, and where it never goes
v1 of this plan removed CrewAI entirely because it added LLM hops to a deterministic path on a budget that could not afford them. With local compute, the right answer is narrower: keep it out of bookkeeping, use it for the one genuinely open-ended task.

Use	Verdict
Parse a voice note into a command	No. One sentence, one deterministic path. Rules + a single validated LLM call already do it, and agents would add non-determinism to money handling
Orchestrate the message pipeline	No. That is a job queue with retries, not a crew — jobs + SKIP LOCKED is the correct tool and is testable
Decide a price, a customer, or a balance	Never. These are code decisions with an audit trail
Weekly shop-insights note (credit risk, slow stock, advice in Hindi)	Yes. Open-ended synthesis over pre-computed facts, no correct single answer, a human approves before anything is sent (Phase 15)
Design rules that make it defensible: code computes every number and passes it in as tool output; agents only rank, explain and phrase; output lands in insights, never in transactions; there is a hard cap of one run a week and six LLM calls; and a @human_feedback step gates delivery.

The interview sentence is now stronger than "I removed it": "I used CrewAI for the weekly advisory note, where the task is open-ended writing over facts my code computed — and deliberately kept it out of the transaction path, because bookkeeping has one correct answer and agents are not how you get it."

15. Security (MVP-appropriate)
Webhook: secret_token set via setWebhook, compared with hmac.compare_digest; unguessable URL path as a second layer.
Authorisation: shop_members allowlist; unknown Telegram users get a polite refusal and no DB row.
Tenant isolation: shop_id on every table and composite foreign keys, so the database rejects cross-shop references.
Secrets: env vars only; .env gitignored; .env.example documents every key; rotate the bot token if it ever lands in a log.
Input: Pydantic extra="forbid" on LLM output, bounded numeric fields, duration/size limits on audio.
SQL: parameterised statements everywhere, no f-strings; the LLM never emits SQL.
Dashboard: short-lived HS256 JWT with shop_id, read-only endpoints, CORS pinned to one origin.
Admin/dev endpoints: X-Admin-Key, constant-time compare, rate-limited.
Audit: audit_logs for every message, decision and reversal; ledgers are append-only, so nothing is silently editable.
Local services: bind Postgres, Ollama and n8n to 127.0.0.1 (never 0.0.0.0 on café Wi-Fi); n8n basic auth on, password from .env; n8n gets a read-only Postgres role (GRANT SELECT only) so a mis-clicked node cannot write.
Tunnel: the quick-tunnel URL is public while it runs — the webhook secret token and the member allowlist are what protect you, so never disable them "just for the demo". Close the tunnel afterwards.
Supabase mirror (if built): RLS enabled on all tables so the auto-generated Data API exposes nothing.
Not now: OAuth, password auth, secret managers, WAF, PII encryption at rest.

16. Scaling path
1 shop (now)	~100 shops	~10,000 shops
Compute	laptop: api + 1 worker (2 threads)	small always-on instance + 2–3 workers	webhook ingress only, autoscaled worker pool
Queue	jobs table + FOR UPDATE SKIP LOCKED (already built)	same, plus per-shop fairness and priority	Redis Streams / SQS, partitioned by shop
STT	local faster-whisper	GPU box or hosted batch	hosted, batched, quota per tenant
DB	derived SUM balances	add (shop_id, customer_id, created_at) indexes; PgBouncer/Supavisor pooling	balance/stock snapshot tables updated in the same transaction; read replicas for dashboards; partition messages and audit_logs by month
STT	Groq free tier	paid tier (~$0.04/audio-hour on Turbo), retry budget per shop	batch API, regional providers, quota per tenant
Isolation	shop_id + composite FKs	Postgres RLS with a per-request app.shop_id	separate schemas or shards for large tenants
Telegram	one bot	one bot, respect per-bot send limits (see Telegram's Bot FAQ)	multiple bots, outbound send queue with rate limiting
Ops	logs + /dev/trace	Sentry free tier, uptime ping, alert on FAILED count	OpenTelemetry traces, SLOs, per-tenant dashboards
Do not build now: queues, Redis, microservices, Kubernetes, multi-region, event sourcing, an outbox table, caching layers. Every one of them is a correct answer to a question you do not have yet — say that in the interview, it lands better than having built them.

17. Interview demo script (5 minutes)
One line: "Shopkeepers keep udhaar in a notebook. This turns a Hindi voice note into a validated ledger entry — running entirely on this laptop, and the LLM is never allowed to touch the arithmetic."
Architecture (30 s, the §3 diagram): claim → enqueue → worker → STT → normalize → parse → resolve → decide → atomic post, with n8n, the crew and forecasting strictly outside the money path.
Happy path: forward a voice clip — "Ramesh Kumar ne paanch kilo chawal udhaar pe liya" → reply ✅ Udhaar: Ramesh Kumar — Rice 5 kg × ₹50 = ₹250, kul udhaar ₹X → show the dashboard row.
Ambiguity: "Ramesh ko 2 kilo cheeni udhaar" → buttons → tap Ramesh Kumar → committed. "It would rather ask than guess; the ask precision and recall are in the report."
Price rules: "…280 rupaye mein" (explicit wins) then "2 kilo moong dal udhaar" (no catalog price → asks). "The LLM never computes a total; money.py does, in paise."
Idempotency: replay.py --times 10 --concurrency 5 → one transaction, stock moved once. Explain the four layers.
Kill something: docker compose kill worker mid-message, restart it, show the reaper completing the job exactly once. Then note that Whisper and the LLM are local — unplug the network and everything except Telegram still runs.
Numbers: the eval table — four parser modes (rules / local LLM / hosted LLM / hybrid), field accuracy, confirmation rate, false update rate 0, STT CER and latency per provider.
The tooling, briefly: n8n's four schedules, the weekly CrewAI note with its human-approval step, and the forecast that only shows when it beats seasonal-naive.
Trade-offs, unprompted: it runs locally by choice, not by accident; agents sit outside bookkeeping deliberately; stock may go negative on purpose; the held-out set is small.
Keep /dev/trace open as your safety net for any "what actually happened there?" question.

18. Resume bullets
"Telegram voice bookkeeping assistant for kirana shops that converts Hindi/Hinglish voice notes into validated stock and credit-ledger entries (FastAPI, PostgreSQL, Groq Whisper); the LLM only emits a Pydantic-validated command while all pricing and posting run in deterministic code — [X]% end-to-end accuracy and [Y]% false-update rate on a [N]-example labelled set."
"Exactly-once bookkeeping over at-least-once Telegram webhooks, built on a Postgres job queue (FOR UPDATE SKIP LOCKED, backoff, dead-letter) with message-keyed unique constraints; replaying one update 10× with 5-way concurrency, and killing the worker mid-job, produced zero duplicate ledger entries."
"Offline-capable speech pipeline running faster-whisper and a local LLM through provider interfaces with hosted fallbacks, so no shop data leaves the machine; [X]% CER and [Y] s median transcription on a [N]-clip Hindi set."
"Hybrid rule-first parser with LLM fallback and a number-grounding check that rejects any figure absent from the transcript, raising field-level accuracy from [A]% (rules only) to [B]% while keeping the confirmation rate at [C]%."
"Append-only inventory and credit ledgers with derived balances, composite foreign keys for per-shop isolation, and reversal-based undo; scheduled reporting in n8n and a weekly CrewAI advisory note, both read-only by design so no automation can write to the ledger."
Fill every bracket with a number you measured. Interviewers ask how.

19. Twenty-two likely interview questions
Why is the LLM not allowed to do arithmetic? Language models are sampling machines; a wrong total silently corrupts a shopkeeper's money and is unauditable. Extraction is fuzzy, pricing is exact — money.py computes in integer paise with ROUND_HALF_UP, and the LLM's numbers must already appear in the transcript.
How do you guarantee exactly-once? Four layers: unique idempotency_key on claim; FOR UPDATE SKIP LOCKED; status guard before posting; unique message_id on transactions. Layers 1–3 are optimisation; layer 4 is the guarantee.
Why claim before transcription? A duplicate delivery would otherwise cost a Whisper call and could double-post; the claim is one cheap insert.
Why not store stock as a column? Because it drifts. SUM over append-only movements can't disagree with its own history, gives a free audit trail, and makes reversal trivial. At scale I'd add a snapshot table updated in the same transaction.
How do you handle two customers named Ramesh? Exact/token matching first; fuzzy only above 92 with an 8-point margin; otherwise Telegram buttons, with the draft persisted in pending_actions so the answer can arrive hours later.
What if a user taps a button twice? UPDATE ... WHERE status='PENDING' RETURNING — only the first tap gets a row; the second gets "already handled". Buttons are also removed from the message after use.
Why rules before an LLM? Shop sentences are formulaic, so rules handle most of them at zero cost, zero latency and full determinism. The LLM is the fallback for the tail, and I have the eval table showing what each contributes.
How do you catch LLM hallucination? Grounding: every returned number must be in the normalized text's number set, every mention must fuzzy-match a span. Failing fields become None, so a hallucination turns into a question, never a ledger entry.
What's your worst-case failure? A false update — silently booking the wrong customer or amount. That's why it's a first-class metric with a target of zero, why ambiguity always asks, and why undo exists.
How does undo work without deleting data? A REVERSAL transaction with negated movement and ledger rows, guarded by reverses_transaction_id UNIQUE. Nothing is mutated; the history stays complete.
Why Telegram, not WhatsApp? WhatsApp Cloud API needs business verification and charges per conversation; Telegram is free, has a documented webhook with a secret token, easy voice download and inline keyboards. The transport sits behind one adapter, so WhatsApp is a later addition, not a rewrite.
Why did you remove CrewAI? I compared four options. Extracting one command has a single deterministic path; agents would add LLM hops, cost, latency and non-determinism to money handling for no capability gain.
Why does it run locally instead of on a free host? Free hosts cap you at 512 MB and 0.1 CPU, which rules out running Whisper or any LLM yourself and puts a daily quota between you and your own demo. On the laptop the whole thing is offline-capable and unlimited, and a cloudflared tunnel gives Telegram a URL when I need one. The same image runs on Render with hosted providers if a public URL matters — that's an env-var change, because every provider is an interface.
Why a job queue instead of FastAPI background tasks? Background tasks die with the process and give you no retries, no visibility and no backpressure. A jobs table with FOR UPDATE SKIP LOCKED gives at-least-once delivery, exponential backoff, a dead-letter state, and a queue depth I can alert on — and it costs one table.
Why is n8n allowed in but agents are not? Scope, not fashion. n8n has read-only DB credentials and admin HTTP endpoints; it schedules summaries and alerts and cannot create a transaction. CrewAI writes a weekly advisory note from facts my code computed. Neither can touch the ledger, and killing both changes nothing about bookkeeping.
What if the local model is worse than the hosted one? Then the eval says so and the hybrid config picks the hosted one — that's why there are four parser modes in the report instead of an opinion.
How do you test something with STT and an LLM in it? Provider interfaces with fixture implementations: the hash-to-transcript STT and a recorded LLM response. Integration tests hit a real Postgres in a service container with a fake Telegram client, so the network is never required.
What do you do about Hinglish number words? A deterministic normalizer: digits, number words 1–90, sau/hazaar composition, fractions (dhai, paav, dedh), modifiers (sawa, saade, paune), plus Devanagari. Two words are genuinely ambiguous — saath (sixty/with) and do (two/give) — and convert only in numeric context.
Why unit conversion at the boundary? Each product has one base unit; a stated unit is converted into it or rejected. "500 gram" becomes 0.5 kg before pricing, and "2 kg soap" is refused rather than guessed.
How is one shop isolated from another? shop_id on every row, membership checked per Telegram user, and composite foreign keys so the DB itself refuses a customer from another shop. RLS is the next step at multi-tenant scale.
Where would this break at 10,000 shops? SUM-derived balances and a single worker. I'd add balance and stock snapshots written in the same transaction, move the queue to Redis Streams or SQS with per-shop partitioning, partition messages and audit_logs by month, and batch STT through a hosted provider.
What would you do differently? Build the held-out eval set before the parser instead of alongside it; the numbers would have been trustworthy earlier.
20. Later: Kannada extension
Do it as a per-shop language setting, not auto-detection (mixed-language detection on 3-second clips is unreliable and a wrong guess ruins the parse).

shops.language = 'kn' selects a Kannada keyword/number dictionary in the normalizer: ಒಂದು/ondu 1 … ಹತ್ತು/hattu 10, ಸಾಲ/saala = credit, ನಗದು/nagadu = cash, ಅಕ್ಕಿ/akki = rice, ಸಕ್ಕರೆ/sakkare = sugar, ತೀರಿಸಿದ/teerisida = repaid, ಕೆಟ್ಟು ಹೋಯಿತು = spoiled.
Product and customer aliases get Kannada spellings — the resolver needs no change.
STT: measure Groq Whisper on Kannada first; it is noticeably weaker than Hindi. Alternatives to evaluate: Sarvam's Indic models (check current free credits) or AI4Bharat IndicConformer self-hosted on your laptop.
Build a separate 100-example Kannada set and report metrics per language — never one blended number.
Only the dictionary, the aliases and the STT choice change. If anything else needs touching, the abstraction was wrong. Say that in the interview: "adding a language took N days and touched two files."
21. What NOT to build
Agents anywhere inside bookkeeping · LLM-generated SQL · WhatsApp · GST invoicing · password/OAuth login · Redis, Celery, Kafka, Kubernetes (the Postgres queue is enough at this size) · microservices · websocket live dashboard · OCR of paper bills · multiple transactions per voice note · UPI/payment integration · STT fine-tuning · self-hosted Langfuse v3 (ClickHouse + Redis + MinIO for one developer) · a Play Store release · offline-first mobile app with sync · i18n framework · admin CRUD UI (use psql and CLI scripts) · a custom domain · a permanent public deployment you then have to babysit.

Every hour spent on these is an hour not spent on the three things that make this project interesting: measured accuracy, provable exactly-once bookkeeping, and a clean line between the deterministic core and everything AI-shaped around it.