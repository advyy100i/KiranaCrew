"""Message pipeline. Three short DB transactions per message, external calls only between them:

  A. lock + mark PROCESSING          (FOR UPDATE SKIP LOCKED: two workers never share a message)
  -- STT / LLM happen here, outside any transaction --
  B. persist transcript / normalized / parsed
  C. decide, then post transaction | create pending | reject — atomically, with a status re-check
  -- Telegram is called only after C commits --
"""
import json
import logging
from dataclasses import asdict, dataclass, field

from sqlalchemy import text

from app.config import Settings, settings as default_settings
from app.domain.decide import decide
from app.domain.models import Decision, Overrides, ParsedCommand
from app.domain.normalize import normalize
from app.domain.parser import parse
from app.domain.resolve import product_vocab
from app.services import jobs, queries, replies
from app.services.bookkeeping import post_decision

log = logging.getLogger(__name__)


class Skip(Exception):
    """Raised when this message needs no further work (duplicate delivery, already handled)."""


@dataclass
class Runtime:
    """Injected dependencies. Tests pass FakeTelegram + FixtureSTT + stub LLMs."""
    engine: object
    tg: object | None = None
    stt: object | None = None
    llm_providers: list = field(default_factory=list)
    settings: Settings = field(default_factory=lambda: default_settings)


@dataclass
class Outcome:
    message_id: int
    status: str
    reply: str
    buttons: list = field(default_factory=list)
    pending_id: str | None = None
    txn_id: int | None = None
    chat_id: int | None = None
    decision: dict | None = None


# ----------------------------------------------------------------------------- claim
def claim_message(conn, shop_id: int, key: str, source: str, kind: str, chat_id: int | None = None,
                  user_id: int | None = None, msg_id: int | None = None, file_id: str | None = None,
                  duration: int | None = None, transcript: str | None = None, enqueue: bool = True) -> int | None:
    """Layer 1 of idempotency: one row per idempotency_key. Enqueues the job in the same transaction (layer 2)."""
    mid = conn.execute(text("""
        INSERT INTO messages (shop_id, idempotency_key, source, telegram_chat_id, telegram_user_id, telegram_message_id,
                              kind, telegram_file_id, audio_duration_s, transcript)
        VALUES (:s, :k, :src, :chat, :user, :mid, :kind, :fid, :dur, :t)
        ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"""),
        {"s": shop_id, "k": key, "src": source, "chat": chat_id, "user": user_id, "mid": msg_id, "kind": kind,
         "fid": file_id, "dur": duration, "t": transcript}).scalar()
    if mid:
        if enqueue:
            jobs.enqueue(conn, "PROCESS_MESSAGE", {"message_id": mid}, dedupe_key=f"msg:{mid}")
        audit(conn, shop_id, f"tg:{user_id}" if user_id else source.lower(), "MESSAGE_RECEIVED", "message", mid,
              {"kind": kind, "key": key})
    return mid


def audit(conn, shop_id, actor, action, entity=None, entity_id=None, details=None) -> None:
    conn.execute(text("""INSERT INTO audit_logs (shop_id, actor, action, entity, entity_id, details)
                         VALUES (:s, :a, :ac, :e, :eid, CAST(:d AS jsonb))"""),
                 {"s": shop_id, "a": actor, "ac": action, "e": entity, "eid": str(entity_id) if entity_id else None,
                  "d": json.dumps(details or {}, default=str)})


def log_call(conn, message_id: int | None, stage: str, provider: str, model: str, latency_ms: int, ok: bool,
             error: str | None = None, prompt_version: str | None = None, input_tokens=None, output_tokens=None) -> None:
    conn.execute(text("""INSERT INTO llm_calls (message_id, stage, provider, model, prompt_version, latency_ms,
                                                input_tokens, output_tokens, ok, error)
                         VALUES (:m, :st, :p, :mo, :pv, :l, :it, :ot, :ok, :e)"""),
                 {"m": message_id, "st": stage, "p": provider or "?", "mo": model or "?", "pv": prompt_version,
                  "l": int(latency_ms or 0), "it": input_tokens, "ot": output_tokens, "ok": ok, "e": error})


# ----------------------------------------------------------------------------- process
def process_message(rt: Runtime, message_id: int) -> Outcome | None:
    s = rt.settings
    # A. lock and mark PROCESSING
    with rt.engine.begin() as conn:
        row = conn.execute(text("SELECT * FROM messages WHERE id=:m FOR UPDATE SKIP LOCKED"),
                           {"m": message_id}).mappings().first()
        if row is None:
            return None                                    # another worker holds it
        if row["status"] not in ("RECEIVED", "PROCESSING"):
            return None                                    # already handled (status guard)
        conn.execute(text("UPDATE messages SET status='PROCESSING', attempts=attempts+1, updated_at=now() WHERE id=:m"),
                     {"m": message_id})
        row = dict(row)

    # -- STT, outside any transaction --
    transcript = row["transcript"]
    stt_provider, stt_conf = row["stt_provider"], row["stt_confidence"]
    if row["kind"] == "VOICE" and transcript is None:
        guard = _voice_guard(row, s)
        if guard:
            return _fail(rt, row, guard, retry=False)
        try:
            audio, filename = rt.tg.download(row["telegram_file_id"], s.max_voice_bytes)
            result = rt.stt.transcribe(audio, filename, language="hi")
        except Exception as e:                            # noqa: BLE001 - worker retries with backoff
            with rt.engine.begin() as conn:
                for a in getattr(rt.stt, "attempts", []):
                    log_call(conn, message_id, "STT", a.get("provider"), a.get("model"), a.get("latency_ms", 0),
                             a.get("ok", False), a.get("error"))
                conn.execute(text("UPDATE messages SET error=:e, updated_at=now() WHERE id=:m"),
                             {"e": f"stt: {e}"[:500], "m": message_id})
            raise
        transcript, stt_provider = result.text, result.provider
        stt_conf = result.avg_logprob
        if result.no_speech_prob is not None and result.no_speech_prob > s.stt_max_no_speech_prob:
            stt_conf = -9.0                                # "probably not speech": force CONFIRM_TRANSCRIPT
        with rt.engine.begin() as conn:                    # B1
            for a in getattr(rt.stt, "attempts", None) or [{"provider": result.provider, "model": result.model,
                                                               "ok": True, "latency_ms": result.latency_ms}]:
                log_call(conn, message_id, "STT", a.get("provider"), a.get("model"), a.get("latency_ms", 0),
                         a.get("ok", False), a.get("error"))
            conn.execute(text("""UPDATE messages SET transcript=:t, stt_provider=:p, stt_confidence=:c, updated_at=now()
                                 WHERE id=:m"""), {"t": transcript, "p": stt_provider, "c": stt_conf, "m": message_id})
        if not transcript.strip():
            return _fail(rt, row, "stt", retry=False)

    # an open ENTER_TEXT question in this chat? then this message is the answer
    if row["telegram_chat_id"]:
        from app.services import confirmations
        with rt.engine.connect() as conn:
            open_pending = confirmations.open_enter_text(conn, row["telegram_chat_id"])
        if open_pending and open_pending["message_id"] != message_id:
            return confirmations.answer_text(rt, open_pending, transcript, answering_message_id=message_id,
                                             actor=_actor(row))

    # -- normalize + parse (LLM only if rules are incomplete and mode allows) --
    calls: list[dict] = []
    with rt.engine.connect() as conn:
        _, products = queries.load_catalog(conn, row["shop_id"])
    norm = normalize(transcript)
    cmd, parser_used = parse(norm, product_vocab(products), s.parser_mode, rt.llm_providers, calls.append)
    with rt.engine.begin() as conn:                        # B2
        for c in calls:
            log_call(conn, message_id, "PARSE", c.get("provider"), c.get("model"), c.get("latency_ms", 0), c["ok"],
                     c.get("error"), c.get("prompt_version"), c.get("input_tokens"), c.get("output_tokens"))
        conn.execute(text("""UPDATE messages SET normalized_text=:n, parsed=CAST(:p AS jsonb), parser_used=:u,
                             updated_at=now() WHERE id=:m"""),
                     {"n": norm, "p": cmd.model_dump_json(), "u": parser_used, "m": message_id})

    # C. decide and post
    overrides = Overrides()
    return decide_and_post(rt, message_id, overrides, actor=_actor(row))


def _actor(row: dict) -> str:
    return f"tg:{row['telegram_user_id']}" if row.get("telegram_user_id") else row["source"].lower()


def _voice_guard(row: dict, s: Settings) -> str | None:
    d = row.get("audio_duration_s")
    if d is not None and d > s.max_voice_seconds:
        return "too_long"
    if d is not None and d < s.min_voice_seconds:
        return "too_short"
    return None


def _fail(rt: Runtime, row: dict, kind: str, retry: bool) -> Outcome:
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE messages SET status='FAILED', error=:e, updated_at=now() WHERE id=:m"),
                     {"e": kind, "m": row["id"]})
        audit(conn, row["shop_id"], "system", "FAILED", "message", row["id"], {"reason": kind})
    out = Outcome(row["id"], "FAILED", replies.failed(kind), chat_id=row["telegram_chat_id"])
    deliver(rt, out)
    return out


# ----------------------------------------------------------------------------- decide + post
def decide_and_post(rt: Runtime, message_id: int, overrides: Overrides, actor: str) -> Outcome:
    with rt.engine.begin() as conn:
        out = decide_and_post_in(conn, rt, message_id, overrides, actor)
    deliver(rt, out)
    return out


def decide_and_post_in(conn, rt: Runtime, message_id: int, overrides: Overrides, actor: str) -> Outcome:
    """Runs inside an open transaction: lock message, re-check status, decide, write rows. Caller commits."""
    s = rt.settings
    row = conn.execute(text("SELECT * FROM messages WHERE id=:m FOR UPDATE"), {"m": message_id}).mappings().first()
    if row is None or row["status"] not in ("PROCESSING", "AWAITING_CONFIRMATION"):
        raise Skip(f"message {message_id} is {row['status'] if row else 'missing'}")
    shop = queries.shop(conn, row["shop_id"])
    customers, products = queries.load_catalog(conn, row["shop_id"])
    cmd = ParsedCommand.model_validate(row["parsed"] or {})
    chat_id = row["telegram_chat_id"]

    # low-confidence transcript: confirm the words before booking anything
    low = (row["kind"] == "VOICE" and row["stt_confidence"] is not None
           and row["stt_confidence"] < s.stt_min_avg_logprob and not overrides.transcript_confirmed)
    if low:
        d = Decision("ASK", "CONFIRM_TRANSCRIPT", customer_mention=row["transcript"])
        options = [{"label": "✅ Haan, sahi hai", "set": {"transcript_confirmed": True}}, {"label": "❌ Nahi", "cancel": True}]
        pid = _create_pending(conn, row, "CONFIRM_TRANSCRIPT", None, overrides, options, s)
        textr, buttons = replies.ask(d, pid, options)
        _save_decision(conn, message_id, d, "AWAITING_CONFIRMATION")
        return Outcome(message_id, "AWAITING_CONFIRMATION", textr, buttons, pending_id=pid, chat_id=chat_id)

    if overrides.new_customer_name and overrides.customer_id is None:      # user accepted "naya customer banayein?"
        name = overrides.new_customer_name.strip().title()
        cid = conn.execute(text("""INSERT INTO customers (shop_id, name) VALUES (:s, :n)
                                   ON CONFLICT (shop_id, name) DO UPDATE SET name=EXCLUDED.name RETURNING id"""),
                           {"s": row["shop_id"], "n": name}).scalar_one()
        audit(conn, row["shop_id"], actor, "CUSTOMER_CREATED", "customer", cid, {"name": name})
        overrides.customer_id = cid
        customers, products = queries.load_catalog(conn, row["shop_id"])

    if overrides.catalog_only:                                                # /add_product, /add_customer wizards
        return _catalog_wizard(conn, rt, row, cmd, overrides, actor)
    if overrides.new_product_name and overrides.product_id is None:        # "naya item" flow: unit -> price -> create
        asked = _new_product_flow(conn, row, cmd, overrides, s, actor, price_needed=(
            cmd.transaction_type in ("CREDIT_SALE", "CASH_SALE", "SALE")
            and cmd.total_amount_rupees is None and cmd.unit_price_rupees is None))
        if asked:
            return asked
        customers, products = queries.load_catalog(conn, row["shop_id"])


    d = decide(cmd, customers, products, shop["confirm_above_paise"], overrides)
    if d.action == "COMMIT":
        txn = post_decision(conn, row["shop_id"], message_id, d, actor)
        bal = queries.customer_balance(conn, row["shop_id"], d.customer.id) if d.customer else None
        stock, low_t = queries.product_stock(conn, row["shop_id"], d.product.id) if d.product else (None, None)
        textr, buttons = replies.committed(d, txn, bal, stock, low_t)
        _save_decision(conn, message_id, d, "COMMITTED", txn)
        audit(conn, row["shop_id"], actor, "COMMITTED", "transaction", txn, _decision_json(d))
        return Outcome(message_id, "COMMITTED", textr, buttons, txn_id=txn, chat_id=chat_id, decision=_decision_json(d))
    if d.action == "ASK":
        kind, fld, options = build_options(d, customers, products)
        pid = _create_pending(conn, row, kind, fld, overrides, options, s)
        textr, buttons = replies.ask(d, pid, options)
        _save_decision(conn, message_id, d, "AWAITING_CONFIRMATION")
        audit(conn, row["shop_id"], actor, "ASKED", "pending_action", pid, {"reason": d.reason})
        return Outcome(message_id, "AWAITING_CONFIRMATION", textr, buttons, pending_id=pid, chat_id=chat_id,
                       decision=_decision_json(d))
    bal = queries.customer_balance(conn, row["shop_id"], d.customer.id) if d.customer else None
    _save_decision(conn, message_id, d, "REJECTED")
    audit(conn, row["shop_id"], actor, "REJECTED", "message", message_id, {"reason": d.reason})
    return Outcome(message_id, "REJECTED", replies.rejected(d, bal), chat_id=chat_id, decision=_decision_json(d))


def build_options(d: Decision, customers, products) -> tuple[str, str | None, list[dict]]:
    """Map an ASK reason to a pending kind, an ENTER_TEXT field, and the button options."""
    r = d.reason
    cancel = {"label": "❌ Cancel", "cancel": True}
    if r in ("AMBIGUOUS_CUSTOMER", "UNKNOWN_CUSTOMER"):
        by_name = {c.name: c for c in customers}
        opts = [{"label": n, "set": {"customer_id": by_name[n].id}} for n in d.candidates if n in by_name]
        if d.customer_mention:
            opts.append({"label": f"➕ Naya: {d.customer_mention.title()}", "set": {"new_customer_name": d.customer_mention}})
        return ("CHOOSE_CUSTOMER" if opts else "ENTER_TEXT"), ("customer" if not opts else None), opts + [cancel]
    if r == "AMBIGUOUS_PRODUCT":
        by_name = {p.name: p for p in products}
        opts = [{"label": n, "set": {"product_id": by_name[n].id}} for n in d.candidates if n in by_name]
        return "CHOOSE_PRODUCT", None, opts + [cancel]
    if r == "UNKNOWN_PRODUCT":
        by_name = {p.name: p for p in products}
        opts = [{"label": f"{n}?", "set": {"product_id": by_name[n].id}, "learn_alias": ["products", by_name[n].id, d.product_mention]}
                for n in d.candidates if n in by_name]
        if d.product_mention:
            opts.append({"label": f"➕ Naya item: {d.product_mention.title()}", "set": {"new_product_name": d.product_mention}})
        return "CHOOSE_PRODUCT", None, opts + [cancel]
    if r == "CHOOSE_PAYMENT":
        return "CHOOSE_PAYMENT", None, [{"label": "📒 Udhaar", "set": {"payment": "CREDIT"}},
                                        {"label": "💵 Cash", "set": {"payment": "CASH"}}, cancel]
    if r == "CONFIRM_LARGE_AMOUNT":
        return "CONFIRM_LARGE_AMOUNT", None, [{"label": "✅ Haan, book karo", "set": {"confirm_large_amount": True}}, cancel]
    field_of = {"MISSING_CUSTOMER": "customer", "MISSING_PRODUCT": "product",
                "MISSING_QUANTITY": "quantity", "UNIT_MISMATCH": "quantity", "MISSING_AMOUNT": "amount",
                "MISSING_PRICE": "price"}
    return "ENTER_TEXT", field_of.get(r, "text"), [cancel]


def _create_pending(conn, row, kind: str, fld: str | None, overrides: Overrides, options: list[dict], s: Settings) -> str:
    conn.execute(text("UPDATE pending_actions SET status='CANCELLED', resolved_at=now() WHERE message_id=:m AND status='PENDING'"),
                 {"m": row["id"]})
    pid = conn.execute(text("""
        INSERT INTO pending_actions (shop_id, message_id, kind, field, draft, options, telegram_chat_id, expires_at)
        VALUES (:s, :m, :k, :f, CAST(:d AS jsonb), CAST(:o AS jsonb), :c, now() + (:ttl * interval '1 minute'))
        RETURNING id"""), {"s": row["shop_id"], "m": row["id"], "k": kind, "f": fld,
                           "d": json.dumps({"overrides": asdict(overrides)}, default=str), "o": json.dumps(options),
                           "c": row["telegram_chat_id"], "ttl": s.pending_ttl_minutes}).scalar_one()
    return str(pid)


def _decision_json(d: Decision) -> dict:
    return {"action": d.action, "reason": d.reason, "type": d.type,
            "customer": d.customer.name if d.customer else None, "customer_id": d.customer.id if d.customer else None,
            "product": d.product.name if d.product else None, "product_id": d.product.id if d.product else None,
            "quantity_base": str(d.quantity_base) if d.quantity_base is not None else None, "unit": d.unit,
            "unit_price_paise": d.unit_price_paise, "amount_paise": d.amount_paise, "price_source": d.price_source,
            "direction": d.direction, "candidates": d.candidates}


def _save_decision(conn, message_id: int, d: Decision, status: str, txn_id: int | None = None) -> None:
    j = _decision_json(d)
    if txn_id:
        j["transaction_id"] = txn_id
    conn.execute(text("UPDATE messages SET decision=CAST(:d AS jsonb), status=:s, updated_at=now() WHERE id=:m"),
                 {"d": json.dumps(j), "s": status, "m": message_id})


# ----------------------------------------------------------------------------- deliver (after commit)
def deliver(rt: Runtime, out: Outcome) -> None:
    if not out.chat_id or rt.tg is None:
        return
    reply_id = rt.tg.send(out.chat_id, out.reply, out.buttons or None)
    if reply_id and out.pending_id:
        with rt.engine.begin() as conn:
            conn.execute(text("UPDATE pending_actions SET telegram_reply_message_id=:r WHERE id=CAST(:p AS uuid)"),
                         {"r": reply_id, "p": out.pending_id})
    if reply_id is None:
        with rt.engine.begin() as conn:
            conn.execute(text("UPDATE messages SET error=COALESCE(error,'') || ' reply_failed' WHERE id=:m"),
                         {"m": out.message_id})



# ----------------------------------------------------------------------------- new product / catalog wizards
def _ask(conn, row, d: Decision, kind: str, fld: str | None, overrides: Overrides, options: list[dict], s: Settings) -> Outcome:
    pid = _create_pending(conn, row, kind, fld, overrides, options, s)
    textr, buttons = replies.ask(d, pid, options)
    _save_decision(conn, row["id"], d, "AWAITING_CONFIRMATION")
    return Outcome(row["id"], "AWAITING_CONFIRMATION", textr, buttons, pending_id=pid, chat_id=row["telegram_chat_id"])


def _new_product_flow(conn, row, cmd: ParsedCommand, o: Overrides, s: Settings, actor: str, price_needed: bool) -> Outcome | None:
    """Collect unit and price (each only if unknown), then create the product and set o.product_id.
    Returns an Outcome when a question was asked, None when the product now exists."""
    from app.domain.money import BASE_UNIT_OF
    from app.services import catalog
    name = (o.new_product_name or "").strip()
    cancel = {"label": "❌ Cancel", "cancel": True}
    if o.new_product_unit is None and cmd.unit in BASE_UNIT_OF:
        o.new_product_unit = BASE_UNIT_OF[cmd.unit]                          # "1 packet maggi" already told us
    if o.new_product_unit is None:
        d = Decision("ASK", "CHOOSE_UNIT", type=cmd.transaction_type, product_mention=name)
        opts = [{"label": u, "set": {"new_product_unit": u}} for u in ("kg", "litre", "piece", "packet")] + [cancel]
        return _ask(conn, row, d, "CHOOSE_PRODUCT", "unit", o, opts, s)
    if o.new_product_price_paise is None and price_needed:
        d = Decision("ASK", "ENTER_PRICE", type=cmd.transaction_type, product_mention=name, unit=o.new_product_unit)
        opts = [{"label": "⏭ Abhi nahi", "set": {"new_product_price_paise": 0}}, cancel]
        return _ask(conn, row, d, "ENTER_TEXT", "new_price", o, opts, s)
    o.product_id = catalog.add_product(conn, row["shop_id"], name, o.new_product_unit, o.new_product_price_paise or None,
                                       actor, aliases=[cmd.product_mention or ""])
    return None


def _catalog_wizard(conn, rt: Runtime, row, cmd: ParsedCommand, o: Overrides, actor: str) -> Outcome:
    """/add_product [args] and /add_customer [name]: ask for whatever is missing, create, reply. No transaction."""
    from app.domain.money import fmt_inr
    from app.services import catalog
    s = rt.settings
    cancel = {"label": "❌ Cancel", "cancel": True}
    if o.new_customer_name == "":                                             # /add_customer with no name
        return _ask(conn, row, Decision("ASK", "ENTER_NAME", product_mention="customer"), "ENTER_TEXT", "new_customer", o, [cancel], s)
    if o.new_customer_name:
        cid = catalog.add_customer(conn, row["shop_id"], o.new_customer_name, actor)
        _save_decision(conn, row["id"], Decision("COMMIT"), "DONE")
        return Outcome(row["id"], "DONE", f"✅ Naya customer: {o.new_customer_name.title()} (#{cid})", chat_id=row["telegram_chat_id"])
    if not o.new_product_name:
        return _ask(conn, row, Decision("ASK", "ENTER_NAME", product_mention="item"), "ENTER_TEXT", "new_name", o, [cancel], s)
    asked = _new_product_flow(conn, row, cmd, o, s, actor, price_needed=True)
    if asked:
        return asked
    p = conn.execute(text("SELECT name, base_unit, selling_price_paise FROM products WHERE id=:i"), {"i": o.product_id}).first()
    rate = f" @ {fmt_inr(p[2])}/{replies.UNIT_LABEL.get(p[1], p[1])}" if p[2] else " (rate nahi; bolte waqt bata dein)"
    _save_decision(conn, row["id"], Decision("COMMIT"), "DONE")
    example = f'Ramesh ne 2 {p[1]} {p[0].lower()} udhaar liya'
    return Outcome(row["id"], "DONE", f"✅ Naya item: {p[0]} ({p[1]}){rate}\nAb bolein: {example}",
                   chat_id=row["telegram_chat_id"])


def start_catalog_wizard(rt: Runtime, shop_id: int, chat_id: int, user_id: int, msg_id: int, cmd_text: str,
                         o: Overrides) -> Outcome | None:
    """Claim the command message (no job, no STT) and run the wizard synchronously. None = duplicate delivery."""
    with rt.engine.begin() as conn:
        mid = claim_message(conn, shop_id, f"tg:{chat_id}:{msg_id}", "TELEGRAM", "TEXT", chat_id, user_id, msg_id,
                            transcript=cmd_text, enqueue=False)
        if mid is None:
            return None
        conn.execute(text("UPDATE messages SET status='PROCESSING', parsed=CAST('{}' AS jsonb), parser_used='command', "
                          "updated_at=now() WHERE id=:m"), {"m": mid})
        out = decide_and_post_in(conn, rt, mid, o, f"tg:{user_id}")
    deliver(rt, out)
    return out
