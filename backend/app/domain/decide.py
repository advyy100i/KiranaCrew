"""Validation + decision: COMMIT / ASK / REJECT. All money arithmetic lives here and in money.py.

Precedence: NOT_A_TRANSACTION -> MULTIPLE_ITEMS / UNSUPPORTED -> customer issue -> product issue ->
UNIT_MISMATCH / MISSING_QUANTITY -> MISSING_AMOUNT -> CHOOSE_PAYMENT -> MISSING_PRICE -> CONFIRM_LARGE_AMOUNT -> COMMIT.
Nothing is ever invented: a missing number becomes a question.
"""
from decimal import Decimal

from .models import (FLAG_MULTIPLE_ITEMS, FLAG_UNSUPPORTED, Customer, Decision, Overrides, ParsedCommand, Product,
                     Resolution)
from .money import line_amount_paise, rupees_to_paise, to_base_qty, unit_price_to_base_paise
from .resolve import resolve_customer, resolve_product

NEEDS_CUSTOMER = {"CREDIT_SALE", "CREDIT_REPAYMENT", "SALE"}
NEEDS_PRODUCT = {"CREDIT_SALE", "CASH_SALE", "SALE", "INVENTORY_PURCHASE", "STOCK_ADJUSTMENT"}
COUNTABLE = {"piece", "packet"}
ASK_REASONS = {"AMBIGUOUS_CUSTOMER", "UNKNOWN_CUSTOMER", "MISSING_CUSTOMER", "AMBIGUOUS_PRODUCT", "UNKNOWN_PRODUCT",
               "MISSING_PRODUCT", "MISSING_QUANTITY", "UNIT_MISMATCH", "MISSING_AMOUNT", "CHOOSE_PAYMENT",
               "MISSING_PRICE", "CONFIRM_LARGE_AMOUNT"}


def decide(p: ParsedCommand, customers: list[Customer], products: list[Product],
           confirm_above_paise: int = 500_000, o: Overrides | None = None) -> Decision:
    o = o or Overrides()
    t = p.transaction_type
    if t == "SALE" and o.payment in {"CREDIT", "CASH"}:
        t = "CREDIT_SALE" if o.payment == "CREDIT" else "CASH_SALE"
    if FLAG_MULTIPLE_ITEMS in p.flags and not p.is_question:              # flag beats a null type from the LLM
        return Decision("REJECT", "MULTIPLE_ITEMS", type=t, customer_mention=p.customer_mention,
                        product_mention=p.product_mention)
    if p.is_question or t is None:
        d = Decision("REJECT", "NOT_A_TRANSACTION", customer_mention=p.customer_mention)
        c = resolve_customer(p.customer_mention, customers)       # lets the reply answer "kitna udhaar hai"
        if c.status == "RESOLVED":
            d.customer = c.value
        return d
    cust = resolve_customer(p.customer_mention, customers)
    prod = resolve_product(p.product_mention, products) if t in NEEDS_PRODUCT else None
    if o.customer_id is not None:
        chosen = next((c for c in customers if c.id == o.customer_id), None)
        cust = Resolution("RESOLVED", chosen) if chosen else cust
    if o.product_id is not None and t in NEEDS_PRODUCT:
        chosen_p = next((x for x in products if x.id == o.product_id), None)
        prod = Resolution("RESOLVED", chosen_p) if chosen_p else prod
    d = Decision("ASK", type=t, direction=p.adjustment_direction,
                 customer_mention=p.customer_mention, product_mention=p.product_mention)
    if cust.status == "RESOLVED":
        d.customer = cust.value
    if prod and prod.status == "RESOLVED":
        d.product = prod.value

    if FLAG_MULTIPLE_ITEMS in p.flags or (prod and prod.status == "MULTIPLE"):
        d.action, d.reason = "REJECT", "MULTIPLE_ITEMS"; return d
    if FLAG_UNSUPPORTED in p.flags:
        d.action, d.reason = "REJECT", "UNSUPPORTED"; return d
    if t in NEEDS_PRODUCT and p.quantity is None and prod.status == "MISSING" and p.total_amount_rupees is None:
        d.action, d.reason = "REJECT", "NOT_A_TRANSACTION"; return d

    if t in NEEDS_CUSTOMER and cust.status != "RESOLVED":
        d.reason = f"{cust.status}_CUSTOMER"; d.candidates = [c.name for c in cust.candidates]; return d
    if t in NEEDS_PRODUCT and prod.status != "RESOLVED":
        d.reason = f"{prod.status}_PRODUCT"; d.candidates = [x.name for x in prod.candidates]; return d

    if t in NEEDS_PRODUCT:
        product = prod.value
        if o.quantity_base is not None and o.quantity_base > 0:                 # answered "kitna?" by text
            d.quantity_base, d.unit = o.quantity_base.quantize(Decimal("0.001")), product.base_unit
        else:
            unit = p.unit or (product.base_unit if product.base_unit in COUNTABLE else None)
            if p.quantity is None or unit is None:
                d.reason = "MISSING_QUANTITY"; return d
            qty = to_base_qty(p.quantity, unit, product.base_unit)
            if qty is None:
                d.reason = "UNIT_MISMATCH"; d.unit = product.base_unit; return d   # "2 kg soap": ask, never guess
            if qty <= 0:
                d.reason = "MISSING_QUANTITY"; return d
            d.quantity_base, d.unit = qty, product.base_unit

    if t == "CREDIT_REPAYMENT":
        if o.total_amount_paise:
            d.amount_paise, d.price_source = o.total_amount_paise, "USER_REPLY"
        elif p.total_amount_rupees is None:
            d.reason = "MISSING_AMOUNT"; return d
        else:
            d.amount_paise = rupees_to_paise(p.total_amount_rupees)
    elif t == "SALE":
        d.reason = "CHOOSE_PAYMENT"; return d
    elif t in {"CREDIT_SALE", "CASH_SALE"}:
        if o.unit_price_paise:
            d.unit_price_paise, d.price_source = o.unit_price_paise, "USER_REPLY"
            d.amount_paise = line_amount_paise(d.quantity_base, o.unit_price_paise)
        elif o.total_amount_paise:
            d.amount_paise, d.price_source = o.total_amount_paise, "USER_REPLY"
        elif p.total_amount_rupees is not None:
            d.amount_paise, d.price_source = rupees_to_paise(p.total_amount_rupees), "EXPLICIT_TOTAL"
        elif p.unit_price_rupees is not None:
            stated = p.unit_price_unit or p.unit or d.product.base_unit
            upb = unit_price_to_base_paise(rupees_to_paise(p.unit_price_rupees), stated, d.product.base_unit)
            if upb is None:
                d.reason = "UNIT_MISMATCH"; d.unit = d.product.base_unit; return d
            d.unit_price_paise, d.price_source = upb, "EXPLICIT_UNIT"
            d.amount_paise = line_amount_paise(d.quantity_base, upb)
        elif d.product.selling_price_paise is not None:
            d.unit_price_paise, d.price_source = d.product.selling_price_paise, "CATALOG"
            d.amount_paise = line_amount_paise(d.quantity_base, d.product.selling_price_paise)
        else:
            d.reason = "MISSING_PRICE"; return d
    elif t == "INVENTORY_PURCHASE" and p.total_amount_rupees is not None:
        d.amount_paise, d.price_source = rupees_to_paise(p.total_amount_rupees), "EXPLICIT_TOTAL"

    if t in {"CREDIT_SALE", "CASH_SALE", "CREDIT_REPAYMENT"} and d.amount_paise is None:
        d.reason = "MISSING_PRICE"; return d                                     # belt and braces: never None
    if d.amount_paise is not None and d.amount_paise > confirm_above_paise and not o.confirm_large_amount:
        d.reason = "CONFIRM_LARGE_AMOUNT"; return d
    d.action, d.reason = "COMMIT", None
    return d
