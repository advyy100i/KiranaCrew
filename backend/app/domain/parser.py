"""Parser policy: rules | llm | hybrid. Hybrid = rules first; the LLM only sees sentences the rules left incomplete.

`providers` is a list of objects with .name/.model/.complete_json(system, user). `log` receives one dict per
LLM call (the pipeline writes them to llm_calls). PARSER_MODE=rules never opens a socket.
"""
from typing import Callable

from .llm_parser import PROMPT_VERSION, parse_llm
from .models import FLAG_EXTRA_NUMBERS, ParsedCommand
from .rules_parser import CASH, CREDIT, parse_rules


def parse(norm: str, product_vocab: set[str], mode: str = "hybrid", providers: list | None = None,
          log: Callable[[dict], None] | None = None) -> tuple[ParsedCommand, str]:
    """Returns (command, parser_used) where parser_used in rules | llm:<provider> | llm_fallback_failed."""
    rules = parse_rules(norm, product_vocab)
    if mode == "rules" or not providers:
        return rules, "rules"
    if mode == "hybrid" and rules.is_complete():
        return rules, "rules"
    for p in providers:
        meta: dict = {}

        def complete(system: str, user: str, _p=p, _meta=meta) -> str:
            r = _p.complete_json(system, user)
            _meta.update(provider=r.provider, model=r.model, latency_ms=r.latency_ms,
                         input_tokens=r.input_tokens, output_tokens=r.output_tokens)
            return r.text
        try:
            cmd, info = parse_llm(norm, complete)
            if log:
                log({**meta, "stage": "PARSE", "prompt_version": PROMPT_VERSION, "ok": True, "error": None,
                     "dropped": info["dropped"]})
            cmd.is_question = rules.is_question
            cmd.flags = sorted(set(cmd.flags) | set(rules.flags))         # rules-side safety flags always apply
            if FLAG_EXTRA_NUMBERS in cmd.flags:                            # half-understood numbers: same policy as rules
                cmd.quantity = cmd.total_amount_rupees = cmd.unit_price_rupees = None
            for f in ("customer_mention", "product_mention"):              # rules mentions are literal spans: safe backfill
                if getattr(cmd, f) is None and getattr(rules, f) is not None:
                    setattr(cmd, f, getattr(rules, f))
            _rules_own_number_roles(cmd, rules)
            _rules_own_type(cmd, rules, norm)
            return cmd, f"llm:{p.name}"
        except Exception as e:                                          # noqa: BLE001 - next provider
            if log:
                log({"provider": getattr(p, "name", "?"), "model": getattr(p, "model", "?"), "stage": "PARSE",
                     "prompt_version": PROMPT_VERSION, "latency_ms": meta.get("latency_ms", 0), "ok": False,
                     "error": str(e)[:500]})
            continue
    return rules, "llm_fallback_failed"


NUM_ROLES = ("quantity", "total_amount_rupees", "unit_price_rupees")


def _rules_own_number_roles(cmd: ParsedCommand, rules: ParsedCommand) -> None:
    """Grounding proves a number exists in the text, not what it means. "55 rupaye kilo" is a unit price; a model that
    files it under total_amount_rupees passes grounding and books Rs 55 instead of 2 x Rs 55. The rules assign roles by
    literal pattern, so when the LLM gives a rules-matched number a different role, the rules' role wins."""
    for role in NUM_ROLES:
        v = getattr(rules, role)
        if v is None:
            continue
        for other in NUM_ROLES:                     # the same number in another slot is a relabel unless rules did it too
            if other != role and getattr(cmd, other) == v and getattr(rules, other) != v:
                setattr(cmd, other, None)
        setattr(cmd, role, v)
        if role == "unit_price_rupees":
            cmd.unit_price_unit = rules.unit_price_unit or cmd.unit_price_unit


def _rules_own_type(cmd: ParsedCommand, rules: ParsedCommand, norm: str) -> None:
    """Type is a keyword decision ("kharide" = bought, "zyada nikla" = found, "udhaar" = credit). When the rules found a
    type, it wins over the LLM's; and the LLM may only assert CREDIT_SALE / CASH_SALE if the keyword is really in the
    text — "paise baad mein dega" is not a payment mode, it is a question for the shopkeeper (SALE -> CHOOSE_PAYMENT)."""
    toks = set(norm.split())
    if rules.transaction_type is not None:
        cmd.transaction_type = rules.transaction_type
        cmd.adjustment_direction = rules.adjustment_direction or cmd.adjustment_direction
        return
    if cmd.transaction_type == "CREDIT_SALE" and not (toks & CREDIT):
        cmd.transaction_type = "SALE"
    elif cmd.transaction_type == "CASH_SALE" and not (toks & CASH):
        cmd.transaction_type = "SALE"
