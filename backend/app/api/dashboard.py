"""Read-only dashboard API. Bearer JWT (HS256) carrying shop_id, issued by the /dashboard bot command."""
import jwt
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text

from app.services import queries

router = APIRouter(prefix="/api/v1/dashboard")


def _shop(request: Request, authorization: str | None) -> int:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "bearer token required")
    try:
        claims = jwt.decode(authorization.split(" ", 1)[1], request.app.state.runtime.settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"bad token: {e}")
    return int(claims["shop_id"])


@router.get("/today")
def today(request: Request, authorization: str | None = Header(default=None)):
    sid = _shop(request, authorization)
    with request.app.state.runtime.engine.connect() as conn:
        s = queries.daily_summary(conn, sid)
        s["insight"] = conn.execute(text("SELECT body FROM insights WHERE shop_id=:s AND approved ORDER BY week_start DESC LIMIT 1"),
                                    {"s": sid}).scalar()
        return s


@router.get("/customers")
def customers(request: Request, authorization: str | None = Header(default=None)):
    sid = _shop(request, authorization)
    with request.app.state.runtime.engine.connect() as conn:
        return queries.balances(conn, sid)


@router.get("/customers/{customer_id}")
def customer(request: Request, customer_id: int, authorization: str | None = Header(default=None)):
    sid = _shop(request, authorization)
    with request.app.state.runtime.engine.connect() as conn:
        return {"balance_paise": queries.customer_balance(conn, sid, customer_id),
                "history": queries.customer_history(conn, sid, customer_id)}


@router.get("/inventory")
def inventory(request: Request, authorization: str | None = Header(default=None)):
    sid = _shop(request, authorization)
    with request.app.state.runtime.engine.connect() as conn:
        rows = queries.stock(conn, sid)
        fc = conn.execute(text("""SELECT product_id, horizon_date, qty_forecast, model, baseline_mase, model_mase FROM forecasts
                                  WHERE shop_id=:s AND horizon_date >= CURRENT_DATE AND model <> 'SeasonalNaive'
                                    AND (model_mase IS NULL OR baseline_mase IS NULL OR model_mase < baseline_mase)
                                  ORDER BY product_id, horizon_date"""), {"s": sid}).mappings()
        by_p: dict[int, list] = {}
        for r in fc:
            by_p.setdefault(r["product_id"], []).append({"date": r["horizon_date"].isoformat(), "qty": float(r["qty_forecast"]),
                                                          "model": r["model"]})
        for r in rows:
            r["forecast_7d"] = by_p.get(r["product_id"], [])
        return rows


@router.get("/transactions")
def transactions(request: Request, limit: int = 50, offset: int = 0, authorization: str | None = Header(default=None)):
    sid = _shop(request, authorization)
    with request.app.state.runtime.engine.connect() as conn:
        rows = queries.recent_transactions(conn, sid, min(limit, 200), offset)
        for r in rows:
            r["stt_latency_ms"] = conn.execute(text("SELECT MAX(latency_ms) FROM llm_calls WHERE message_id=:m AND stage='STT'"),
                                               {"m": r["message_id"]}).scalar() if r["message_id"] else None
        return rows


@router.get("/messages")
def messages(request: Request, limit: int = 50, authorization: str | None = Header(default=None)):
    sid = _shop(request, authorization)
    with request.app.state.runtime.engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(
            "SELECT id, kind, status, transcript, parser_used, stt_provider, stt_confidence, decision, created_at "
            "FROM messages WHERE shop_id=:s ORDER BY id DESC LIMIT :l"), {"s": sid, "l": min(limit, 200)}).mappings()]
