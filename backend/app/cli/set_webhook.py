"""python -m app.cli.set_webhook --url https://<tunnel>.trycloudflare.com/telegram/webhook | --info | --delete | --commands"""
import argparse
import json
import re

from app.config import settings
from app.telegram.client import Telegram


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--commands", action="store_true", help="register the bot command menu")
    a = ap.parse_args()
    tg = Telegram(settings.telegram_bot_token)
    if a.url:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", settings.telegram_webhook_secret or ""):
            raise SystemExit("TELEGRAM_WEBHOOK_SECRET must be 1-256 chars of A-Za-z0-9_-")
        print(tg.set_webhook(a.url, settings.telegram_webhook_secret))
    if a.delete:
        print(tg.delete_webhook())
    if a.commands:
        print(tg.set_commands())
    if a.info or not (a.url or a.delete or a.commands):
        print(json.dumps(tg.webhook_info(), indent=2))


if __name__ == "__main__":
    main()
