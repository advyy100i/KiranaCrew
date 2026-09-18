# Architecture (one page)

```
 phone ──voice──► Telegram ──POST update (≥1×)──► FastAPI /telegram/webhook
                                                     │ secret token + shop_members check
                                                     │ INSERT messages ON CONFLICT DO NOTHING  ← layer 1
                                                     │ INSERT jobs (dedupe_key msg:<id>)        ← layer 2
                                                     └──► 200 OK (nothing slow happens here)

 worker thread: UPDATE jobs ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)                 ← layer 3
   A. lock message (FOR UPDATE SKIP LOCKED), status → PROCESSING                         [txn]
      STT: faster-whisper (local) → Groq (fallback) → fixture (tests)                     [no txn]
   B. persist transcript, normalized text, parsed JSON, llm_calls                          [txn]
      parse: rules → (incomplete?) → Ollama → Groq → Gemini → grounding check              [no txn]
   C. lock message, status guard ← layer 4, decide():                                      [txn]
        COMMIT → bookkeeping.post_decision: transactions (message_id UNIQUE ← layer 5)
                 + inventory_movements + credit_ledger, message → COMMITTED
        ASK    → pending_actions (one PENDING per message) + message → AWAITING_CONFIRMATION
        REJECT → message → REJECTED
   after commit: Telegram sendMessage (+ inline buttons: choices / Undo)

 callback tap: UPDATE pending_actions SET status='RESOLVED' WHERE status='PENDING' RETURNING draft
               → merge choice into Overrides → decide() again → C (same transaction) → reply, clear buttons
 typed answer:  open ENTER_TEXT in this chat? → normalize("dhai sau") = 250 → same path
 undo:          REVERSAL transaction + negated rows (reverses_transaction_id UNIQUE)

 reaper (60 s): stale RUNNING jobs → QUEUED; stuck messages re-enqueued; expired pendings → EXPIRED; heartbeat

 outside the money path (read-only by credential/design):
   n8n           daily summary 21:00 · low-stock 2h · failed-message alert 15m · nightly FORECAST/INSIGHTS enqueue
   forecast job  StatsForecast AutoETS vs SeasonalNaive, rolling-origin MASE → forecasts (shown only if it wins)
   insights job  build_facts() in SQL → 3 roles (credit / stock / advisor) → insights(approved=false) → human → send
   dashboard     Flutter web, /api/v1/dashboard/*, 24 h HS256 JWT from the /dashboard bot command
```

Tenant isolation: `shop_id` on every row and composite FKs `(shop_id, customer_id)` / `(shop_id, product_id)`.
Money: paise `BIGINT`, `ROUND_HALF_UP`; quantity `NUMERIC(12,3)` in the product's base unit; stock may go negative on purpose.
