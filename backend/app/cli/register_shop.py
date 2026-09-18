"""Allow a Telegram user to write to a shop.
   python -m app.cli.register_shop --telegram-user-id <id> [--shop "Sharma Kirana Store"] [--role OWNER|STAFF]"""
import argparse

from sqlalchemy import text

from app.db import get_engine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram-user-id", type=int, required=True)
    ap.add_argument("--shop", default=None, help="shop name; default = the first shop")
    ap.add_argument("--role", default="OWNER", choices=["OWNER", "STAFF"])
    ap.add_argument("--name", default=None)
    a = ap.parse_args()
    with get_engine().begin() as c:
        if a.shop:
            sid = c.execute(text("SELECT id FROM shops WHERE name=:n"), {"n": a.shop}).scalar()
            if sid is None:
                sid = c.execute(text("INSERT INTO shops (name) VALUES (:n) RETURNING id"), {"n": a.shop}).scalar_one()
        else:
            sid = c.execute(text("SELECT id FROM shops ORDER BY id LIMIT 1")).scalar()
            if sid is None:
                raise SystemExit("no shop yet: run app.cli.seed_demo or pass --shop")
        c.execute(text("""INSERT INTO shop_members (telegram_user_id, shop_id, role, display_name) VALUES (:u, :s, :r, :n)
                          ON CONFLICT (telegram_user_id) DO UPDATE SET shop_id=EXCLUDED.shop_id, role=EXCLUDED.role"""),
                  {"u": a.telegram_user_id, "s": sid, "r": a.role, "n": a.name})
        print(f"user {a.telegram_user_id} -> shop {sid} ({a.role})")


if __name__ == "__main__":
    main()
