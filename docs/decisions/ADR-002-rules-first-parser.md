# ADR-002: Rules first, LLM only for the tail, and the LLM never touches money

**Status:** accepted · **Date:** 2026-09-18

## Decision
`normalize()` → `parse_rules()` handles the formulaic majority deterministically. Only when the rules leave the
command incomplete (`ParsedCommand.is_complete()` is false) does the hybrid parser call an LLM. The LLM's contract is
`LLMExtraction`: mentions and copied numbers only — never IDs, never totals. A grounding check nulls any number not
present in the normalized text and any mention that does not fuzzy-match a span. Pricing happens in `money.py`
in integer paise with `ROUND_HALF_UP`.

## Why
- Shop sentences are formulaic; rules are free, instant and auditable. The eval shows what the LLM adds.
- A hallucinated quantity becomes a question (`MISSING_QUANTITY`) instead of a ledger entry.
- `PARSER_MODE=rules` is a network-free mode that still commits most sentences — the demo fallback.

## Consequences
- Every LLM call is logged to `llm_calls` with prompt version; `messages.parser_used` records the split.
- Rules-side safety flags (`MULTIPLE_ITEMS`, `EXTRA_NUMBERS`, `UNSUPPORTED`) apply even on the LLM path.
