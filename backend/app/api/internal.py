"""Endpoints for n8n (admin key). Reads + enqueue only; nothing here can create a transaction."""
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

from app.api.dev import admin
from app.domain.money import fmt_inr
from app.services import jobs, queries

router = APIRouter(prefix="/internal")


@router.post("/daily-summary")
def daily_summary(request: Request, shop_id: int = 1, send: bool = False, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    rt = request.app.state.runtime
    with rt.engine.connect() as conn:
        s = queries.daily_summary(conn, shop_id)
        chat = conn.execute(__import__("sqlalchemy").text(
            "SELECT telegram_user_id FROM shop_members WHERE shop_id=:s AND role='OWNER' LIMIT 1"), {"s": shop_id}).scalar()
    low = ", ".join(f"{r['name']} ({r['stock']:.0f} {r['base_unit']})" for r in s["low_stock"]) or "koi nahi"
    text = (f"📊 Aaj ka hisaab ({s['date']})\nBikri: {fmt_inr(s['sales_paise'])} (cash {fmt_inr(s['cash_sales_paise'])})\n"
            f"Udhaar diya: {fmt_inr(s['credit_given_paise'])}\nUdhaar wapas: {fmt_inr(s['credit_collected_paise'])}\n"
            f"Kul baaki: {fmt_inr(s['outstanding_paise'])}\nKam stock: {low}")
    sent = False
    if send and chat and rt.tg:
        sent = rt.tg.send(chat, text) is not None
    return {"summary": s, "text": text, "sent": sent}


class Enqueue(BaseModel):
    kind: str
    payload: dict = {}
    dedupe_key: str | None = None


@router.post("/enqueue")
def enqueue(request: Request, body: Enqueue, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    if body.kind not in ("DAILY_SUMMARY", "FORECAST", "INSIGHTS"):
        return {"error": "kind not allowed here"}
    with request.app.state.runtime.engine.begin() as conn:
        return {"job_id": jobs.enqueue(conn, body.kind, body.payload, body.dedupe_key)}


@router.get("/low-stock")
def low_stock(request: Request, shop_id: int = 1, x_admin_key: str | None = Header(default=None)):
    admin(request, x_admin_key)
    with request.app.state.runtime.engine.connect() as conn:
        return queries.low_stock(conn, shop_id)
