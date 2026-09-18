"""Job worker: N threads claim jobs with FOR UPDATE SKIP LOCKED; a reaper thread recovers stale work every 60 s.
   python -m app.cli.worker [--concurrency 2] [--once]"""
import argparse
import logging
import threading
import time

from sqlalchemy import text

from app.runtime import get_runtime
from app.services import jobs
from app.services import replies
from app.services.pipeline import Outcome, Runtime, Skip, deliver, process_message

logging.getLogger("httpx").setLevel(logging.WARNING)   # never log the bot token
log = logging.getLogger("worker")


def handle(rt: Runtime, job: dict) -> None:
    kind, payload = job["kind"], job["payload"]
    if kind == "PROCESS_MESSAGE":
        process_message(rt, int(payload["message_id"]))
    elif kind == "TEST_FAIL":
        raise RuntimeError("poisoned job (test)")
    elif kind == "DAILY_SUMMARY":
        from app.api.internal import daily_summary  # noqa: F401 - n8n calls the endpoint; kept for parity
        log.info("DAILY_SUMMARY jobs are handled by n8n via /internal/daily-summary")
    elif kind == "FORECAST":
        from app.forecast.demand import run_forecast
        run_forecast(rt.engine, int(payload.get("shop_id", 1)))
    elif kind == "INSIGHTS":
        from app.crew.insights import run_insights
        run_insights(rt, int(payload.get("shop_id", 1)))
    else:
        raise RuntimeError(f"unknown job kind {kind}")


def run_one(rt: Runtime) -> bool:
    """Claim and run a single job. Returns False when the queue is empty."""
    with rt.engine.begin() as conn:
        job = jobs.claim(conn)
    if not job:
        return False
    try:
        handle(rt, job)
        with rt.engine.begin() as conn:
            jobs.done(conn, job["id"])
    except Skip as e:
        log.info("job %s skipped: %s", job["id"], e)
        with rt.engine.begin() as conn:
            jobs.done(conn, job["id"])
    except Exception as e:                                        # noqa: BLE001
        log.exception("job %s failed (attempt %s)", job["id"], job["attempts"])
        with rt.engine.begin() as conn:
            dead = jobs.fail(conn, job, f"{type(e).__name__}: {e}")
            if dead and job["kind"] == "PROCESS_MESSAGE":
                mid = int(job["payload"]["message_id"])
                conn.execute(text("UPDATE messages SET status='FAILED', error=:e, updated_at=now() WHERE id=:m "
                                  "AND status IN ('RECEIVED','PROCESSING')"), {"e": str(e)[:500], "m": mid})
                chat = conn.execute(text("SELECT telegram_chat_id FROM messages WHERE id=:m"), {"m": mid}).scalar()
        if dead and job["kind"] == "PROCESS_MESSAGE" and chat:
            deliver(rt, Outcome(mid, "FAILED", replies.failed("stt" if "stt" in str(e).lower() else "error"), chat_id=chat))
    return True


def loop(rt: Runtime, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            if not run_one(rt):
                stop.wait(1.0)
        except Exception:                                         # noqa: BLE001 - DB hiccup: back off, keep going
            log.exception("worker loop error")
            stop.wait(2.0)


def reaper(rt: Runtime, stop: threading.Event, every: float = 60.0) -> None:
    while not stop.is_set():
        try:
            with rt.engine.begin() as conn:
                jobs.heartbeat(conn)
                r = jobs.reap(conn)
                if any(r.values()):
                    log.info("reaper: %s", r)
        except Exception:                                         # noqa: BLE001
            log.exception("reaper error")
        stop.wait(every)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--once", action="store_true", help="drain the queue and exit")
    ap.add_argument("--reap-every", type=float, default=60.0)
    a = ap.parse_args()
    rt = get_runtime()
    n = a.concurrency or rt.settings.worker_concurrency
    with rt.engine.begin() as conn:
        jobs.heartbeat(conn)
    if getattr(rt.stt, "providers", None):
        p = rt.stt.providers[0]
        if hasattr(p, "load"):
            log.info("loading STT model %s ...", getattr(p, "model", ""))
            t0 = time.time()
            try:
                p.load(); log.info("STT model loaded in %.1fs", time.time() - t0)
            except Exception as e:                                # noqa: BLE001
                log.warning("STT model not loaded (%s); will fall back at request time", e)
    if a.once:
        while run_one(rt):
            pass
        return
    stop = threading.Event()
    threads = [threading.Thread(target=loop, args=(rt, stop), daemon=True, name=f"worker-{i}") for i in range(n)]
    threads.append(threading.Thread(target=reaper, args=(rt, stop, a.reap_every), daemon=True, name="reaper"))
    for t in threads:
        t.start()
    log.info("worker up: %d threads, stt=%s, parser=%s", n, rt.settings.stt_provider, rt.settings.parser_mode)
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
