# ADR-005: Agents live outside bookkeeping

**Status:** accepted · **Date:** 2026-09-18

## Decision
CrewAI (or the plain three-role fallback in `app/crew/insights.py`) writes one weekly advisory note. Code computes
every number (`build_facts`); agents only rank, explain and phrase; output lands in `insights` with
`approved=false`; a human approves before it is sent. Hard caps: one run per shop-week, six LLM calls.
Agents are not used to parse voice notes, orchestrate the pipeline, or decide a price, customer or balance.

## Why
Bookkeeping has one correct answer per sentence; a deterministic path plus one validated, grounded LLM call already
produces it, and agents would add hops, latency and non-determinism to money handling for no capability gain.
The weekly note is open-ended synthesis over facts — the one task where agents genuinely help.

## Consequences
`insights.facts` records exactly what the agents were given, so any claim in a note can be checked against it.
Deleting the `insights` and `forecasts` tables cannot break a ledger.
