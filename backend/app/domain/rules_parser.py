"""Rule-first parser. Input: normalized text. Output: ParsedCommand with mentions (no DB IDs, no arithmetic).

Design rule: when the sentence carries two of anything (two customers, two quantities, a sale and a repayment)
the parser flags it instead of picking one. Picking silently is how false updates happen.
"""
from decimal import Decimal

from .models import FLAG_EXTRA_NUMBERS, FLAG_MULTIPLE_ITEMS, FLAG_UNSUPPORTED, ParsedCommand
from .normalize import UNITS

NAME_MARKERS = {"ne", "ko", "se"}                      # <name> ne / ko / se
MARKERS = NAME_MARKERS | {"ka", "ke", "ki", "took", "paid", "bought", "gave"}
FILLERS = {"aaj", "abhi", "bhai", "bhaiya", "ji", "shri", "smt", "mr", "umm", "haan", "toh", "to", "kal"}
CREDIT = {"udhaar"}
CASH = {"cash"}
REPAY = {"chuka", "chukaya", "chukaye", "chukai", "jama", "wapas", "paid", "clear", "lautaya", "lautaye"}
ARRIVAL = {"aaya", "aaye", "aayi", "mila", "mile", "mili", "received", "stock", "maal"}
PURCHASE_VERBS = {"kharida", "kharide", "kharidi"}
SALE_VERBS = {"liya", "li", "liye", "le", "diya", "di", "diye", "becha", "bechi", "beche", "sale", "sold", "took", "gave"}
ADJUST_OUT = {"kharab", "toot", "expire", "chori", "sad", "fat", "damaged", "barbaad", "leak"}
FOUND = {"zyada", "extra"}
SHORT = {"kam"}
EMERGE = {"nikla", "nikle", "nikli"}
QUESTION = {"kitna", "kitne", "kya", "kaun", "kab", "how", "what", "?"}
PRICE_WORDS = {"bhav", "rate", "hisaab"}
STOP = {"pe", "mein", "ka", "ke", "ki", "ko", "ne", "se", "aur", "ho", "hua", "hue", "hai", "tha", "gaya", "gayi",
        "gaye", "kiya", "kiye", "hisaab", "rate", "bhav", "per", "prati", "payment", "on", "check", "ginti",
        "aaj", "abhi", "ji", "rupaye", "wala", "wale", "lag", "bhi", "the", "de", "apna", "apne", "kal", "mujhe",
        "humein", "hamein", "unhe", "unko", "usko", "paise", "paisa", "baad", "dega", "degi", "denge", "chala",
        "phir", "fir", "bhi", "umm", "haan", "toh", "to", "ab"}
KEYWORDS = (CREDIT | CASH | REPAY | ARRIVAL | PURCHASE_VERBS | SALE_VERBS | ADJUST_OUT | FOUND | SHORT
            | EMERGE | QUESTION)
SUB_UNIT = {("kg", "g"): Decimal("0.001"), ("litre", "ml"): Decimal("0.001")}


def _num(t: str) -> Decimal | None:
    try:
        d = Decimal(t)
    except Exception:
        return None
    return d if d > 0 else None                        # "0 kilo" is not a quantity; let it be an extra number


def _namey(t: str, product_vocab: set[str]) -> bool:
    return (_num(t) is None and t not in KEYWORDS and t not in UNITS and t not in product_vocab
            and t not in STOP and t not in FILLERS and t != "rupaye" and t not in PRICE_WORDS)


def _customer_span(toks: list[str], product_vocab: set[str]) -> tuple[list[int], str | None, list[int]]:
    """Returns (indices of the customer name, the marker used, indices of any *second* name+marker)."""
    # a) name-like run immediately before the first ne/ko/se marker
    first: list[int] = []
    marker = None
    markers = [i for i, t in enumerate(toks) if t in NAME_MARKERS]
    for mi in markers:
        run = []
        k = mi - 1
        while k >= 0 and (_namey(toks[k], product_vocab) or (toks[k] in FILLERS and run)):
            if toks[k] not in FILLERS:
                run.insert(0, k)
            k -= 1
        if run:
            if not first:
                first, marker = run, toks[mi]
            else:
                return first, marker, run + [mi]           # a second "<name> ne" => two actors
    if first:
        return first, marker, []
    # b) leading name-like tokens before the first number / keyword / unit / product word
    span = []
    for i, t in enumerate(toks):
        if t in MARKERS:
            if span:
                marker = t
            break
        if _num(t) is not None or t in KEYWORDS or t in UNITS or t in product_vocab or t in STOP:
            break
        if t in FILLERS:
            continue
        span.append(i)
    return span, marker, []


def parse_rules(norm: str, product_vocab: set[str]) -> ParsedCommand:
    toks = norm.split()
    used: set[int] = set()
    flags: list[str] = []
    s = set(toks)

    # 1) customer mention
    span, marker, second = _customer_span(toks, product_vocab)
    customer = " ".join(toks[k] for k in span) if span else None
    used.update(span)
    if marker and span and span[-1] + 1 < len(toks) and toks[span[-1] + 1] == marker:
        used.add(span[-1] + 1)
    if second:
        flags.append(FLAG_MULTIPLE_ITEMS)

    # 2) unit price:  <n> rupaye [per|prati|ka|ke] <unit>   |   <n> [rupaye] (ke|ka) (bhav|rate|hisaab)
    unit_price, unit_price_unit = None, None
    for i, t in enumerate(toks):
        if i in used or _num(t) is None:
            continue
        j = i + 1
        has_rupaye = j < len(toks) and toks[j] == "rupaye"
        if has_rupaye:
            j += 1
        k = j + (1 if j < len(toks) and toks[j] in {"per", "prati", "ka", "ke"} else 0)
        if has_rupaye and k < len(toks) and toks[k] in UNITS:
            unit_price, unit_price_unit = _num(t), toks[k]; used.update(range(i, k + 1)); break
        if k < len(toks) and k != j and toks[k] in PRICE_WORDS:   # "55 ke bhav se" -> per stated unit
            unit_price, unit_price_unit = _num(t), None; used.update(range(i, k + 1)); break

    # 3) quantity: <n> <unit> [<n> <sub-unit>], else a bare number not followed by rupaye
    qty, unit = None, None
    for i, t in enumerate(toks):
        if i not in used and _num(t) is not None and i + 1 < len(toks) and toks[i + 1] in UNITS:
            qty, unit = _num(t), toks[i + 1]; used.update({i, i + 1})
            if (i + 3 < len(toks) and _num(toks[i + 2]) is not None and (unit, toks[i + 3]) in SUB_UNIT):
                qty += _num(toks[i + 2]) * SUB_UNIT[(unit, toks[i + 3])]      # "2 kg 500 g" = 2.5 kg
                used.update({i + 2, i + 3})
            break
    if qty is None:
        for i, t in enumerate(toks):
            if i not in used and _num(t) is not None and (i + 1 >= len(toks) or toks[i + 1] != "rupaye"):
                qty = _num(t); used.add(i)
                unit = next((x for j, x in enumerate(toks) if j not in used and x in UNITS), None)
                break

    # 4) total amount: <n> rupaye
    total = None
    for i, t in enumerate(toks):
        if i not in used and _num(t) is not None and i + 1 < len(toks) and toks[i + 1] == "rupaye":
            total = _num(t); used.update({i, i + 1}); break

    # 5) anything numeric left over means the sentence said more than we understood
    extra = [t for i, t in enumerate(toks) if i not in used and _num(t) is not None]
    zero = [t for t in toks if t.replace(".", "").isdigit() and Decimal(t) == 0]
    if extra or zero:
        flags.append(FLAG_EXTRA_NUMBERS)
    second_qty = [i for i, t in enumerate(toks)
                  if i not in used and _num(t) is not None and i + 1 < len(toks) and toks[i + 1] in UNITS]
    if second_qty:
        flags.append(FLAG_MULTIPLE_ITEMS)

    leftover = [t for i, t in enumerate(toks)
                if i not in used and t not in STOP and t not in KEYWORDS and t not in UNITS and _num(t) is None
                and t not in FILLERS]
    has_product_word = any(t in product_vocab for t in leftover)
    money_only = total is not None and qty is None and not has_product_word

    # 6) classify (order matters)
    ttype, direction = None, None
    if s & QUESTION:
        return ParsedCommand(is_question=True, customer_mention=customer)
    if s & REPAY and (s & SALE_VERBS) and qty is not None and has_product_word:
        flags.append(FLAG_MULTIPLE_ITEMS)                  # "...udhaar liya aur 100 rupaye chukaye"
    if (s & CREDIT) and (s & CASH) and not (s & REPAY) and (qty is not None or has_product_word):
        flags.append(FLAG_MULTIPLE_ITEMS)                  # "...udhaar liya aur 200 cash diye"
    if s & ADJUST_OUT or (s & SHORT and s & EMERGE):
        ttype, direction = "STOCK_ADJUSTMENT", "OUT"
    elif s & FOUND and s & EMERGE:
        ttype, direction = "STOCK_ADJUSTMENT", "IN"
    elif money_only and customer and marker == "ko":
        flags.append(FLAG_UNSUPPORTED)                     # money handed *to* a customer: not a repayment
        ttype = "CREDIT_REPAYMENT"
    elif money_only and (customer or s & REPAY):
        ttype = "CREDIT_REPAYMENT"
    elif (s & REPAY) and qty is None and not has_product_word:
        ttype = "CREDIT_REPAYMENT"                         # "Ramesh ne udhaar chukaya" -> ask amount
    elif (s & REPAY) and not (s & SALE_VERBS) and customer and marker != "ko":
        ttype = "CREDIT_REPAYMENT"                         # "5 kilo chawal ka udhaar chukaya 250 rupaye"
        qty, unit = None, None
    elif (s & ARRIVAL or s & PURCHASE_VERBS) and (customer is None or marker == "se"):
        ttype = "INVENTORY_PURCHASE"; customer = None
    elif s & CREDIT:
        ttype = "CREDIT_SALE"
    elif s & CASH:
        ttype = "CASH_SALE"
    elif s & (SALE_VERBS | PURCHASE_VERBS):
        ttype = "SALE" if customer else "CASH_SALE"
    elif customer and qty is not None and has_product_word:
        ttype = "SALE"                                     # "Ramesh ne 5 kilo chawal" -> ask payment

    if FLAG_EXTRA_NUMBERS in flags:                        # never commit on a half-understood sentence
        qty, total, unit_price = None, None, None

    return ParsedCommand(
        transaction_type=ttype, customer_mention=customer,
        product_mention=" ".join(leftover) or None, quantity=qty, unit=unit,
        total_amount_rupees=total, unit_price_rupees=unit_price, unit_price_unit=unit_price_unit,
        adjustment_direction=direction, flags=sorted(set(flags)),
    )
