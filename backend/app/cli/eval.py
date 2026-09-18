"""Offline evaluation.
  python -m app.cli.eval --dataset ../eval/dataset.jsonl --catalog ../eval/seed_catalog.json [--parser rules|llm|hybrid]
                         [--provider ollama|groq|gemini] [--model qwen2.5:3b] [--errors] [--json out.json]
Metrics are defined in the plan (§11). The false-update rate is the number to defend."""
import argparse
import json
import socket
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal

from app.domain.decide import decide
from app.domain.models import Customer, Product
from app.domain.normalize import normalize
from app.domain.parser import parse
from app.domain.resolve import product_vocab

FIELDS = ["type", "customer", "product", "quantity", "unit", "amount_paise", "price_source", "direction"]


def load_catalog(path):
    cat = json.load(open(path, encoding="utf-8"))
    customers = [Customer(i + 1, c["name"], c["aliases"]) for i, c in enumerate(cat["customers"])]
    products = [Product(i + 1, p["name"], p["base_unit"], p["selling_price_paise"], p["aliases"])
                for i, p in enumerate(cat["products"])]
    return customers, products


def predict(text, customers, products, parser="rules", providers=None, calls=None):
    norm = normalize(text)
    t0 = time.perf_counter()
    parsed, used = parse(norm, product_vocab(products), parser, providers or [], (calls.append if calls is not None else None))
    d = decide(parsed, customers, products)
    return {
        "action": d.action, "reason": d.reason, "type": d.type,
        "customer": d.customer.name if d.customer else None,
        "product": d.product.name if d.product else None,
        "quantity": float(d.quantity_base) if d.quantity_base is not None else None,
        "unit": d.unit, "amount_paise": d.amount_paise, "price_source": d.price_source,
        "direction": d.direction, "candidates": sorted(d.candidates), "normalized": norm, "parser_used": used,
        "parsed": {k: v for k, v in parsed.model_dump(mode="json").items() if v not in (None, False, [])},
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


def same(field, gold, pred):
    if field == "quantity" and gold is not None and pred is not None:
        return abs(Decimal(str(gold)) - Decimal(str(pred))) < Decimal("0.001")
    return gold == pred


def evaluate(rows, customers, products, parser, providers=None):
    m = Counter(); per_field = defaultdict(lambda: [0, 0]); per_cat = defaultdict(lambda: [0, 0]); errors = []
    used = Counter(); latencies = []
    for r in rows:
        calls = []
        g, p = r["expected"], predict(r["text"], customers, products, parser, providers, calls)
        used[p["parser_used"]] += 1; latencies.append(p["latency_ms"])
        m["n"] += 1
        m[f"gold_{g['action']}"] += 1; m[f"pred_{p['action']}"] += 1
        if "type" in g:
            m["type_total"] += 1; m["type_correct"] += same("type", g["type"], p["type"])
        if g["action"] == "COMMIT":
            for f in FIELDS:
                if f in g:
                    per_field[f][1] += 1; per_field[f][0] += same(f, g[f], p[f])
        action_ok = g["action"] == p["action"]
        if g["action"] == "COMMIT":
            e2e = action_ok and all(same(f, g[f], p[f]) for f in FIELDS if f in g)
        elif g["action"] == "ASK":
            e2e = action_ok and p["reason"] == g["ask_reason"] and \
                  (not g.get("candidates") or sorted(g["candidates"]) == p["candidates"])
        else:
            e2e = action_ok and p["reason"] == g["reject_reason"]
        m["e2e"] += e2e
        per_cat[r["category"]][1] += 1; per_cat[r["category"]][0] += e2e
        if p["action"] == "ASK":
            m["asked"] += 1; m["asked_and_gold_ask"] += g["action"] == "ASK"
        if p["action"] == "COMMIT" and not e2e:
            m["false_updates"] += 1
        if not e2e:
            errors.append({"id": r["id"], "text": r["text"], "gold": g, "pred": p, "llm_calls": calls})
    latencies.sort()
    m["p50_ms"] = latencies[len(latencies) // 2] if latencies else 0
    m["p95_ms"] = latencies[int(len(latencies) * 0.95)] if latencies else 0
    return m, per_field, per_cat, errors, used


def report(m, per_field, per_cat, used, out=sys.stdout):
    pct = lambda a, b: f"{100 * a / b:.1f}% ({a}/{b})" if b else "n/a"
    w = lambda s: print(s, file=out)
    w(f"examples: {m['n']}   parser split: {dict(used)}   latency p50/p95: {m['p50_ms']}/{m['p95_ms']} ms")
    w(f"end-to-end accuracy:        {pct(m['e2e'], m['n'])}")
    w(f"type classification:        {pct(m['type_correct'], m['type_total'])}")
    total_c = sum(v[0] for v in per_field.values()); total_t = sum(v[1] for v in per_field.values())
    w(f"field-level (COMMIT rows):  {pct(total_c, total_t)}")
    for f, (c, t) in per_field.items():
        w(f"   {f:<13} {pct(c, t)}")
    w(f"confirmation rate:          {pct(m['asked'], m['n'])}")
    w(f"ask precision:              {pct(m['asked_and_gold_ask'], m['asked'])}")
    w(f"ask recall:                 {pct(m['asked_and_gold_ask'], m['gold_ASK'])}")
    w(f"FALSE UPDATE RATE:          {pct(m['false_updates'], m['n'])}")
    w("per category:")
    for c, (ok, t) in sorted(per_cat.items()):
        w(f"   {c:<20} {pct(ok, t)}")


class _NoSocket:
    """PARSER_MODE=rules must never open a socket; this makes any attempt raise loudly."""
    def __enter__(self):
        self._orig = socket.socket.connect
        def boom(*a, **k): raise RuntimeError("network access during rules-only eval")
        socket.socket.connect = boom
        return self

    def __exit__(self, *a):
        socket.socket.connect = self._orig


def build_providers(name: str | None, model: str | None):
    from app.config import settings
    from app.llm.factory import build_llm_chain
    if name:
        settings.llm_providers = name
        if model:
            if name == "ollama": settings.ollama_model = model
            elif name == "groq": settings.groq_llm_model = model
            elif name == "gemini": settings.gemini_model = model
    return build_llm_chain(settings)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True); ap.add_argument("--catalog", required=True)
    ap.add_argument("--parser", default="rules", choices=["rules", "llm", "hybrid"])
    ap.add_argument("--provider", default=None); ap.add_argument("--model", default=None)
    ap.add_argument("--errors", action="store_true"); ap.add_argument("--json", default=None)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.dataset, encoding="utf-8")]
    customers, products = load_catalog(a.catalog)
    if a.parser == "rules":
        with _NoSocket():
            m, pf, pc, errs, used = evaluate(rows, customers, products, "rules")
    else:
        providers = build_providers(a.provider, a.model)
        if not providers:
            raise SystemExit("no LLM provider configured (check LLM_PROVIDERS / keys / ollama)")
        m, pf, pc, errs, used = evaluate(rows, customers, products, a.parser, providers)
    report(m, pf, pc, used)
    if a.errors:
        for e in errs:
            print(json.dumps(e, ensure_ascii=False, default=str))
    if a.json:
        json.dump({"dataset": a.dataset, "n": m["n"], "parser": a.parser, "provider": a.provider, "model": a.model,
                   "metrics": dict(m), "per_field": dict(pf), "per_category": dict(pc), "parser_split": dict(used),
                   "errors": errs, "date": time.strftime("%Y-%m-%d")}, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1, default=str)
