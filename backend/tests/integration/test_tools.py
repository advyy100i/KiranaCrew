"""The optional tooling (forecast, insights) must read only, write only to its own tables, and never send unapproved."""
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.config import Settings
from app.crew.insights import approve_and_send, build_facts, run_insights
from app.llm.base import LLMResponse
from app.services.pipeline import Runtime
from app.telegram.client import FakeTelegram

pytestmark = pytest.mark.integration


class StubLLM:
    name, model = "stub", "stub"

    def __init__(self):
        self.calls = 0

    def complete_json(self, system, user, timeout=30.0):
        self.calls += 1
        return LLMResponse(text=json.dumps({"text": f"line from call {self.calls}"}), provider="stub", model="stub", latency_ms=1)


def test_build_facts_is_deterministic_and_numeric(engine, seeded):
    ws = date.today() - timedelta(days=date.today().weekday() + 7)
    with engine.connect() as c:
        f1 = build_facts(c, seeded, ws); f2 = build_facts(c, seeded, ws)
    assert f1 == f2
    assert set(f1) >= {"sales_by_product", "rising_balances", "top_balances", "slow_moving", "low_stock", "week_total"}
    assert len(f1["top_balances"]) <= 5


def test_insights_stub_crew_capped_and_never_sent_unapproved(engine, seeded):
    tg = FakeTelegram(); stub = StubLLM()
    rt = Runtime(engine=engine, tg=tg, stt=None, llm_providers=[stub], settings=Settings(database_url=str(engine.url)))
    out = run_insights(rt, seeded)
    assert out["llm_calls"] == 3 and stub.calls == 3 and "line from call 3" in out["body"]
    with engine.connect() as c:
        approved, facts = c.execute(text("SELECT approved, facts FROM insights WHERE id=:i"), {"i": out["insight_id"]}).first()
        assert approved is False and "sales_by_product" in facts
        assert c.execute(text("SELECT count(*) FROM llm_calls WHERE stage='INSIGHTS'")).scalar() == 3
    assert tg.sent == []                                             # nothing goes out before approval
    again = run_insights(rt, seeded)
    assert again.get("skipped") and stub.calls == 3                  # one run per week
    res = approve_and_send(rt, out["insight_id"])
    assert res["approved"] and res["sent"] and "salah" in tg.sent[-1]["text"]


def test_insights_failure_writes_nothing(engine, seeded):
    class Boom:
        name, model = "boom", "boom"
        def complete_json(self, *a, **k): raise RuntimeError("llm down")
    rt = Runtime(engine=engine, tg=FakeTelegram(), stt=None, llm_providers=[Boom()], settings=Settings(database_url=str(engine.url)))
    with pytest.raises(RuntimeError):
        run_insights(rt, seeded, force=True)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM insights")).scalar() == 0


def test_forecast_writes_both_models_with_scores(engine):
    from app.cli.seed_demo import seed
    from app.forecast.demand import run_forecast
    from tests.conftest import CATALOG
    sid = seed(engine, CATALOG, reset=True, telegram_user_id=None, history_weeks=10)["shop_id"]
    out = run_forecast(engine, sid)
    assert out["products"] > 0 and out["rows"] == out["products"] * 14
    with engine.connect() as c:
        models = {r[0] for r in c.execute(text("SELECT DISTINCT model FROM forecasts"))}
        assert models == {"AutoETS", "SeasonalNaive"}
        assert c.execute(text("SELECT count(*) FROM forecasts WHERE baseline_mase IS NULL")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM transactions WHERE created_by='forecast'")).scalar() == 0
