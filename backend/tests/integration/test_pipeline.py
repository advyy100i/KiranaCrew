"""End-to-end through the FastAPI app with FakeTelegram + FixtureSTT. Real Postgres."""
import copy
import json
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.services import jobs
from app.services.pipeline import Runtime
from app.stt.factory import build_stt
from app.telegram.client import FakeTelegram

pytestmark = pytest.mark.integration

ADMIN = {"X-Admin-Key": "test-admin"}
SECRET = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
AUDIO = b"fake-ogg-bytes-credit-sale"
CHAT, USER = 777, 4242


@pytest.fixture
def rt(engine, seeded):
    s = Settings(database_url=engine.url.render_as_string(hide_password=False), stt_provider="fixture", parser_mode="rules",
                 admin_api_key="test-admin", telegram_webhook_secret="test-secret", run_worker_inline=False, jwt_secret="t")
    stt = build_stt(s, fixture_map={__import__("hashlib").sha256(AUDIO).hexdigest(): "Ramesh Kumar ne paanch kilo chawal udhaar pe liya"})
    return Runtime(engine=engine, tg=FakeTelegram(files={"file-1": AUDIO}), stt=stt, llm_providers=[], settings=s)


@pytest.fixture
def client(rt):
    app = create_app(rt)
    with TestClient(app) as c:
        yield c


def voice_update(message_id=100, file_id="file-1", duration=4):
    return {"update_id": 1, "message": {"message_id": message_id, "from": {"id": USER}, "chat": {"id": CHAT},
                                        "voice": {"file_id": file_id, "duration": duration}}}


def text_update(text_, message_id):
    return {"update_id": 1, "message": {"message_id": message_id, "from": {"id": USER}, "chat": {"id": CHAT}, "text": text_}}


def counts(engine):
    with engine.connect() as c:
        return tuple(c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                     for t in ("transactions", "inventory_movements", "credit_ledger"))


def rice_stock(engine):
    with engine.connect() as c:
        return c.execute(text("SELECT stock FROM v_product_stock WHERE name='Rice'")).scalar()


def drain(rt):
    from app.cli.worker import run_one
    n = 0
    while run_one(rt):
        n += 1
    return n


def sim(client, text_, key=None, **kw):
    r = client.post("/dev/simulate", json={"text": text_, "key": key, **kw}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- webhook security
def test_webhook_rejects_bad_secret(client):
    assert client.post("/telegram/webhook", json=voice_update()).status_code == 401
    assert client.post("/telegram/webhook", json=voice_update(), headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}).status_code == 401


def test_unregistered_user_creates_no_row(client, rt):
    u = voice_update(); u["message"]["from"]["id"] = 999999
    assert client.post("/telegram/webhook", json=u, headers=SECRET).status_code == 200
    with rt.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM messages")).scalar() == 0
    assert "registered nahi" in rt.tg.sent[-1]["text"]


# ---------------------------------------------------------------- idempotency
def test_same_update_ten_times_sequential_posts_once(client, rt):
    before, stock0 = counts(rt.engine), rice_stock(rt.engine)
    for _ in range(10):
        assert client.post("/telegram/webhook", json=voice_update(), headers=SECRET).status_code == 200
    with rt.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM messages")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM jobs WHERE kind='PROCESS_MESSAGE'")).scalar() == 1
    assert drain(rt) == 1
    after = counts(rt.engine)
    assert tuple(a - b for a, b in zip(after, before)) == (1, 1, 1)
    assert rice_stock(rt.engine) == stock0 - 5
    assert "Udhaar: Ramesh Kumar" in rt.tg.sent[-1]["text"] and rt.tg.sent[-1]["buttons"][0][0]["callback_data"].startswith("un:")


def test_concurrent_duplicates_post_once(client, rt):
    before = counts(rt.engine)
    errors = []

    def hit():
        try:
            assert client.post("/telegram/webhook", json=voice_update(message_id=200), headers=SECRET).status_code == 200
        except Exception as e:                                   # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=hit) for _ in range(5)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors
    # five workers race for the same job / message
    ws = [threading.Thread(target=drain, args=(rt,)) for _ in range(5)]
    [t.start() for t in ws]; [t.join() for t in ws]
    after = counts(rt.engine)
    assert tuple(a - b for a, b in zip(after, before)) == (1, 1, 1)


def test_simulate_duplicate_key(client, rt):
    before = counts(rt.engine)
    a = sim(client, "Sunita Devi ne 2 litre doodh udhaar liya", key="sim:fixed-1")
    b = sim(client, "Sunita Devi ne 2 litre doodh udhaar liya", key="sim:fixed-1")
    assert a["status"] == "COMMITTED" and b["duplicate"] is True
    assert counts(rt.engine)[0] - before[0] == 1


# ---------------------------------------------------------------- confirmations
def test_ambiguous_customer_buttons_and_double_tap(client, rt):
    before = counts(rt.engine)
    out = sim(client, "Ramesh ne 2 kilo cheeni udhaar liya")
    assert out["status"] == "AWAITING_CONFIRMATION" and out["decision"]["reason"] == "AMBIGUOUS_CUSTOMER"
    labels = [b["text"] for row in out["buttons"] for b in row]
    assert "Ramesh Kumar" in labels and "Ramesh Sharma" in labels
    idx = labels.index("Ramesh Sharma")
    r1 = client.post("/dev/simulate/choose", json={"pending_id": out["pending_id"], "index": idx}, headers=ADMIN).json()
    assert r1["status"] == "COMMITTED" and r1["decision"]["customer"] == "Ramesh Sharma"
    r2 = client.post("/dev/simulate/choose", json={"pending_id": out["pending_id"], "index": idx}, headers=ADMIN).json()
    assert r2["status"] == "SKIPPED"                             # second tap: no row returned, nothing posted
    assert counts(rt.engine)[0] - before[0] == 1


def test_product_then_payment_chain(client, rt):
    out = sim(client, "Suresh ne 1 litre tel liya")
    assert out["decision"]["reason"] == "AMBIGUOUS_PRODUCT"
    labels = [b["text"] for row in out["buttons"] for b in row]
    out2 = client.post("/dev/simulate/choose", json={"pending_id": out["pending_id"], "index": labels.index("Refined Oil")}, headers=ADMIN).json()
    assert out2["status"] == "AWAITING_CONFIRMATION" and out2["decision"]["reason"] == "CHOOSE_PAYMENT"
    labels2 = [b["text"] for row in out2["buttons"] for b in row]
    out3 = client.post("/dev/simulate/choose", json={"pending_id": out2["pending_id"], "index": labels2.index("💵 Cash")}, headers=ADMIN).json()
    assert out3["status"] == "COMMITTED" and out3["decision"]["type"] == "CASH_SALE" and out3["decision"]["product"] == "Refined Oil"


def test_unknown_customer_creates_new_on_confirm(client, rt):
    out = sim(client, "Kavita ne 2 kilo chawal udhaar liya")
    assert out["decision"]["reason"] == "UNKNOWN_CUSTOMER"
    labels = [b["text"] for row in out["buttons"] for b in row]
    idx = next(i for i, l in enumerate(labels) if l.startswith("➕"))
    r = client.post("/dev/simulate/choose", json={"pending_id": out["pending_id"], "index": idx}, headers=ADMIN).json()
    assert r["status"] == "COMMITTED" and r["decision"]["customer"] == "Kavita"


def test_missing_price_answered_by_text(client, rt):
    out = sim(client, "Ramesh Kumar ne 2 kilo moong dal udhaar liya")
    assert out["decision"]["reason"] == "MISSING_PRICE"
    r = client.post("/dev/simulate/answer", json={"pending_id": out["pending_id"], "text": "ek sau bees rupaye"}, headers=ADMIN).json()
    assert r["status"] == "COMMITTED" and r["decision"]["amount_paise"] == 24000 and r["decision"]["price_source"] == "USER_REPLY"


def test_missing_quantity_answered_by_next_telegram_message(client, rt):
    client.post("/telegram/webhook", json=text_update("Ramesh Kumar ne chawal udhaar liya", 300), headers=SECRET)
    drain(rt)
    assert "kitna" in rt.tg.sent[-1]["text"].lower()
    client.post("/telegram/webhook", json=text_update("dhai kilo", 301), headers=SECRET)
    drain(rt)
    assert "Udhaar: Ramesh Kumar — Rice 2.5 kg" in rt.tg.sent[-1]["text"]
    assert rt.tg.cleared                                         # stale buttons removed


def test_large_amount_confirm_and_cancel(client, rt):
    before = counts(rt.engine)
    out = sim(client, "Mohan Lal ne 20 kilo chawal udhaar liya 12000 rupaye ka")
    assert out["decision"]["reason"] == "CONFIRM_LARGE_AMOUNT"
    labels = [b["text"] for row in out["buttons"] for b in row]
    r = client.post("/dev/simulate/choose", json={"pending_id": out["pending_id"], "index": labels.index("❌ Cancel")}, headers=ADMIN).json()
    assert r["status"] == "REJECTED" and counts(rt.engine)[0] == before[0]


def test_expired_pending(client, rt):
    out = sim(client, "Ramesh ne 2 kilo cheeni udhaar liya")
    with rt.engine.begin() as c:
        c.execute(text("UPDATE pending_actions SET expires_at = now() - interval '1 minute' WHERE id=CAST(:p AS uuid)"), {"p": out["pending_id"]})
    r = client.post("/dev/simulate/choose", json={"pending_id": out["pending_id"], "index": 0}, headers=ADMIN).json()
    assert r["status"] == "SKIPPED"
    with rt.engine.begin() as c:
        rep = jobs.reap(c)
    assert rep["expired_pendings"] == 1


def test_undo_twice_reverses_once(client, rt):
    stock0 = rice_stock(rt.engine)
    out = sim(client, "Ramesh Kumar ne 5 kilo chawal udhaar liya")
    assert rice_stock(rt.engine) == stock0 - 5
    r1 = client.post("/dev/simulate/undo", json={"transaction_id": out["transaction_id"]}, headers=ADMIN).json()
    r2 = client.post("/dev/simulate/undo", json={"transaction_id": out["transaction_id"]}, headers=ADMIN).json()
    assert r1["status"] == "REVERSED" and r2["status"] == "SKIPPED"
    assert rice_stock(rt.engine) == stock0


def test_reject_and_question_reply(client, rt):
    assert sim(client, "hello")["status"] == "REJECTED"
    out = sim(client, "Sunita Devi ka hisaab kitna hai")
    assert out["status"] == "REJECTED" and "Sunita Devi ka udhaar" in out["reply"]


def test_low_confidence_transcript_asks_first(client, rt):
    client.post("/telegram/webhook", json=voice_update(message_id=400), headers=SECRET)
    with rt.engine.begin() as c:
        c.execute(text("UPDATE messages SET transcript='Ramesh Kumar ne paanch kilo chawal udhaar pe liya', stt_confidence=-2.5 "
                       "WHERE telegram_message_id=400"))
    drain(rt)
    assert "Maine suna" in rt.tg.sent[-1]["text"]
    with rt.engine.connect() as c:
        pid = str(c.execute(text("SELECT id FROM pending_actions WHERE status='PENDING' AND kind='CONFIRM_TRANSCRIPT'")).scalar())
    r = client.post("/dev/simulate/choose", json={"pending_id": pid, "index": 0}, headers=ADMIN).json()
    assert r["status"] == "COMMITTED"


# ---------------------------------------------------------------- guards, failures, reaper
def test_voice_too_long_rejected_before_stt(client, rt):
    client.post("/telegram/webhook", json=voice_update(message_id=500, duration=300), headers=SECRET)
    drain(rt)
    with rt.engine.connect() as c:
        assert c.execute(text("SELECT status, error FROM messages WHERE telegram_message_id=500")).first() == ("FAILED", "too_long")
        assert c.execute(text("SELECT count(*) FROM llm_calls")).scalar() == 0
    assert "60 second" in rt.tg.sent[-1]["text"]


def test_stt_failure_retries_then_dead_no_partial_write(client, rt):
    before = counts(rt.engine)
    client.post("/telegram/webhook", json=voice_update(message_id=600, file_id="file-missing"), headers=SECRET)
    for _ in range(6):
        with rt.engine.begin() as c:
            c.execute(text("UPDATE jobs SET run_after=now()"))
        drain(rt)
    with rt.engine.connect() as c:
        assert c.execute(text("SELECT status FROM jobs WHERE dedupe_key LIKE 'msg:%' ORDER BY id DESC LIMIT 1")).scalar() == "DEAD"
        assert c.execute(text("SELECT status FROM messages WHERE telegram_message_id=600")).scalar() == "FAILED"
    assert counts(rt.engine) == before
    assert rt.tg.sent[-1]["chat_id"] == CHAT and "dobara" in rt.tg.sent[-1]["text"].lower()


def test_poisoned_job_dead_after_max_attempts(rt):
    with rt.engine.begin() as c:
        jid = jobs.enqueue(c, "TEST_FAIL", {}, max_attempts=3)
    for _ in range(4):
        with rt.engine.begin() as c:
            c.execute(text("UPDATE jobs SET run_after=now() WHERE id=:j"), {"j": jid})
        drain(rt)
    with rt.engine.connect() as c:
        st, att = c.execute(text("SELECT status, attempts FROM jobs WHERE id=:j"), {"j": jid}).first()
    assert (st, att) == ("DEAD", 3)


def test_reaper_recovers_killed_worker(client, rt):
    """Simulate a worker that claimed the job and died: job RUNNING + stale, message PROCESSING."""
    before = counts(rt.engine)
    client.post("/telegram/webhook", json=voice_update(message_id=700), headers=SECRET)
    with rt.engine.begin() as c:
        c.execute(text("UPDATE jobs SET status='RUNNING', locked_at=now() - interval '10 minutes' WHERE dedupe_key LIKE 'msg:%'"))
        c.execute(text("UPDATE messages SET status='PROCESSING', updated_at=now() - interval '10 minutes' WHERE telegram_message_id=700"))
    assert drain(rt) == 0                                            # nothing claimable yet
    with rt.engine.begin() as c:
        rep = jobs.reap(c, stale=timedelta(minutes=5))
    assert rep["requeued_jobs"] == 1
    assert drain(rt) == 1
    assert counts(rt.engine)[0] - before[0] == 1


def test_trace_and_stats(client, rt):
    out = sim(client, "Ramesh Kumar ne 5 kilo chawal udhaar liya")
    tr = client.get(f"/dev/trace/{out['message_id']}", headers=ADMIN).json()
    assert tr["stages"]["parser_used"] == "rules" and tr["transactions"][0]["type"] == "CREDIT_SALE"
    assert list(tr["rows_written"].values())[0]["ledger"][0]["amount_paise"] == 25000
    st = client.get("/dev/stats", headers=ADMIN).json()
    assert st["transactions"] >= 1 and "queue_depth" in st
    assert client.get("/dev/stats").status_code == 401


def test_commands_and_dashboard_jwt(client, rt):
    client.post("/telegram/webhook", json=text_update("/balance", 800), headers=SECRET)
    client.post("/telegram/webhook", json=text_update("/dashboard", 801), headers=SECRET)
    assert "Udhaar (kul" in rt.tg.sent[-2]["text"]
    token = rt.tg.sent[-1]["text"].split("token=")[1]
    r = client.get("/api/v1/dashboard/today", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and "sales_paise" in r.json()
    assert client.get("/api/v1/dashboard/today").status_code == 401
    assert client.get("/readyz").json()["db"] is True


def test_recorded_update_replays_from_disk(client, rt):
    """demo/updates/credit_sale.json must exist and be a valid voice update for replay.py."""
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[3] / "demo" / "updates" / "credit_sale.json"
    upd = json.loads(p.read_text(encoding="utf-8"))
    upd = copy.deepcopy(upd); upd["message"]["from"]["id"] = USER; upd["message"]["chat"]["id"] = CHAT
    rt.tg.files[upd["message"]["voice"]["file_id"]] = AUDIO
    assert client.post("/dev/webhook-replay", json=upd, headers=ADMIN).status_code == 200
