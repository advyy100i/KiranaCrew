"""Weekly shop-insights note. The only agentic part of the system, and it lives outside bookkeeping:

  1. build_facts()  — plain SQL computes every number (weekly sales, rising balances, slow stock, top balances).
  2. the crew       — three agents (credit-risk, stock, advisor) rank / explain / phrase. They never compute.
  3. a row in `insights` with approved=false; a human approves (CLI) before anything is sent.

Caps: one run per shop per week (UNIQUE shop_id, week_start), max 6 LLM calls, every call logged to llm_calls.
CrewAI is optional: if it is not installed, the same three prompts run sequentially through our LLM provider chain.
"""
import json
import logging
from datetime import date, timedelta

from sqlalchemy import text

from app.domain.money import fmt_inr

log = logging.getLogger("insights")
MAX_CALLS = 6


def build_facts(conn, shop_id: int, week_start: date) -> dict:
    """Everything the agents are allowed to know. Deterministic, unit-tested; stored in insights.facts."""
    wk_end = week_start + timedelta(days=7)
    sales = [dict(r) for r in conn.execute(text("""
        SELECT p.name AS product, p.base_unit, SUM(t.quantity) AS qty, SUM(t.amount_paise) AS amount_paise
        FROM transactions t JOIN products p ON p.shop_id=t.shop_id AND p.id=t.product_id
        WHERE t.shop_id=:s AND t.type IN ('CREDIT_SALE','CASH_SALE') AND t.created_at >= :a AND t.created_at < :b
          AND NOT EXISTS (SELECT 1 FROM transactions r WHERE r.reverses_transaction_id=t.id)
        GROUP BY p.name, p.base_unit ORDER BY amount_paise DESC"""), {"s": shop_id, "a": week_start, "b": wk_end}).mappings()]
    # customers whose balance rose for 3+ consecutive weeks without any repayment
    rising = []
    for c in conn.execute(text("SELECT id, name FROM customers WHERE shop_id=:s AND is_active"), {"s": shop_id}).mappings():
        weeks = []
        for k in range(3, 0, -1):
            a, b = wk_end - timedelta(days=7 * k), wk_end - timedelta(days=7 * (k - 1))
            r = conn.execute(text("""SELECT COALESCE(SUM(amount_paise),0), COALESCE(SUM(CASE WHEN amount_paise<0 THEN 1 END),0)
                                     FROM credit_ledger WHERE shop_id=:s AND customer_id=:c AND created_at >= :a AND created_at < :b"""),
                             {"s": shop_id, "c": c["id"], "a": a, "b": b}).first()
            weeks.append((int(r[0]), int(r[1])))
        if all(d > 0 and pays == 0 for d, pays in weeks):
            bal = conn.execute(text("SELECT balance_paise FROM v_customer_balance WHERE shop_id=:s AND customer_id=:c"),
                               {"s": shop_id, "c": c["id"]}).scalar()
            rising.append({"customer": c["name"], "balance_paise": int(bal), "added_last_3_weeks_paise": sum(d for d, _ in weeks)})
    top = [dict(r) for r in conn.execute(text("""SELECT name, balance_paise FROM v_customer_balance
                                                 WHERE shop_id=:s AND balance_paise > 0 ORDER BY balance_paise DESC LIMIT 5"""),
                                         {"s": shop_id}).mappings()]
    stock = [dict(r) for r in conn.execute(text("SELECT name, base_unit, stock, low_stock_threshold FROM v_product_stock WHERE shop_id=:s"),
                                           {"s": shop_id}).mappings()]
    sold = {r["product"]: float(r["qty"]) for r in sales}
    slow = [{"product": r["name"], "stock": float(r["stock"]), "sold_this_week": sold.get(r["name"], 0.0)}
            for r in stock if float(r["stock"]) > 0 and sold.get(r["name"], 0.0) < 0.1 * float(r["stock"])]
    low = [{"product": r["name"], "stock": float(r["stock"]), "threshold": float(r["low_stock_threshold"])}
           for r in stock if r["low_stock_threshold"] is not None and r["stock"] < r["low_stock_threshold"]]
    forecast = [dict(r) for r in conn.execute(text("""
        SELECT p.name AS product, SUM(f.qty_forecast) AS next_7d, f.model_mase < f.baseline_mase AS beats_baseline
        FROM forecasts f JOIN products p ON p.shop_id=f.shop_id AND p.id=f.product_id
        WHERE f.shop_id=:s AND f.model='AutoETS' AND f.horizon_date >= :d
        GROUP BY p.name, f.model_mase < f.baseline_mase"""), {"s": shop_id, "d": wk_end}).mappings()]
    return {"week_start": week_start.isoformat(), "week_end": (wk_end - timedelta(days=1)).isoformat(),
            "sales_by_product": [{"product": r["product"], "unit": r["base_unit"], "qty": float(r["qty"]),
                                  "amount": fmt_inr(int(r["amount_paise"]))} for r in sales],
            "week_total": fmt_inr(sum(int(r["amount_paise"]) for r in sales)),
            "rising_balances": rising, "top_balances": [{"customer": r["name"], "balance": fmt_inr(int(r["balance_paise"]))} for r in top],
            "slow_moving": slow, "low_stock": low,
            "forecast_next_7d": [{"product": r["product"], "qty": float(r["next_7d"]), "beats_baseline": bool(r["beats_baseline"])}
                                 for r in forecast]}


CREDIT_PROMPT = ("You are a credit-risk analyst for a small Indian grocery shop. Using ONLY the facts given, rank the customers "
                 "in rising_balances and top_balances by risk and explain each flag in ONE short line. Do not compute or change "
                 "any number; quote them as given. Reply as plain text, max 6 lines.")
STOCK_PROMPT = ("You are an inventory analyst. Using ONLY the facts given (slow_moving, low_stock, forecast_next_7d — trust a "
                "forecast only where beats_baseline is true), name over- and under-stocked items in max 5 short lines. "
                "Do not compute new numbers.")
ADVISOR_PROMPT = ("You advise a kirana shopkeeper. Given the two analyst notes and the facts, write 5 short lines of plain, simple "
                  "Hindi written in Latin script (Hinglish) that the shopkeeper can act on this week. Quote amounts exactly as given. "
                  "No headings, no markdown.")


def _run_plain(providers, facts: dict, log_call) -> str:
    """Fallback without CrewAI: the same three roles, sequential, through our own provider chain."""
    calls = 0

    def ask(system: str, user: str) -> str:
        nonlocal calls
        if calls >= MAX_CALLS:
            raise RuntimeError("insights call cap reached")
        for p in providers:
            try:
                resp = p.complete_json(system + "\nReturn JSON: {\"text\": \"...\"}", user)
                calls += 1
                log_call(p.name, p.model, resp.latency_ms, True, None, resp.input_tokens, resp.output_tokens)
                try:
                    return json.loads(resp.text).get("text", resp.text)
                except json.JSONDecodeError:
                    return resp.text
            except Exception as e:                                        # noqa: BLE001
                calls += 1
                log_call(getattr(p, "name", "?"), getattr(p, "model", "?"), 0, False, str(e)[:300], None, None)
        raise RuntimeError("no LLM provider answered")
    f = json.dumps(facts, ensure_ascii=False)
    credit = ask(CREDIT_PROMPT, f)
    stock = ask(STOCK_PROMPT, f)
    return ask(ADVISOR_PROMPT, f"FACTS: {f}\nCREDIT NOTE: {credit}\nSTOCK NOTE: {stock}")


def _run_crewai(model: str, base_url: str, facts: dict) -> str:
    from crewai import Agent, Crew, Task, LLM
    llm = LLM(model=f"ollama/{model}", base_url=base_url, temperature=0)
    f = json.dumps(facts, ensure_ascii=False)
    credit = Agent(role="Credit-risk analyst", goal="rank credit risk from given facts", backstory=CREDIT_PROMPT, llm=llm, max_iter=2)
    stock = Agent(role="Inventory analyst", goal="flag over/under stock from given facts", backstory=STOCK_PROMPT, llm=llm, max_iter=2)
    advisor = Agent(role="Shop advisor", goal="5 lines of actionable Hinglish advice", backstory=ADVISOR_PROMPT, llm=llm, max_iter=2)
    t1 = Task(description=f"FACTS: {f}", expected_output="max 6 lines", agent=credit)
    t2 = Task(description=f"FACTS: {f}", expected_output="max 5 lines", agent=stock)
    t3 = Task(description="Write the weekly note from the analyst outputs and the facts.", expected_output="5 lines Hinglish",
              agent=advisor, context=[t1, t2])
    return str(Crew(agents=[credit, stock, advisor], tasks=[t1, t2, t3], verbose=False).kickoff())


def run_insights(rt, shop_id: int, week_start: date | None = None, force: bool = False) -> dict:
    week_start = week_start or (date.today() - timedelta(days=date.today().weekday() + 7))
    with rt.engine.connect() as conn:
        exists = conn.execute(text("SELECT id FROM insights WHERE shop_id=:s AND week_start=:w"), {"s": shop_id, "w": week_start}).scalar()
        if exists and not force:
            return {"insight_id": exists, "skipped": "already generated for this week"}
        facts = build_facts(conn, shop_id, week_start)
    calls: list[dict] = []

    def log_call(provider, model, latency_ms, ok, error, it, ot):
        calls.append(dict(provider=provider, model=model, latency_ms=latency_ms, ok=ok, error=error, it=it, ot=ot))
    body = None
    try:
        import crewai  # noqa: F401
        s = rt.settings
        body = _run_crewai(s.ollama_model, s.ollama_base_url, facts)
        calls.append(dict(provider="crewai/ollama", model=s.ollama_model, latency_ms=0, ok=True, error=None, it=None, ot=None))
    except ImportError:
        body = _run_plain(rt.llm_providers, facts, log_call)
    finally:
        with rt.engine.begin() as conn:
            for c in calls:
                conn.execute(text("""INSERT INTO llm_calls (stage, provider, model, latency_ms, input_tokens, output_tokens, ok, error)
                                     VALUES ('INSIGHTS', :p, :m, :l, :it, :ot, :ok, :e)"""),
                             {"p": c["provider"], "m": c["model"], "l": int(c["latency_ms"] or 0), "it": c["it"], "ot": c["ot"],
                              "ok": c["ok"], "e": c["error"]})
    if not body:
        raise RuntimeError("insights produced no text")
    with rt.engine.begin() as conn:
        iid = conn.execute(text("""INSERT INTO insights (shop_id, week_start, body, facts, approved)
                                   VALUES (:s, :w, :b, CAST(:f AS jsonb), false)
                                   ON CONFLICT (shop_id, week_start) DO UPDATE SET body=EXCLUDED.body, facts=EXCLUDED.facts, approved=false
                                   RETURNING id"""), {"s": shop_id, "w": week_start, "b": body.strip(), "f": json.dumps(facts, default=str)}).scalar_one()
    return {"insight_id": iid, "week_start": week_start.isoformat(), "body": body.strip(), "llm_calls": len(calls)}


def approve_and_send(rt, insight_id: int) -> dict:
    """The human-feedback step: nothing is sent until someone approves."""
    with rt.engine.begin() as conn:
        row = conn.execute(text("UPDATE insights SET approved=true WHERE id=:i RETURNING shop_id, body"), {"i": insight_id}).first()
        if not row:
            raise SystemExit("no such insight")
        chat = conn.execute(text("SELECT telegram_user_id FROM shop_members WHERE shop_id=:s AND role='OWNER' LIMIT 1"),
                            {"s": row[0]}).scalar()
    sent = bool(chat and rt.tg and rt.tg.send(chat, "📝 Is hafte ki salah:\n" + row[1]))
    if sent:
        with rt.engine.begin() as conn:
            conn.execute(text("UPDATE insights SET sent_at=now() WHERE id=:i"), {"i": insight_id})
    return {"approved": True, "sent": sent}
