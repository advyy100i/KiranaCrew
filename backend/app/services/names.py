"""Display names for catalog entities."""
import re

_PACK = re.compile(r"(\d) ?(G|Ml|L|Kg)\b")


def pretty_name(name: str) -> str:
    """Title Case, but pack sizes keep their unit lower-case: "bournvita 500 g" -> "Bournvita 500g"."""
    n = " ".join(name.split()).title()
    return _PACK.sub(lambda m: m.group(1) + m.group(2).lower(), n)
