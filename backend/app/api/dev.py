"""Dev / admin endpoints (X-Admin-Key). Always enabled: they are the demo failover when Telegram is unavailable.
/dev/simulate runs the whole pipeline synchronously on a text message with a synthetic idempotency key."""
import hmac
import uuid

from fastapi import APIRouter, Body, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.services import confirmations, jobs, queries
from app.services.pipeline import Skip, claim_message, process_message

router = APIRouter(prefix="/dev")


def _rt(request: Request):
    return request.app.state.runtime


def admin(request: Request, x_admin_key: str | None = Header(default=None)):
    expected = _rt(request).settings.admin_api_key
    if not expected or not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=401, detail="admin key required")


class Simulate(BaseModel):
    text: str
    shop_id: int = 1
    key: str | None = None            # pass the same key twice to demonstrate idempotency
    chat_id: int | None = None        # set to route replies through the (fake) telegram in tests


def _outcome(out) -> dict:
    if out is None:
        return {"status": "SKIPPED"}
    return {"message_id": out.message_id, "status": out.status, "reply": out.reply, "buttons": out.buttons,
            "pending_id": out.pending_id, "transaction_id": out.txn_id, "decision": out.decision}


@router.post("/simulate")
def simulate(request: Request, body: Simulate, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    rt = _rt(request)
    key = body.key or f"sim:{uuid.uuid4()}"
    with rt.engine.begin() as conn:
        mid = claim_message(conn, body.shop_id, key, "SIMULATOR", "TEXT", chat_id=body.chat_id, transcript=body.text)
    if mid is None:
        with rt.engine.connect() as conn:
            existing = conn.execute(text("SELECT id, status FROM messages WHERE idempotency_key=:k"), {"k": key}).first()
        return {"duplicate": True, "message_id": existing[0], "status": existing[1]}
    with rt.engine.begin() as conn:                      # mark the queued job done: we run it inline here
        conn.execute(text("UPDATE jobs SET status='DONE', updated_at=now() WHERE dedupe_key=:k"), {"k": f"msg:{mid}"})
    return _outcome(process_message(rt, mid))


class Choose(BaseModel):
    pending_id: str
    index: int
    shop_id: int = 1


@router.post("/simulate/choose")
def choose(request: Request, body: Choose, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    data = f"pa:{body.pending_id.replace('-', '')}:{body.index}"
    return _outcome(confirmations.resolve_callback(_rt(request), data, None, None, body.shop_id))


class Answer(BaseModel):
    pending_id: str
    text: str


@router.post("/simulate/answer")
def answer(request: Request, body: Answer, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    rt = _rt(request)
    with rt.engine.connect() as conn:
        p = conn.execute(text("SELECT id, message_id, shop_id, kind, field, draft, telegram_chat_id, telegram_reply_message_id "
                              "FROM pending_actions WHERE id=CAST(:id AS uuid) AND status='PENDING' AND kind='ENTER_TEXT'"),
                         {"id": body.pending_id}).mappings().first()
    if not p:
        raise HTTPException(404, "no open ENTER_TEXT pending with that id")
    try:
        return _outcome(confirmations.answer_text(rt, dict(p), body.text, None, "simulator"))
    except Skip as e:
        return {"status": "SKIPPED", "detail": str(e)}


class Undo(BaseModel):
    transaction_id: int
    shop_id: int = 1


@router.post("/simulate/undo")
def undo(request: Request, body: Undo, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    return _outcome(confirmations.undo(_rt(request), body.transaction_id, body.shop_id, None, "simulator", None))


@router.post("/webhook-replay")
def webhook_replay(request: Request, update: dict = Body(...), x_admin_key: str | None = Header(default=None)):
    """Feed a stored Telegram update through the real webhook handler (used by app.cli.replay)."""
    admin(request, x_admin_key)
    from fastapi import BackgroundTasks
    from app.api.telegram import webhook
    bg = BackgroundTasks()
    out = webhook(request, bg, update, _rt(request).settings.telegram_webhook_secret)
    return out


@router.get("/trace/{message_id}")
def trace(request: Request, message_id: int, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    with _rt(request).engine.connect() as conn:
        m = conn.execute(text("SELECT * FROM messages WHERE id=:m"), {"m": message_id}).mappings().first()
        if not m:
            raise HTTPException(404)
        calls = [dict(r) for r in conn.execute(text("SELECT stage, provider, model, prompt_version, latency_ms, input_tokens, "
                                                    "output_tokens, ok, error, created_at FROM llm_calls WHERE message_id=:m ORDER BY id"),
                                               {"m": message_id}).mappings()]
        txns = [dict(r) for r in conn.execute(text("SELECT * FROM transactions WHERE message_id=:m"), {"m": message_id}).mappings()]
        rows = {}
        for t in txns:
            rows[t["id"]] = {
                "movements": [dict(r) for r in conn.execute(text("SELECT product_id, qty_delta, reason FROM inventory_movements WHERE transaction_id=:t"), {"t": t["id"]}).mappings()],
                "ledger": [dict(r) for r in conn.execute(text("SELECT customer_id, amount_paise FROM credit_ledger WHERE transaction_id=:t"), {"t": t["id"]}).mappings()],
            }
        pend = [dict(r) for r in conn.execute(text("SELECT id, kind, field, status, options, created_at, resolved_at FROM pending_actions WHERE message_id=:m ORDER BY created_at"), {"m": message_id}).mappings()]
        jobs_ = [dict(r) for r in conn.execute(text("SELECT id, status, attempts, last_error, run_after FROM jobs WHERE dedupe_key=:k"), {"k": f"msg:{message_id}"}).mappings()]
        audit = [dict(r) for r in conn.execute(text("SELECT actor, action, entity, entity_id, details, created_at FROM audit_logs WHERE entity_id=:e OR (entity='message' AND entity_id=:e) ORDER BY id"), {"e": str(message_id)}).mappings()]
    return {"message": dict(m), "stages": {"transcript": m["transcript"], "normalized": m["normalized_text"],
                                          "parsed": m["parsed"], "parser_used": m["parser_used"], "decision": m["decision"]},
            "llm_calls": calls, "transactions": txns, "rows_written": rows, "pending_actions": pend, "jobs": jobs_, "audit": audit}


@router.get("/stats")
def stats(request: Request, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    with _rt(request).engine.connect() as conn:
        out = queries.counts(conn)
        out["worker_alive"] = jobs.worker_alive(conn)
        out["stt_provider"] = _rt(request).settings.stt_provider
        out["parser_mode"] = _rt(request).settings.parser_mode
        return out


@router.post("/reap")
def reap(request: Request, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    with _rt(request).engine.begin() as conn:
        return jobs.reap(conn)
