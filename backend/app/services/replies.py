"""Reply text + inline keyboards. Pure functions; nothing here touches the DB or Telegram.

callback_data layout (<= 64 bytes): pa:<uuid-hex>:<index>  answers a pending action;  un:<txn_id>  undoes.
"""
from decimal import Decimal

from app.domain.models import Decision
from app.domain.money import fmt_inr

TYPE_LABEL = {"CREDIT_SALE": "Udhaar", "CASH_SALE": "Cash sale", "CREDIT_REPAYMENT": "Wapasi",
              "INVENTORY_PURCHASE": "Stock aaya", "STOCK_ADJUSTMENT": "Stock adjust", "REVERSAL": "Undo"}
UNIT_LABEL = {"kg": "kg", "litre": "L", "piece": "pc", "packet": "pkt"}

Button = dict  # {"text": str, "callback_data": str}


def _q(q: Decimal | None, unit: str | None) -> str:
    if q is None:
        return ""
    s = f"{Decimal(q).normalize():f}"
    return f"{s} {UNIT_LABEL.get(unit or '', unit or '')}".strip()


def pa_button(text: str, pending_id: str, index: int) -> Button:
    return {"text": text[:40], "callback_data": f"pa:{pending_id.replace('-', '')}:{index}"}


def undo_button(txn_id: int) -> Button:
    return {"text": "↩️ Undo", "callback_data": f"un:{txn_id}"}


def committed(d: Decision, txn_id: int, balance_paise: int | None, stock: Decimal | None,
              low_threshold: Decimal | None) -> tuple[str, list[list[Button]]]:
    t = d.type
    lines = []
    if t == "CREDIT_SALE":
        lines.append(f"✅ Udhaar: {d.customer.name} — {d.product.name} {_q(d.quantity_base, d.unit)}"
                     f"{' × ' + fmt_inr(d.unit_price_paise) if d.unit_price_paise else ''} = {fmt_inr(d.amount_paise)}")
        if balance_paise is not None:
            lines.append(f"Kul udhaar: {fmt_inr(balance_paise)}")
    elif t == "CASH_SALE":
        who = f"{d.customer.name} — " if d.customer else ""
        lines.append(f"✅ Cash: {who}{d.product.name} {_q(d.quantity_base, d.unit)} = {fmt_inr(d.amount_paise)}")
    elif t == "CREDIT_REPAYMENT":
        lines.append(f"✅ Wapasi: {d.customer.name} ne {fmt_inr(d.amount_paise)} diye")
        if balance_paise is not None:
            lines.append(f"Baaki udhaar: {fmt_inr(balance_paise)}" if balance_paise >= 0
                         else f"Advance: {fmt_inr(-balance_paise)}")
    elif t == "INVENTORY_PURCHASE":
        amt = f" ({fmt_inr(d.amount_paise)})" if d.amount_paise else ""
        lines.append(f"✅ Stock aaya: {d.product.name} +{_q(d.quantity_base, d.unit)}{amt}")
    elif t == "STOCK_ADJUSTMENT":
        sign = "-" if d.direction == "OUT" else "+"
        lines.append(f"✅ Stock adjust: {d.product.name} {sign}{_q(d.quantity_base, d.unit)}")
    if d.product and stock is not None:
        s = f"{Decimal(stock).normalize():f}"
        warn = ""
        if Decimal(stock) < 0:
            warn = " ⚠️ stock negative — koi purchase log karna bhool gaye?"
        elif low_threshold is not None and Decimal(stock) < Decimal(low_threshold):
            warn = " ⚠️ kam hai"
        lines.append(f"Stock: {d.product.name} {s} {UNIT_LABEL.get(d.unit or '', d.unit or '')}{warn}")
    if d.price_source == "CATALOG":
        lines.append("(catalog rate)")
    return "\n".join(lines), [[undo_button(txn_id)]]


def ask(d: Decision, pending_id: str, options: list[dict]) -> tuple[str, list[list[Button]]]:
    """options: [{label, ...}] already stored on the pending row; buttons index into it."""
    r = d.reason
    rows: list[list[Button]] = []
    if r == "AMBIGUOUS_CUSTOMER":
        text = f"Kaunse customer? \"{d.customer_mention}\""
    elif r == "UNKNOWN_CUSTOMER":
        text = f"\"{d.customer_mention}\" naam ka customer nahi mila. Naya customer banayein?"
    elif r == "MISSING_CUSTOMER":
        text = "Customer ka naam bataiye (text ya voice mein)."
    elif r == "AMBIGUOUS_PRODUCT":
        text = f"Kaunsa item? \"{d.product_mention}\""
    elif r == "UNKNOWN_PRODUCT":
        text = f"\"{d.product_mention}\" catalog mein nahi hai. Yeh koi item hai, ya naya item banayein?"
    elif r == "CHOOSE_UNIT":
        text = f"Naya item \"{(d.product_mention or '').title()}\" — kis unit mein bikta hai?"
    elif r == "ENTER_PRICE":
        tail = "is entry ke liye total poochh lenge" if d.type else "bina rate ke save hoga, bolte waqt rate batana hoga"
        text = (f"{(d.product_mention or '').title()} ka rate? (rupaye per {UNIT_LABEL.get(d.unit or '', d.unit)}) "
                f"- ya 'Abhi nahi' dabayein, {tail}.")
    elif r == "ENTER_NAME":
        text = ("Naye customer ka naam likhiye." if d.product_mention == "customer" else
                "Naye item ka naam likhiye (unit aur rate saath mein bhi de sakte hain, jaise: Maggi packet 14).")
    elif r == "MISSING_PRODUCT":
        text = "Kaunsa item? Naam likhiye."
    elif r == "MISSING_QUANTITY":
        text = f"{d.product.name if d.product else 'Item'} kitna? (jaise: 2 kilo / 3 packet)"
    elif r == "UNIT_MISMATCH":
        text = f"{d.product.name} {UNIT_LABEL.get(d.unit or '', d.unit)} mein bikta hai — kitne {UNIT_LABEL.get(d.unit or '', d.unit)}?"
    elif r == "MISSING_AMOUNT":
        text = f"{d.customer.name if d.customer else 'Customer'} ne kitne rupaye diye?"
    elif r == "CHOOSE_PAYMENT":
        text = (f"{d.customer.name if d.customer else ''} — {d.product.name if d.product else ''} "
                f"{_q(d.quantity_base, d.unit)}: udhaar ya cash?")
    elif r == "MISSING_PRICE":
        text = f"{d.product.name} ka rate catalog mein nahi hai. Kya bhav laga? (rupaye per {UNIT_LABEL.get(d.unit or '', d.unit)})"
    elif r == "CONFIRM_LARGE_AMOUNT":
        text = f"⚠️ Badi rakam: {fmt_inr(d.amount_paise)}. {TYPE_LABEL.get(d.type, d.type)} book karein?"
    elif r == "CONFIRM_TRANSCRIPT":
        text = f"Maine suna: \"{d.customer_mention}\" — sahi hai?"
    else:
        text = "Thoda samajh nahi aaya, dobara bataiye."
    row: list[Button] = []
    for i, o in enumerate(options):
        row.append(pa_button(o["label"], pending_id, i))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return text, rows


def rejected(d: Decision, balance_paise: int | None = None) -> str:
    if d.reason == "MULTIPLE_ITEMS":
        return "Ek message mein sirf ek entry — alag alag bhejiye."
    if d.reason == "UNSUPPORTED":
        return "Cash udhaar dena abhi support nahi hai — sirf saamaan ka udhaar aur wapasi."
    if d.customer and balance_paise is not None:
        return f"{d.customer.name} ka udhaar: {fmt_inr(balance_paise)}"
    return ("Samajh nahi aaya. Aise bolein: \"Ramesh ne 5 kilo chawal udhaar liya\" / "
            "\"Sunita ne 200 rupaye diye\" / \"50 kilo chawal aaya\". /help dekhein.")


def failed(kind: str) -> str:
    return {"stt": "Audio samajh nahi aaya. Dobara bolein ya text likhein.",
            "too_long": "Ek message mein ek entry, 60 second se kam.",
            "too_short": "Voice note bahut chhota hai.",
            "too_big": "Audio file bahut badi hai.",
            }.get(kind, "Kuch gadbad ho gayi. Thodi der baad dobara try karein.")


def undone(txn_id: int) -> str:
    return f"↩️ Entry #{txn_id} undo ho gayi."


HELP = ("KiranaCrew — voice ya text mein bolein:\n"
        "• Udhaar: \"Ramesh ne 5 kilo chawal udhaar liya\"\n"
        "• Cash: \"2 kilo cheeni cash mein bechi\"\n"
        "• Wapasi: \"Sunita ne 200 rupaye diye\"\n"
        "• Stock aaya: \"50 kilo chawal aaya\"\n"
        "• Kharab: \"2 packet biscuit expire ho gaye\"\n"
        "Naya item ya naya customer: bas bol dein — bot poochh lega.\n"
        "Commands: /balance /stock /last /products /customers /dashboard\n"
        "Catalog: /add_product Maggi packet 14 · /add_customer Kavita · /alias Rice chaval · /price Rice 52 · "
        "/remove_product Maggi")

