from decimal import ROUND_HALF_UP, Decimal

BASE_UNIT_OF = {"kg": "kg", "g": "kg", "litre": "litre", "ml": "litre",
                "piece": "piece", "dozen": "piece", "packet": "packet"}
FACTOR = {"kg": Decimal(1), "g": Decimal("0.001"), "litre": Decimal(1), "ml": Decimal("0.001"),
          "piece": Decimal(1), "dozen": Decimal(12), "packet": Decimal(1)}


def to_base_qty(qty: Decimal, unit: str, base_unit: str) -> Decimal | None:
    if BASE_UNIT_OF.get(unit) != base_unit:
        return None
    return (qty * FACTOR[unit]).quantize(Decimal("0.001"))


def rupees_to_paise(rupees: Decimal) -> int:
    return int((rupees * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def unit_price_to_base_paise(price_paise: int, stated_unit: str, base_unit: str) -> int | None:
    if BASE_UNIT_OF.get(stated_unit) != base_unit:
        return None
    return int((Decimal(price_paise) / FACTOR[stated_unit]).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def line_amount_paise(qty_base: Decimal, unit_price_paise: int) -> int:
    return int((qty_base * unit_price_paise).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt_inr(paise: int) -> str:
    rupees = Decimal(paise) / 100
    return f"₹{rupees:,.2f}".replace(".00", "")
