"""Telegram webhook. Does two inserts and returns 200; everything slow happens in the worker.
Text commands (/balance etc.) are read-only and answered inline."""
import hmac
import logging

import jwt
from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, Request
from sqlalchemy import text

from app.domain.models import Overrides
from app.domain.money import fmt_inr
from app.services import catalog, confirmations, jobs, queries, replies
from app.services.pipeline import claim_message, process_message, start_catalog_wizard

log = logging.getLogger(__name__)
router = APIRouter()


def _rt(request: Request):
    return request.app.state.runtime


def _check_secret(given: str | None, expected: str) -> None:
    if not expected or not given or not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="bad secret")


@router.post("/telegram/webhook")
def webhook(request: Request, background: BackgroundTasks, update: dict = Body(...),
            x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    rt = _rt(request)
    _check_secret(x_telegram_bot_api_secret_token, rt.settings.telegram_webhook_secret)
    if cb := update.get("callback_query"):
        user_id = cb["from"]["id"]
        with rt.engine.connect() as conn:
            member = queries.member(conn, user_id)
        if not member:
            background.add_task(rt.tg.answer_callback, cb["id"], "Registered nahi hai.")
            return {"ok": True}
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        background.add_task(_safe, confirmations.resolve_callback, rt, cb.get("data", ""), chat_id, user_id,
                            member["shop_id"], cb["id"])
        return {"ok": True}
    msg = update.get("message")
    if not msg or "from" not in msg:
        return {"ok": True}
    user_id, chat_id = msg["from"]["id"], msg["chat"]["id"]
    text_ = msg.get("text")
    if text_ and text_.startswith("/start"):
        background.add_task(rt.tg.send, chat_id, f"Namaste! Aapka Telegram user id: {user_id}\n"
                            f"Admin se is id ko shop mein register karwayein, phir voice note bhejiye.")
        return {"ok": True}
    with rt.engine.connect() as conn:
        member = queries.member(conn, user_id)
    if not member:                                            # unknown sender: never touch the DB
        background.add_task(rt.tg.send, chat_id, "Yeh bot aapke liye registered nahi hai.")
        return {"ok": True}
    if text_ and text_.startswith("/"):
        c = text_.split()[0].split("@")[0].lower()
        if c in ("/add_product", "/add_customer"):                          # wizards: buttons, not error messages
            if member["role"] != "OWNER":
                background.add_task(rt.tg.send, chat_id, "Sirf owner catalog badal sakta hai.")
                return {"ok": True}
            arg = text_.split(maxsplit=1)[1] if len(text_.split()) > 1 else ""
            o = Overrides(catalog_only=True)
            if c == "/add_customer":
                o.new_customer_name = " ".join(arg.split()).title()            # "" => ask for the name
            else:
                parsed = catalog.parse_product_args(arg)
                o.new_product_name, o.new_product_unit = parsed["name"], parsed["unit"]
                o.new_product_price_paise = None if parsed["ambiguous_price"] else parsed["price_paise"]
            background.add_task(_safe, start_catalog_wizard, rt, member["shop_id"], chat_id, user_id, msg["message_id"], text_, o)
            return {"ok": True}
        background.add_task(rt.tg.send, chat_id, command_reply(rt, member["shop_id"], text_, user_id, member["role"]))
        return {"ok": True}
    voice = msg.get("voice") or msg.get("audio")
    key = f"tg:{chat_id}:{msg['message_id']}"
    with rt.engine.begin() as conn:
        if voice:
            mid = claim_message(conn, member["shop_id"], key, "TELEGRAM", "VOICE", chat_id, user_id, msg["message_id"],
                                file_id=voice["file_id"], duration=voice.get("duration"))
        elif text_:
            mid = claim_message(conn, member["shop_id"], key, "TELEGRAM", "TEXT", chat_id, user_id, msg["message_id"],
                                transcript=text_)
        else:
            mid = None
    if mid and rt.settings.run_worker_inline:
        background.add_task(_safe, process_message, rt, mid)
    return {"ok": True}


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:                                          # noqa: BLE001
        log.exception("background task failed")


CATALOG_COMMANDS = {"/alias", "/price", "/remove_product", "/remove_customer"}


def command_reply(rt, shop_id: int, cmd: str, user_id: int, role: str = "OWNER") -> str:
    c = cmd.split()[0].split("@")[0].lower()
    if c in CATALOG_COMMANDS:
        if role != "OWNER":
            return "Sirf owner catalog badal sakta hai."
        with rt.engine.begin() as conn:
            return catalog_command(conn, shop_id, c, cmd.split(maxsplit=1)[1] if len(cmd.split()) > 1 else "", f"tg:{user_id}")
    with rt.engine.connect() as conn:
        if c == "/help":
            return replies.HELP
        if c == "/products":
            rows = queries.stock(conn, shop_id)
            return "Items:\n" + "\n".join(
                f"• {r['name']} ({r['base_unit']}) "
                f"{fmt_inr(r['selling_price_paise']) + '/' + r['base_unit'] if r['selling_price_paise'] else 'rate nahi'}"
                for r in rows)
        if c == "/customers":
            rows = queries.balances(conn, shop_id)
            return "Customers:\n" + "\n".join(f"• {r['name']}: {fmt_inr(r['balance_paise'])}" for r in rows)
        if c == "/balance":
            rows = [r for r in queries.balances(conn, shop_id) if r["balance_paise"] != 0][:20]
            total = sum(r["balance_paise"] for r in rows if r["balance_paise"] > 0)
            body = "\n".join(f"• {r['name']}: {fmt_inr(r['balance_paise'])}" for r in rows) or "Koi udhaar nahi."
            return f"Udhaar (kul {fmt_inr(total)}):\n{body}"
        if c == "/stock":
            rows = queries.stock(conn, shop_id)
            return "Stock:\n" + "\n".join(
                f"• {r['name']}: {r['stock']:.3f} {r['base_unit']}".replace(".000", "")
                + (" ⚠️" if r["low_stock_threshold"] is not None and r["stock"] < r["low_stock_threshold"] else "")
                for r in rows)
        if c == "/last":
            rows = queries.recent_transactions(conn, shop_id, limit=5)
            return "Aakhri entries:\n" + "\n".join(
                f"#{r['id']} {replies.TYPE_LABEL.get(r['type'], r['type'])}: {r['customer'] or ''} {r['product'] or ''} "
                f"{r['quantity'] or ''} {r['unit'] or ''} {fmt_inr(r['amount_paise']) if r['amount_paise'] else ''}"
                + (" (undone)" if r["reversed"] else "") for r in rows)
        if c == "/dashboard":
            token = jwt.encode({"shop_id": shop_id, "sub": str(user_id), "exp": __import__("time").time() + 86400},
                               rt.settings.jwt_secret, algorithm="HS256")
            return f"Dashboard: http://localhost:8000/dashboard/#token={token}"
        if c == "/stats":
            return str(queries.counts(conn))
    return replies.HELP


@router.get("/telegram/queue")
def queue_depth(request: Request):
    with _rt(request).engine.connect() as conn:
        return {"alive": jobs.worker_alive(conn), **{k: v for k, v in queries.counts(conn).items() if k.startswith(("queue", "jobs"))}}


_ = text  # keep import for type checkers


def catalog_command(conn, shop_id: int, c: str, arg: str, actor: str) -> str:
    """/add_product <name> <kg|litre|piece|packet> [<rupees>] · /add_customer <name> · /alias <item|customer> <spelling>
       /price <item> <rupees> · /remove_product <item> · /remove_customer <name>"""
    words = arg.replace("=", " ").replace(",", " ").split()
    try:
        if c == "/alias":
            if len(words) < 2:
                return "Aise: /alias Rice chaval   ya   /alias \"Ramesh Kumar\" rameshji"
            alias = words[-1]; target = " ".join(words[:-1]).strip('"')
            r = catalog.find_product(conn, shop_id, target)
            if r.status == "RESOLVED":
                ok = catalog.add_alias(conn, shop_id, "products", r.value.id, alias, actor)
                return f"✅ \"{alias}\" = {r.value.name}" if ok else f"\"{alias}\" pehle se {r.value.name} ka naam hai"
            rc = catalog.find_customer(conn, shop_id, target)
            if rc.status == "RESOLVED":
                ok = catalog.add_alias(conn, shop_id, "customers", rc.value.id, alias, actor)
                return f"✅ \"{alias}\" = {rc.value.name}" if ok else f"\"{alias}\" pehle se {rc.value.name} ka naam hai"
            return f"\"{target}\" na item mila na customer. Pehle /add_product ya /add_customer."
        if c == "/price":
            parsed = catalog.parse_product_args(arg)
            if not parsed["name"] or parsed["price_paise"] is None:
                return "Aise: /price Rice 52   (item ka naam aur naya rate)"
            price = parsed["price_paise"]
            r = catalog.find_product(conn, shop_id, parsed["name"])
            if r.status != "RESOLVED":
                return f"Item nahi mila ({r.status.lower()}). /products dekhein."
            catalog.set_price(conn, shop_id, r.value.id, price, actor)
            return f"✅ {r.value.name}: {fmt_inr(price)}/{r.value.base_unit}"
        if c == "/remove_product":
            r = catalog.find_product(conn, shop_id, arg)
            if r.status != "RESOLVED":
                return f"Item nahi mila ({r.status.lower()})."
            catalog.deactivate(conn, shop_id, "products", r.value.id, actor)
            return f"✅ {r.value.name} hata diya (history rahegi)."
        if c == "/remove_customer":
            r = catalog.find_customer(conn, shop_id, arg)
            if r.status != "RESOLVED":
                return f"Customer nahi mila ({r.status.lower()})."
            catalog.deactivate(conn, shop_id, "customers", r.value.id, actor)
            return f"✅ {r.value.name} hata diya (khata history rahegi)."
    except ValueError as e:
        return f"Nahi hua: {e}"
    return replies.HELP
