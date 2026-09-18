"""Held-out set: written from adversarial / realistic phrasings the shipped dataset never exercised.
Labelled by hand, by reading the sentence, NOT by running the parser. Run only at phase boundaries:
  python -m app.cli.eval --dataset ../eval/heldout.jsonl --catalog ../eval/seed_catalog.json --errors
"""
import json
from collections import Counter

C = lambda ty, cu, pr, q, u, amt=None, src=None, d=None: dict(action="COMMIT", type=ty, customer=cu, product=pr, quantity=q, unit=u, amount_paise=amt, price_source=src, direction=d)  # noqa
A = lambda ty, reason, cands=None: dict(action="ASK", type=ty, ask_reason=reason, **({"candidates": cands} if cands else {}))  # noqa
R = lambda reason="NOT_A_TRANSACTION": dict(action="REJECT", reject_reason=reason)  # noqa
RK, RS, KK = "Ramesh Kumar", "Ramesh Sharma", "Rakesh Kumar"
rows = [
 # --- false-update traps (the metric to defend) ---
 ("HO01", "trap_name", "Ramesh Kumari ne 5 kilo chawal udhaar pe liya", A("CREDIT_SALE", "AMBIGUOUS_CUSTOMER", [RK, RS, KK])),
 ("HO02", "trap_name", "Rameshwar ne 5 kilo chawal udhaar pe liya", A("CREDIT_SALE", "UNKNOWN_CUSTOMER")),
 ("HO03", "trap_name", "Suresh Kumar ne 5 kilo chawal udhaar pe liya", A("CREDIT_SALE", "AMBIGUOUS_CUSTOMER", [RK, KK, "Suresh Yadav"])),
 ("HO04", "trap_name", "Sunil ne 5 kilo chawal udhaar liya", A("CREDIT_SALE", "AMBIGUOUS_CUSTOMER", ["Sunita Devi"])),
 ("HO05", "trap_name", "Raamesh Kumaar ne 2 kilo cheeni udhaar liya", C("CREDIT_SALE", RK, "Sugar", 2, "kg", 9000, "CATALOG")),
 ("HO06", "trap_name", "Sunitha Devi ne 1 litre doodh udhaar liya", C("CREDIT_SALE", "Sunita Devi", "Milk", 1, "litre", 6000, "CATALOG")),
 ("HO07", "trap_sign", "Ramesh Kumar ko 500 rupaye udhaar diye", R("UNSUPPORTED")),
 ("HO08", "trap_sign", "Ramesh Kumar ko 500 rupaye diye", R("UNSUPPORTED")),
 ("HO09", "trap_sign", "Ramesh Kumar ne 500 rupaye diye", C("CREDIT_REPAYMENT", RK, None, None, None, 50000)),
 ("HO10", "trap_repay", "Ramesh Kumar ne 5 kilo chawal ka udhaar chukaya 250 rupaye", C("CREDIT_REPAYMENT", RK, None, None, None, 25000)),
 ("HO11", "trap_two", "Ramesh Kumar ne 2 kilo chawal udhaar liya aur 100 rupaye chukaye", R("MULTIPLE_ITEMS")),
 ("HO12", "trap_two", "Ramesh Kumar ne 5 kilo chawal udhaar liya aur 200 rupaye cash diye", R("MULTIPLE_ITEMS")),
 ("HO13", "trap_two", "Rakesh Kumar ne 3 kilo chawal udhaar liya aur Ramesh ne 2 kilo", R("MULTIPLE_ITEMS")),
 ("HO14", "trap_two", "Ramesh Kumar ne 2 kilo chawal udhaar liya 2 kilo cheeni bhi", R("MULTIPLE_ITEMS")),
 ("HO15", "trap_price", "Anil ne 2 kilo chawal udhaar liya 55 ke bhav se", C("CREDIT_SALE", "Anil Gupta", "Rice", 2, "kg", 11000, "EXPLICIT_UNIT")),
 ("HO16", "trap_price", "Anil ne 2 kilo chawal udhaar liya 55 rupaye ke rate se", C("CREDIT_SALE", "Anil Gupta", "Rice", 2, "kg", 11000, "EXPLICIT_UNIT")),
 ("HO17", "trap_numbers", "Ramesh Kumar ne 5 5 5 kilo chawal udhaar liya", A("CREDIT_SALE", "MISSING_QUANTITY")),
 ("HO18", "trap_numbers", "Ramesh Kumar ne 5 kilo chawal udhaar liya 10 lakh rupaye", A("CREDIT_SALE", "CONFIRM_LARGE_AMOUNT")),
 ("HO19", "trap_numbers", "Ramesh Kumar ne 0 kilo chawal udhaar liya", A("CREDIT_SALE", "MISSING_QUANTITY")),
 ("HO20", "trap_numbers", "Ramesh Kumar ne 1,000 rupaye chukaye", C("CREDIT_REPAYMENT", RK, None, None, None, 100000)),
 ("HO21", "trap_unit", "Mohan ne 2 kg sabun udhaar liya", A("CREDIT_SALE", "UNIT_MISMATCH")),
 # --- hindi numbers the shipped set barely covered ---
 ("HO22", "numbers", "Ramesh Kumar ne ek sau bees rupaye diye", C("CREDIT_REPAYMENT", RK, None, None, None, 12000)),
 ("HO23", "numbers", "Sunita Devi ne teen sau pachas rupaye jama kiye", C("CREDIT_REPAYMENT", "Sunita Devi", None, None, None, 35000)),
 ("HO24", "numbers", "Mohan Lal ne ek hazaar do sau pachas rupaye chukaye", C("CREDIT_REPAYMENT", "Mohan Lal", None, None, None, 125000)),
 ("HO25", "numbers", "Pooja ne sawa kilo cheeni udhaar li", C("CREDIT_SALE", "Pooja Verma", "Sugar", 1.25, "kg", 5625, "CATALOG")),
 ("HO26", "numbers", "Anil Gupta ne sawa sau rupaye diye", C("CREDIT_REPAYMENT", "Anil Gupta", None, None, None, 12500)),
 ("HO27", "numbers", "Suresh ne 2 kilo 500 gram atta udhaar liya", C("CREDIT_SALE", "Suresh Yadav", "Atta", 2.5, "kg", 10000, "CATALOG")),
 ("HO28", "numbers", "Pooja ne aadha darjan ande udhaar liye", C("CREDIT_SALE", "Pooja Verma", "Eggs", 6, "piece", 4200, "CATALOG")),
 ("HO29", "numbers", "Ramesh Kumar ne paune do sau rupaye chukaye", C("CREDIT_REPAYMENT", RK, None, None, None, 17500)),
 ("HO30", "numbers", "Rakesh ne do sau rupaye de diye", C("CREDIT_REPAYMENT", KK, None, None, None, 20000)),
 # --- word order / phrasing ---
 ("HO31", "order", "5 kilo chawal Ramesh Kumar ko udhaar diya", C("CREDIT_SALE", RK, "Rice", 5, "kg", 25000, "CATALOG")),
 ("HO32", "order", "namak 3 packet Sunita ko udhaar", C("CREDIT_SALE", "Sunita Devi", "Salt", 3, "packet", 7500, "CATALOG")),
 ("HO33", "order", "Ramesh Kumar ne 5 kilo chawal", A("SALE", "CHOOSE_PAYMENT")),
 ("HO34", "order", "Ramesh Kumar ne 5 kilo chawal udhaar pe liya aur chala gaya", C("CREDIT_SALE", RK, "Rice", 5, "kg", 25000, "CATALOG")),
 ("HO35", "order", "Ramesh Kumar ne paanch kilo chawal udhaar pe liya tha kal", C("CREDIT_SALE", RK, "Rice", 5, "kg", 25000, "CATALOG")),
 ("HO36", "order", "Ramesh Kumar ne 5 kilo chawal liye udhaar mein", C("CREDIT_SALE", RK, "Rice", 5, "kg", 25000, "CATALOG")),
 ("HO37", "order", "Ramesh Kumar ne apna udhaar 200 rupaye chukaya", C("CREDIT_REPAYMENT", RK, None, None, None, 20000)),
 ("HO38", "order", "Ramesh Kumar ne 200 rupaye wapas kiye", C("CREDIT_REPAYMENT", RK, None, None, None, 20000)),
 ("HO39", "order", "Sharma traders se 50 kilo chawal aaya", C("INVENTORY_PURCHASE", None, "Rice", 50, "kg")),
 ("HO40", "order", "50 kilo chawal aaya 2000 rupaye ka", C("INVENTORY_PURCHASE", None, "Rice", 50, "kg", 200000, "EXPLICIT_TOTAL")),
 ("HO41", "order", "Rakesh ne 2 kilo chawal liya paise baad mein dega", A("SALE", "CHOOSE_PAYMENT")),
 ("HO42", "order", "Ramesh Kumar ne 5 kilo cheeni cash mein liya 200 rupaye diye", C("CASH_SALE", RK, "Sugar", 5, "kg", 20000, "EXPLICIT_TOTAL")),
 ("HO43", "order", "Suresh ne 1 litre tel udhaar liya", A("CREDIT_SALE", "AMBIGUOUS_PRODUCT", ["Mustard Oil", "Refined Oil"])),
 ("HO44", "order", "Suresh ne 1 litre oil udhaar liya", A("CREDIT_SALE", "UNKNOWN_PRODUCT")),
 ("HO45", "order", "Ramesh Kumar ne udhaar chukaya", A("CREDIT_REPAYMENT", "MISSING_AMOUNT")),
 ("HO46", "order", "Ramesh Kumar ne 5 kg chaawal udhar liya", C("CREDIT_SALE", RK, "Rice", 5, "kg", 25000, "CATALOG")),
 ("HO47", "order", "Mohan Lal ne 3 saboon udhaar liye", C("CREDIT_SALE", "Mohan Lal", "Soap", 3, "piece", 10500, "CATALOG")),
 ("HO48", "malformed", "Sunita Devi ka hisaab kitna hai", R()),
 ("HO49", "malformed", "namaste bhai kaise ho", R()),
 ("HO50", "malformed", "chawal khatam ho gaya", R()),
 ("HO51", "devanagari", "रमेश कुमार ने एक सौ बीस रुपये दिए", C("CREDIT_REPAYMENT", RK, None, None, None, 12000)),
 ("HO52", "devanagari", "सुनीता देवी ने सवा किलो चीनी उधार ली", C("CREDIT_SALE", "Sunita Devi", "Sugar", 1.25, "kg", 5625, "CATALOG")),
 ("HO53", "trap_two", "Ramesh ne 2 kilo chawal aur Rakesh ne 1 kilo cheeni udhaar liya", R("MULTIPLE_ITEMS")),
 ("HO54", "trap_price", "Ramesh Kumar ne 2 kilo chawal 55 rupaye kilo udhaar liya", C("CREDIT_SALE", RK, "Rice", 2, "kg", 11000, "EXPLICIT_UNIT")),
 ("HO55", "trap_sign", "Ramesh Kumar ko 200 rupaye wapas diye", R("UNSUPPORTED")),
]
with open("heldout.jsonl", "w", encoding="utf-8") as f:
    for id_, cat, text, exp in rows:
        exp = {k: v for k, v in exp.items() if not (v is None and k in {"price_source", "direction"})}
        f.write(json.dumps({"id": id_, "category": cat, "text": text, "expected": exp}, ensure_ascii=False) + "\n")
print(len(rows), "rows", Counter(r[1] for r in rows))
