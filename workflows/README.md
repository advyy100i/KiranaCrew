# n8n workflows (version-controlled exports)

Import each JSON in n8n (http://localhost:5678, basic auth from `.env`). Create these credentials once:

| Credential | Type | Value |
|---|---|---|
| `KiranaCrew admin key (X-Admin-Key)` | Header Auth | name `X-Admin-Key`, value = `ADMIN_API_KEY` from `.env` |
| `KiranaCrew bot` | Telegram API | the bot token |
| `KiranaCrew read-only (kirana_ro)` | Postgres | host `db`, db `kirana`, user `kirana_ro` (see `backend/migrations/002_readonly_role.sql`) |

Environment variables for the n8n container: `OWNER_CHAT_ID` (shopkeeper), `OPERATOR_CHAT_ID` (you).

Boundary rule (ADR-004): every workflow only reads (`/dev/stats`, `v_product_stock`) or enqueues (`/internal/enqueue`) or
asks the API to send a summary. None can write to `transactions`; the Postgres credential is a `SELECT`-only role.
