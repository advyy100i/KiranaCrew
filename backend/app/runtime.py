"""One Runtime per process, built from settings. Tests build their own with fakes."""
from functools import lru_cache

from app.config import settings
from app.db import get_engine
from app.llm.factory import build_llm_chain
from app.services.pipeline import Runtime
from app.stt.factory import build_stt
from app.telegram.client import Telegram


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    return Runtime(engine=get_engine(), tg=Telegram(settings.telegram_bot_token), stt=build_stt(settings),
                   llm_providers=build_llm_chain(settings) if settings.parser_mode != "rules" else [],
                   settings=settings)
