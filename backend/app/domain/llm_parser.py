"""LLM fallback parser: prompt, strict validation, and the grounding check.

The LLM is a *mention extractor*. Every number it returns must already appear in the normalized text and every
mention must fuzzy-match a span of it; anything else is set to None so a hallucination becomes a question,
never a ledger entry. Pure: takes a `complete(system, user) -> str` callable, no network here.
"""
import json
import re
from decimal import Decimal
from typing import Callable

from pydantic import ValidationError
from rapidfuzz import fuzz

from .models import LLMExtraction, ParsedCommand
from .normalize import numbers_in

PROMPT_VERSION = "p2"
SYSTEM = """You extract ONE shop transaction from a Hindi/Hinglish sentence spoken by a kirana shopkeeper.
The text is already normalized: numbers are digits, units are kg|g|litre|ml|piece|packet|dozen, money is "<n> rupaye",
credit is "udhaar", cash is "cash".
Return ONLY a JSON object with exactly these keys:
transaction_type: one of CREDIT_SALE (customer took goods on credit / udhaar), CASH_SALE (goods sold for cash),
  SALE (goods taken, payment unclear), CREDIT_REPAYMENT (customer paid back money: chukaya/jama/wapas/diye),
  INVENTORY_PURCHASE (stock arrived / bought for the shop: aaya/kharida), STOCK_ADJUSTMENT (kharab/expire/toot/chori
  = OUT, extra found = IN), or null if the sentence is not a transaction or contains two transactions.
customer_mention: the person named, copied verbatim, or null. Never a product word.
product_mention: the product words copied verbatim, or null.
quantity: number copied exactly as written, or null.  unit: kg|g|litre|ml|piece|packet|dozen or null.
total_amount_rupees: a number followed by rupaye that is the TOTAL, copied exactly, or null.
unit_price_rupees: a number that is a PER-UNIT price (rupaye kilo / ke bhav / ke rate), or null.
unit_price_unit: unit of that price or null.
adjustment_direction: OUT|IN|null.
Rules: copy numbers exactly as they appear. NEVER calculate a total, price or sum. NEVER invent a name or product.
Use null when unsure. No prose, no markdown, JSON only."""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict:
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _span_match(mention: str | None, norm: str, threshold: int = 80) -> str | None:
    if not mention:
        return None
    m = " ".join(str(mention).lower().split())
    if not m:
        return None
    if m in norm:
        return m
    toks = norm.split(); n = len(m.split())
    best = max((fuzz.ratio(m, " ".join(toks[i:i + k])) for k in {max(1, n - 1), n, n + 1}
                for i in range(0, max(1, len(toks) - k + 1))), default=0)
    return m if best >= threshold else None


def ground(x: LLMExtraction, norm: str) -> tuple[LLMExtraction, list[str]]:
    """Null out any field that is not literally supported by the text. Returns (grounded, dropped_fields)."""
    nums = numbers_in(norm)
    dropped: list[str] = []
    data = x.model_dump()
    for f in ("quantity", "total_amount_rupees", "unit_price_rupees"):
        v = data.get(f)
        if v is not None and Decimal(str(v)) not in nums:
            data[f] = None; dropped.append(f)
    for f in ("customer_mention", "product_mention"):
        if data.get(f) is not None and _span_match(data[f], norm) is None:
            data[f] = None; dropped.append(f)
    toks = set(norm.split())
    for f in ("unit", "unit_price_unit"):
        if data.get(f) is not None and data[f] not in toks:
            data[f] = None; dropped.append(f)
    if data.get("total_amount_rupees") is not None and "rupaye" not in toks:
        data["total_amount_rupees"] = None; dropped.append("total_amount_rupees")
    return LLMExtraction.model_validate(data), dropped


def parse_llm(norm: str, complete: Callable[[str, str], str], retries: int = 1) -> tuple[ParsedCommand, dict]:
    """Ask the model, validate, ground. Raises ValueError when no valid JSON came back after the retry."""
    user = f"Sentence: {norm}\nJSON:"
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        raw = complete(SYSTEM, user if attempt == 0 else user + "\n(Return valid JSON with exactly the listed keys.)")
        try:
            data = _extract_json(raw)
            if not isinstance(data, dict):
                data = {}
            if isinstance(data, dict):
                data = {k: v for k, v in data.items() if k in LLMExtraction.model_fields}
                for k in ("quantity", "total_amount_rupees", "unit_price_rupees"):
                    v = data.get(k)
                    if isinstance(v, str):                       # small models write "5 kg" / "250 rupaye"
                        m = re.search(r"\d+(?:\.\d+)?", v)
                        data[k] = m.group(0) if m else None
                        if k == "quantity" and m and not data.get("unit"):
                            u = re.sub(r"[\d.\s]", "", v)
                            if u in ("kg", "g", "litre", "ml", "piece", "packet", "dozen"):
                                data["unit"] = u
                    elif isinstance(v, (int, float)) and v <= 0:
                        data[k] = None
                for k in ("customer_mention", "product_mention", "unit", "unit_price_unit", "adjustment_direction"):
                    if data.get(k) is not None and not isinstance(data[k], str):
                        data[k] = None
                if data.get("transaction_type") not in LLMExtraction.model_fields["transaction_type"].annotation.__args__[0].__args__:
                    data["transaction_type"] = None
            x = LLMExtraction.model_validate(data)
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            last_err = e
            continue
        grounded, dropped = ground(x, norm)
        return ParsedCommand(**grounded.model_dump()), {"dropped": dropped, "raw": raw[:500], "attempt": attempt}
    raise ValueError(f"LLM returned no valid extraction: {last_err}")
