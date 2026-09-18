# ADR-003: Append-only ledgers, derived balances, and exactly-once posting

**Status:** accepted · **Date:** 2026-09-18

## Decision
There is no `stock` or `balance` column. `v_product_stock` and `v_customer_balance` are `SUM` views over
append-only `inventory_movements` and `credit_ledger`. Undo inserts a `REVERSAL` transaction with negated rows,
guarded by `reverses_transaction_id UNIQUE`. Exactly-once over Telegram's at-least-once delivery is five layers:
1. `messages.idempotency_key UNIQUE` — the claim insert, before STT, before any LLM call.
2. `jobs.dedupe_key UNIQUE` — one job per message.
3. `FOR UPDATE SKIP LOCKED` on the job and again on the message row.
4. Status guard: post only if the message is still `PROCESSING` / `AWAITING_CONFIRMATION`.
5. `transactions.message_id UNIQUE` — the database backstop.

Composite foreign keys `(shop_id, customer_id)` / `(shop_id, product_id)` make cross-shop references impossible.

## Why
Balances that are sums cannot drift from their history; reversal is trivial; the audit trail is free. Layers 1–4
are optimisations; layer 5 is the guarantee, and `tests/integration/test_pipeline.py` replays one update 10× with
5-way concurrency and kills a worker mid-job to prove it.

## Consequences
At 10k shops, add snapshot tables updated in the same transaction (§16 of the plan). Not now.
