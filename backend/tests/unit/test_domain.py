import json
import pathlib
from decimal import Decimal

import pytest

from app.domain.decide import decide
from app.domain.models import FLAG_MULTIPLE_ITEMS, FLAG_UNSUPPORTED, Customer, LLMExtraction, ParsedCommand, Product
from app.domain.money import fmt_inr, line_amount_paise, rupees_to_paise, to_base_qty, unit_price_to_base_paise
from app.domain.normalize import normalize, numbers_in
from app.domain.resolve import product_vocab, resolve_customer, resolve_product, skeleton
from app.domain.rules_parser import parse_rules

CATALOG = json.load(open(pathlib.Path(__file__).resolve().parents[3] / "eval" / "seed_catalog.json", encoding="utf-8"))
CUSTOMERS = [Customer(i + 1, c["name"], c["aliases"]) for i, c in enumerate(CATALOG["customers"])]
PRODUCTS = [Product(i + 1, p["name"], p["base_unit"], p["selling_price_paise"], p["aliases"])
            for i, p in enumerate(CATALOG["products"])]
VOCAB = product_vocab(PRODUCTS)


def run(text, confirm_above=500_000):
    return decide(parse_rules(normalize(text), VOCAB), CUSTOMERS, PRODUCTS, confirm_above)


# ---------- normalizer ----------
@pytest.mark.parametrize("raw, expected", [
    ("5 5 5 kilo", "5 5 5 kg"),
    ("saade teen sau rupaye", "350 rupaye"),
    ("do hazaar paanch sau rupaye", "2500 rupaye"),
    ("1,000 rupaye", "1000 rupaye"),
    ("₹ 1,250", "1250 rupaye"),
    ("Rs.250", "250 rupaye"),
    ("1,00,000", "100000"),
    ("ek sau bees", "120"),
    ("teen sau pachas", "350"),
    ("ek hazaar do sau pachas", "1250"),
    ("paanch sau bees", "520"),
    ("sawa sau", "125"),
    ("सवा सौ", "125"),
    ("sawa kilo", "1.25 kg"),
    ("paune do sau", "175"),
    ("dhai sau", "250"),
    ("dedh sau", "150"),
    ("das hazaar", "10000"),
    ("1.5 lakh", "150000"),
    ("do lakh pachas hazaar", "250000"),
    ("sau rupaye", "100 rupaye"),
    ("sau sau", "100 100"),
    ("Ramesh ke saath do kilo", "ramesh ke saath 2 kg"),
    ("saath kilo", "60 kg"),
    ("de do", "de do"),
    ("2.5 kg", "2.5 kg"),
    ("5kg", "5 kg"),
    ("5 kg.", "5 kg"),
    ("2 kilo 500 gram", "2 kg 500 g"),
    ("पाँच किलो चावल", "5 kg चावल"),
    ("saade paanch kilo", "5.5 kg"),
    ("५ किलो", "5 kg"),
    ("adha kilo", "0.5 kg"),
    ("ek darjan ande", "1 dozen ande"),
    ("udhaarpe liya", "udhaar pe liya"),
    ("Rakesh Kumar ne, 2 kilo... chawal udhaar liya.", "rakesh kumar ne 2 kg chawal udhaar liya"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_numbers_in():
    assert numbers_in("ramesh ne 5 kg chawal 250 rupaye") == {Decimal(5), Decimal(250)}


# ---------- money ----------
def test_money_rounding_and_units():
    assert rupees_to_paise(Decimal("2.505")) == 251
    assert to_base_qty(Decimal("500"), "g", "kg") == Decimal("0.500")
    assert to_base_qty(Decimal("2"), "kg", "piece") is None
    assert to_base_qty(Decimal("1"), "dozen", "piece") == Decimal("12.000")
    assert unit_price_to_base_paise(8400, "dozen", "piece") == 700
    assert unit_price_to_base_paise(5, "g", "kg") == 5000
    assert line_amount_paise(Decimal("0.333"), 14000) == 4662          # 0.333 * 140 = 46.62
    assert line_amount_paise(Decimal("2.5"), 4000) == 10000
    assert fmt_inr(25000) == "₹250" and fmt_inr(123456) == "₹1,234.56"


# ---------- resolution ----------
def test_skeleton():
    assert skeleton("Raamesh Kumaar") == skeleton("Ramesh Kumar")
    assert skeleton("Kumari") != skeleton("Kumar")
    assert skeleton("Sunitha") == skeleton("Sunita")


def test_resolve_customer_policy():
    assert resolve_customer("ramesh kumar", CUSTOMERS).value.name == "Ramesh Kumar"
    assert resolve_customer("ramesh", CUSTOMERS).status == "AMBIGUOUS"
    assert resolve_customer("raamesh kumaar", CUSTOMERS).value.name == "Ramesh Kumar"
    r = resolve_customer("ramesh kumari", CUSTOMERS)
    assert r.status == "AMBIGUOUS" and "Ramesh Kumar" in [c.name for c in r.candidates]
    assert resolve_customer("kavita", CUSTOMERS).status == "UNKNOWN"
    assert resolve_customer(None, CUSTOMERS).status == "MISSING"
    assert resolve_customer("रमेश कुमार", CUSTOMERS).value.name == "Ramesh Kumar"


def test_resolve_product_policy():
    assert resolve_product("chawal", PRODUCTS).value.name == "Rice"
    assert resolve_product("dal", PRODUCTS).status == "AMBIGUOUS"
    assert resolve_product("toor dal", PRODUCTS).value.name == "Toor Dal"
    assert resolve_product("chawal cheeni", PRODUCTS).status == "MULTIPLE"
    assert resolve_product("saboon", PRODUCTS).value.name == "Soap"
    assert resolve_product("maggi", PRODUCTS).status == "UNKNOWN"


# ---------- parser flags ----------
def test_parser_flags_two_actors_and_mixed_payment():
    assert FLAG_MULTIPLE_ITEMS in parse_rules(normalize("Rakesh Kumar ne 3 kilo chawal udhaar liya aur Ramesh ne 2 kilo"), VOCAB).flags
    assert FLAG_MULTIPLE_ITEMS in parse_rules(normalize("Ramesh Kumar ne 5 kilo chawal udhaar liya aur 200 rupaye cash diye"), VOCAB).flags
    assert FLAG_UNSUPPORTED in parse_rules(normalize("Ramesh Kumar ko 500 rupaye udhaar diye"), VOCAB).flags


def test_parser_never_crashes_on_zero_or_negative():
    p = parse_rules(normalize("Ramesh Kumar ne 0 kilo chawal udhaar liya"), VOCAB)
    assert p.quantity is None
    parse_rules(normalize("-5 kilo chawal kharab"), VOCAB)


def test_llm_contract_forbids_extra_fields():
    with pytest.raises(Exception):
        LLMExtraction.model_validate({"transaction_type": "CREDIT_SALE", "customer_id": 3})
    assert ParsedCommand(transaction_type="CREDIT_SALE", customer_mention="x", product_mention="y",
                         quantity=Decimal(1)).is_complete()
    assert not ParsedCommand(transaction_type="CREDIT_SALE", customer_mention="x").is_complete()


# ---------- decision branches ----------
@pytest.mark.parametrize("text, action, reason", [
    ("hello", "REJECT", "NOT_A_TRANSACTION"),
    ("Sunita Devi ka hisaab kitna hai", "REJECT", "NOT_A_TRANSACTION"),
    ("Ramesh Kumar ne 2 kilo chawal aur 1 kilo cheeni udhaar liya", "REJECT", "MULTIPLE_ITEMS"),
    ("Ramesh Kumar ko 500 rupaye udhaar diye", "REJECT", "UNSUPPORTED"),
    ("Ramesh ne 5 kilo chawal udhaar liya", "ASK", "AMBIGUOUS_CUSTOMER"),
    ("Kavita ne 5 kilo chawal udhaar liya", "ASK", "UNKNOWN_CUSTOMER"),
    ("5 kilo chawal udhaar diya", "ASK", "MISSING_CUSTOMER"),
    ("Ramesh Kumar ne 5 kilo dal udhaar liya", "ASK", "AMBIGUOUS_PRODUCT"),
    ("Ramesh Kumar ne 1 packet maggi udhaar liya", "ASK", "UNKNOWN_PRODUCT"),
    ("Ramesh Kumar ne chawal udhaar liya", "ASK", "MISSING_QUANTITY"),
    ("Ramesh Kumar ne 2 kg sabun udhaar liya", "ASK", "UNIT_MISMATCH"),
    ("Ramesh Kumar ne udhaar chukaya", "ASK", "MISSING_AMOUNT"),
    ("Ramesh Kumar ne 2 kilo chawal liya", "ASK", "CHOOSE_PAYMENT"),
    ("Ramesh Kumar ne 2 kilo moong dal udhaar liya", "ASK", "MISSING_PRICE"),
    ("Mohan Lal ne 20 kilo chawal udhaar liya 12000 rupaye ka", "ASK", "CONFIRM_LARGE_AMOUNT"),
    ("Ramesh Kumar ne 5 kilo chawal udhaar liya", "COMMIT", None),
])
def test_decision_branches(text, action, reason):
    d = run(text)
    assert (d.action, d.reason) == (action, reason)


def test_price_precedence():
    assert run("Ramesh Kumar ne 5 kilo chawal 280 rupaye mein udhaar liya").amount_paise == 28000
    d = run("Ramesh Kumar ne 2 kilo chawal 55 rupaye kilo udhaar liya")
    assert (d.amount_paise, d.price_source) == (11000, "EXPLICIT_UNIT")
    d = run("Ramesh Kumar ne 2 kilo chawal udhaar liya")
    assert (d.amount_paise, d.price_source) == (10000, "CATALOG")


def test_no_commit_without_amount_on_sale():
    for text in ["Ramesh Kumar ne 2 kilo moong dal udhaar liya", "2 kilo moong dal cash mein becha"]:
        d = run(text)
        assert not (d.action == "COMMIT" and d.amount_paise is None)


def test_question_carries_customer_for_balance_reply():
    d = run("Sunita Devi ka hisaab kitna hai")
    assert d.action == "REJECT" and d.customer and d.customer.name == "Sunita Devi"
