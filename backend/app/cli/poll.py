"""Long-polling transport: no tunnel, no inbound connectivity. Deletes the webhook (only one consumer may exist),
then forwards each update to the local webhook with the secret header. The offset advances only on a 2xx,
so a failed delivery is retried on the next poll.   python -m app.cli.poll [--api http://127.0.0.1:8000]"""
import argparse
import logging
import time

import httpx

from app.config import settings
from app.telegram.client import Telegram

log = logging.getLogger("poll")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--timeout", type=int, default=30)
    a = ap.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs full URLs = the bot token
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    tg = Telegram(settings.telegram_bot_token)
    print("deleteWebhook:", tg.delete_webhook())
    offset = None
    headers = {"X-Telegram-Bot-Api-Secret-Token": settings.telegram_webhook_secret}
    while True:
        try:
            updates = tg.get_updates(offset, a.timeout)
        except Exception as e:                                    # noqa: BLE001
            log.warning("getUpdates failed: %s", e); time.sleep(3); continue
        for u in updates:
            try:
                r = httpx.post(f"{a.api}/telegram/webhook", json=u, headers=headers, timeout=30)
            except httpx.HTTPError as e:
                log.warning("local webhook unreachable (%s); will retry update %s", e, u["update_id"]); time.sleep(2); break
            if 200 <= r.status_code < 300:
                offset = u["update_id"] + 1
                log.info("forwarded update %s", u["update_id"])
            else:
                log.warning("webhook returned %s for update %s; retrying", r.status_code, u["update_id"]); time.sleep(2); break


if __name__ == "__main__":
    main()
