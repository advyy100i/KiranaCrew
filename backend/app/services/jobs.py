"""Durable work queue on one Postgres table. At-least-once, FOR UPDATE SKIP LOCKED, exponential backoff, dead-letter."""
import json
import socket
import os
from datetime import timedelta

from sqlalchemy import text

BACKOFF_BASE_SECONDS = 20
STALE_RUNNING = timedelta(minutes=5)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def enqueue(conn, kind: str, payload: dict, dedupe_key: str | None = None, max_attempts: int = 5,
            delay_seconds: int = 0) -> int | None:
    """Returns the job id, or None when dedupe_key already exists (enqueued at most once)."""
    return conn.execute(text("""
        INSERT INTO jobs (kind, payload, dedupe_key, max_attempts, run_after)
        VALUES (:k, CAST(:p AS jsonb), :d, :ma, now() + (:delay * interval '1 second'))
        ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"""),
        {"k": kind, "p": json.dumps(payload), "d": dedupe_key, "ma": max_attempts, "delay": delay_seconds}).scalar()


def claim(conn) -> dict | None:
    """One worker slot, one job. Other workers skip the locked row instead of waiting."""
    row = conn.execute(text("""
        UPDATE jobs SET status='RUNNING', attempts=attempts+1, locked_at=now(), updated_at=now()
        WHERE id = (SELECT id FROM jobs
                    WHERE status='QUEUED' AND run_after <= now()
                    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
        RETURNING id, kind, payload, attempts, max_attempts""")).mappings().first()
    return dict(row) if row else None


def done(conn, job_id: int) -> None:
    conn.execute(text("UPDATE jobs SET status='DONE', updated_at=now(), locked_at=NULL WHERE id=:id"), {"id": job_id})


def fail(conn, job: dict, err: str) -> bool:
    """Exponential backoff (20s * 2^attempts), then DEAD. Returns True when the job is dead."""
    dead = job["attempts"] >= job["max_attempts"]
    conn.execute(text("""UPDATE jobs SET status=:st, last_error=:e, updated_at=now(), locked_at=NULL,
                         run_after = now() + (:secs * interval '1 second')
                       WHERE id=:id"""),
                 {"st": "DEAD" if dead else "QUEUED", "e": err[:2000],
                  "secs": BACKOFF_BASE_SECONDS * (2 ** job["attempts"]), "id": job["id"]})
    return dead


def reap(conn, stale: timedelta = STALE_RUNNING) -> dict:
    """Recover from killed workers: RUNNING jobs older than `stale` go back to QUEUED, and any message
    stuck in RECEIVED/PROCESSING without a live job is re-enqueued."""
    requeued = conn.execute(text("""
        UPDATE jobs SET status='QUEUED', updated_at=now(), locked_at=NULL, last_error='reaped: stale RUNNING'
        WHERE status='RUNNING' AND locked_at < now() - (:secs * interval '1 second') RETURNING id"""),
        {"secs": int(stale.total_seconds())}).rowcount
    stuck = conn.execute(text("""
        SELECT m.id FROM messages m
        WHERE m.status IN ('RECEIVED','PROCESSING') AND m.updated_at < now() - (:secs * interval '1 second')
          AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.dedupe_key = 'msg:' || m.id AND j.status IN ('QUEUED','RUNNING'))
        """), {"secs": int(stale.total_seconds())}).scalars().all()
    re_enqueued = 0
    for mid in stuck:
        dead = conn.execute(text("SELECT 1 FROM jobs WHERE dedupe_key = :k AND status='DEAD'"), {"k": f"msg:{mid}"}).scalar()
        if dead:
            conn.execute(text("UPDATE messages SET status='FAILED', error=COALESCE(error,'job dead'), updated_at=now() WHERE id=:m"),
                         {"m": mid})
            continue
        conn.execute(text("UPDATE jobs SET status='QUEUED', updated_at=now(), locked_at=NULL, run_after=now() WHERE dedupe_key=:k"),
                     {"k": f"msg:{mid}"})
        re_enqueued += 1
    expired = conn.execute(text("""UPDATE pending_actions SET status='EXPIRED', resolved_at=now()
                                   WHERE status='PENDING' AND expires_at < now() RETURNING message_id""")).scalars().all()
    for mid in expired:
        conn.execute(text("UPDATE messages SET status='REJECTED', error='confirmation expired', updated_at=now() "
                          "WHERE id=:m AND status='AWAITING_CONFIRMATION'"), {"m": mid})
    return {"requeued_jobs": requeued, "re_enqueued_messages": re_enqueued, "expired_pendings": len(expired)}


def heartbeat(conn, worker_id: str = WORKER_ID) -> None:
    conn.execute(text("""INSERT INTO worker_heartbeats (worker_id, seen_at) VALUES (:w, now())
                         ON CONFLICT (worker_id) DO UPDATE SET seen_at=now()"""), {"w": worker_id})


def worker_alive(conn, max_age_seconds: int = 60) -> bool:
    return bool(conn.execute(text("SELECT 1 FROM worker_heartbeats WHERE seen_at > now() - (:s * interval '1 second') LIMIT 1"),
                             {"s": max_age_seconds}).scalar())
