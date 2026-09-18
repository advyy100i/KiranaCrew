"""Human confirmation: button callbacks, typed answers to open questions, and undo.

A callback is resolved with a conditional UPDATE (status='PENDING' AND expires_at > now()); only the first tap gets
a row back, so a double tap or a stale button cannot post twice. The choice is merged into the draft overrides and
decide_and_post_in re-runs the *same* decision path inside the same transaction.
"""
import json
import re
import uuid
from dataclasses import asdict
from decimal import Decimal

from sqlalchemy import text

from app.domain.decide import decide
from app.domain.models import Overrides
from app.domain.money import rupees_to_paise, to_base_qty
from app.domain.normalize import UNITS, normalize
from app.services import queries, replies
from app.services.bookkeeping import reverse_transaction
from app.services.pipeline import Outcome, Runtime, Skip, audit, decide_and_post_in, deliver

CB = re.compile(r"^pa:([0-9a-f]{32}):(\d{1,2})$")
UNDO = re.compile(r"^un:(\d{1,12})$")


def _overrides(draft: dict) -> Overrides:
    o = Overrides(**{k: v for k, v in (draft.get("overrides") or {}).items() if k in Overrides.__dataclass_fields__})
    if o.quantity_base is not None:
        o.quantity_base = Decimal(str(o.quantity_base))
    return o


def open_enter_text(conn, chat_id: int) -> dict | None:
    r = conn.execute(text("""SELECT id, message_id, shop_id, kind, field, draft, telegram_reply_message_id, telegram_chat_id
                             FROM pending_actions WHERE telegram_chat_id=:c AND status='PENDING' AND kind='ENTER_TEXT'
                               AND expires_at > now() ORDER BY created_at DESC LIMIT 1"""), {"c": chat_id}).mappings().first()
    return dict(r) if r else None


# ----------------------------------------------------------------------------- callbacks
def resolve_callback(rt: Runtime, data: str, chat_id: int | None, user_id: int | None, shop_id: int,
                     callback_id: str | None = None) -> Outcome | None:
    actor = f"tg:{user_id}" if user_id else "simulator"
    if m := UNDO.match(data or ""):
        return undo(rt, int(m.group(1)), shop_id, chat_id, actor, callback_id)
    m = CB.match(data or "")
    if not m:
        _answer(rt, callback_id, "?")
        return None
    pid, idx = str(uuid.UUID(m.group(1))), int(m.group(2))
    with rt.engine.begin() as conn:
        row = conn.execute(text("""
            UPDATE pending_actions SET status='RESOLVED', resolved_at=now()
            WHERE id=CAST(:id AS uuid) AND status='PENDING' AND expires_at > now() AND shop_id=:s
            RETURNING id, message_id, kind, field, draft, options, telegram_chat_id, telegram_reply_message_id"""),
            {"id": pid, "s": shop_id}).mappings().first()
        if row is None:
            state = conn.execute(text("SELECT status, expires_at < now() AS expired FROM pending_actions WHERE id=CAST(:id AS uuid)"),
                                 {"id": pid}).first()
            msg = "Yeh sawaal purana ho gaya." if (state and (state[1] or state[0] == "EXPIRED")) else "Yeh pehle hi ho chuka hai."
            _answer(rt, callback_id, msg)
            return None
        row = dict(row)
        options = row["options"] if isinstance(row["options"], list) else json.loads(row["options"])
        if idx >= len(options):
            _answer(rt, callback_id, "?")
            return None
        choice = options[idx]
        if choice.get("cancel"):
            conn.execute(text("UPDATE pending_actions SET status='CANCELLED' WHERE id=CAST(:id AS uuid)"), {"id": pid})
            conn.execute(text("UPDATE messages SET status='REJECTED', error='cancelled by user', updated_at=now() WHERE id=:m"),
                         {"m": row["message_id"]})
            audit(conn, shop_id, actor, "CANCELLED", "pending_action", pid, {})
            out = Outcome(row["message_id"], "REJECTED", "❌ Cancel ho gaya. Dobara bhejiye.", chat_id=row["telegram_chat_id"])
        else:
            o = _overrides(row["draft"] if isinstance(row["draft"], dict) else json.loads(row["draft"]))
            for k, v in (choice.get("set") or {}).items():
                setattr(o, k, v)
            if choice.get("learn_alias"):                                  # "did you mean X?" -> teach that spelling
                from app.services.catalog import add_alias
                table, eid, alias = choice["learn_alias"]
                if alias:
                    add_alias(conn, shop_id, table, int(eid), alias, actor)
            try:
                out = decide_and_post_in(conn, rt, row["message_id"], o, actor)
            except Skip:
                _answer(rt, callback_id, "Yeh pehle hi ho chuka hai.")
                return None
    _answer(rt, callback_id, "✔")
    if rt.tg and row["telegram_chat_id"] and row["telegram_reply_message_id"]:
        rt.tg.clear_buttons(row["telegram_chat_id"], row["telegram_reply_message_id"])
    deliver(rt, out)
    return out


def _answer(rt: Runtime, callback_id: str | None, text_: str) -> None:
    if rt.tg and callback_id:
        rt.tg.answer_callback(callback_id, text_)


# ----------------------------------------------------------------------------- typed answers
NUM = re.compile(r"^\d+(\.\d+)?$")


def answer_text(rt: Runtime, pending: dict, answer: str, answering_message_id: int | None, actor: str) -> Outcome:
    """The next message in a chat with an open ENTER_TEXT question answers it. Numbers go through the normalizer
    ("dhai sau" -> 250); names/products patch the stored ParsedCommand; then the same decision path runs."""
    norm = normalize(answer)
    toks = norm.split()
    nums = [Decimal(t) for t in toks if NUM.match(t)]
    unit = next((t for t in toks if t in UNITS), None)
    fld = pending["field"]
    pid = str(pending["id"])
    with rt.engine.begin() as conn:
        row = conn.execute(text("""UPDATE pending_actions SET status='RESOLVED', resolved_at=now()
                                   WHERE id=CAST(:id AS uuid) AND status='PENDING' RETURNING draft, message_id, shop_id"""),
                           {"id": pid}).mappings().first()
        if row is None:
            raise Skip("pending already resolved")
        o = _overrides(row["draft"] if isinstance(row["draft"], dict) else json.loads(row["draft"]))
        mid, shop_id = row["message_id"], row["shop_id"]
        if answering_message_id:
            conn.execute(text("""UPDATE messages SET status='REJECTED', normalized_text=:n,
                                 decision=CAST(:d AS jsonb), updated_at=now() WHERE id=:m"""),
                         {"n": norm, "d": json.dumps({"answered_pending": pid, "for_message": mid}), "m": answering_message_id})
        reask = None
        if fld in ("quantity", "amount", "price", "new_price"):
            if len(nums) != 1:
                reask = "Sirf ek number bataiye (jaise: 2 kilo / 250)."
            elif fld == "quantity":
                shop_products = queries.load_catalog(conn, shop_id)[1]
                cmd_row = conn.execute(text("SELECT parsed FROM messages WHERE id=:m"), {"m": mid}).scalar()
                from app.domain.models import ParsedCommand
                d = decide(ParsedCommand.model_validate(cmd_row or {}), queries.load_catalog(conn, shop_id)[0], shop_products,
                           10**12, o)
                base = d.product.base_unit if d.product else None
                q = to_base_qty(nums[0], unit or base, base) if base else None
                if q is None or q <= 0:
                    reask = f"{d.product.name if d.product else 'Item'} {base} mein bikta hai — kitne {base}?"
                else:
                    o.quantity_base = q
            elif fld == "amount":
                o.total_amount_paise = rupees_to_paise(nums[0])
            elif fld == "price":
                o.unit_price_paise = rupees_to_paise(nums[0])
            elif fld == "new_price":
                o.new_product_price_paise = rupees_to_paise(nums[0])
        elif fld in ("new_name", "new_customer"):                             # catalog wizard answers
            words = " ".join(t for t in toks if not NUM.match(t) and t != "rupaye")
            if not words:
                reask = "Naam likhiye."
            elif fld == "new_name":
                from app.services.catalog import parse_product_args
                parsed = parse_product_args(answer)                           # "Maggi packet 14" works here too
                o.new_product_name = parsed["name"] or words.title()
                o.new_product_unit = o.new_product_unit or parsed["unit"]
                o.new_product_price_paise = o.new_product_price_paise or parsed["price_paise"]
            else:
                o.new_customer_name = words.title()
        elif fld in ("customer", "product"):
            words = " ".join(t for t in toks if not NUM.match(t) and t not in UNITS and t != "rupaye")
            if not words:
                reask = "Naam likhiye."
            else:
                key = "customer_mention" if fld == "customer" else "product_mention"
                conn.execute(text("UPDATE messages SET parsed = parsed || CAST(:p AS jsonb), updated_at=now() WHERE id=:m"),
                             {"p": json.dumps({key: words}), "m": mid})
                if fld == "customer":
                    o.customer_id = None; o.new_customer_name = None
                else:
                    o.product_id = None
        if reask:
            from app.services.pipeline import _create_pending
            mrow = conn.execute(text("SELECT * FROM messages WHERE id=:m FOR UPDATE"), {"m": mid}).mappings().first()
            npid = _create_pending(conn, dict(mrow), "ENTER_TEXT", fld, o, [{"label": "❌ Cancel", "cancel": True}], rt.settings)
            out = Outcome(mid, "AWAITING_CONFIRMATION", reask, [[replies.pa_button("❌ Cancel", npid, 0)]],
                          pending_id=npid, chat_id=pending.get("telegram_chat_id"))
        else:
            out = decide_and_post_in(conn, rt, mid, o, actor)
    if rt.tg and pending.get("telegram_chat_id") and pending.get("telegram_reply_message_id"):
        rt.tg.clear_buttons(pending["telegram_chat_id"], pending["telegram_reply_message_id"])
    deliver(rt, out)
    return out


# ----------------------------------------------------------------------------- undo
def undo(rt: Runtime, txn_id: int, shop_id: int, chat_id: int | None, actor: str, callback_id: str | None) -> Outcome | None:
    with rt.engine.begin() as conn:
        t = conn.execute(text("""SELECT id, message_id, created_at < now() - (:w * interval '1 minute') AS too_old
                                 FROM transactions WHERE id=:t AND shop_id=:s"""),
                         {"t": txn_id, "s": shop_id, "w": rt.settings.undo_window_minutes}).first()
        if t is None:
            _answer(rt, callback_id, "Entry nahi mili."); return None
        if t[2]:
            _answer(rt, callback_id, f"Undo sirf {rt.settings.undo_window_minutes} minute tak."); return None
        rev = reverse_transaction(conn, shop_id, txn_id, actor)
        if rev is None:
            _answer(rt, callback_id, "Pehle hi undo ho chuki hai."); return None
        audit(conn, shop_id, actor, "REVERSED", "transaction", txn_id, {"reversal_id": rev})
        out = Outcome(t[1] or 0, "REVERSED", replies.undone(txn_id), txn_id=rev, chat_id=chat_id)
    _answer(rt, callback_id, "↩️")
    deliver(rt, out)
    return out


def overrides_dict(o: Overrides) -> dict:
    return asdict(o)
