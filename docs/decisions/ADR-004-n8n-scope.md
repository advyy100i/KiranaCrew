# ADR-004: n8n is read-only

**Status:** accepted · **Date:** 2026-09-18

## Decision
n8n (Docker, `--profile tools`) runs four schedules: daily summary, low-stock watch, failed-message alert, nightly
jobs. It reaches the system only through admin-key HTTP endpoints that read or enqueue (`/internal/*`, `/dev/stats`)
and a `SELECT`-only Postgres role (`kirana_ro`, migration 002). It has no path to `transactions`.

## Why
Scheduled reads and notifications are exactly what a low-code tool is good at, and the boundary is enforced by
credentials, not by discipline. Stop the container and not one transaction is lost or delayed.

## Consequences
Workflow JSON exports live in `workflows/` and are versioned like code. A workflow that "just needs to write once"
is a change to this ADR, not to a node.
