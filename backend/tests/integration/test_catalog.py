"""The catalog grows from the chat: new items, new customers, learned spellings, prices — no SQL, owner only."""
import pytest
from sqlalchemy import text

from tests.integration.test_pipeline import ADMIN, CHAT, SECRET, USER, client, rt, sim, text_update, drain  # noqa: F401

pytestmark = pytest.mark.integration  # noqa
# ruff: noqa: F811 — pytest fixtures are re-exported names, not redefinitions


def labels(out):
    return [b["text"] for row in out["buttons"] for b in row]


def choose(client, out, label_prefix):
    idx = next(i for i, l in enumerate(labels(out)) if l.startswith(label_prefix))
    return client.post("/dev/simulate/choose", json={"pending_id": out["pending_id"], "index": idx}, headers=ADMIN).json()


def test_new_item_from_chat_unit_inferred_price_answered(client, rt):
    out = sim(client, "Pooja Verma ne 2 packet maggi udhaar liye")
    assert out["decision"]["reason"] == "UNKNOWN_PRODUCT" and any(l.startswith("➕ Naya item: Maggi") for l in labels(out))
    out2 = choose(client, out, "➕ Naya item")
    assert out2["status"] == "AWAITING_CONFIRMATION" and "rate" in out2["reply"].lower()      # unit came from "packet"
    out3 = client.post("/dev/simulate/answer", json={"pending_id": out2["pending_id"], "text": "14 rupaye"}, headers=ADMIN).json()
    assert out3["status"] == "COMMITTED" and out3["decision"]["product"] == "Maggi" and out3["decision"]["amount_paise"] == 2800
    with rt.engine.connect() as c:
        unit, price, aliases = c.execute(text("SELECT base_unit, selling_price_paise, aliases FROM products WHERE name='Maggi'")).first()
    assert (unit, price) == ("packet", 1400) and "maggi" in aliases
    # next time it resolves straight away, with the catalog price
    again = sim(client, "Anil ne 1 packet maggi udhaar liya")
    assert again["status"] == "COMMITTED" and again["decision"]["price_source"] == "CATALOG" and again["decision"]["amount_paise"] == 1400


def test_new_item_asks_unit_when_not_said_and_price_can_be_skipped(client, rt):
    out = sim(client, "Sunita Devi ne 3 shampoo udhaar liye")
    out2 = choose(client, out, "➕ Naya item")
    assert out2["status"] == "AWAITING_CONFIRMATION" and "unit" in out2["reply"].lower()
    assert set(labels(out2)) >= {"kg", "litre", "piece", "packet"}
    out3 = choose(client, out2, "piece")
    assert "rate" in out3["reply"].lower()
    out4 = choose(client, out3, "⏭")                                        # skip price -> ask this entry's price
    assert out4["status"] == "AWAITING_CONFIRMATION" and out4["decision"]["reason"] == "MISSING_PRICE"
    out5 = client.post("/dev/simulate/answer", json={"pending_id": out4["pending_id"], "text": "das"}, headers=ADMIN).json()
    assert out5["status"] == "COMMITTED" and out5["decision"]["amount_paise"] == 3000
    with rt.engine.connect() as c:
        assert c.execute(text("SELECT selling_price_paise FROM products WHERE name='Shampoo'")).scalar() is None


def test_did_you_mean_teaches_alias(client, rt):
    out = sim(client, "Ramesh Kumar ne 1 litre oil udhaar liya")
    assert out["decision"]["reason"] == "UNKNOWN_PRODUCT" and "Refined Oil?" in labels(out)
    r = choose(client, out, "Refined Oil?")
    assert r["status"] == "COMMITTED" and r["decision"]["product"] == "Refined Oil"
    with rt.engine.connect() as c:
        assert "oil" in c.execute(text("SELECT aliases FROM products WHERE name='Refined Oil'")).scalar()
    assert sim(client, "Ramesh Kumar ne 1 litre oil udhaar liya")["decision"]["product"] == "Refined Oil"


def test_catalog_commands(client, rt):
    def cmd(t, mid):
        client.post("/telegram/webhook", json=text_update(t, mid), headers=SECRET)
        return rt.tg.sent[-1]["text"]
    # order-free, punctuation-tolerant, units and money in any spelling
    assert "Naya item: Parle G (packet) @ ₹10/pkt" in cmd("/add_product Parle G packets 10", 900)
    assert "Naya item: Chai Patti (kg) @ ₹300/kg" in cmd("/add_product 300 rs chai patti, kilo", 901)
    assert "Naya item: Sarson Tel (litre) @ ₹180/L" in cmd("/add_product Sarson tel ₹180 per litre", 9011)
    assert "Naya customer: Kavita Sharma" in cmd("/add_customer kavita sharma", 902)
    assert "✅" in cmd("/alias Parle G parleji", 903)
    assert "✅ Parle G: ₹12/packet" in cmd("/price Parle G 12 rupaye", 904)
    assert "Parle G" in cmd("/products", 905) and "Kavita Sharma" in cmd("/customers", 906)
    entry = sim(client, "Kavita Sharma ne 3 parleji udhaar liye")
    assert entry["status"] == "COMMITTED" and entry["decision"]["amount_paise"] == 3600
    assert "hata diya" in cmd("/remove_product Parle G", 907)
    assert sim(client, "Kavita Sharma ne 3 parleji udhaar liye")["decision"]["reason"] == "UNKNOWN_PRODUCT"


def test_add_product_wizard_asks_for_what_is_missing(client, rt):
    def send(t, mid):
        client.post("/telegram/webhook", json=text_update(t, mid), headers=SECRET)
        drain(rt)
        return rt.tg.sent[-1]
    # only a name -> unit buttons -> price by text
    r = send("/add_product Maggi", 920)
    assert "unit" in r["text"].lower() and [b["text"] for b in r["buttons"][0]] == ["kg", "litre"]
    idx = next(i for i, b in enumerate([b for row in r["buttons"] for b in row]) if b["text"] == "packet")
    pid = [b for row in r["buttons"] for b in row][idx]["callback_data"].split(":")[1]
    client.post("/dev/simulate/choose", json={"pending_id": pid, "index": idx}, headers=ADMIN)
    assert "rate" in rt.tg.sent[-1]["text"].lower()
    r = send("chaudah rupaye", 921)
    assert "Naya item: Maggi (packet) @ ₹14/pkt" in r["text"]
    # nothing at all -> asks the name; a full answer finishes in one step
    r = send("/add_product", 922)
    assert "naam" in r["text"].lower()
    r = send("Bournvita 500 g packet 250", 923)
    assert "Naya item: Bournvita 500g (packet) @ ₹250/pkt" in r["text"]
    # price skipped -> saved without a rate
    r = send("/add_product Shampoo piece", 924)
    skip = next(b for row in r["buttons"] for b in row if b["text"].startswith("⏭"))
    pid, idx = skip["callback_data"].split(":")[1], int(skip["callback_data"].split(":")[2])
    client.post("/dev/simulate/choose", json={"pending_id": pid, "index": idx}, headers=ADMIN)
    assert "rate nahi" in rt.tg.sent[-1]["text"]
    with rt.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM transactions WHERE created_by LIKE 'tg:%'")).scalar() == 0   # wizards book nothing
        assert c.execute(text("SELECT count(*) FROM products WHERE name IN ('Maggi','Shampoo') AND is_active")).scalar() == 2
    # /add_customer with no name asks
    r = send("/add_customer", 925)
    assert "customer ka naam" in r["text"].lower()
    r = send("Geeta Ben", 926)
    assert "Naya customer: Geeta Ben" in r["text"]


def test_staff_cannot_change_catalog(client, rt):
    with rt.engine.begin() as c:
        c.execute(text("UPDATE shop_members SET role='STAFF' WHERE telegram_user_id=:u"), {"u": USER})
    client.post("/telegram/webhook", json=text_update("/add_product Chai kg 300", 910), headers=SECRET)
    assert "Sirf owner" in rt.tg.sent[-1]["text"]
    with rt.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM products WHERE name='Chai'")).scalar() == 0
