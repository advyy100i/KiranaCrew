"""python -m app.cli.insights [--shop-id 1] [--week-start YYYY-MM-DD] [--force]   generate (unapproved)
   python -m app.cli.insights --approve <id>                                      human approval -> send"""
import argparse
from datetime import date

from app.crew.insights import approve_and_send, run_insights
from app.runtime import get_runtime

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shop-id", type=int, default=1); ap.add_argument("--week-start", default=None)
    ap.add_argument("--force", action="store_true"); ap.add_argument("--approve", type=int, default=None)
    a = ap.parse_args()
    rt = get_runtime()
    if a.approve:
        print(approve_and_send(rt, a.approve))
    else:
        ws = date.fromisoformat(a.week_start) if a.week_start else None
        out = run_insights(rt, a.shop_id, ws, a.force)
        print(out.get("body") or out)
        if "insight_id" in out and "body" in out:
            print(f"\n[insight #{out['insight_id']} saved, NOT sent. Approve with: python -m app.cli.insights --approve {out['insight_id']}]")
