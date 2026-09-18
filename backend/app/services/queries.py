"""Read-side helpers. Everything here is a plain SELECT; nothing writes."""
from datetime import date

from sqlalchemy import text

from app.domain.models import Customer, Product


def load_catalog(conn, shop_id: int) -> tuple[list[Customer], list[Product]]:
    customers = [Customer(r["id"], r["name"], list(r["aliases"])) for r in conn.execute(text(
        "SELECT id, name, aliases FROM customers WHERE shop_id=:s AND is_active ORDER BY id"), {"s": shop_id}).mappings()]
    products = [Product(r["id"], r["name"], r["base_unit"], r["selling_price_paise"], list(r["aliases"]))
                for r in conn.execute(text(
                    "SELECT id, name, base_unit, selling_price_paise, aliases FROM products "
                    "WHERE shop_id=:s AND is_active ORDER BY id"), {"s": shop_id}).mappings()]
    return customers, products


def shop(conn, shop_id: int) -> dict | None:
    r = conn.execute(text("SELECT id, name, language, confirm_above_paise FROM shops WHERE id=:s"),
                     {"s": shop_id}).mappings().first()
    return dict(r) if r else None


def member(conn, telegram_user_id: int) -> dict | None:
    r = conn.execute(text("SELECT telegram_user_id, shop_id, role FROM shop_members WHERE telegram_user_id=:u"),
                     {"u": telegram_user_id}).mappings().first()
    return dict(r) if r else None


def customer_balance(conn, shop_id: int, customer_id: int) -> int:
    return conn.execute(text("SELECT balance_paise FROM v_customer_balance WHERE shop_id=:s AND customer_id=:c"),
                        {"s": shop_id, "c": customer_id}).scalar() or 0


def product_stock(conn, shop_id: int, product_id: int) -> tuple:
    r = conn.execute(text("SELECT stock, low_stock_threshold FROM v_product_stock WHERE shop_id=:s AND product_id=:p"),
                     {"s": shop_id, "p": product_id}).first()
    return (r[0], r[1]) if r else (0, None)


def balances(conn, shop_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(text(
        "SELECT customer_id, name, balance_paise, last_activity_at FROM v_customer_balance "
        "WHERE shop_id=:s ORDER BY balance_paise DESC, name"), {"s": shop_id}).mappings()]


def stock(conn, shop_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(text(
        "SELECT product_id, name, base_unit, stock, low_stock_threshold, selling_price_paise FROM v_product_stock "
        "WHERE shop_id=:s AND is_active ORDER BY name"), {"s": shop_id}).mappings()]


def low_stock(conn, shop_id: int) -> list[dict]:
    return [r for r in stock(conn, shop_id) if r["low_stock_threshold"] is not None and r["stock"] < r["low_stock_threshold"]]


def recent_transactions(conn, shop_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT t.id, t.type, t.amount_paise, t.quantity, t.unit, t.unit_price_paise, t.price_source, t.created_at,
               t.reverses_transaction_id, c.name AS customer, p.name AS product, t.message_id,
               m.transcript, m.parser_used, m.stt_provider, m.status AS message_status,
               EXISTS (SELECT 1 FROM transactions r WHERE r.reverses_transaction_id = t.id) AS reversed
        FROM transactions t
        LEFT JOIN customers c ON c.shop_id=t.shop_id AND c.id=t.customer_id
        LEFT JOIN products p ON p.shop_id=t.shop_id AND p.id=t.product_id
        LEFT JOIN messages m ON m.id=t.message_id
        WHERE t.shop_id=:s ORDER BY t.id DESC LIMIT :l OFFSET :o"""), {"s": shop_id, "l": limit, "o": offset}).mappings()]


def customer_history(conn, shop_id: int, customer_id: int, limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT t.id, t.type, t.amount_paise, t.quantity, t.unit, t.created_at, p.name AS product, l.amount_paise AS ledger_delta
        FROM credit_ledger l JOIN transactions t ON t.shop_id=l.shop_id AND t.id=l.transaction_id
        LEFT JOIN products p ON p.shop_id=t.shop_id AND p.id=t.product_id
        WHERE l.shop_id=:s AND l.customer_id=:c ORDER BY t.id DESC LIMIT :l"""),
        {"s": shop_id, "c": customer_id, "l": limit}).mappings()]


def daily_summary(conn, shop_id: int, day: date | None = None) -> dict:
    day = day or date.today()
    r = conn.execute(text("""
        SELECT
          COALESCE(SUM(CASE WHEN type IN ('CREDIT_SALE','CASH_SALE') THEN amount_paise END), 0) AS sales_paise,
          COALESCE(SUM(CASE WHEN type = 'CASH_SALE' THEN amount_paise END), 0) AS cash_sales_paise,
          COALESCE(SUM(CASE WHEN type = 'CREDIT_SALE' THEN amount_paise END), 0) AS credit_given_paise,
          COALESCE(SUM(CASE WHEN type = 'CREDIT_REPAYMENT' THEN amount_paise END), 0) AS credit_collected_paise,
          COUNT(*) FILTER (WHERE type <> 'REVERSAL') AS transactions,
          COUNT(*) FILTER (WHERE type = 'REVERSAL') AS reversals
        FROM transactions WHERE shop_id=:s AND (created_at AT TIME ZONE 'Asia/Kolkata')::date = :d
          AND id NOT IN (SELECT reverses_transaction_id FROM transactions WHERE reverses_transaction_id IS NOT NULL)"""),
        {"s": shop_id, "d": day}).mappings().first()
    out = dict(r)
    out["date"] = day.isoformat()
    out["outstanding_paise"] = conn.execute(text(
        "SELECT COALESCE(SUM(balance_paise),0) FROM v_customer_balance WHERE shop_id=:s AND balance_paise > 0"),
        {"s": shop_id}).scalar()
    out["low_stock"] = low_stock(conn, shop_id)
    return out


def counts(conn) -> dict:
    out = {}
    for t in ["messages", "transactions", "inventory_movements", "credit_ledger", "pending_actions", "jobs", "llm_calls"]:
        out[t] = conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()      # table names are a fixed allowlist
    out["jobs_by_status"] = {r[0]: r[1] for r in conn.execute(text("SELECT status, count(*) FROM jobs GROUP BY status"))}
    out["messages_by_status"] = {r[0]: r[1] for r in conn.execute(text("SELECT status, count(*) FROM messages GROUP BY status"))}
    out["parser_split"] = {r[0] or "none": r[1] for r in conn.execute(text("SELECT parser_used, count(*) FROM messages GROUP BY parser_used"))}
    out["queue_depth"] = out["jobs_by_status"].get("QUEUED", 0) + out["jobs_by_status"].get("RUNNING", 0)
    out["failed_messages"] = out["messages_by_status"].get("FAILED", 0)
    out["dead_jobs"] = out["jobs_by_status"].get("DEAD", 0)
    hb = conn.execute(text("SELECT MAX(seen_at) FROM worker_heartbeats")).scalar()
    out["worker_last_seen"] = hb.isoformat() if hb else None
    return out
