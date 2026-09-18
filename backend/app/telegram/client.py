"""Thin Telegram Bot API client over httpx. Never called while a DB transaction is open.
FakeTelegram (below) records calls for tests and the simulator."""
import logging
import pathlib

import httpx

DEMO_AUDIO = pathlib.Path(__file__).resolve().parents[3] / "demo" / "audio"

log = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class Telegram:
    name = "telegram"

    def __init__(self, token: str, timeout: float = 20.0):
        self.token, self.timeout = token, timeout
        self.base = f"https://api.telegram.org/bot{token}"

    def _call(self, method: str, **params) -> dict:
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN not set")
        r = httpx.post(f"{self.base}/{method}", json=params, timeout=self.timeout)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 or not body.get("ok"):
            raise TelegramError(f"{method}: {r.status_code} {body.get('description', r.text[:200])}")
        return body["result"]

    def send(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> int | None:
        params = {"chat_id": chat_id, "text": text}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        try:
            return self._call("sendMessage", **params)["message_id"]
        except (TelegramError, httpx.HTTPError) as e:
            log.warning("telegram send failed: %s", e)
            return None

    def answer_callback(self, callback_query_id: str, text: str | None = None) -> None:
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_query_id, **({"text": text} if text else {}))
        except (TelegramError, httpx.HTTPError) as e:
            log.warning("answerCallbackQuery failed: %s", e)

    def clear_buttons(self, chat_id: int, message_id: int, text: str | None = None) -> None:
        try:
            if text:
                self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)
            else:
                self._call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": []})
        except (TelegramError, httpx.HTTPError) as e:
            log.info("clear_buttons failed (probably already edited): %s", e)

    def download(self, file_id: str, max_bytes: int) -> tuple[bytes, str]:
        if file_id.startswith("demo-"):                       # recorded demo clips live on disk, no token needed
            local = DEMO_AUDIO / f"{file_id}.ogg"
            if not local.exists():
                raise TelegramError(f"demo clip missing: {local}")
            return local.read_bytes(), local.name
        info = self._call("getFile", file_id=file_id)
        path = info["file_path"]
        if info.get("file_size", 0) > max_bytes:
            raise TelegramError("file too big")
        r = httpx.get(f"https://api.telegram.org/file/bot{self.token}/{path}", timeout=60.0)
        if r.status_code >= 400:
            raise TelegramError(f"download {r.status_code}")
        return r.content, path.rsplit("/", 1)[-1]

    def set_webhook(self, url: str, secret: str) -> dict:
        return self._call("setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"],
                          drop_pending_updates=False)

    def delete_webhook(self) -> dict:
        return self._call("deleteWebhook", drop_pending_updates=False)

    def webhook_info(self) -> dict:
        return self._call("getWebhookInfo")

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
        params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        r = httpx.post(f"{self.base}/getUpdates", json=params, timeout=timeout + 10)
        body = r.json()
        if not body.get("ok"):
            raise TelegramError(body.get("description", "getUpdates failed"))
        return body["result"]

    def set_commands(self) -> dict:
        return self._call("setMyCommands", commands=[
            {"command": "start", "description": "Shuru karein / apna user id dekhein"},
            {"command": "help", "description": "Kaise bolein"},
            {"command": "balance", "description": "Sab customers ka udhaar"},
            {"command": "stock", "description": "Stock list"},
            {"command": "last", "description": "Aakhri entries"},
            {"command": "dashboard", "description": "Dashboard link"},
            {"command": "products", "description": "Items aur rate"},
            {"command": "customers", "description": "Customers aur unka udhaar"},
            {"command": "add_product", "description": "Naya item: /add_product Maggi packet 14 (ya sirf /add_product)"},
            {"command": "add_customer", "description": "Naya customer: /add_customer Kavita"},
            {"command": "alias", "description": "Spelling sikhayein: /alias Rice chaval"},
            {"command": "price", "description": "Rate badlein: /price Rice 52"},
            {"command": "remove_product", "description": "Item hatayein"},
        ])


class FakeTelegram:
    """Records every call; returns increasing message ids. Used by tests and by /dev/simulate."""
    name = "fake"

    def __init__(self, files: dict[str, bytes] | None = None):
        self.sent: list[dict] = []
        self.answered: list[dict] = []
        self.cleared: list[dict] = []
        self.files = files or {}
        self._next = 1000

    def send(self, chat_id, text, buttons=None):
        self._next += 1
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": buttons or [], "message_id": self._next})
        return self._next

    def answer_callback(self, callback_query_id, text=None):
        self.answered.append({"id": callback_query_id, "text": text})

    def clear_buttons(self, chat_id, message_id, text=None):
        self.cleared.append({"chat_id": chat_id, "message_id": message_id, "text": text})

    def download(self, file_id, max_bytes):
        if file_id not in self.files:
            raise TelegramError("no such file")
        return self.files[file_id], f"{file_id}.oga"
