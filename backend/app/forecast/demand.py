"""Nightly 7-day demand forecast per product, honestly scored against a seasonal-naive baseline.

Rolling-origin backtest (3 folds x 7 days) computes MASE for AutoETS and SeasonalNaive per product; both go into
`forecasts` so the dashboard can show the model only where it beats the baseline. Imported only by the FORECAST job
and its CLI — never by the API process. Reads transactions; writes forecasts. Nothing else.
"""
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text

log = logging.getLogger("forecast")
HORIZON, SEASON, FOLDS, MIN_DAYS = 7, 7, 3, 28


def daily_sales(engine, shop_id: int) -> pd.DataFrame:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT t.product_id, (t.created_at AT TIME ZONE 'Asia/Kolkata')::date AS ds, SUM(t.quantity) AS y
            FROM transactions t
            WHERE t.shop_id=:s AND t.type IN ('CREDIT_SALE','CASH_SALE') AND t.quantity IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM transactions r WHERE r.reverses_transaction_id = t.id)
            GROUP BY 1, 2 ORDER BY 1, 2"""), {"s": shop_id}).all()
    df = pd.DataFrame(rows, columns=["unique_id", "ds", "y"])
    if df.empty:
        return df
    df["ds"] = pd.to_datetime(df["ds"]); df["y"] = df["y"].astype(float)
    # dense daily index per product (missing day = zero sales)
    out = []
    for pid, g in df.groupby("unique_id"):
        idx = pd.date_range(g["ds"].min(), df["ds"].max(), freq="D")
        s = g.set_index("ds")["y"].reindex(idx, fill_value=0.0)
        out.append(pd.DataFrame({"unique_id": pid, "ds": idx, "y": s.values}))
    return pd.concat(out, ignore_index=True)


def mase(y_true: np.ndarray, y_pred: np.ndarray, y_train: np.ndarray, m: int = SEASON) -> float:
    scale = np.mean(np.abs(y_train[m:] - y_train[:-m])) if len(y_train) > m else np.nan
    if not scale or np.isnan(scale):
        return float("nan")
    return float(np.mean(np.abs(y_true - y_pred)) / scale)


def _fit_predict(train: pd.DataFrame, h: int) -> pd.DataFrame:
    from statsforecast import StatsForecast
    from statsforecast.models import AutoETS, SeasonalNaive
    sf = StatsForecast(models=[AutoETS(season_length=SEASON), SeasonalNaive(season_length=SEASON)], freq="D", n_jobs=1)
    return sf.forecast(df=train, h=h).reset_index() if "unique_id" not in sf.forecast(df=train, h=h).columns \
        else sf.forecast(df=train, h=h).reset_index(drop=True)


def backtest(df: pd.DataFrame) -> dict[int, dict[str, float]]:
    """Rolling origin: the last FOLDS*HORIZON days are held out fold by fold; no future rows leak into training."""
    scores: dict[int, dict[str, list]] = {}
    for pid, g in df.groupby("unique_id"):
        g = g.sort_values("ds").reset_index(drop=True)
        if len(g) < MIN_DAYS + FOLDS * HORIZON:
            continue
        scores[pid] = {"AutoETS": [], "SeasonalNaive": []}
        for k in range(FOLDS, 0, -1):
            cut = len(g) - k * HORIZON
            train, test = g.iloc[:cut], g.iloc[cut:cut + HORIZON]
            fc = _fit_predict(train, HORIZON)
            for model in scores[pid]:
                scores[pid][model].append(mase(test["y"].values, fc[model].values[:len(test)], train["y"].values))
    return {pid: {m: float(np.nanmean(v)) for m, v in s.items()} for pid, s in scores.items()}


def run_forecast(engine, shop_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    df = daily_sales(engine, shop_id)
    if df.empty:
        return {"products": 0, "reason": "no sales history"}
    scores = backtest(df)
    eligible = df[df["unique_id"].isin(scores.keys())]
    if eligible.empty:
        return {"products": 0, "reason": f"need >= {MIN_DAYS + FOLDS * HORIZON} days per product"}
    fc = _fit_predict(eligible, HORIZON)
    written, wins = 0, 0
    with engine.begin() as c:
        c.execute(text("DELETE FROM forecasts WHERE shop_id=:s AND horizon_date >= :d"), {"s": shop_id, "d": today})
        for _, r in fc.iterrows():
            pid = int(r["unique_id"]); sc = scores[pid]
            for model in ("AutoETS", "SeasonalNaive"):
                c.execute(text("""INSERT INTO forecasts (shop_id, product_id, horizon_date, qty_forecast, model, baseline_mase, model_mase)
                                  VALUES (:s, :p, :d, :q, :m, :b, :mm)
                                  ON CONFLICT (shop_id, product_id, horizon_date, model) DO UPDATE
                                  SET qty_forecast=EXCLUDED.qty_forecast, baseline_mase=EXCLUDED.baseline_mase, model_mase=EXCLUDED.model_mase"""),
                          {"s": shop_id, "p": pid, "d": pd.Timestamp(r["ds"]).date(), "q": round(max(0.0, float(r[model])), 3),
                           "m": model, "b": sc["SeasonalNaive"], "mm": sc[model]})
                written += 1
        wins = sum(1 for sc in scores.values() if sc["AutoETS"] < sc["SeasonalNaive"])
    summary = {"products": len(scores), "rows": written, "model_beats_baseline": wins,
               "scores": {pid: sc for pid, sc in scores.items()}, "horizon_start": (today + timedelta(days=1)).isoformat()}
    log.info("forecast: %s", summary)
    return summary
