"""Catalog writes: products, customers, aliases, prices. The only place these tables are mutated after seeding.
Every change is audited. Names are stored Title Case; aliases lower-case, de-duplicated."""
from decimal import Decimal

from sqlalchemy import text

from app.domain.money import BASE_UNIT_OF, rupees_to_paise
from app.domain.normalize import UNITS, normalize
from app.services.names import pretty_name
from app.services.pipeline import audit

NOISE = {"rupaye", "per", "prati", "ka", "ke", "ki", "mein", "rate", "price", "unit", "naam", "name", "item", "hai", "ho",
         "at", "for", "of", "and", "aur", "wala", "wale", "pe", "se"}

UNIT_WORDS = {"kg": "kg", "kilo": "kg", "litre": "litre", "liter": "litre", "l": "litre", "piece": "piece", "pc": "piece",
              "packet": "packet", "pkt": "packet", "g": "kg", "ml": "litre", "dozen": "piece"}


def _alias_list(*words: str | None) -> list[str]:
    out: list[str] = []
    for w in words:
        for tok in [w] if w else []:
            a = " ".join(tok.lower().split())
            if a and a not in out:
                out.append(a)
    return out


def add_product(conn, shop_id: int, name: str, base_unit: str, price_paise: int | None, actor: str,
                aliases: list[str] | None = None, low_stock_threshold=None) -> int:
    base_unit = BASE_UNIT_OF.get(UNIT_WORDS.get(base_unit.lower(), base_unit.lower()), None)
    if base_unit is None:
        raise ValueError("unit must be kg | litre | piece | packet")
    name = pretty_name(name)
    pid = conn.execute(text("""
        INSERT INTO products (shop_id, name, aliases, base_unit, selling_price_paise, low_stock_threshold)
        VALUES (:s, :n, :a, :u, :p, :lo)
        ON CONFLICT (shop_id, name) DO UPDATE SET is_active = true, base_unit = EXCLUDED.base_unit,
            selling_price_paise = COALESCE(EXCLUDED.selling_price_paise, products.selling_price_paise),
            aliases = (SELECT ARRAY(SELECT DISTINCT unnest(products.aliases || EXCLUDED.aliases)))
        RETURNING id"""), {"s": shop_id, "n": name, "a": _alias_list(name, *(aliases or [])), "u": base_unit,
                           "p": price_paise or None, "lo": low_stock_threshold}).scalar_one()
    audit(conn, shop_id, actor, "PRODUCT_ADDED", "product", pid, {"name": name, "unit": base_unit, "price_paise": price_paise})
    return pid


def add_customer(conn, shop_id: int, name: str, actor: str, aliases: list[str] | None = None) -> int:
    name = pretty_name(name)
    cid = conn.execute(text("""
        INSERT INTO customers (shop_id, name, aliases) VALUES (:s, :n, :a)
        ON CONFLICT (shop_id, name) DO UPDATE SET is_active = true,
            aliases = (SELECT ARRAY(SELECT DISTINCT unnest(customers.aliases || EXCLUDED.aliases)))
        RETURNING id"""), {"s": shop_id, "n": name, "a": _alias_list(*(aliases or []))}).scalar_one()
    audit(conn, shop_id, actor, "CUSTOMER_ADDED", "customer", cid, {"name": name})
    return cid


def add_alias(conn, shop_id: int, table: str, entity_id: int, alias: str, actor: str) -> bool:
    """Teach a spelling. table is 'products' or 'customers' (fixed allowlist, not user input)."""
    if table not in ("products", "customers"):
        raise ValueError(table)
    a = " ".join(alias.lower().split())
    if not a:
        return False
    n = conn.execute(text(f"""UPDATE {table} SET aliases = array_append(aliases, :a)
                              WHERE shop_id=:s AND id=:i AND NOT (:a = ANY(aliases)) AND lower(name) <> :a"""),
                     {"a": a, "s": shop_id, "i": entity_id}).rowcount
    if n:
        audit(conn, shop_id, actor, "ALIAS_ADDED", table[:-1], entity_id, {"alias": a})
    return bool(n)


def set_price(conn, shop_id: int, product_id: int, price_paise: int | None, actor: str) -> None:
    conn.execute(text("UPDATE products SET selling_price_paise=:p WHERE shop_id=:s AND id=:i"),
                 {"p": price_paise, "s": shop_id, "i": product_id})
    audit(conn, shop_id, actor, "PRICE_SET", "product", product_id, {"price_paise": price_paise})


def deactivate(conn, shop_id: int, table: str, entity_id: int, actor: str) -> None:
    if table not in ("products", "customers"):
        raise ValueError(table)
    conn.execute(text(f"UPDATE {table} SET is_active=false WHERE shop_id=:s AND id=:i"), {"s": shop_id, "i": entity_id})
    audit(conn, shop_id, actor, "DEACTIVATED", table[:-1], entity_id, {})


def find_product(conn, shop_id: int, mention: str):
    from app.domain.resolve import resolve_product
    from app.services.queries import load_catalog
    _, products = load_catalog(conn, shop_id)
    return resolve_product(mention, products)


def find_customer(conn, shop_id: int, mention: str):
    from app.domain.resolve import resolve_customer
    from app.services.queries import load_catalog
    customers, _ = load_catalog(conn, shop_id)
    return resolve_customer(mention, customers)


def parse_product_args(arg: str) -> dict:
    """Order-free: "Maggi 14 packet", "maggi, packet, Rs14", "Maggi packet 14 rupaye", "14 rs Maggi pkt" all give
    {name: "Maggi", unit: "packet", price_paise: 1400}. Anything missing is None and the caller asks for it."""
    raw = normalize(arg.replace("=", " ").replace("@", " ")).split()
    isnum = lambda t: t.replace(".", "", 1).isdigit()
    toks: list[str] = []
    i = 0
    while i < len(raw):                                 # "500 g" / "200 ml" is a pack size, part of the name
        if isnum(raw[i]) and i + 1 < len(raw) and raw[i + 1] in ("g", "ml"):
            toks.append(raw[i] + raw[i + 1]); i += 2
        else:
            toks.append(raw[i]); i += 1

    def is_unit(i: int) -> bool:                       # bare "g" / "ml" never count: "Parle G" is a name
        return toks[i] in UNITS and toks[i] not in ("g", "ml")
    unit = next((BASE_UNIT_OF[toks[i]] for i in range(len(toks)) if is_unit(i)), None)
    nums = [Decimal(t) for t in toks if isnum(t)]
    price = None
    for i, t in enumerate(toks):                       # "14 rupaye" beats a bare number
        if t == "rupaye" and i > 0 and toks[i - 1].replace(".", "", 1).isdigit():
            price = Decimal(toks[i - 1]); break
    if price is None and len(nums) == 1:
        price = nums[0]
    name = " ".join(t for i, t in enumerate(toks) if not is_unit(i) and not isnum(t) and t not in NOISE).strip()
    name = pretty_name(name)
    return {"name": name or None, "unit": unit, "price_paise": rupees_to_paise(price) if price else None,
            "ambiguous_price": price is None and len(nums) > 1}
