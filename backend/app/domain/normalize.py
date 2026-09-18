"""Deterministic Hindi/Hinglish normalizer: script-agnostic tokens, digits, canonical units and keywords.

normalize("Ramesh ne saade teen sau rupaye diye") -> "ramesh ne 350 rupaye diye"
It never invents a number: "5 5 5 kilo" stays "5 5 5 kg".
"""
import re
import unicodedata
from decimal import Decimal

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

CANON = {
    # units
    "kilo": "kg", "kilos": "kg", "kg": "kg", "kgs": "kg", "kilogram": "kg", "किलो": "kg", "केजी": "kg",
    "gram": "g", "grams": "g", "gm": "g", "gms": "g", "g": "g", "ग्राम": "g",
    "litre": "litre", "litres": "litre", "liter": "litre", "liters": "litre", "ltr": "litre", "लीटर": "litre",
    "ml": "ml", "मिली": "ml",
    "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece", "peace": "piece", "पीस": "piece",
    "nag": "piece", "नग": "piece",
    "packet": "packet", "packets": "packet", "pkt": "packet", "packit": "packet", "पैकेट": "packet",
    "dozen": "dozen", "darjan": "dozen", "दर्जन": "dozen",
    # money
    "rupaye": "rupaye", "rupay": "rupaye", "rupee": "rupaye", "rupees": "rupaye", "rupiya": "rupaye",
    "rupaiye": "rupaye", "rs": "rupaye", "रुपये": "rupaye", "रुपए": "rupaye", "रुपया": "rupaye", "रूपये": "rupaye",
    "rupya": "rupaye", "रु": "rupaye",
    # payment / intent keywords
    "udhaar": "udhaar", "udhar": "udhaar", "udhaari": "udhaar", "उधार": "udhaar", "credit": "udhaar",
    "udhaarpe": "udhaar pe", "udharpe": "udhaar pe", "baaki": "udhaar", "बाकी": "udhaar",
    "cash": "cash", "nakad": "cash", "nagad": "cash", "naqad": "cash", "नकद": "cash", "नगद": "cash", "कैश": "cash",
    "chuka": "chuka", "चुका": "chuka", "chukaya": "chukaya", "चुकाया": "chukaya", "chukaye": "chukaye",
    "चुकाए": "chukaye", "चुकाये": "chukaye", "chukai": "chukai", "chukta": "chukaya", "jama": "jama", "जमा": "jama",
    "wapas": "wapas", "vapas": "wapas", "वापस": "wapas", "lautaya": "lautaya", "lautaye": "lautaye",
    "kharab": "kharab", "khrab": "kharab", "खराब": "kharab", "ख़राब": "kharab", "toot": "toot", "tut": "toot",
    "toota": "toot", "toote": "toot", "टूट": "toot", "टूटा": "toot", "टूटे": "toot", "expire": "expire",
    "expired": "expire", "एक्सपायर": "expire", "chori": "chori", "चोरी": "chori", "सड़": "sad", "sad": "sad",
    "stock": "stock", "स्टॉक": "stock", "maal": "maal", "माल": "maal",
    "aaya": "aaya", "aya": "aaya", "आया": "aaya", "aaye": "aaye", "आए": "aaye", "आये": "aaye",
    "aayi": "aayi", "आई": "aayi", "mila": "mila", "मिला": "mila", "mile": "mile", "मिले": "mile", "mili": "mili",
    "kharida": "kharida", "khareeda": "kharida", "खरीदा": "kharida", "kharide": "kharide", "खरीदे": "kharide",
    "kharidi": "kharidi", "खरीदी": "kharidi",
    "becha": "becha", "बेचा": "becha", "bechi": "bechi", "beche": "beche",
    "liya": "liya", "lia": "liya", "लिया": "liya", "li": "li", "ली": "li", "liye": "liye", "लिए": "liye", "लिये": "liye",
    "le": "le", "ले": "le", "diya": "diya", "दिया": "diya", "di": "di", "दी": "di", "diye": "diye", "दिए": "diye", "दिये": "diye",
    "de": "de", "दे": "de",
    # grammar markers
    "ne": "ne", "nay": "ne", "ने": "ne", "ko": "ko", "को": "ko", "se": "se", "से": "se",
    "ka": "ka", "का": "ka", "ke": "ke", "के": "ke", "ki": "ki", "की": "ki",
    "mein": "mein", "me": "mein", "mai": "mein", "main": "mein", "में": "mein",
    "pe": "pe", "par": "pe", "पे": "pe", "पर": "pe", "aaj": "aaj", "आज": "aaj",
    "ho": "ho", "हो": "ho", "gaya": "gaya", "गया": "gaya", "gayi": "gayi", "गई": "gayi", "gaye": "gaye", "गए": "gaye",
    "kiye": "kiye", "किए": "kiye", "किये": "kiye", "kiya": "kiya", "किया": "kiya",
    "kitna": "kitna", "कितना": "kitna", "kitne": "kitne", "कितने": "kitne", "kya": "kya", "क्या": "kya",
    "aur": "aur", "और": "aur", "bhav": "bhav", "भाव": "bhav", "rate": "rate", "रेट": "rate",
    "hisaab": "hisaab", "hisab": "hisaab", "हिसाब": "hisaab", "prati": "prati", "प्रति": "prati",
}

NUMBER_WORDS = {
    "ek": 1, "एक": 1, "do": 2, "दो": 2, "teen": 3, "tin": 3, "तीन": 3, "char": 4, "chaar": 4, "चार": 4,
    "paanch": 5, "panch": 5, "paach": 5, "पांच": 5, "पाँच": 5, "chhah": 6, "chhe": 6, "chah": 6, "che": 6, "छह": 6, "छः": 6,
    "saat": 7, "सात": 7, "aath": 8, "आठ": 8, "nau": 9, "नौ": 9, "das": 10, "दस": 10,
    "gyarah": 11, "ग्यारह": 11, "barah": 12, "baarah": 12, "बारह": 12, "terah": 13, "तेरह": 13,
    "chaudah": 14, "चौदह": 14, "pandrah": 15, "पंद्रह": 15, "solah": 16, "सोलह": 16, "satrah": 17, "सत्रह": 17,
    "atharah": 18, "अठारह": 18, "unnis": 19, "उन्नीस": 19, "bees": 20, "बीस": 20, "pachees": 25, "पच्चीस": 25,
    "tees": 30, "तीस": 30, "chalis": 40, "chaalis": 40, "चालीस": 40, "pachas": 50, "pachaas": 50, "पचास": 50,
    "saath": 60, "साठ": 60, "sattar": 70, "सत्तर": 70, "assi": 80, "अस्सी": 80, "nabbe": 90, "नब्बे": 90,
}
FRACTIONS = {"aadha": "0.5", "adha": "0.5", "aadhi": "0.5", "आधा": "0.5", "आधी": "0.5",
             "dedh": "1.5", "डेढ़": "1.5", "डेढ": "1.5", "dhai": "2.5", "dhaai": "2.5", "ढाई": "2.5",
             "paav": "0.25", "pav": "0.25", "पाव": "0.25"}
MODIFIERS = {"sawa": "0.25", "सवा": "0.25", "saade": "0.5", "sadhe": "0.5", "साढ़े": "0.5", "paune": "-0.25", "पौने": "-0.25"}
MULTIPLIERS = {"sau": 100, "सौ": 100, "hazaar": 1000, "hazar": 1000, "hajar": 1000, "हज़ार": 1000, "हजार": 1000,
               "lakh": 100000, "lac": 100000, "लाख": 100000}
# Words that are numbers only in numeric context ("do" = two / "give"; "saath" = sixty / "with").
CONTEXT_ONLY = {"do", "दो", "saath"}
UNITS = {"kg", "g", "litre", "ml", "piece", "packet", "dozen"}


def _is_decimal(t: str) -> bool:
    return re.fullmatch(r"\d+(\.\d+)?", t) is not None


def _base_value(t: str) -> Decimal | None:
    if _is_decimal(t):
        return Decimal(t)
    if t in NUMBER_WORDS:
        return Decimal(NUMBER_WORDS[t])
    if t in FRACTIONS:
        return Decimal(FRACTIONS[t])
    return None


def _numeric_context(tokens: list[str], j: int) -> bool:
    nxt = tokens[j + 1] if j + 1 < len(tokens) else ""
    return nxt in UNITS or nxt in MULTIPLIERS or nxt == "rupaye"


def _read_number(tokens: list[str], i: int) -> tuple[int, Decimal | None]:
    """Read one spoken number starting at i. Returns (next index, value) or (i, None).

    Grammar: [modifier] base [multiplier] [remainder]... e.g. "ek hazaar do sau pachas" = 1250,
    "saade teen sau" = 350, "sawa sau" = 125, "sawa" + unit = 1.25. Two adjacent bare numbers stop the read.
    """
    total, cur, mod, j, used, after_mult = Decimal(0), None, None, i, False, False
    while j < len(tokens):
        t = tokens[j]
        nxt = tokens[j + 1] if j + 1 < len(tokens) else ""
        if t in MODIFIERS and cur is None and mod is None:
            if _base_value(nxt) is not None or nxt in MULTIPLIERS:
                mod = Decimal(MODIFIERS[t]); j += 1; continue
            if nxt in UNITS:                               # "sawa kilo" = 1.25 kg, "paune kilo" = 0.75
                cur = Decimal(1) + Decimal(MODIFIERS[t]); used = True; j += 1; break
            break
        if t in MULTIPLIERS:
            if after_mult and (cur is None or MULTIPLIERS[t] < 1000):
                break                                      # "sau sau" is two numbers
            base = cur if cur is not None else Decimal(1)
            if mod is not None and cur is None:            # "sawa sau" = 125
                base = Decimal(1) + mod; mod = None
            m = MULTIPLIERS[t]
            if m >= 1000:
                total += base * m; cur = None
            else:
                cur = base * m
            used, after_mult = True, True; j += 1; continue
        v = _base_value(t)
        if v is None:
            break
        if cur is not None:
            if after_mult and v < 100 and cur % 100 == 0:  # "ek sau bees" = 120
                cur += v; after_mult = False; j += 1; continue
            break                                          # "5 5" is two numbers, not one
        if t in CONTEXT_ONLY and not _numeric_context(tokens, j):
            break
        cur = v + mod if mod is not None else v
        mod, used, after_mult = None, True, False
        j += 1
    if not used:
        return i, None
    return j, total + (cur or 0)


def _fmt(d: Decimal) -> str:
    s = f"{d.normalize():f}"
    return s


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFC", text).lower().translate(DEVANAGARI_DIGITS)
    s = re.sub(r"(?<=\d),(?=\d{2,3}\b)", "", s)                     # 1,000 / 1,00,000 -> 1000 / 100000
    s = re.sub(r"₹\s*(\d+(?:\.\d+)?)", r" \1 rupaye ", s)
    s = re.sub(r"\brs\.?\s*(\d+(?:\.\d+)?)", r" \1 rupaye ", s)
    s = re.sub(r"(\d)([^\d\s.,])", r"\1 \2", s)
    s = re.sub(r"([^\d\s.,])(\d)", r"\1 \2", s)
    s = re.sub(r"[।,!?;:\"'()\[\]{}|/\-–—+*]+", " ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    tokens = " ".join(CANON.get(t, t) for t in s.split()).split()
    out, i = [], 0
    while i < len(tokens):
        j, value = _read_number(tokens, i)
        if value is not None:
            out.append(_fmt(value)); i = j
        else:
            out.append(tokens[i]); i += 1
    return " ".join(out)


def numbers_in(norm: str) -> set[Decimal]:
    """All numeric tokens of a normalized string — the grounding set for LLM output."""
    return {Decimal(t) for t in norm.split() if _is_decimal(t)}
