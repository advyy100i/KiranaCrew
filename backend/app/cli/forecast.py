"""python -m app.cli.forecast [--shop-id 1]  — runs the nightly forecast job now and prints the backtest table."""
import argparse
import json

from app.db import get_engine
from app.forecast.demand import run_forecast

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--shop-id", type=int, default=1)
    a = ap.parse_args()
    out = run_forecast(get_engine(), a.shop_id)
    print(json.dumps({k: v for k, v in out.items() if k != "scores"}, indent=1))
    for pid, sc in sorted(out.get("scores", {}).items()):
        winner = "AutoETS" if sc["AutoETS"] < sc["SeasonalNaive"] else "baseline"
        print(f"product {pid:>3}  AutoETS MASE {sc['AutoETS']:.3f}   SeasonalNaive MASE {sc['SeasonalNaive']:.3f}   -> show {winner}")
