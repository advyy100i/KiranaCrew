"""Map free-text mentions to catalog entities. Deterministic; never guesses between close candidates.

Customer policy (in order): exact name/alias -> token subset ("ramesh" in "ramesh kumar") -> phonetic-skeleton
match ("raamesh" == "ramesh", but "kumari" != "kumar") -> otherwise fuzzy candidates are only ever *offered*
(AMBIGUOUS), never auto-accepted. Character-level fuzzy scores cannot tell an STT vowel slip from a different
person, and booking the wrong person is the worst failure in this project.

Product policy: same, except a single fuzzy hit >= 88 with a clear margin is accepted — product vocab is small,
spellings vary wildly in STT output, and the product name is echoed in the confirmation reply with an Undo button.
"""
import re
import unicodedata

from rapidfuzz import fuzz

from .models import Customer, Product, Resolution

ASK_FLOOR = 80             # below this a name is unknown, not "did you mean"
PRODUCT_AUTO_ACCEPT = 88   # fuzzy score for a product token to resolve without asking
PRODUCT_MARGIN = 6
PRODUCT_SUGGEST = 70     # below auto-accept but worth a "did you mean?" button

_SKELETON_RULES = [("ee", "i"), ("oo", "u"), ("aa", "a"), ("ii", "i"), ("uu", "u"), ("ph", "f"), ("sh", "s"),
                   ("ch", "c"), ("th", "t"), ("dh", "d"), ("bh", "b"), ("gh", "g"), ("kh", "k"), ("ck", "k"),
                   ("w", "v"), ("z", "j"), ("q", "k")]


def _n(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s).lower().split())


def skeleton(s: str) -> str:
    """Phonetic skeleton for Latin-script Hindi names: raamesh/ramesh -> rames, sunitha -> sunita. Devanagari untouched."""
    out = []
    for tok in _n(s).split():
        if re.search(r"[ऀ-ॿ]", tok):
            out.append(tok); continue
        t = tok
        for a, b in _SKELETON_RULES:
            t = t.replace(a, b)
        t = re.sub(r"(.)\1+", r"\1", t)
        out.append(t)
    return " ".join(out)


def resolve_customer(mention: str | None, customers: list[Customer]) -> Resolution:
    if not mention:
        return Resolution("MISSING")
    m = _n(mention)
    names = {c.id: [_n(c.name)] + [_n(a) for a in c.aliases] for c in customers}
    exact = [c for c in customers if m in names[c.id]]
    if len(exact) == 1:
        return Resolution("RESOLVED", exact[0])
    mt = set(m.split())
    hits = exact or [c for c in customers if any(mt <= set(n.split()) for n in names[c.id])]
    if not hits:                                      # phonetic skeleton: exact, then token-subset
        sk = skeleton(m); skt = set(sk.split())
        sk_names = {c.id: [skeleton(n) for n in names[c.id]] for c in customers}
        hits = [c for c in customers if sk in sk_names[c.id]]
        hits = hits or [c for c in customers if any(skt <= set(n.split()) for n in sk_names[c.id])]
    if len(hits) == 1:
        return Resolution("RESOLVED", hits[0])
    if len(hits) > 1:
        return Resolution("AMBIGUOUS", candidates=hits)
    scored = sorted(((max(fuzz.WRatio(m, n) for n in names[c.id]), c) for c in customers),
                    key=lambda x: x[0], reverse=True)
    good = [c for s, c in scored if s >= ASK_FLOOR]
    sk_tokens = set(skeleton(m).split())                  # shares a first name or surname => worth offering
    good += [c for c in customers if c not in good
             and any(sk_tokens & set(skeleton(n).split()) for n in names[c.id])]
    if not good:
        return Resolution("UNKNOWN")
    return Resolution("AMBIGUOUS", candidates=good[:4])   # "did you mean ...?" — offered, never assumed


def product_vocab(products: list[Product]) -> set[str]:
    return {tok for p in products for a in p.aliases + [p.name] for tok in _n(a).split()}


def resolve_product(mention: str | None, products: list[Product]) -> Resolution:
    if not mention:
        return Resolution("MISSING")
    toks = _n(mention).split()
    alias_map: dict[str, set[int]] = {}
    sk_map: dict[str, set[int]] = {}
    for p in products:
        for a in p.aliases + [p.name]:
            alias_map.setdefault(_n(a), set()).add(p.id)
            sk_map.setdefault(skeleton(a), set()).add(p.id)
    by_id = {p.id: p for p in products}
    groups, i = [], 0
    while i < len(toks):
        for n in (3, 2, 1):
            phrase = " ".join(toks[i:i + n])
            if len(toks[i:i + n]) == n and (phrase in alias_map or skeleton(phrase) in sk_map):
                groups.append(alias_map.get(phrase) or sk_map[skeleton(phrase)]); i += n; break
        else:
            i += 1
    if not groups:  # fuzzy, single tokens only, for STT spelling noise
        single = [(a, ids) for a, ids in alias_map.items() if " " not in a]
        for t in toks:
            if len(t) < 4:
                continue
            ranked = sorted(((fuzz.ratio(t, a), ids) for a, ids in single), key=lambda x: x[0], reverse=True)
            if ranked and ranked[0][0] >= PRODUCT_AUTO_ACCEPT:
                runner = next((s for s, ids in ranked[1:] if ids != ranked[0][1]), 0)
                if ranked[0][0] - runner >= PRODUCT_MARGIN:
                    groups.append(ranked[0][1])
    distinct = {frozenset(g) for g in groups}
    if not distinct:                                  # offer near misses; picking one teaches the spelling
        near = sorted(((max(fuzz.partial_ratio(t, a) for t in toks if len(t) >= 3) if any(len(t) >= 3 for t in toks) else 0,
                        a, ids) for a, ids in alias_map.items()), key=lambda x: x[0], reverse=True)
        seen: list[int] = []
        for score, a, ids in near:
            if score < PRODUCT_SUGGEST or len(seen) >= 3:
                break
            for i in sorted(ids):
                if i not in seen:
                    seen.append(i)
        return Resolution("UNKNOWN", candidates=[by_id[i] for i in seen])
    if len(distinct) > 1:
        return Resolution("MULTIPLE")
    ids = sorted(next(iter(distinct)))
    if len(ids) == 1:
        return Resolution("RESOLVED", by_id[ids[0]])
    return Resolution("AMBIGUOUS", candidates=[by_id[x] for x in ids])
