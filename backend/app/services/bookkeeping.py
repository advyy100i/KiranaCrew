"""The only code that writes to transactions / inventory_movements / credit_ledger.

Every function takes an open connection and does NOT commit: the caller wraps it in `with engine.begin()`,
so a failure anywhere rolls back every row written for that message. Stock may go negative on purpose —
a forgotten purchase must not block a real sale; the reply carries a warning instead.
"""
from decimal import Decimal

from sqlalchemy import text

from app.domain.models import Decision

ADJUST_REASON = {"OUT": "DAMAGED", "IN": "FOUND"}


def _lock_product(conn, shop_id: int, product_id: int) -> None:
    conn.execute(text("SELECT id FROM products WHERE shop_id=:s AND id=:p FOR UPDATE"), {"s": shop_id, "p": product_id})


def _insert_txn(conn, shop_id: int, message_id: int | None, d: Decision, created_by: str, ttype: str,
                reverses: int | None = None) -> int:
    return conn.execute(text("""
        INSERT INTO transactions (shop_id, message_id, type, customer_id, product_id, quantity, unit,
                                  unit_price_paise, amount_paise, price_source, reverses_transaction_id, created_by)
        VALUES (:s, :m, :t, :c, :p, :q, :u, :up, :amt, :src, :rev, :by) RETURNING id"""), {
        "s": shop_id, "m": message_id, "t": ttype,
        "c": d.customer.id if d.customer else None, "p": d.product.id if d.product else None,
        "q": d.quantity_base, "u": d.unit, "up": d.unit_price_paise, "amt": d.amount_paise,
        "src": d.price_source, "rev": reverses, "by": created_by}).scalar_one()


def _movement(conn, shop_id: int, txn_id: int, product_id: int, qty_delta: Decimal, reason: str) -> None:
    conn.execute(text("""INSERT INTO inventory_movements (shop_id, transaction_id, product_id, qty_delta, reason)
                         VALUES (:s, :t, :p, :q, :r)"""),
                 {"s": shop_id, "t": txn_id, "p": product_id, "q": qty_delta, "r": reason})


def _ledger(conn, shop_id: int, txn_id: int, customer_id: int, amount_paise: int) -> None:
    conn.execute(text("""INSERT INTO credit_ledger (shop_id, transaction_id, customer_id, amount_paise)
                         VALUES (:s, :t, :c, :a)"""),
                 {"s": shop_id, "t": txn_id, "c": customer_id, "a": amount_paise})


def post_decision(conn, shop_id: int, message_id: int | None, d: Decision, created_by: str) -> int:
    """Post one COMMIT decision atomically. Returns the transaction id. Raises on anything invalid."""
    if d.action != "COMMIT":
        raise ValueError(f"cannot post a {d.action} decision")
    t = d.type
    if t in {"CREDIT_SALE", "CASH_SALE"}:
        if d.product is None or d.quantity_base is None or d.amount_paise is None:
            raise ValueError("sale needs product, quantity and amount")
        if t == "CREDIT_SALE" and d.customer is None:
            raise ValueError("credit sale needs a customer")
        _lock_product(conn, shop_id, d.product.id)
        txn = _insert_txn(conn, shop_id, message_id, d, created_by, t)
        _movement(conn, shop_id, txn, d.product.id, -d.quantity_base, "SALE")
        if t == "CREDIT_SALE":
            _ledger(conn, shop_id, txn, d.customer.id, d.amount_paise)
    elif t == "CREDIT_REPAYMENT":
        if d.customer is None or not d.amount_paise:
            raise ValueError("repayment needs customer and amount")
        txn = _insert_txn(conn, shop_id, message_id, d, created_by, t)
        _ledger(conn, shop_id, txn, d.customer.id, -d.amount_paise)
    elif t == "INVENTORY_PURCHASE":
        if d.product is None or d.quantity_base is None:
            raise ValueError("purchase needs product and quantity")
        _lock_product(conn, shop_id, d.product.id)
        txn = _insert_txn(conn, shop_id, message_id, d, created_by, t)
        _movement(conn, shop_id, txn, d.product.id, d.quantity_base, "PURCHASE")
    elif t == "STOCK_ADJUSTMENT":
        if d.product is None or d.quantity_base is None or d.direction not in ADJUST_REASON:
            raise ValueError("adjustment needs product, quantity and direction")
        _lock_product(conn, shop_id, d.product.id)
        txn = _insert_txn(conn, shop_id, message_id, d, created_by, t)
        sign = -1 if d.direction == "OUT" else 1
        _movement(conn, shop_id, txn, d.product.id, sign * d.quantity_base, ADJUST_REASON[d.direction])
    else:
        raise ValueError(f"unknown transaction type {t}")
    if message_id is not None:
        conn.execute(text("UPDATE messages SET status='COMMITTED', updated_at=now() WHERE id=:m"), {"m": message_id})
    return txn


def reverse_transaction(conn, shop_id: int, txn_id: int, created_by: str) -> int | None:
    """Undo: a REVERSAL transaction plus negated movement / ledger rows. Returns None if already reversed
    (reverses_transaction_id is UNIQUE, so a double tap cannot double-apply)."""
    row = conn.execute(text("""SELECT id, type, customer_id, product_id, quantity, unit, unit_price_paise,
                                      amount_paise FROM transactions WHERE shop_id=:s AND id=:t FOR UPDATE"""),
                       {"s": shop_id, "t": txn_id}).mappings().first()
    if row is None or row["type"] == "REVERSAL":
        return None
    already = conn.execute(text("SELECT 1 FROM transactions WHERE reverses_transaction_id=:t"), {"t": txn_id}).scalar()
    if already:
        return None
    if row["product_id"]:
        _lock_product(conn, shop_id, row["product_id"])
    rev = conn.execute(text("""
        INSERT INTO transactions (shop_id, message_id, type, customer_id, product_id, quantity, unit,
                                  unit_price_paise, amount_paise, price_source, reverses_transaction_id, created_by)
        VALUES (:s, NULL, 'REVERSAL', :c, :p, :q, :u, :up, :amt, NULL, :rev, :by) RETURNING id"""), {
        "s": shop_id, "c": row["customer_id"], "p": row["product_id"], "q": row["quantity"], "u": row["unit"],
        "up": row["unit_price_paise"], "amt": row["amount_paise"], "rev": txn_id, "by": created_by}).scalar_one()
    for m in conn.execute(text("""SELECT product_id, qty_delta FROM inventory_movements
                                  WHERE shop_id=:s AND transaction_id=:t"""), {"s": shop_id, "t": txn_id}).mappings():
        _movement(conn, shop_id, rev, m["product_id"], -m["qty_delta"], "REVERSAL")
    for l in conn.execute(text("""SELECT customer_id, amount_paise FROM credit_ledger
                                  WHERE shop_id=:s AND transaction_id=:t"""), {"s": shop_id, "t": txn_id}).mappings():
        _ledger(conn, shop_id, rev, l["customer_id"], -l["amount_paise"])
    return rev
