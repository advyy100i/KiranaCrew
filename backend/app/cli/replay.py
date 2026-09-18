"""Idempotency demo: post one stored Telegram update N times, K at a time, through the running API.
   python -m app.cli.replay --update ../demo/updates/credit_sale.json --times 10 --concurrency 5
Prints transactions / movements / ledger counts before and after, plus rice stock. Expected: +1 / +1 / +1."""
import argparse
import json
import pathlib
import threading
import time

import httpx
from sqlalchemy import text

from app.config import settings
from app.db import get_engine


def counts() -> dict:
    with get_engine().connect() as c:
        out = {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
               for t in ("messages", "transactions", "inventory_movements", "credit_ledger")}
        out["rice_stock"] = float(c.execute(text("SELECT stock FROM v_product_stock WHERE name='Rice' LIMIT 1")).scalar() or 0)
        out["queue_depth"] = c.execute(text("SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')")).scalar()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", required=True)
    ap.add_argument("--times", type=int, default=10)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--fresh", action="store_true", help="bump message_id so this run creates one NEW transaction")
    ap.add_argument("--wait", type=float, default=20.0, help="seconds to wait for the worker to drain")
    a = ap.parse_args()
    upd = json.loads(pathlib.Path(a.update).read_text(encoding="utf-8"))
    if a.fresh:
        upd["message"]["message_id"] = int(time.time())
    before = counts()
    print("before:", before)
    results, lock = [], threading.Lock()

    def post():
        r = httpx.post(f"{a.api}/telegram/webhook", json=upd,
                       headers={"X-Telegram-Bot-Api-Secret-Token": settings.telegram_webhook_secret}, timeout=30)
        with lock:
            results.append(r.status_code)
    for batch in range(0, a.times, a.concurrency):
        ts = [threading.Thread(target=post) for _ in range(min(a.concurrency, a.times - batch))]
        [t.start() for t in ts]; [t.join() for t in ts]
    print(f"posted {len(results)} updates ({a.concurrency} at a time): statuses {sorted(set(results))}")
    t0 = time.time()
    while time.time() - t0 < a.wait and counts()["queue_depth"] > 0:
        time.sleep(0.5)
    after = counts()
    print("after: ", after)
    print("delta: ", {k: (after[k] - before[k]) for k in before})


if __name__ == "__main__":
    main()
