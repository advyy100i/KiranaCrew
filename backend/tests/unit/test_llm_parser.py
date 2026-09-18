import json
import socket
from decimal import Decimal

import pytest

from app.domain.llm_parser import ground, parse_llm
from app.domain.models import LLMExtraction
from app.domain.parser import parse
from app.llm.base import LLMError, LLMResponse
from tests.unit.test_domain import VOCAB

NORM = "ramesh kumar ne 5 kg chawal udhaar pe liya"


class StubLLM:
    def __init__(self, name, replies=None, raise_=None):
        self.name, self.model, self.replies, self.raise_, self.calls = name, "stub", list(replies or []), raise_, 0

    def complete_json(self, system, user, timeout=30.0):
        self.calls += 1
        if self.raise_:
            raise LLMError(self.raise_)
        return LLMResponse(text=self.replies.pop(0), provider=self.name, model=self.model, latency_ms=3)


def test_grounding_rejects_hallucinated_quantity_and_name():
    x = LLMExtraction(transaction_type="CREDIT_SALE", customer_mention="ramesh kumar", product_mention="chawal",
                      quantity=Decimal(50), unit="kg", total_amount_rupees=Decimal(250))
    g, dropped = ground(x, NORM)
    assert g.quantity is None and g.total_amount_rupees is None and set(dropped) == {"quantity", "total_amount_rupees"}
    assert g.customer_mention == "ramesh kumar" and g.product_mention == "chawal"
    x2 = LLMExtraction(customer_mention="suresh yadav", product_mention="cheeni", quantity=Decimal(5))
    g2, dropped2 = ground(x2, NORM)
    assert g2.customer_mention is None and g2.product_mention is None and g2.quantity == 5


def test_parse_llm_strips_fences_and_retries_once():
    bad_then_good = ["not json at all", "```json\n" + json.dumps({"transaction_type": "CREDIT_SALE", "customer_mention": "ramesh kumar",
                     "product_mention": "chawal", "quantity": "5", "unit": "kg"}) + "\n```"]
    stub = StubLLM("s", bad_then_good)
    cmd, info = parse_llm(NORM, lambda s, u: stub.complete_json(s, u).text)
    assert cmd.transaction_type == "CREDIT_SALE" and cmd.quantity == 5 and info["attempt"] == 1


def test_parse_llm_coerces_number_strings_but_still_grounds():
    stub = StubLLM("s", [json.dumps({"transaction_type": "CREDIT_SALE", "customer_mention": "ramesh kumar",
                                     "product_mention": "chawal", "quantity": "5 kg", "total_amount_rupees": "250 rupaye"})])
    cmd, info = parse_llm(NORM, lambda s, u: stub.complete_json(s, u).text)
    assert cmd.quantity == 5 and cmd.unit == "kg"
    assert cmd.total_amount_rupees is None and "total_amount_rupees" in info["dropped"]   # 250 is not in the text


def test_parse_llm_rejects_extra_keys_softly_and_unknown_type():
    stub = StubLLM("s", [json.dumps({"transaction_type": "LOAN", "customer_id": 7, "customer_mention": "ramesh kumar"})])
    cmd, _ = parse_llm(NORM, lambda s, u: stub.complete_json(s, u).text)
    assert cmd.transaction_type is None and cmd.customer_mention == "ramesh kumar"


def test_hybrid_uses_rules_when_complete_and_llm_otherwise():
    stub = StubLLM("s", [json.dumps({"transaction_type": "CREDIT_SALE", "customer_mention": "ramesh kumar",
                                     "product_mention": "chawal", "quantity": "5", "unit": "kg"})])
    cmd, used = parse(NORM, VOCAB, "hybrid", [stub])
    assert used == "rules" and stub.calls == 0
    cmd, used = parse("ramesh kumar 5 kg chawal ke liye aaya tha udhaar", VOCAB, "llm", [stub])
    assert used == "llm:s" and stub.calls == 1 and cmd.transaction_type == "CREDIT_SALE"


def test_provider_chain_falls_through_and_then_back_to_rules():
    down = StubLLM("ollama", raise_="connection refused")
    ok = StubLLM("groq", [json.dumps({"transaction_type": "CREDIT_SALE", "customer_mention": "ramesh kumar",
                                      "product_mention": "chawal", "quantity": "5", "unit": "kg"})])
    log = []
    cmd, used = parse(NORM, VOCAB, "llm", [down, ok], log.append)
    assert used == "llm:groq" and [l["ok"] for l in log] == [False, True]
    cmd, used = parse(NORM, VOCAB, "llm", [StubLLM("a", raise_="x"), StubLLM("b", raise_="y")])
    assert used == "llm_fallback_failed" and cmd.transaction_type == "CREDIT_SALE"     # rules output survives


def test_rules_mode_never_opens_a_socket(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("socket opened in rules mode")
    monkeypatch.setattr(socket.socket, "connect", boom)
    from app.llm.ollama import OllamaLLM
    cmd, used = parse(NORM, VOCAB, "rules", [OllamaLLM("http://127.0.0.1:1", "x")])
    assert used == "rules"


def test_rules_safety_flags_survive_llm_path():
    stub = StubLLM("s", [json.dumps({"transaction_type": "CREDIT_SALE", "customer_mention": "rakesh kumar",
                                     "product_mention": "chawal", "quantity": "3", "unit": "kg"})])
    cmd, used = parse("rakesh kumar ne 3 kg chawal udhaar liya aur ramesh ne 2 kg", VOCAB, "llm", [stub])
    assert "MULTIPLE_ITEMS" in cmd.flags


@pytest.mark.parametrize("bad", ["", "{}", "null"])
def test_parse_llm_empty_outputs_give_empty_command(bad):
    stub = StubLLM("s", [bad, bad])
    if bad == "":
        with pytest.raises(ValueError):
            parse_llm(NORM, lambda s, u: stub.complete_json(s, u).text)
    else:
        cmd, _ = parse_llm(NORM, lambda s, u: stub.complete_json(s, u).text)
        assert cmd.transaction_type is None


def test_llm_path_inherits_extra_numbers_policy_and_backfills_mentions():
    stub = StubLLM("s", [json.dumps({"transaction_type": "CREDIT_SALE", "customer_mention": "ramesh kumar",
                                     "quantity": "5", "unit": "kg"})])
    cmd, used = parse("ramesh kumar ne 5 5 5 kg chawal udhaar liya", VOCAB, "llm", [stub])
    assert cmd.quantity is None and cmd.product_mention == "chawal" and "EXTRA_NUMBERS" in cmd.flags


def test_llm_cannot_relabel_a_unit_price_as_a_total():
    stub = StubLLM("s", [json.dumps({"transaction_type": "CREDIT_SALE", "customer_mention": "ramesh kumar",
                                     "product_mention": "chawal", "quantity": "2", "unit": "kg", "total_amount_rupees": "55"})])
    cmd, used = parse("ramesh kumar ne 2 kg chawal 55 rupaye kg udhaar liya", VOCAB, "llm", [stub])
    assert cmd.total_amount_rupees is None and cmd.unit_price_rupees == 55 and cmd.unit_price_unit == "kg"


def test_llm_duplicate_number_in_quantity_and_total_is_cleaned():
    stub = StubLLM("s", [json.dumps({"transaction_type": "CREDIT_SALE", "customer_mention": "sunita devi",
                                     "quantity": "350", "total_amount_rupees": "350"})])
    cmd, used = parse("sunita devi ne 350 rupaye jama kiye", VOCAB, "llm", [stub])
    assert cmd.quantity is None and cmd.total_amount_rupees == 350


def test_llm_cannot_overrule_keyword_type_or_invent_payment_mode():
    stub = StubLLM("s", [json.dumps({"transaction_type": "CASH_SALE", "product_mention": "biscuit", "quantity": "30", "unit": "packet"}),
                         json.dumps({"transaction_type": "CREDIT_SALE", "customer_mention": "rakesh", "product_mention": "chawal",
                                     "quantity": "2", "unit": "kg"})])
    cmd, _ = parse("30 packet biscuit kharide", VOCAB, "llm", [stub])
    assert cmd.transaction_type == "INVENTORY_PURCHASE"
    cmd, _ = parse("rakesh ne 2 kg chawal liya paise baad mein dega", VOCAB, "llm", [stub])
    assert cmd.transaction_type == "SALE"                     # neither udhaar nor cash was said -> ask
