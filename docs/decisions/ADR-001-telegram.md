# ADR-001: Telegram Bot API as the voice transport

**Status:** accepted · **Date:** 2026-09-18

## Decision
Voice notes arrive through the Telegram Bot API (raw HTTPS, no framework), delivered either by webhook through a
temporary cloudflared tunnel or by long polling (`app.cli.poll`). The transport is one adapter
(`app/telegram/client.py`); the pipeline never sees Telegram objects after `claim_message`.

## Why
- Free, no business verification, documented webhook with a `secret_token`, inline keyboards for confirmations,
  direct `.ogg` download. WhatsApp Cloud API needs verification and bills per conversation.
- At-least-once delivery is documented, so idempotency (ADR-003) is designed in from the first insert.
- Two transports with the same handler mean a dead tunnel is a 15-second fix, not a demo failure.

## Consequences
- `FakeTelegram` makes the whole pipeline testable without the network.
- WhatsApp later is a second adapter, not a rewrite.
