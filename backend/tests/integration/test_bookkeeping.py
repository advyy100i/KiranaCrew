from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.models import Decision
from app.services import queries
from app.services.bookkeeping import post_decision, reverse_transaction

pytestmark = pytest.mark.integration


def _cat(conn, shop_id):
    customers, products = queries.load_catalog(conn, shop_id)
    return {c.name: c for c in customers}, {p.name: p for p in products}


def _stock(conn, shop_id, product_id):
    return Decimal(queries.product_stock(conn, shop_id, product_id)[0])


def test_migrations_idempotent(engine):
    from app.cli.migrate import migrate
    assert migrate(engine) == []


def test_seed_matches_catalog(engine, seeded):
    with engine.connect() as c:
        C, P = _cat(c, seeded)
        assert _stock(c, seeded, P["Rice"].id) == Decimal("100.000")
        assert len(C) == 8 and len(P) == 12


def test_credit_sale_posts_three_rows(engine, seeded):
    with engine.connect() as c:
        C, P = _cat(c, seeded)
        before_stock = _stock(c, seeded, P["Rice"].id)
        before_bal = queries.customer_balance(c, seeded, C["Ramesh Kumar"].id)
    d = Decision("COMMIT", type="CREDIT_SALE", customer=C["Ramesh Kumar"], product=P["Rice"],
                 quantity_base=Decimal("5"), unit="kg", unit_price_paise=5000, amount_paise=25000, price_source="CATALOG")
    with engine.begin() as c:
        txn = post_decision(c, seeded, None, d, "test")
    with engine.connect() as c:
        assert _stock(c, seeded, P["Rice"].id) == before_stock - 5
        assert queries.customer_balance(c, seeded, C["Ramesh Kumar"].id) == before_bal + 25000
        assert c.execute(text("SELECT count(*) FROM inventory_movements WHERE transaction_id=:t"), {"t": txn}).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM credit_ledger WHERE transaction_id=:t"), {"t": txn}).scalar() == 1


def test_cash_sale_repayment_purchase_adjustment(engine, seeded):
    with engine.connect() as c:
        C, P = _cat(c, seeded)
        s0 = _stock(c, seeded, P["Sugar"].id); b0 = queries.customer_balance(c, seeded, C["Sunita Devi"].id)
    with engine.begin() as c:
        post_decision(c, seeded, None, Decision("COMMIT", type="CASH_SALE", product=P["Sugar"], quantity_base=Decimal("2"),
                                                unit="kg", unit_price_paise=4500, amount_paise=9000, price_source="CATALOG"), "t")
        post_decision(c, seeded, None, Decision("COMMIT", type="CREDIT_REPAYMENT", customer=C["Sunita Devi"],
                                                amount_paise=b0 + 10_000), "t")     # more than owed => negative balance ok
        post_decision(c, seeded, None, Decision("COMMIT", type="INVENTORY_PURCHASE", product=P["Sugar"],
                                                quantity_base=Decimal("50"), unit="kg", amount_paise=200000,
                                                price_source="EXPLICIT_TOTAL"), "t")
        post_decision(c, seeded, None, Decision("COMMIT", type="STOCK_ADJUSTMENT", product=P["Sugar"],
                                                quantity_base=Decimal("1.5"), unit="kg", direction="OUT"), "t")
    with engine.connect() as c:
        assert _stock(c, seeded, P["Sugar"].id) == s0 - 2 + 50 - Decimal("1.5")
        assert queries.customer_balance(c, seeded, C["Sunita Devi"].id) == -10_000


def test_rounding_third_of_kilo(engine, seeded):
    from app.domain.money import line_amount_paise
    with engine.connect() as c:
        C, P = _cat(c, seeded)
    amt = line_amount_paise(Decimal("0.333"), 14000)
    assert amt == 4662
    with engine.begin() as c:
        post_decision(c, seeded, None, Decision("COMMIT", type="CASH_SALE", product=P["Toor Dal"], quantity_base=Decimal("0.333"),
                                                unit="kg", unit_price_paise=14000, amount_paise=amt, price_source="CATALOG"), "t")


def test_rollback_writes_nothing(engine, seeded):
    with engine.connect() as c:
        C, P = _cat(c, seeded)
        n0 = c.execute(text("SELECT count(*) FROM transactions")).scalar()
        m0 = c.execute(text("SELECT count(*) FROM inventory_movements")).scalar()
    d = Decision("COMMIT", type="CREDIT_SALE", customer=C["Ramesh Kumar"], product=P["Rice"],
                 quantity_base=Decimal("5"), unit="kg", unit_price_paise=5000, amount_paise=25000, price_source="CATALOG")
    with pytest.raises(RuntimeError):
        with engine.begin() as c:
            post_decision(c, seeded, None, d, "t")
            raise RuntimeError("simulated crash after the ledger insert")
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM transactions")).scalar() == n0
        assert c.execute(text("SELECT count(*) FROM inventory_movements")).scalar() == m0


def test_composite_fk_blocks_cross_shop(engine, seeded):
    with engine.begin() as c:
        other = c.execute(text("INSERT INTO shops (name) VALUES ('Other Shop') RETURNING id")).scalar_one()
        C, P = _cat(c, seeded)
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("""INSERT INTO transactions (shop_id, type, customer_id, amount_paise, created_by)
                              VALUES (:s, 'CREDIT_REPAYMENT', :c, 100, 't')"""), {"s": other, "c": C["Ramesh Kumar"].id})


def test_reverse_once_only(engine, seeded):
    with engine.connect() as c:
        C, P = _cat(c, seeded)
        s0 = _stock(c, seeded, P["Rice"].id); b0 = queries.customer_balance(c, seeded, C["Mohan Lal"].id)
    d = Decision("COMMIT", type="CREDIT_SALE", customer=C["Mohan Lal"], product=P["Rice"],
                 quantity_base=Decimal("3"), unit="kg", unit_price_paise=5000, amount_paise=15000, price_source="CATALOG")
    with engine.begin() as c:
        txn = post_decision(c, seeded, None, d, "t")
    with engine.begin() as c:
        assert reverse_transaction(c, seeded, txn, "t") is not None
    with engine.begin() as c:
        assert reverse_transaction(c, seeded, txn, "t") is None            # second undo is a no-op
    with engine.connect() as c:
        assert _stock(c, seeded, P["Rice"].id) == s0
        assert queries.customer_balance(c, seeded, C["Mohan Lal"].id) == b0
        assert c.execute(text("SELECT count(*) FROM transactions WHERE reverses_transaction_id=:t"), {"t": txn}).scalar() == 1


def test_views_agree_with_hand_sums(engine, seeded):
    with engine.connect() as c:
        for r in queries.stock(c, seeded):
            manual = c.execute(text("SELECT COALESCE(SUM(qty_delta),0) FROM inventory_movements WHERE shop_id=:s AND product_id=:p"),
                               {"s": seeded, "p": r["product_id"]}).scalar()
            assert Decimal(r["stock"]) == Decimal(manual)
