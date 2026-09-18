"""Reproducible demo data. Even the opening stock goes through real bookkeeping (INVENTORY_PURCHASE rows).
   python -m app.cli.seed_demo --catalog ../eval/seed_catalog.json --reset [--telegram-user-id 123 --history-weeks 12]
Running it twice without --reset is a no-op (the shop already exists)."""
import argparse
import json
import pathlib
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from app.db import get_engine
from app.domain.models import Customer, Decision, Product
from app.services.bookkeeping import post_decision

RESET_SQL = """TRUNCATE audit_logs, llm_calls, forecasts, insights, pending_actions, jobs, credit_ledger,
               inventory_movements, transactions, messages, shop_members, customers, products, shops,
               worker_heartbeats RESTART IDENTITY CASCADE"""


def _backdate(conn, txn_id: int, when: datetime) -> None:
    for t in ("transactions", "inventory_movements", "credit_ledger"):
        col = "id" if t == "transactions" else "transaction_id"
        conn.execute(text(f"UPDATE {t} SET created_at=:w WHERE {col}=:t"), {"w": when, "t": txn_id})   # fixed table list


def seed(engine, catalog_path: pathlib.Path, reset: bool, telegram_user_id: int | None,
         history_weeks: int, seed_value: int = 7) -> dict:
    cat = json.loads(catalog_path.read_text(encoding="utf-8"))
    rnd = random.Random(seed_value)
    with engine.begin() as conn:
        if reset:
            conn.execute(text(RESET_SQL))
        existing = conn.execute(text("SELECT id FROM shops WHERE name=:n"), {"n": cat["shop"]["name"]}).scalar()
        if existing:
            if telegram_user_id:
                conn.execute(text("""INSERT INTO shop_members (telegram_user_id, shop_id) VALUES (:u, :s)
                                     ON CONFLICT (telegram_user_id) DO UPDATE SET shop_id=EXCLUDED.shop_id"""),
                             {"u": telegram_user_id, "s": existing})
            return {"shop_id": existing, "seeded": False}
        shop_id = conn.execute(text("INSERT INTO shops (name, language) VALUES (:n, :l) RETURNING id"),
                               {"n": cat["shop"]["name"], "l": cat["shop"].get("language", "hi")}).scalar_one()
        if telegram_user_id:
            conn.execute(text("INSERT INTO shop_members (telegram_user_id, shop_id, role) VALUES (:u, :s, 'OWNER')"),
                         {"u": telegram_user_id, "s": shop_id})
        customers: list[Customer] = []
        for c in cat["customers"]:
            cid = conn.execute(text("INSERT INTO customers (shop_id, name, aliases) VALUES (:s, :n, :a) RETURNING id"),
                               {"s": shop_id, "n": c["name"], "a": c.get("aliases", [])}).scalar_one()
            customers.append(Customer(cid, c["name"], c.get("aliases", [])))
        products: list[Product] = []
        for p in cat["products"]:
            pid = conn.execute(text("""INSERT INTO products (shop_id, name, aliases, base_unit, selling_price_paise,
                                                             low_stock_threshold)
                                       VALUES (:s, :n, :a, :u, :pr, :lo) RETURNING id"""),
                               {"s": shop_id, "n": p["name"], "a": p.get("aliases", []), "u": p["base_unit"],
                                "pr": p.get("selling_price_paise"), "lo": p.get("low_stock_threshold")}).scalar_one()
            products.append(Product(pid, p["name"], p["base_unit"], p.get("selling_price_paise"), p.get("aliases", [])))

        now = datetime.now(timezone.utc)
        start = now - timedelta(weeks=history_weeks)
        # opening stock: enough to cover the synthetic history plus the catalog's opening_stock at the end
        for p, spec in zip(products, cat["products"]):
            demand = _expected_demand(spec, history_weeks)
            qty = Decimal(str(spec.get("opening_stock", 0))) + demand
            d = Decision("COMMIT", type="INVENTORY_PURCHASE", product=p, quantity_base=qty, unit=p.base_unit)
            _backdate(conn, post_decision(conn, shop_id, None, d, "seed"), start - timedelta(days=1))

        # synthetic daily sales history with weekly seasonality (for the forecaster), deterministic
        made = 0
        for p, spec in zip(products, cat["products"]):
            base = spec.get("daily_demand", _default_daily(spec))
            if base <= 0:
                continue
            for day in range(history_weeks * 7):
                when = start + timedelta(days=day, hours=rnd.randint(8, 20))
                weekly = 1.0 + 0.35 * (1 if when.weekday() in (5, 6) else -0.3)
                qty = Decimal(str(round(max(0.0, rnd.gauss(base * weekly, base * 0.25)), 3)))
                if qty <= 0:
                    continue
                if p.base_unit in ("piece", "packet"):
                    qty = qty.quantize(Decimal("1")) or Decimal(1)
                credit = rnd.random() < 0.4
                cust = rnd.choice(customers) if credit else None
                if p.selling_price_paise is None:
                    continue
                amt = int((qty * p.selling_price_paise).quantize(Decimal("1")))
                d = Decision("COMMIT", type="CREDIT_SALE" if credit else "CASH_SALE", customer=cust, product=p,
                             quantity_base=qty, unit=p.base_unit, unit_price_paise=p.selling_price_paise,
                             amount_paise=amt, price_source="CATALOG")
                _backdate(conn, post_decision(conn, shop_id, None, d, "seed"), when)
                made += 1
        # a few repayments so balances are not monotonic
        for cust in customers:
            bal = conn.execute(text("SELECT balance_paise FROM v_customer_balance WHERE shop_id=:s AND customer_id=:c"),
                               {"s": shop_id, "c": cust.id}).scalar() or 0
            if bal > 0:
                for k in range(3):
                    pay = int(int(bal) * rnd.uniform(0.1, 0.3))
                    if pay <= 0:
                        continue
                    d = Decision("COMMIT", type="CREDIT_REPAYMENT", customer=cust, amount_paise=pay)
                    _backdate(conn, post_decision(conn, shop_id, None, d, "seed"), now - timedelta(days=rnd.randint(1, 40)))
        # bring stock to the catalog's stated opening numbers (so the demo starts at "rice 100 kg")
        for p, spec in zip(products, cat["products"]):
            cur = conn.execute(text("SELECT stock FROM v_product_stock WHERE shop_id=:s AND product_id=:p"),
                               {"s": shop_id, "p": p.id}).scalar()
            target = Decimal(str(spec.get("opening_stock", 0)))
            diff = (target - Decimal(cur)).quantize(Decimal("0.001"))
            if diff != 0:
                d = Decision("COMMIT", type="INVENTORY_PURCHASE" if diff > 0 else "STOCK_ADJUSTMENT", product=p,
                             quantity_base=abs(diff), unit=p.base_unit, direction=None if diff > 0 else "OUT")
                _backdate(conn, post_decision(conn, shop_id, None, d, "seed"), now - timedelta(hours=1))
        conn.execute(text("INSERT INTO audit_logs (shop_id, actor, action, details) VALUES (:s, 'seed', 'SEEDED', :d)"),
                     {"s": shop_id, "d": json.dumps({"history_weeks": history_weeks, "sales": made})})
        return {"shop_id": shop_id, "seeded": True, "sales_rows": made}


def _default_daily(spec: dict) -> float:
    return {"kg": 4.0, "litre": 3.0, "piece": 6.0, "packet": 5.0}[spec["base_unit"]]


def _expected_demand(spec: dict, weeks: int) -> Decimal:
    if spec.get("selling_price_paise") is None:
        return Decimal(0)
    return Decimal(str(round(spec.get("daily_demand", _default_daily(spec)) * weeks * 7 * 1.3, 3)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=str(pathlib.Path(__file__).resolve().parents[3] / "eval" / "seed_catalog.json"))
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--telegram-user-id", type=int, default=None)
    ap.add_argument("--history-weeks", type=int, default=12)
    a = ap.parse_args()
    print(seed(get_engine(), pathlib.Path(a.catalog), a.reset, a.telegram_user_id, a.history_weeks))
